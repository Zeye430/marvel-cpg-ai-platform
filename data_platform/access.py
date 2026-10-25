"""Bounded table reads for already-authenticated principals, never arbitrary SQL."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import date

import duckdb
from .artifacts import verified_artifact
from .serving import VIEWS, ESTIMATED_VIEWS, MANAGER_AGGREGATE_VIEWS


class DataUnavailable(RuntimeError):
    pass


# read_page: fixed views, stable keyset order and the filters each view supports.
PAGE_VIEWS = {
    "oos_predictions": (("scoring_run_id", "scoring_date", "store_id", "sku_id"), {"store_id", "sku_id"}),
    "visit_priority": (("scoring_run_id", "scoring_date", "priority_rank", "store_id"), {"store_id"}),
    "store_sku_daily": (("date", "store_id", "sku_id"), {"store_id", "sku_id"}),
    "stores": (("store_id",), {"store_id"}),
    "products": (("sku_id",), {"sku_id"}),
    "model_health": (("model_version", "report_id", "group_kind", "group_id"), set()),
    "model_monitoring": (("monitoring_run_id", "model_version"), set()),
}
MAX_PAGE = 1000


def _plain(value):
    return value.isoformat() if isinstance(value, date) else value


def scope_fingerprint(assignments):
    """Stable digest of one principal's actual (store_id, role, scope_version) assignments."""
    rows = sorted([str(s), str(r), str(v)] for s, r, v in assignments)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def check_page_request(name, page_size, store_id, sku_id, start, end):
    """Shared read_page argument validation; returns (keys, relation, date_column, scoped)."""
    if name not in PAGE_VIEWS or type(page_size) is not int or not 1 <= page_size <= MAX_PAGE:
        raise ValueError("Unsupported paged view or invalid page size")
    keys, filters = PAGE_VIEWS[name]
    relation, date_column, scoped = VIEWS[name]
    for label, value in (("store_id", store_id), ("sku_id", sku_id)):
        if value is not None and (label not in filters or not isinstance(value, str)):
            raise ValueError(f"{label} filter is not supported for {name}")
    if (start is not None or end is not None) and not date_column:
        raise ValueError("This view has no date filter")
    for bound in (start, end):
        if bound is not None and type(bound) is not date:
            raise ValueError("Date filters must be datetime.date values")
    return keys, relation, date_column, scoped


class CursorCodec:
    """HMAC-authenticated keyset cursors bound to their read context (per reader instance)."""

    def __init__(self):
        self._key = secrets.token_bytes(32)

    def encode(self, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_plain).encode()
        tag = hmac.new(self._key, body, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(body).decode() + "." + tag

    def decode(self, cursor, expected, keys):
        try:
            body_text, tag = cursor.rsplit(".", 1)
            body = base64.urlsafe_b64decode(body_text.encode())
        except (ValueError, AttributeError) as error:
            raise ValueError("Invalid page cursor") from error
        if not hmac.compare_digest(hmac.new(self._key, body, hashlib.sha256).hexdigest(), tag):
            raise ValueError("Invalid page cursor")
        payload = json.loads(body)
        if any(payload.get(k) != v for k, v in expected.items()):
            raise ValueError("Page cursor does not match this view, filters, principal or scope")
        last = payload["after"]
        if not isinstance(last, list) or len(last) != len(keys):
            raise ValueError("Invalid page cursor")
        return [date.fromisoformat(v) if k in ("date", "scoring_date") and isinstance(v, str) else v
                for k, v in zip(keys, last)]


class ServingReader:
    backend = "duckdb"

    def __init__(self, artifact_dir):
        self.record,self.database,_=verified_artifact(artifact_dir,kind="serving")
        self._cursors = CursorCodec()

    def _scope(self, con, principal_id):
        rows = con.execute("SELECT role, scope_version, count(*) FROM gold.account_scope WHERE principal_id=? GROUP BY 1,2",
                           [principal_id]).fetchall()
        if not rows:
            raise PermissionError("No configured account scope")
        return rows

    def _manager_full_scope(self, con, principal_id, rows):
        if len(rows) != 1 or rows[0][0] != "manager":
            raise PermissionError("Model health/monitoring aggregates are manager-only")
        missing = con.execute("""SELECT count(*) FROM gold.dim_stores WHERE store_id NOT IN
            (SELECT store_id FROM gold.account_scope WHERE principal_id=?)""", [principal_id]).fetchone()[0]
        if missing:
            raise PermissionError("Global model reports require full-population manager scope")

    def _guard(self, con, name, principal_id, allow_candidate_estimates):
        rows = self._scope(con, principal_id)
        if name in ESTIMATED_VIEWS:
            if self.record.get("estimate_views_status") != "Candidate_not_approved":
                raise DataUnavailable("Candidate estimate views are not present in this publication")
            if allow_candidate_estimates is not True:
                raise DataUnavailable("Candidate estimates require explicit opt-in; not operationally approved")
        if name in {"oos_predictions","visit_priority"} and self.record["model_outputs_status"]!="Available":
            raise DataUnavailable("No released model outputs; missing predictions are not zero risk")
        if name in MANAGER_AGGREGATE_VIEWS:
            self._manager_full_scope(con, principal_id, rows)
            present = con.execute("SELECT count(*) FROM information_schema.views WHERE table_schema='serving' AND table_name=?",
                                  ["vw_"+name]).fetchone()[0]
            if not present:
                raise DataUnavailable("This publication has no model report views")
        return rows

    def read(self,name,principal_id,*,start=None,end=None,limit=1000,allow_candidate_estimates=False):
        if name not in VIEWS or type(limit) is not int or not 1<=limit<=10000:
            raise ValueError("Unknown view or invalid row limit")
        relation,date_column,scoped=VIEWS[name]
        with duckdb.connect(str(self.database),read_only=True) as con:
            # Authentication is the application's responsibility. Principal IDs
            # must come from a verified session, never from an LLM/user query.
            self._guard(con, name, principal_id, allow_candidate_estimates)
            predicates=[];params=[]
            if scoped:
                predicates.append("store_id IN (SELECT store_id FROM gold.account_scope WHERE principal_id=?)")
                params.append(principal_id)
            for bound,operator in ((start,">="),(end,"<=")):
                if bound is not None:
                    if not date_column:
                        raise ValueError("This reference view has no date filter")
                    predicates.append(f"{date_column}{operator}?");params.append(bound)
            sql=f"SELECT * FROM serving.vw_{name}"
            if predicates:
                sql+=" WHERE "+" AND ".join(predicates)
            cursor=con.execute(sql+" ORDER BY ALL LIMIT ?",params+[limit])
            names=[c[0] for c in cursor.description]
            return [dict(zip(names,row)) for row in cursor.fetchall()]

    def _fingerprint(self, con, principal_id):
        return scope_fingerprint(con.execute("SELECT store_id, role, scope_version FROM gold.account_scope WHERE principal_id=?",
                                             [principal_id]).fetchall())

    def read_page(self, name, principal_id, *, start=None, end=None, store_id=None, sku_id=None,
                  after=None, page_size=MAX_PAGE):
        """Keyset pages over a fixed view allowlist; scope is always re-applied."""
        keys, relation, date_column, scoped = check_page_request(name, page_size, store_id, sku_id, start, end)
        with duckdb.connect(str(self.database), read_only=True) as con:
            rows = self._guard(con, name, principal_id, False)
            identity = {"backend": self.backend, "artifact": self.record["artifact_id"], "name": name,
                        "principal": principal_id, "scope_version": sorted(r[1] for r in rows),
                        "scope_fingerprint": self._fingerprint(con, principal_id),
                        "filters": {"start": _plain(start), "end": _plain(end), "store_id": store_id, "sku_id": sku_id}}
            predicates, params = [], []
            if scoped or name == "stores":
                predicates.append("store_id IN (SELECT store_id FROM gold.account_scope WHERE principal_id=?)")
                params.append(principal_id)
            for column, value in (("store_id", store_id), ("sku_id", sku_id)):
                if value is not None:
                    predicates.append(f"{column}=?"); params.append(value)
            for bound, operator in ((start, ">="), (end, "<=")):
                if bound is not None:
                    predicates.append(f"{date_column}{operator}?"); params.append(bound)
            if after is not None:
                values = self._cursors.decode(after, identity, keys)
                predicates.append("(" + ",".join(keys) + ") > (" + ",".join("?" * len(keys)) + ")")
                params += values
            sql = f"SELECT * FROM serving.vw_{name}"
            if predicates:
                sql += " WHERE " + " AND ".join(predicates)
            cursor = con.execute(sql + " ORDER BY " + ",".join(keys) + " LIMIT ?", params + [page_size + 1])
            names = [c[0] for c in cursor.description]
            result = [dict(zip(names, row)) for row in cursor.fetchall()]
        next_cursor = None
        if len(result) > page_size:
            result = result[:page_size]
            next_cursor = self._cursors.encode({**identity, "after": [result[-1][k] for k in keys]})
        return {"rows": result, "next_cursor": next_cursor, "artifact_id": self.record["artifact_id"]}

    def principal_scope(self, principal_id):
        """Role/store-count summary for an authenticated principal (no row data)."""
        with duckdb.connect(str(self.database), read_only=True) as con:
            rows = self._scope(con, principal_id)
            stores = con.execute("SELECT count(DISTINCT store_id) FROM gold.account_scope WHERE principal_id=?",
                                 [principal_id]).fetchone()[0]
            total = con.execute("SELECT count(*) FROM gold.dim_stores").fetchone()[0]
            fingerprint = self._fingerprint(con, principal_id)
        roles = sorted({r[0] for r in rows})
        return {"roles": roles, "stores": stores, "full_population": stores == total,
                "manager_full_scope": roles == ["manager"] and stores == total, "scope_fingerprint": fingerprint}

    def schema(self):
        with duckdb.connect(str(self.database),read_only=True) as con:
            available={r[0] for r in con.execute("SELECT table_name FROM information_schema.views WHERE table_schema='serving'").fetchall()}
            return {name:{"columns":[{"name":r[0],"type":r[1]} for r in con.execute(f"DESCRIBE serving.vw_{name}").fetchall()],
                          "store_scoped":definition[2],"date_column":definition[1],
                          "estimate_release_status":"Candidate_not_approved" if name in ESTIMATED_VIEWS else "Not an estimate view"}
                    for name,definition in VIEWS.items() if "vw_"+name in available}
