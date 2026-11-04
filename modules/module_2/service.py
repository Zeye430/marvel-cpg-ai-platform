"""Module 2 application service: scoped, read-only, JSON-safe responses.

The principal always comes from the server session, never from query text or
a browser control. Every read goes through a shared business reader (the local
DuckDB ServingReader by default, or an injected PostgreSQL reader), which
re-applies account scope; this service adds filters, display ranks, counts and
honest status (Unreleased/Stale/Denied/Unavailable), never invented values.
Every public call first re-checks the principal's current scope. A failed or
malformed check ends that request (Permission_denied / unavailable) and drops the
principal's cached data; cached results are keyed by the verified scope
fingerprint, so a scope change or revocation is never answered from cache.
A request that reads several views re-checks scope, release and session
generation after its last read: if any changed meanwhile, nothing is returned
or cached (a logout/clear during a load is never undone by that load).
"""

import csv
import io
import json
import re
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from data_platform.access import DataUnavailable, ServingReader

from . import business_priority as bp
from .contracts import Module2Error, read_json

FILTERS = ("channel", "region", "segment_tier", "msl_only", "alert_only", "unknown_only", "search", "view", "urgency")
LEGACY_STATUS_FILTERS = ("msl_only", "alert_only", "unknown_only")
# Business views (display projections; they may overlap and never reorder the stored ranking).
VIEWS = ("all", "current_msl", "prevention", "verify_data")
VIEW_LABELS = {"all": "All priorities", "current_msl": "Current MSL gaps", "prevention": "Prevent stockouts",
               "verify_data": "Verify data"}
OBJECTIVES = {"current_msl": "Check shelf stock and replenishment",
              "prevention": "Confirm stock and upcoming replenishment",
              "routine": "Routine store review"}
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
RISK_BINS = 10
CONTEXT_LIMIT = 10000          # reader maximum; a full page is treated as possibly truncated
URGENCY_FILTER = {"urgent": 3, "high": 2, "monitor": 1}
OOS_MEASURES = ("current", "28d")
OOS_WINDOW_DAYS = 28
STORE_FILTERS = ("channel", "region", "segment_tier", "search", "urgency")
ANALYTICS_CACHE_ENTRIES = 32           # completed aggregates only
ANALYTICS_MAX_ROWS = 2_000_000         # beyond this the aggregate is abandoned, never truncated


def needs_data_followup(row):
    """Single follow-up definition shared by totals, lists and the unknown_only filter:
    any scored store with unknown current inventory or unknown MSL state."""
    return bool(row["inventory_unknown_count"] or row["msl_unknown_count"])


class PermissionDenied(PermissionError):
    pass


class ScopeChanged(DataUnavailable):
    """Scope, release or session changed while a request was loading; its result is discarded."""


def _evidence_value(drivers, feature):
    for item in drivers.get("evidence", []):
        if item.get("feature") == feature:
            return item.get("value")
    return None


def in_view(row, view):
    """Shared predicate for summary counts, views and exports."""
    if view == "current_msl":
        return bool(row["msl_gap_count"])
    if view == "prevention":
        return bool(row["prevention_alert_count"])
    if view == "verify_data":
        return bool(row["data_check_reasons"])
    return True


def visit_objective(row, view="all"):
    """Rule-based suggestion: the selected view's task, else the stored bucket's task."""
    verify = "Verify store data: " + "; ".join(r[0].lower() + r[1:] for r in row["data_check_reasons"]) \
        if row["data_check_reasons"] else None
    if view in ("current_msl", "prevention"):
        return OBJECTIVES[view]
    if view == "verify_data" and verify:
        return verify
    if row["bucket"] is None:
        return verify
    if row["bucket"] == 1:
        return OBJECTIVES["current_msl"]
    if row["prevention_alert_count"] is None and verify:
        return verify          # the prediction source could not be matched
    if row["bucket"] == 2:
        return OBJECTIVES["prevention"]
    if row["bucket"] == 3 and verify:
        return verify
    return OBJECTIVES["routine"]


@dataclass(frozen=True)
class Principal:
    principal_id: str
    authenticated: bool
    is_demo: bool = True
    display_name: str = ""


@dataclass
class Response:
    status: str
    data_as_of: str = None
    model_version: str = None
    scoring_run_id: str = None
    publication_kind: str = None
    synthetic: bool = False
    eligibility_status: str = None
    scope: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    limitations: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    next_cursor: str = None
    totals: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)
    message: str = ""
    # Internal, request-owned: the scope fingerprint verified for the request that built
    # this response. Not serialized and not part of equality.
    scope_identity: str = field(default=None, repr=False, compare=False)

    def to_dict(self):
        data = asdict(self)
        data.pop("scope_identity", None)
        return data


def resolve_published(root):
    """Pin the currently published candidate artifact of an isolated root."""
    pointer = Path(root) / "published" / "serving.json"
    if not pointer.exists():
        raise DataUnavailable("No published Module 2 candidate in this root")
    return read_json(pointer, "publication pointer")["directory"]


class Module2Service:
    def __init__(self, artifact_dir=None, *, latest_source_as_of=None, reader=None):
        """Exactly one of ``artifact_dir`` (local DuckDB serving) or ``reader`` (a shared
        business reader, e.g. PostgreSQL) is required.

        ``latest_source_as_of``: newest accepted source date D known to the operator.
        Staleness compares the candidate's data date with that D, never with the
        computer clock (historical demo data is not a pipeline failure).
        """
        if (artifact_dir is None) == (reader is None):
            raise Module2Error("Provide exactly one of artifact_dir or reader")
        if reader is not None and not callable(getattr(reader, "principal_scope", None)):
            raise Module2Error("The reader must provide principal_scope (BusinessReader interface)")
        self.reader = ServingReader(artifact_dir) if reader is None else reader
        self.latest_source_as_of = latest_source_as_of
        self.record = self.reader.record
        self.outputs = self.record.get("model_outputs") or {}
        self._cache = {}
        self._fingerprints = {}

    # ------------------------------------------------------------ helpers
    def _base(self, status, **extra):
        return Response(status=status, data_as_of=self.record.get("as_of"),
                        model_version=self.outputs.get("model_version"),
                        scoring_run_id=self.outputs.get("scoring_run_id"),
                        publication_kind=self.outputs.get("publication_kind", "Base_serving_no_model_outputs"),
                        synthetic=bool(self.outputs.get("synthetic")),
                        eligibility_status=self.outputs.get("eligibility_status"), **extra)

    def _check(self, principal):
        if not isinstance(principal, Principal) or not principal.authenticated or not principal.principal_id:
            raise PermissionDenied("Sign in required")

    def _authorize(self, principal):
        """Verify the principal's current scope for this request and return its fingerprint.

        Any failure (denied, unavailable, missing or malformed identity) clears the
        principal's cached data and is raised, ending the request; nothing is read
        from or written to the cache without a verified fingerprint.
        """
        pid = principal.principal_id
        fingerprints = self.__dict__.setdefault("_fingerprints", {})
        scope = getattr(self.reader, "principal_scope", None)
        if scope is None:
            # Only reachable for objects built without __init__ (unit-test doubles); every
            # service constructed normally requires a reader with principal_scope.
            return None
        try:
            result = scope(pid)
        except (PermissionError, DataUnavailable):
            self.clear(pid)
            raise
        fingerprint = result.get("scope_fingerprint") if isinstance(result, dict) else None
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            self.clear(pid)
            raise DataUnavailable("Scope identity could not be verified; request not served")
        if fingerprints.get(pid) != fingerprint:
            self.clear(pid)
            fingerprints[pid] = fingerprint
        return fingerprint

    def _all(self, name, principal, **filters):
        rows, cursor = [], None
        while True:
            page = self.reader.read_page(name, principal.principal_id, after=cursor, page_size=1000, **filters)
            rows += page["rows"]
            cursor = page["next_cursor"]
            if cursor is None:
                return rows

    def _contexts(self, principal, scoring_date):
        """Scoped price/audit context for the scoring date: {store: {sku: row or None}}, or None
        when it cannot be read completely (never partially or silently zero-filled)."""
        read = getattr(self.reader, "read", None)
        if read is None or scoring_date is None:
            return None
        try:
            rows = read("negotiation_context", principal.principal_id, start=scoring_date, end=scoring_date,
                        limit=CONTEXT_LIMIT)
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001 - a missing/unreadable optional view means "unavailable", never zero
            return None
        if len(rows) >= CONTEXT_LIMIT:
            return None
        out = {}
        for r in rows:
            skus = out.setdefault(r["store_id"], {})
            skus[r["sku_id"]] = None if r["sku_id"] in skus else r      # duplicate rows are not used
        return out

    def _state_lock(self):
        return self.__dict__.setdefault("_state_guard", threading.Lock())

    def _token(self, principal_id):
        """Request token: session generation (changes whenever clear() drops this principal or
        everyone) and the release identity the request starts with."""
        generations = self.__dict__.setdefault("_generations", {})
        return (self.__dict__.get("_epoch", 0), generations.get(principal_id, 0),
                self.reader.record.get("artifact_id"))

    def _verify(self, principal, fingerprint, token):
        """After a multi-read load: the scope, release and session must be the ones the request
        started with. A revoked scope raises PermissionError; any other change ScopeChanged."""
        if self._authorize(principal) != fingerprint:
            raise ScopeChanged("Account scope changed while loading; nothing was shown. Please retry.")
        current = self._token(principal.principal_id)
        if current[2] != token[2]:
            raise ScopeChanged("The data release changed while loading; nothing was shown. Please retry.")
        if current != token:
            raise ScopeChanged("The session ended while loading; nothing was shown.")

    def _store_if_current(self, cache, key, value, principal_id, token):
        """Insert only while the session generation is unchanged (atomic with clear())."""
        with self._state_lock():
            if self._token(principal_id) != token:
                raise ScopeChanged("The session ended while loading; nothing was shown.")
            cache[key] = value

    def _cached(self, key, loader, fingerprint, principal=None):
        # key = (kind, principal_id); fingerprint was verified by _authorize for this request.
        # A fresh load is re-verified (scope, release, session) before it is cached or returned.
        full = (self.record["artifact_id"], fingerprint) + key
        value = self._cache.get(full)
        if value is None:
            token = self._token(key[1])
            value = loader()
            if principal is not None:
                self._verify(principal, fingerprint, token)
            self._store_if_current(self._cache, full, value, key[1], token)
        return value

    def clear(self, principal_id=None):
        """Logout/session end: drop that principal's cached data and invalidate its in-flight loads."""
        with self._state_lock():
            if principal_id is None:
                self.__dict__["_epoch"] = self.__dict__.get("_epoch", 0) + 1
            else:
                generations = self.__dict__.setdefault("_generations", {})
                generations[principal_id] = generations.get(principal_id, 0) + 1
            self.clear_analytics(principal_id)
            if principal_id is None:
                self._cache.clear()
                self.__dict__.get("_fingerprints", {}).clear()
            else:
                for key in [k for k in self._cache if principal_id in k]:
                    del self._cache[key]
                self.__dict__.get("_fingerprints", {}).pop(principal_id, None)

    def _stale(self, scoring_date):
        as_of = date.fromisoformat(str(self.record["as_of"]))
        latest = self.latest_source_as_of or as_of
        return scoring_date is not None and (scoring_date < as_of or as_of < latest)

    # ------------------------------------------------------------ priorities
    def _scoped_priorities(self, principal):
        priorities = self._all("visit_priority", principal)
        stores = {s["store_id"]: s for s in self._all("stores", principal)}
        predictions = {}
        for p in self._all("oos_predictions", principal):       # every published SKU, not the top-five reasons
            predictions.setdefault(p["store_id"], []).append(p)
        scoring_date = str(priorities[0]["scoring_date"]) if priorities else None
        contexts = self._contexts(principal, scoring_date)
        components = {}
        rows = []
        for i, row in enumerate(sorted(priorities, key=lambda r: r["priority_rank"]), start=1):
            reason = json.loads(row["reason_json"])
            store = stores.get(row["store_id"], {})
            mine = predictions.get(row["store_id"], [])
            matched = bool(mine) and all(p["scoring_run_id"] == row["scoring_run_id"]
                                         and str(p["scoring_date"]) == str(row["scoring_date"]) for p in mine)
            prevention = None
            if matched:
                # Known in stock now (reported OOS flag False) and the existing alert decision.
                prevention = sum(1 for p in mine if p["is_predicted_oos"]
                                 and _evidence_value(json.loads(p["drivers_json"]), "oos_observed") is False)
            checks = []
            if reason["inventory_unknown_count"]:
                checks.append(f"Inventory not reported for {reason['inventory_unknown_count']} SKU(s)")
            if reason["msl_unknown_count"]:
                checks.append(f"MSL status unknown for {reason['msl_unknown_count']} SKU(s)")
            if not matched:
                checks.append("Prediction source does not match the ranking (scoring run or date); "
                              "prevention count unavailable")
            comp = bp.store_components(mine if matched else [],
                                       None if contexts is None else contexts.get(row["store_id"], {}),
                                       scoring_date, int(row["msl_gap_count"]), prevention)
            components[row["store_id"]] = comp
            rows.append({"display_rank": i, "global_rank": int(row["priority_rank"]), "store_id": row["store_id"],
                         "store_name": store.get("store_name"), "channel": store.get("channel"),
                         "region": store.get("region"), "segment_tier": store.get("segment_tier"),
                         "bucket": reason["bucket"], "bucket_label": reason["bucket_label"],
                         "risk_score": row["risk_score"], "msl_gap_count": int(row["msl_gap_count"]),
                         "msl_unknown_count": reason["msl_unknown_count"], "alert_count": reason["alert_count"],
                         "inventory_unknown_count": reason["inventory_unknown_count"], "sku_count": reason["sku_count"],
                         "proxy_status": reason["reference_sales_proxy"]["status"],
                         "proxy_known_sum": reason["reference_sales_proxy"]["known_sum"],
                         "impact_fallback": reason["impact_fallback_to_risk"], "top_skus": reason["top_skus"],
                         "scoring_date": str(row["scoring_date"]),
                         "prevention_alert_count": prevention, "data_check_reasons": checks,
                         **self._business_fields(comp)})
        unscored = sorted(set(stores) - {r["store_id"] for r in rows})
        return rows, stores, unscored, components

    @staticmethod
    def _business_fields(comp):
        impact = comp["sales_at_risk"] if comp else {"value": None, "status": "Unavailable", "estimated_skus": 0,
                                                     "sku_count": 0}
        return {"sales_at_risk_usd": impact["value"], "sales_at_risk_status": impact["status"],
                "sales_at_risk_estimated_skus": impact["estimated_skus"],
                "shelf_risk": comp["shelf_risk"] if comp else None, "msl_risk": comp["msl_risk"] if comp else None,
                "urgency": comp["urgency"] if comp else None,
                "urgency_label": bp.URGENCY_LABELS.get(comp["urgency"], "Unavailable") if comp else "Unavailable",
                "current_oos_skus": sum(1 for s in comp["skus"] if s["oos_now"] is True) if comp else None,
                "priority_score": None, "score_missing_components": [], "overall_rank": None,
                "ranking_policy_version": bp.POLICY_VERSION}

    @staticmethod
    def _unranked(stores, unscored, scoring_date):
        """Scoped stores without a usable score: explicit, never given a rank or probability."""
        return [{"display_rank": None, "global_rank": None, "store_id": s, "store_name": stores[s].get("store_name"),
                 "channel": stores[s].get("channel"), "region": stores[s].get("region"),
                 "segment_tier": stores[s].get("segment_tier"), "bucket": None, "bucket_label": "Not ranked",
                 "risk_score": None, "msl_gap_count": None, "msl_unknown_count": None, "alert_count": None,
                 "inventory_unknown_count": None, "sku_count": None, "proxy_status": None, "proxy_known_sum": None,
                 "impact_fallback": None, "top_skus": [], "scoring_date": scoring_date,
                 "prevention_alert_count": None, "data_check_reasons": ["No usable model score for this store"],
                 **Module2Service._business_fields(None)}
                for s in sorted(unscored)]

    def _business_scope(self, principal):
        """Every authorized store with its business-priority projection: score and overall rank are
        computed over this whole set (never filters, pages or other principals' stores)."""
        fingerprint = self._authorize(principal)
        rows, stores, unscored, components = self._cached(("priorities", principal.principal_id),
                                                          lambda: self._scoped_priorities(principal), fingerprint,
                                                          principal)
        scoring = rows[0]["scoring_date"] if rows else str(self.record.get("as_of"))
        everyone = [dict(r) for r in rows] + self._unranked(stores, unscored, scoring)
        pct = bp.impact_percentiles({r["store_id"]: r["sales_at_risk_usd"] for r in everyone})
        for r in everyone:
            r["impact_percentile"] = pct[r["store_id"]]
            r["priority_score"], r["score_missing_components"] = bp.priority_score(
                pct[r["store_id"]], r["shelf_risk"], r["msl_risk"])
            r["risk_category"] = bp.risk_category(r)
        bp.assign_overall_ranks(everyone)
        return fingerprint, rows, stores, unscored, components, everyone

    @staticmethod
    def _apply(rows, filters):
        filters = dict(filters or {})
        unknown = set(filters) - set(FILTERS)
        if unknown:
            raise Module2Error(f"Unsupported filter: {sorted(unknown)}")
        out = []
        for row in rows:
            if any(filters.get(k) and row[k] != filters[k] for k in ("channel", "region", "segment_tier")):
                continue
            if filters.get("msl_only") and not row["msl_gap_count"]:
                continue
            if filters.get("alert_only") and not row["alert_count"]:
                continue
            if filters.get("unknown_only") and not needs_data_followup(row):
                continue
            if filters.get("urgency"):
                level = URGENCY_FILTER.get(filters["urgency"])
                if level is None:
                    raise Module2Error(f"Unsupported urgency filter: {filters['urgency']}")
                if row.get("urgency") != level:
                    continue
            text = (filters.get("search") or "").strip().lower()
            if text and text not in f"{row['store_id']} {row['store_name']}".lower():
                continue
            out.append(row)
        return out

    @staticmethod
    def _parse_sort(sort, view):
        """None keeps the legacy stored order (no view) or the default business order (view)."""
        if sort is None:
            return ("score", True) if view is not None else None
        if isinstance(sort, dict):
            sort = (sort.get("key"), sort.get("direction", "desc"))
        try:
            key, direction = sort
        except (TypeError, ValueError):
            raise Module2Error("sort must be (key, 'asc'|'desc')") from None
        if key not in bp.SORT_KEYS or direction not in ("asc", "desc"):
            raise Module2Error(f"Unsupported sort: {sort}")
        return key, direction == "desc"

    @staticmethod
    def _impact_total(rows):
        known = [r for r in rows if r["sales_at_risk_usd"] is not None]
        incomplete = sum(1 for r in rows if r["sales_at_risk_status"] != "Complete")
        if not known:
            return {"value": None, "status": "Unavailable", "incomplete_stores": incomplete}
        return {"value": sum(r["sales_at_risk_usd"] for r in known),
                "status": "Complete" if not incomplete else "Lower bound", "incomplete_stores": incomplete}

    def get_priorities(self, principal, filters=None, cursor=None, page_size=50, sort=None):
        self._check(principal)
        if type(page_size) is not int or not 1 <= page_size <= 200:
            raise Module2Error("page_size must be 1..200")
        filters = dict(filters or {})
        view = filters.pop("view", None)
        if view is not None:
            if view not in VIEWS:
                raise Module2Error(f"Unsupported view: {view}")
            if any(filters.get(k) for k in LEGACY_STATUS_FILTERS):
                raise Module2Error("Choose a business view or a legacy status filter, not both")
        order = self._parse_sort(sort, view)
        try:
            fingerprint, ranked, stores, unscored, _, everyone = self._business_scope(principal)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except ScopeChanged as error:
            return self._base("Unavailable", message=str(error))
        except DataUnavailable as error:
            return self._base("Unreleased", message=str(error))
        full = bool(len(stores)) and self.reader.record.get("rows", {}).get("stores") == len(stores)
        if not full:
            # Partial scopes never see global positions (they would reveal others' rows).
            everyone = [{**r, "global_rank": None} for r in everyone]
        rows = [r for r in everyone if r["display_rank"] is not None]
        summary = None
        if view is None:
            filtered = self._apply(rows, filters)       # legacy behaviour: ranked stores only, stored order
        else:
            base = self._apply(everyone, filters)
            summary = {"stores": len(base), **{v: sum(1 for r in base if in_view(r, v)) for v in VIEWS[1:]}}
            filtered = [{**r, "visit_objective": visit_objective(r, view)} for r in base if in_view(r, view)]
        if order is not None:
            filtered = bp.sort_rows(filtered, *order)    # the whole filtered set, before paging/export
        start = int(cursor or 0)
        if start < 0 or start > len(filtered):
            raise Module2Error("Invalid cursor")
        page = filtered[start:start + page_size]
        scoring = date.fromisoformat(rows[0]["scoring_date"]) if rows else None
        totals = {"scoped_stores": len(stores), "ranked_stores": len(rows), "filtered_stores": len(filtered),
                  "alert_stores": sum(1 for r in rows if r["alert_count"]),
                  "msl_gap_stores": sum(1 for r in rows if r["msl_gap_count"]),
                  "data_followup_stores": sum(1 for r in rows if needs_data_followup(r)) + len(unscored),
                  "unscored_stores": unscored, "full_population_scope": bool(full),
                  "prevention_alert_stores": sum(1 for r in rows if r["prevention_alert_count"]),
                  "policy": {"version": bp.POLICY_VERSION, "weights": dict(bp.WEIGHTS),
                             "price_basis": bp.PRICE_BASES[bp.PRICE_BASIS][1], "window_days": bp.WINDOW_DAYS,
                             "audit_max_age_days": bp.AUDIT_MAX_AGE_DAYS, "status": "Proposed, not approved"}}
        if order is not None:
            totals["sort"] = {"key": order[0], "direction": "desc" if order[1] else "asc"}
        if summary is not None:
            totals["summary"] = summary         # after channel/region/tier/search, before the view
            totals["view"] = view
            totals["focus"] = {"urgent": sum(1 for r in base if r["urgency"] == 3),
                               "prevention": summary["prevention"], "verify_data": summary["verify_data"]}
            totals["stats"] = {"sales_at_risk": self._impact_total(base),
                               "action_stores": sum(1 for r in base if (r["urgency"] or 0) >= 2),
                               "msl_gap_skus": sum(r["msl_gap_count"] or 0 for r in base),
                               "data_check_stores": summary["verify_data"]}
            regions = {}
            for r in base:
                regions.setdefault(r["region"] or "Unknown region", []).append(r)
            totals["regions"] = [{"region": name, "stores": len(group),
                                  "action_stores": sum(1 for r in group if (r["urgency"] or 0) >= 2),
                                  "sales_at_risk": self._impact_total(group)}
                                 for name, group in sorted(regions.items())]
        status = "Empty" if not rows else ("Stale" if self._stale(scoring) else "Ok")
        return self._base(status, rows=page, totals=totals, scope_identity=fingerprint,
                          next_cursor=str(start + page_size) if start + page_size < len(filtered) else None,
                          scope={"principal_id": principal.principal_id, "stores": len(stores),
                                 "rank_semantics": "overall_rank orders your authorized stores by the proposed "
                                                   "business-priority score; sorting never changes it" + (
                                                       "; global_rank is the full-population stored order"
                                                       if full else "")},
                          coverage={"ranked_of_scoped": f"{len(rows)}/{len(stores)}"},
                          limitations=["Risk index = max SKU probability, not a calibrated store probability",
                                       "Sales at risk is a 7-day scenario at the stated price basis, not lost net "
                                       "revenue or profit; incomplete inputs make it a lower bound or unavailable",
                                       "Business-priority weights and cut-offs are proposals, not approved policy",
                                       "Ranking is a transparent heuristic, not route optimization"])

    def get_population(self, principal):
        """Aggregates over every authorized store, independent of table pagination."""
        self._check(principal)
        try:
            fingerprint = self._authorize(principal)
            rows, stores, unscored, _ = self._cached(("priorities", principal.principal_id),
                                                     lambda: self._scoped_priorities(principal), fingerprint,
                                                     principal)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except ScopeChanged as error:
            return self._base("Unavailable", message=str(error))
        except DataUnavailable as error:
            return self._base("Unreleased", message=str(error))
        counts = [0] * RISK_BINS
        for r in rows:
            counts[min(int(r["risk_score"] * RISK_BINS), RISK_BINS - 1)] += 1
        followups = [{"store_id": r["store_id"], "store_name": r["store_name"], "display_rank": r["display_rank"],
                      "bucket_label": r["bucket_label"], "inventory_unknown_count": r["inventory_unknown_count"],
                      "msl_unknown_count": r["msl_unknown_count"], "scored": True}
                     for r in rows if needs_data_followup(r)]
        followups += [{"store_id": s, "store_name": stores.get(s, {}).get("store_name"), "display_rank": None,
                       "bucket_label": "No valid model score", "inventory_unknown_count": None,
                       "msl_unknown_count": None, "scored": False} for s in unscored]
        return self._base("Ok" if rows else "Empty", detail={
            "risk_histogram": [{"lower": i / RISK_BINS, "upper": (i + 1) / RISK_BINS, "stores": c}
                               for i, c in enumerate(counts)],
            "followups": followups, "followup_definition": "unknown current inventory or MSL state, or no valid score",
            "scored_stores": len(rows), "scoped_stores": len(stores)},
            totals={"data_followup_stores": len(followups), "ranked_stores": len(rows), "scoped_stores": len(stores)})

    # ------------------------------------------------------------ out-of-stock analytics
    @contextmanager
    def _analytics_lock(self, key):
        """One in-flight build per key. Locks are reference-counted and removed when the last
        holder or waiter leaves, so unique filter combinations do not accumulate locks."""
        locks = self.__dict__.setdefault("_analytics_locks", {})
        guard = self.__dict__.setdefault("_analytics_guard", threading.Lock())
        with guard:
            entry = locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with guard:
                entry[1] -= 1
                if entry[1] == 0 and locks.get(key) is entry:
                    del locks[key]

    def _analytics_cache(self):
        return self.__dict__.setdefault("_analytics", OrderedDict())

    def clear_analytics(self, principal_id=None):
        cache = self._analytics_cache()
        for key in [k for k in cache if principal_id is None or principal_id in k]:
            del cache[key]

    def get_oos_analytics(self, principal, filters=None, measure="current"):
        """Which SKUs are reported out of stock most, over the principal's (filtered) stores.

        ``current``: reported state on the as-of date only (a previous observation is not current).
        ``28d``: distinct reported OOS store x SKU x day observations in [as_of-27, as_of].
        Missing/null observations are excluded and disclosed; MSL comes from each observation
        (store x SKU x day), with unknown kept separate. The aggregate is built page by page,
        abandoned on any failure, limit, scope or release change, and cached only when complete.
        """
        self._check(principal)
        if measure not in OOS_MEASURES:
            raise Module2Error(f"Unsupported measure: {measure}")
        filters = {k: v for k, v in dict(filters or {}).items() if v not in (None, "", False)}
        unknown = set(filters) - set(STORE_FILTERS)
        if unknown:
            raise Module2Error(f"Unsupported analytics filter: {sorted(unknown)}")
        try:
            fingerprint, _, stores, _, _, everyone = self._business_scope(principal)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except DataUnavailable as error:
            return self._base("Unavailable", message=str(error))
        token = self._token(principal.principal_id)
        as_of = date.fromisoformat(str(self.record["as_of"]))
        start = as_of if measure == "current" else as_of - timedelta(days=OOS_WINDOW_DAYS - 1)
        key = (type(self.reader).__name__, self.record.get("artifact_id"), principal.principal_id, fingerprint,
               measure, start.isoformat(), as_of.isoformat(), tuple(sorted((k, str(v)) for k, v in filters.items())))
        cache = self._analytics_cache()
        with self._analytics_lock(key):                      # one in-flight build per key
            try:
                if key in cache:
                    self._verify(principal, fingerprint, token)    # every cache use rechecks scope and session
                    cache.move_to_end(key)
                    return self._base("Ok", detail=cache[key], scope_identity=fingerprint)
                base = self._apply(everyone, filters)
                detail = self._aggregate_oos(principal, {r["store_id"]: r for r in base}, start, as_of, measure)
                self._verify(principal, fingerprint, token)
                with self._state_lock():                      # atomic with clear(): a logout is never undone
                    if self._token(principal.principal_id) != token:
                        raise ScopeChanged("The session ended while loading; nothing was shown.")
                    cache[key] = detail
                    while len(cache) > ANALYTICS_CACHE_ENTRIES:
                        cache.popitem(last=False)
            except PermissionError as error:
                self.clear_analytics(principal.principal_id)
                return self._base("Permission_denied", message=str(error))
            except ScopeChanged as error:
                self.clear_analytics(principal.principal_id)
                return self._base("Unavailable", message=str(error))
            except (DataUnavailable, ValueError) as error:
                return self._base("Unavailable", message=f"Out-of-stock analytics could not be completed: {error}")
        return self._base("Ok", detail=detail, scope_identity=fingerprint)

    def _aggregate_oos(self, principal, base, start, end, measure):
        products = {p["sku_id"]: p for p in self._all("products", principal)}
        per_sku, per_day = {}, {}
        totals = {"rows": 0, "pages": 0, "observed": 0, "unknown": 0, "duplicates": 0, "oos": 0,
                  "msl": {"MSL": 0, "Non-MSL": 0, "Unknown": 0}}
        stores_seen, previous, cursor = set(), None, None
        while True:
            page = self.reader.read_page("store_sku_daily", principal.principal_id, start=start, end=end,
                                         after=cursor, page_size=1000)
            totals["pages"] += 1
            for row in page["rows"]:
                totals["rows"] += 1
                if totals["rows"] > ANALYTICS_MAX_ROWS:
                    raise DataUnavailable("Row limit reached; no partial result is shown")
                if row["store_id"] not in base:
                    continue
                grain = (str(row["date"]), row["store_id"], row["sku_id"])
                if grain == previous:                    # keyset order makes duplicates adjacent
                    totals["duplicates"] += 1
                    continue
                previous = grain
                stores_seen.add(row["store_id"])
                sku = per_sku.setdefault(row["sku_id"], {"oos": 0, "observed": 0, "unknown": 0, "stores": {},
                                                         "msl": {"MSL": 0, "Non-MSL": 0, "Unknown": 0},
                                                         "labels": set()})
                sku["labels"].add("MSL" if row["is_msl"] is True else ("Non-MSL" if row["is_msl"] is False else "Unknown"))
                day = per_day.setdefault(grain[0], {"oos": 0, "observed": 0, "unknown": 0})
                if row["oos_observed"] is None:
                    sku["unknown"] += 1; day["unknown"] += 1; totals["unknown"] += 1
                    continue
                sku["observed"] += 1; day["observed"] += 1; totals["observed"] += 1
                if row["oos_observed"]:
                    group = "MSL" if row["is_msl"] is True else ("Non-MSL" if row["is_msl"] is False else "Unknown")
                    sku["oos"] += 1; day["oos"] += 1; totals["oos"] += 1
                    sku["msl"][group] += 1; totals["msl"][group] += 1
                    # Grouped per store too, so an MSL filter selects the same population everywhere.
                    per_store = sku["stores"].setdefault(row["store_id"], {"MSL": 0, "Non-MSL": 0, "Unknown": 0})
                    per_store[group] += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        unit = "stores out of stock now" if measure == "current" else "store-days out of stock"
        skus = []
        for sku_id, s in per_sku.items():
            groups = sorted(s["labels"])
            skus.append({"sku_id": sku_id, "product_name": products.get(sku_id, {}).get("product_name"),
                         "oos": s["oos"], "observed": s["observed"], "unknown": s["unknown"],
                         "msl": s["msl"], "msl_group": groups[0] if len(groups) == 1 else ("Mixed" if groups else None),
                         "stores": [{"store_id": sid, "store_name": base[sid]["store_name"], "oos": sum(m.values()),
                                     "msl": dict(m), "overall_rank": base[sid]["overall_rank"]}
                                    for sid, m in sorted(s["stores"].items(),
                                                         key=lambda x: (-sum(x[1].values()), x[0]))]})
        skus.sort(key=lambda s: (-s["oos"], s["sku_id"]))
        return {"measure": measure, "unit": unit, "window": [start.isoformat(), end.isoformat()],
                "skus": skus, "totals": totals, "stores_in_filter": len(base),
                "stores_with_observations": len(stores_seen),
                "daily": [{"date": d, **v} for d, v in sorted(per_day.items())] if measure == "28d" else [],
                "coverage_note": "Counts use reported observations only. The applicable store x SKU assortment is "
                                 "not confirmed, so expected days and rates are not shown; unreported "
                                 "observations are listed as unknown, not as in stock.",
                "definition": ("Distinct stores whose reported state on the as-of date is out of stock"
                               if measure == "current" else
                               "Distinct reported out-of-stock store x SKU x day observations in the window")}

    # ------------------------------------------------------------ detail
    def get_store_detail(self, principal, store_id, scoring_date=None):
        self._check(principal)
        if not isinstance(store_id, str) or not store_id:
            raise Module2Error("store_id required")
        fingerprint = self._authorize(principal)          # denied/unavailable end the request
        token = self._token(principal.principal_id)
        if not self.reader.read_page("stores", principal.principal_id, store_id=store_id)["rows"]:
            raise PermissionDenied("Not available")          # no existence/row-count disclosure
        try:
            predictions = self._all("oos_predictions", principal, store_id=store_id)
        except DataUnavailable as error:
            return self._base("Unreleased", message=str(error))
        as_of = date.fromisoformat(str(self.record["as_of"]))
        scoring = scoring_date or as_of
        products = {p["sku_id"]: p for p in self._all("products", principal)}
        history = self._all("store_sku_daily", principal, store_id=store_id,       # 30 days ending at as-of
                            start=scoring - timedelta(days=29), end=scoring)
        trail = [{"date": str(h["date"]), "sku_id": h["sku_id"], "units_sold_observed": h["units_sold_observed"],
                  "inventory_observed": h["inventory_observed"], "oos_observed": h["oos_observed"],
                  "inventory_data_status": h["inventory_data_status"], "is_msl": h.get("is_msl"),
                  "receipt_units": h["receipt_units_from_deliveries"]} for h in history]
        # MSL is store x SKU x day evidence: the as-of report's own value, never the product default.
        # Without an as-of report (or with a null value) it stays unknown.
        msl_as_of = {h["sku_id"]: h["is_msl"] for h in trail if h["date"] == scoring.isoformat()}
        skus = []
        for p in sorted(predictions, key=lambda r: (-r["probability"], r["sku_id"])):
            drivers = json.loads(p["drivers_json"])
            product = products.get(p["sku_id"], {})
            skus.append({"sku_id": p["sku_id"], "product_name": product.get("product_name"),
                         "category": product.get("category"), "is_msl": msl_as_of.get(p["sku_id"]),
                         "probability": p["probability"], "threshold": p["threshold"],
                         "is_predicted_oos": p["is_predicted_oos"], "input_status": p["input_status"],
                         "threshold_route": drivers["threshold_route"], "evidence": drivers["evidence"],
                         "prediction_window": [drivers["prediction_start"], drivers["prediction_end"]]})
        latest = {}
        for h in trail:
            if h["sku_id"] not in latest or h["date"] > latest[h["sku_id"]]["date"]:
                latest[h["sku_id"]] = h
        # Each row keeps its own reported date and MSL value (a past row is not the as-of state).
        reported_now = [{**h, "product_name": products.get(h["sku_id"], {}).get("product_name"),
                         "is_as_of": h["date"] == scoring.isoformat()}
                        for h in sorted(latest.values(), key=lambda h: h["sku_id"])]
        store, business = None, None
        try:
            _, _, _, _, components, everyone = self._business_scope(principal)
            match = next((r for r in everyone if r["store_id"] == store_id), None)
            if match is not None:
                store = {**match, "visit_objective": visit_objective(match)}
                business = self._business_detail(store, components.get(store_id), trail, scoring)
        except DataUnavailable:
            store = None
        try:
            self._verify(principal, fingerprint, token)    # nothing read under an older scope is returned
        except ScopeChanged as error:
            return self._base("Unavailable", message=str(error))
        status = "Stale" if self._stale(scoring) else "Ok"
        return self._base(status, detail={"store_id": store_id, "skus": skus, "history": trail,
                                          "scoring_date": scoring.isoformat(), "reported_now": reported_now,
                                          "store": store, "business": business},
                          limitations=["Current reported state and future predicted risk are shown separately",
                                       "Drivers are rule-based context, not causal attribution"])

    @staticmethod
    def _oos_days(trail, as_of):
        """Reported OOS days per SKU over the inclusive 28-day window [as_of-27, as_of].

        Counts only rows that exist: ``unreported_days`` are rows whose stock state was not
        reported. Days with no row at all are NOT counted as missing reports, because the
        published data does not confirm the SKU's applicable assortment on those days; the
        number of days with a row is shown instead. ``current`` / ``is_msl`` come from the
        as-of row only (never carried forward); ``as_of_report`` says whether that row exists
        ("reported"), exists without a stock state ("unreported") or is absent ("missing").
        """
        first = (as_of - timedelta(days=OOS_WINDOW_DAYS - 1)).isoformat() if as_of else None
        out, seen = {}, set()
        for h in sorted(trail, key=lambda r: (r["date"], r["sku_id"])):
            if first and h["date"] < first or (h["date"], h["sku_id"]) in seen:
                continue
            seen.add((h["date"], h["sku_id"]))
            counts = out.setdefault(h["sku_id"], {"reported_oos_days": 0, "reported_days": 0, "unreported_days": 0,
                                                  "days_with_rows": 0, "window_days": OOS_WINDOW_DAYS,
                                                  "current": None, "is_msl": None, "as_of_report": "missing"})
            counts["days_with_rows"] += 1
            if h["oos_observed"] is None:
                counts["unreported_days"] += 1
            else:
                counts["reported_days"] += 1
                counts["reported_oos_days"] += bool(h["oos_observed"])
            if as_of and h["date"] == as_of.isoformat():
                counts["current"] = h["oos_observed"]
                counts["is_msl"] = h.get("is_msl")
                counts["as_of_report"] = "unreported" if h["oos_observed"] is None else "reported"
        return out

    OOS_COVERAGE_NOTE = ("Counts use the rows in the published data. Unreported = a row exists but its stock state "
                         "was not reported. Days with no row are not counted as missing reports: whether the SKU was "
                         "expected in the store on those days is not confirmed, so days with data are shown instead.")

    @staticmethod
    def _business_detail(store, comp, trail, as_of=None):
        """Store analysis for the detail page: SKU scenario estimates, latest audit, input coverage,
        detail-only confidence and rule-based actions. Nothing here is a shelf-loss amount."""
        if comp is None:
            first = (as_of - timedelta(days=OOS_WINDOW_DAYS - 1)).isoformat() if as_of else None
            return {"available": False, "confidence": "Unavailable", "skus": {}, "audits": {}, "actions": [
                "Verify store data: no usable model score for this store"], "coverage": {},
                "oos_days": Module2Service._oos_days(trail, as_of),
                "oos_window": [first, as_of.isoformat() if as_of else None],
                "oos_coverage_note": Module2Service.OOS_COVERAGE_NOTE}
        skus = {s["sku_id"]: s for s in comp["skus"]}
        oos_days = Module2Service._oos_days(trail, as_of)
        projection = {s: bp.scenario_path(e["inventory"], e["velocity"], e["scheduled"], e["days_until_next_receipt"])
                      for s, e in skus.items()}
        # MSL for actions = the as-of report's store x SKU value (as in the analytics view); unknown is not MSL.
        msl_as_of = {s: d["is_msl"] for s, d in oos_days.items()}
        msl_now = sorted(s for s, e in skus.items() if msl_as_of.get(s) is True and e["oos_now"] is True)
        other_now = sorted(s for s, e in skus.items() if msl_as_of.get(s) is not True and e["oos_now"] is True)
        prevent = sorted(s for s, e in skus.items() if e["oos_now"] is False and e["is_predicted_oos"])
        noncompliant = sorted(s for s, a in comp["audits"].items() if a["status"] == "Fresh" and a["compliant"] is False)
        actions = []
        if msl_now:
            actions.append("Check shelf and backroom stock for MSL SKU(s): " + ", ".join(msl_now) + ".")
        if other_now:
            actions.append("Check replenishment for out-of-stock SKU(s): " + ", ".join(other_now) + ".")
        if prevent:
            actions.append("Confirm the next delivery for " + ", ".join(prevent)
                           + " (predicted stockout risk in the next 7 days).")
        if noncompliant:
            dates = sorted({comp["audits"][s]["date"] for s in noncompliant})
            actions.append(f"Check planogram compliance: the latest audit ({dates[-1]}) found {len(noncompliant)} "
                           "SKU(s) non-compliant.")
        if store["data_check_reasons"]:
            actions.append("Verify store data: " + "; ".join(store["data_check_reasons"]) + ".")
        missing = [s for s, e in skus.items() if not e["estimable"]]
        if missing:
            actions.append(f"Sales at risk excludes {len(missing)} SKU(s) with missing inputs; see estimate quality.")
        if not actions:
            actions.append("Routine store review.")
        total = len(skus)
        coverage = {"skus": total,
                    "inventory": sum(1 for e in skus.values() if e["inventory"] is not None),
                    "sales_rate": sum(1 for e in skus.values() if e["velocity"] is not None),
                    "schedule": sum(1 for e in skus.values() if e["scheduled"] is not None),
                    "price": sum(1 for e in skus.values() if e["price"] is not None),
                    "fresh_audit": comp["audit_fresh_skus"], "estimated": sum(1 for e in skus.values() if e["estimable"])}
        return {"available": True, "confidence": bp.confidence(comp), "skus": skus, "audits": comp["audits"],
                "actions": actions, "coverage": coverage, "oos_days": oos_days, "projection": projection,
                "oos_window": [(as_of - timedelta(days=OOS_WINDOW_DAYS - 1)).isoformat() if as_of else None,
                               as_of.isoformat() if as_of else None],
                "oos_coverage_note": Module2Service.OOS_COVERAGE_NOTE,
                "price_basis": bp.PRICE_BASES[bp.PRICE_BASIS][1], "weights": dict(bp.WEIGHTS),
                "audit_max_age_days": bp.AUDIT_MAX_AGE_DAYS, "policy_version": bp.POLICY_VERSION,
                "assumptions": [
                    "Units short = max(0, 7 x prior 28-day in-stock daily sales - current reported inventory - units "
                    "on the known schedule for the next 7 days); receipts already delivered are in the inventory.",
                    "Scheduled deliveries are assumed to arrive; they are not guaranteed receipts.",
                    "Valued at the " + bp.PRICE_BASES[bp.PRICE_BASIS][1].lower() + " in USD (Owner-confirmed currency; "
                    "the source does not state currency). The brief's list-price basis is pending client confirmation.",
                    "This is Marvel's sell-in exposure under a scenario, not recognized net revenue or profit; "
                    "delayed or substituted purchases are not observed.",
                    "Shelf risk is the share of SKUs non-compliant at the latest audit within "
                    f"{bp.AUDIT_MAX_AGE_DAYS} days; it has no dollar value and a past audit is not proof of today's shelf."]}

    # ------------------------------------------------------------ manager views
    def get_model_health(self, principal, model_version=None):
        self._check(principal)
        try:
            fingerprint = self._authorize(principal)
            rows = self._cached(("health", principal.principal_id), lambda: self._all("model_health", principal),
                                fingerprint, principal)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except DataUnavailable as error:
            return self._base("Unavailable", message=str(error))
        if model_version:
            rows = [r for r in rows if r["model_version"] == model_version]
        clean = [{k: (str(v) if isinstance(v, date) else v) for k, v in r.items()} for r in rows]
        return self._base("Ok" if clean else "Empty", rows=clean,
                          limitations=["Development results informed selection; validation is iterative; June is a "
                                       "disclosed holdout confirmation, not an untouched independent test"])

    def get_monitoring(self, principal, window=None):
        self._check(principal)
        try:
            self._authorize(principal)
            rows = self._all("model_monitoring", principal)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except DataUnavailable as error:
            return self._base("Unavailable", message=str(error))
        clean = [{k: (str(v) if isinstance(v, date) else v) for k, v in r.items()} for r in rows]
        pending = all(r["performance_status"] == "Pending_outcomes" for r in clean) if clean else True
        return self._base("Pending" if pending else "Ok", rows=clean,
                          limitations=["Monitoring evaluates issued predictions after outcomes mature; it is not the release test"])

    @staticmethod
    def _export_page(response):
        if response.status == "Permission_denied":
            raise PermissionDenied(response.message)
        if response.status == "Unreleased":
            raise DataUnavailable(response.message)
        return response

    def export_visit_plan(self, principal, filters=None, sort=None):
        """Complete CSV of the filtered priorities, or an error — never a partial file.

        Every page is checked, and every page must carry the same request-owned scope
        identity as the first page (taken from each response itself, never from shared
        service state another request can change). A denied/unavailable page, a page
        without a verified identity or a scope change aborts the whole export.
        """
        response = self._export_page(self.get_priorities(principal, filters, page_size=200, sort=sort))
        scope = response.scope_identity
        if scope is None:
            raise DataUnavailable("Scope identity could not be verified; no export was produced")
        rows, cursor = list(response.rows), response.next_cursor
        while cursor:
            more = self._export_page(self.get_priorities(principal, filters, cursor=cursor, page_size=200, sort=sort))
            if more.scope_identity != scope:
                raise PermissionDenied("Account scope changed during export; no partial export was produced")
            rows += more.rows
            cursor = more.next_cursor
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        # Legacy columns keep their order; the business columns are appended.
        writer.writerow(["display_rank", "store_id", "store_name", "channel", "region", "segment_tier", "urgency",
                         "risk_score", "msl_gap_count", "alert_skus", "unknown_inventory_skus", "top_skus",
                         "scoring_date", "model_version", "publication_kind",
                         "visit_objective", "prevention_alert_skus", "data_checks",
                         "overall_rank", "priority_score", "sales_at_risk_usd", "sales_at_risk_status",
                         "shelf_risk", "msl_risk", "urgency_level", "risk_category", "ranking_policy_version"])

        def blank(value):
            return "" if value is None else value
        for r in rows:
            writer.writerow([blank(r["display_rank"]), r["store_id"], r["store_name"], r["channel"], r["region"],
                             r["segment_tier"], r["bucket_label"],
                             "" if r["risk_score"] is None else f"{r['risk_score']:.4f}", blank(r["msl_gap_count"]),
                             blank(r["alert_count"]), blank(r["inventory_unknown_count"]),
                             " ".join(s["sku_id"] for s in r["top_skus"][:3]), r["scoring_date"],
                             response.model_version, response.publication_kind,
                             r.get("visit_objective") or visit_objective(r), blank(r["prevention_alert_count"]),
                             "; ".join(r["data_check_reasons"]),
                             blank(r["overall_rank"]), "" if r["priority_score"] is None else f"{r['priority_score']:.1f}",
                             "" if r["sales_at_risk_usd"] is None else f"{r['sales_at_risk_usd']:.2f}",
                             r["sales_at_risk_status"], "" if r["shelf_risk"] is None else f"{r['shelf_risk']:.1f}",
                             "" if r["msl_risk"] is None else f"{r['msl_risk']:.1f}", r["urgency_label"],
                             r.get("risk_category", ""), r["ranking_policy_version"]])
        return buffer.getvalue()


def demo_scope(store_ids, version="demo-round-robin-v1 (not a customer territory)"):
    """Explicit demo assignment: sorted stores round-robin to Rep A-F; manager gets all."""
    reps = [f"demo:rep-{c}" for c in "abcdef"]
    assignments = [{"principal_id": reps[i % len(reps)], "store_id": s, "role": "sales"}
                   for i, s in enumerate(sorted(store_ids))]
    assignments += [{"principal_id": "demo:manager", "store_id": s, "role": "manager"} for s in sorted(store_ids)]
    return {"version": version, "assignments": assignments,
            "note": "Demo-only assignment generated for local review; not client territories"}
