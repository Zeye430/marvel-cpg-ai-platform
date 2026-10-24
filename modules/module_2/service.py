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
"""

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from data_platform.access import DataUnavailable, ServingReader

from .contracts import Module2Error, read_json

FILTERS = ("channel", "region", "segment_tier", "msl_only", "alert_only", "unknown_only", "search")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
RISK_BINS = 10


def needs_data_followup(row):
    """Single follow-up definition shared by totals, lists and the unknown_only filter:
    any scored store with unknown current inventory or unknown MSL state."""
    return bool(row["inventory_unknown_count"] or row["msl_unknown_count"])


class PermissionDenied(PermissionError):
    pass


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

    def _cached(self, key, loader, fingerprint):
        # key = (kind, principal_id); fingerprint was verified by _authorize for this request.
        # The value built here is returned even if another request clears the cache meanwhile.
        full = (self.record["artifact_id"], fingerprint) + key
        value = self._cache.get(full)
        if value is None:
            value = loader()
            self._cache[full] = value
        return value

    def clear(self, principal_id=None):
        """Logout/session end: drop that principal's cached data."""
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
        rows = []
        for i, row in enumerate(sorted(priorities, key=lambda r: r["priority_rank"]), start=1):
            reason = json.loads(row["reason_json"])
            store = stores.get(row["store_id"], {})
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
                         "scoring_date": str(row["scoring_date"])})
        unscored = sorted(set(stores) - {r["store_id"] for r in rows})
        return rows, stores, unscored

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
            text = (filters.get("search") or "").strip().lower()
            if text and text not in f"{row['store_id']} {row['store_name']}".lower():
                continue
            out.append(row)
        return out

    def get_priorities(self, principal, filters=None, cursor=None, page_size=50):
        self._check(principal)
        if type(page_size) is not int or not 1 <= page_size <= 200:
            raise Module2Error("page_size must be 1..200")
        try:
            fingerprint = self._authorize(principal)
            rows, stores, unscored = self._cached(("priorities", principal.principal_id),
                                                  lambda: self._scoped_priorities(principal), fingerprint)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
        except DataUnavailable as error:
            return self._base("Unreleased", message=str(error))
        full = bool(len(stores)) and self.reader.record.get("rows", {}).get("stores") == len(stores)
        if not full:
            # Partial scopes never see global positions (they would reveal others' rows).
            rows = [{**r, "global_rank": None} for r in rows]
        filtered = self._apply(rows, filters)
        start = int(cursor or 0)
        if start < 0 or start > len(filtered):
            raise Module2Error("Invalid cursor")
        page = filtered[start:start + page_size]
        scoring = date.fromisoformat(rows[0]["scoring_date"]) if rows else None
        totals = {"scoped_stores": len(stores), "ranked_stores": len(rows), "filtered_stores": len(filtered),
                  "alert_stores": sum(1 for r in rows if r["alert_count"]),
                  "msl_gap_stores": sum(1 for r in rows if r["msl_gap_count"]),
                  "data_followup_stores": sum(1 for r in rows if needs_data_followup(r)) + len(unscored),
                  "unscored_stores": unscored, "full_population_scope": bool(full)}
        status = "Empty" if not rows else ("Stale" if self._stale(scoring) else "Ok")
        return self._base(status, rows=page, totals=totals, scope_identity=fingerprint,
                          next_cursor=str(start + page_size) if start + page_size < len(filtered) else None,
                          scope={"principal_id": principal.principal_id, "stores": len(stores),
                                 "rank_semantics": "display_rank is continuous within your scope; global_rank is the "
                                                   "full-population order" if full else
                                                   "display_rank is continuous within your authorized stores only"},
                          coverage={"ranked_of_scoped": f"{len(rows)}/{len(stores)}"},
                          limitations=["Risk index = max SKU probability, not a calibrated store probability",
                                       "Reference sales proxy uses static list price; not lost sales; no currency",
                                       "Ranking is a transparent heuristic, not route optimization"])

    def get_population(self, principal):
        """Aggregates over every authorized store, independent of table pagination."""
        self._check(principal)
        try:
            fingerprint = self._authorize(principal)
            rows, stores, unscored = self._cached(("priorities", principal.principal_id),
                                                  lambda: self._scoped_priorities(principal), fingerprint)
        except PermissionError as error:
            return self._base("Permission_denied", message=str(error))
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

    # ------------------------------------------------------------ detail
    def get_store_detail(self, principal, store_id, scoring_date=None):
        self._check(principal)
        if not isinstance(store_id, str) or not store_id:
            raise Module2Error("store_id required")
        self._authorize(principal)                        # denied/unavailable end the request
        if not self.reader.read_page("stores", principal.principal_id, store_id=store_id)["rows"]:
            raise PermissionDenied("Not available")          # no existence/row-count disclosure
        try:
            predictions = self._all("oos_predictions", principal, store_id=store_id)
        except DataUnavailable as error:
            return self._base("Unreleased", message=str(error))
        as_of = date.fromisoformat(str(self.record["as_of"]))
        scoring = scoring_date or as_of
        products = {p["sku_id"]: p for p in self._all("products", principal)}
        history = self._all("store_sku_daily", principal, store_id=store_id,
                            start=scoring - timedelta(days=28), end=scoring)
        skus = []
        for p in sorted(predictions, key=lambda r: (-r["probability"], r["sku_id"])):
            drivers = json.loads(p["drivers_json"])
            product = products.get(p["sku_id"], {})
            skus.append({"sku_id": p["sku_id"], "product_name": product.get("product_name"),
                         "category": product.get("category"), "is_msl": product.get("is_msl"),
                         "probability": p["probability"], "threshold": p["threshold"],
                         "is_predicted_oos": p["is_predicted_oos"], "input_status": p["input_status"],
                         "threshold_route": drivers["threshold_route"], "evidence": drivers["evidence"],
                         "prediction_window": [drivers["prediction_start"], drivers["prediction_end"]]})
        trail = [{"date": str(h["date"]), "sku_id": h["sku_id"], "units_sold_observed": h["units_sold_observed"],
                  "inventory_observed": h["inventory_observed"], "oos_observed": h["oos_observed"],
                  "inventory_data_status": h["inventory_data_status"],
                  "receipt_units": h["receipt_units_from_deliveries"]} for h in history]
        status = "Stale" if self._stale(scoring) else "Ok"
        return self._base(status, detail={"store_id": store_id, "skus": skus, "history": trail,
                                          "scoring_date": scoring.isoformat()},
                          limitations=["Current reported state and future predicted risk are shown separately",
                                       "Drivers are rule-based context, not causal attribution"])

    # ------------------------------------------------------------ manager views
    def get_model_health(self, principal, model_version=None):
        self._check(principal)
        try:
            fingerprint = self._authorize(principal)
            rows = self._cached(("health", principal.principal_id), lambda: self._all("model_health", principal),
                                fingerprint)
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

    def export_visit_plan(self, principal, filters=None):
        """Complete CSV of the filtered priorities, or an error — never a partial file.

        Every page is checked, and every page must carry the same request-owned scope
        identity as the first page (taken from each response itself, never from shared
        service state another request can change). A denied/unavailable page, a page
        without a verified identity or a scope change aborts the whole export.
        """
        response = self._export_page(self.get_priorities(principal, filters, page_size=200))
        scope = response.scope_identity
        if scope is None:
            raise DataUnavailable("Scope identity could not be verified; no export was produced")
        rows, cursor = list(response.rows), response.next_cursor
        while cursor:
            more = self._export_page(self.get_priorities(principal, filters, cursor=cursor, page_size=200))
            if more.scope_identity != scope:
                raise PermissionDenied("Account scope changed during export; no partial export was produced")
            rows += more.rows
            cursor = more.next_cursor
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["display_rank", "store_id", "store_name", "channel", "region", "segment_tier", "urgency",
                         "risk_score", "msl_gap_count", "alert_skus", "unknown_inventory_skus", "top_skus",
                         "scoring_date", "model_version", "publication_kind"])
        for r in rows:
            writer.writerow([r["display_rank"], r["store_id"], r["store_name"], r["channel"], r["region"],
                             r["segment_tier"], r["bucket_label"], f"{r['risk_score']:.4f}", r["msl_gap_count"],
                             r["alert_count"], r["inventory_unknown_count"],
                             " ".join(s["sku_id"] for s in r["top_skus"][:3]), r["scoring_date"],
                             response.model_version, response.publication_kind])
        return buffer.getvalue()


def demo_scope(store_ids, version="demo-round-robin-v1 (not a customer territory)"):
    """Explicit demo assignment: sorted stores round-robin to Rep A-F; manager gets all."""
    reps = [f"demo:rep-{c}" for c in "abcdef"]
    assignments = [{"principal_id": reps[i % len(reps)], "store_id": s, "role": "sales"}
                   for i, s in enumerate(sorted(store_ids))]
    assignments += [{"principal_id": "demo:manager", "store_id": s, "role": "manager"} for s in sorted(store_ids)]
    return {"version": version, "assignments": assignments,
            "note": "Demo-only assignment generated for local review; not client territories"}
