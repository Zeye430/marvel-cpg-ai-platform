"""Scoped, bounded reads from a published PostgreSQL serving release.

Same operations, limits, ordering and error categories as the local DuckDB
``ServingReader``. The release is resolved once and pinned for the reader's
lifetime, so every read and every page comes from one immutable release even
if an operator activates another; a new reader picks up the new release.
Each call runs its scope check and data query in one short read-only
transaction (no transaction or cursor stays open between calls), so a scope
change or revocation applies to the very next call. Credentials are trusted
backend secrets: never hand this object or its connection to a browser or LLM.
"""

import re
import threading
from contextlib import contextmanager
from datetime import date

from .access import (DataUnavailable, ESTIMATED_VIEWS, MANAGER_AGGREGATE_VIEWS, MAX_PAGE, CursorCodec, _plain,
                     check_page_request, scope_fingerprint)
from .postgres_config import sanitize
from .postgres_schema import from_postgres, load_contract, pg_type, quote_ident
from .serving import VIEWS

_RELEASE_ID = re.compile(r"r[0-9a-f]{24}")
SCOPE_SQL = "SELECT store_id FROM platform_access.account_scope WHERE principal_id = %s"


def _as_date(value):
    """read() accepts dates or ISO date strings (as the DuckDB reader does); anything else is rejected."""
    if value is None or type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("Date filters must be dates or ISO date strings")


class _Connections:
    """At most ``config.max_connections`` short-lived connections per reader; idle ones are reused."""

    def __init__(self, config):
        self.config = config
        self._slots = threading.BoundedSemaphore(config.max_connections)
        self._idle, self._lock, self.closed = [], threading.Lock(), False

    @contextmanager
    def transaction(self):
        if self.closed:
            raise DataUnavailable("Reader is closed")
        if not self._slots.acquire(timeout=self.config.connect_timeout + self.config.statement_timeout_ms / 1000):
            raise DataUnavailable("Shared data service is busy; retry")
        conn = None
        try:
            with self._lock:
                conn = self._idle.pop() if self._idle else None
            if conn is None or conn.closed:
                try:
                    conn = self.config.connect()
                except ConnectionError as error:
                    raise DataUnavailable(f"Shared data service unavailable ({error})") from None
                conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
            import psycopg2
            try:
                with conn.cursor() as cur:
                    yield cur
                conn.commit()
            except psycopg2.Error as error:
                conn.rollback()
                raise DataUnavailable(f"Shared data query failed ({sanitize(error, self.config)})") from None
            except BaseException:
                if not conn.closed:
                    conn.rollback()
                raise
        finally:
            if conn is not None:
                with self._lock:
                    if conn.closed or self.closed:
                        conn.close()
                    else:
                        self._idle.append(conn)
            self._slots.release()

    def close(self):
        with self._lock:
            self.closed = True
            for conn in self._idle:
                conn.close()
            self._idle.clear()


class PostgresServingReader:
    backend = "postgres"

    def __init__(self, config, *, release_id=None):
        if config.purpose not in ("app", "test"):
            raise ValueError("The serving reader uses the read-only application configuration")
        if release_id is not None and (not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id)):
            raise ValueError("Invalid release ID")
        self.config = config
        self._pool = _Connections(config)
        self._cursors = CursorCodec()
        contract = load_contract()
        with self._pool.transaction() as cur:
            if release_id is None:
                cur.execute("SELECT publication_id, release_id FROM platform_meta.current_release")
                row = cur.fetchone()
                if not row:
                    raise DataUnavailable("No published shared data release")
                publication_id, release_id = row
            else:
                publication_id = None
            cur.execute("""SELECT release_id, source_artifact_id, manifest, views, row_counts
                FROM platform_meta.published_releases WHERE release_id = %s""", [release_id])
            row = cur.fetchone()
            if not row:
                raise DataUnavailable("Release is not a published, validated shared data release")
        _, source_artifact_id, manifest, views, row_counts = row
        self.release_id = release_id
        self._schema = "platform_release_" + release_id
        self._views = frozenset(views)
        self._columns = {n: contract["views"][n]["columns"] for n in views}
        self.record = {**manifest, "backend": "postgres", "artifact_id": release_id, "release_id": release_id,
                       "source_artifact_id": source_artifact_id, "publication_id": publication_id,
                       "rows": {**manifest.get("rows", {}), **row_counts}, "views": sorted(views)}

    # ------------------------------------------------------------------ helpers
    def close(self):
        self._pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _relation(self, name):
        return f"{self._schema}.{quote_ident('vw_' + name)}"

    def _rows(self, cur, name):
        logicals = dict(self._columns[name])
        names = [c[0] for c in cur.description]
        return [{n: from_postgres(v, logicals[n]) for n, v in zip(names, row)} for row in cur.fetchall()]

    def _scope(self, cur, principal_id):
        if not isinstance(principal_id, str) or not principal_id:
            raise PermissionError("No configured account scope")
        cur.execute("""SELECT role, scope_version, count(*) FROM platform_access.account_scope
            WHERE principal_id = %s GROUP BY 1, 2""", [principal_id])
        rows = cur.fetchall()
        if not rows:
            raise PermissionError("No configured account scope")
        return rows

    def _fingerprint(self, cur, principal_id):
        cur.execute("SELECT store_id, role, scope_version FROM platform_access.account_scope WHERE principal_id = %s",
                    [principal_id])
        return scope_fingerprint(cur.fetchall())

    def _guard(self, cur, name, principal_id, allow_candidate_estimates):
        rows = self._scope(cur, principal_id)
        if name in ESTIMATED_VIEWS:
            if self.record.get("estimate_views_status") != "Candidate_not_approved" or name not in self._views:
                raise DataUnavailable("Candidate estimate views are not present in this publication")
            if allow_candidate_estimates is not True:
                raise DataUnavailable("Candidate estimates require explicit opt-in; not operationally approved")
        if name in {"oos_predictions", "visit_priority"} and self.record.get("model_outputs_status") != "Available":
            raise DataUnavailable("No released model outputs; missing predictions are not zero risk")
        if name in MANAGER_AGGREGATE_VIEWS:
            if len(rows) != 1 or rows[0][0] != "manager":
                raise PermissionError("Model health/monitoring aggregates are manager-only")
            cur.execute(f"SELECT count(*) FROM {self._relation('stores')} WHERE store_id NOT IN ({SCOPE_SQL})",
                        [principal_id])
            if cur.fetchone()[0]:
                raise PermissionError("Global model reports require full-population manager scope")
            if name not in self._views:
                raise DataUnavailable("This publication has no model report views")
        if name not in self._views:
            raise DataUnavailable("This view is not present in the published release")
        return rows

    # ------------------------------------------------------------------ public API (same as ServingReader)
    def read(self, name, principal_id, *, start=None, end=None, limit=1000, allow_candidate_estimates=False):
        if name not in VIEWS or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("Unknown view or invalid row limit")
        relation, date_column, scoped = VIEWS[name]
        start, end = (_as_date(start), _as_date(end))
        with self._pool.transaction() as cur:
            self._guard(cur, name, principal_id, allow_candidate_estimates)
            predicates, params = [], []
            if scoped:
                predicates.append(f"store_id IN ({SCOPE_SQL})")
                params.append(principal_id)
            for bound, operator in ((start, ">="), (end, "<=")):
                if bound is not None:
                    if not date_column:
                        raise ValueError("This reference view has no date filter")
                    predicates.append(f"{quote_ident(date_column)} {operator} %s")
                    params.append(bound)
            order = ", ".join(f"{quote_ident(c)} ASC NULLS LAST" for c, _ in self._columns[name])
            sql = f"SELECT * FROM {self._relation(name)}"
            if predicates:
                sql += " WHERE " + " AND ".join(predicates)
            cur.execute(sql + f" ORDER BY {order} LIMIT %s", params + [limit])
            return self._rows(cur, name)

    def read_page(self, name, principal_id, *, start=None, end=None, store_id=None, sku_id=None,
                  after=None, page_size=MAX_PAGE):
        """Keyset pages over the fixed view allowlist; scope is re-checked on every page."""
        keys, relation, date_column, scoped = check_page_request(name, page_size, store_id, sku_id, start, end)
        with self._pool.transaction() as cur:
            rows = self._guard(cur, name, principal_id, False)
            identity = {"backend": self.backend, "artifact": self.release_id, "name": name, "principal": principal_id,
                        "scope_version": sorted(r[1] for r in rows),
                        "scope_fingerprint": self._fingerprint(cur, principal_id),
                        "filters": {"start": _plain(start), "end": _plain(end), "store_id": store_id, "sku_id": sku_id}}
            predicates, params = [], []
            if scoped or name == "stores":
                predicates.append(f"store_id IN ({SCOPE_SQL})")
                params.append(principal_id)
            for column, value in (("store_id", store_id), ("sku_id", sku_id)):
                if value is not None:
                    predicates.append(f"{quote_ident(column)} = %s")
                    params.append(value)
            for bound, operator in ((start, ">="), (end, "<=")):
                if bound is not None:
                    predicates.append(f"{quote_ident(date_column)} {operator} %s")
                    params.append(bound)
            if after is not None:
                values = self._cursors.decode(after, identity, keys)
                predicates.append("(" + ", ".join(quote_ident(k) for k in keys) + ") > ("
                                  + ", ".join(["%s"] * len(keys)) + ")")
                params += values
            sql = f"SELECT * FROM {self._relation(name)}"
            if predicates:
                sql += " WHERE " + " AND ".join(predicates)
            cur.execute(sql + " ORDER BY " + ", ".join(quote_ident(k) for k in keys) + " LIMIT %s",
                        params + [page_size + 1])
            result = self._rows(cur, name)
        next_cursor = None
        if len(result) > page_size:
            result = result[:page_size]
            next_cursor = self._cursors.encode({**identity, "after": [result[-1][k] for k in keys]})
        return {"rows": result, "next_cursor": next_cursor, "artifact_id": self.release_id}

    def principal_scope(self, principal_id):
        """Role/store-count summary for an authenticated principal (no row data)."""
        with self._pool.transaction() as cur:
            rows = self._scope(cur, principal_id)
            cur.execute(f"""SELECT count(DISTINCT a.store_id) FROM platform_access.account_scope a
                JOIN {self._relation('stores')} s ON s.store_id = a.store_id WHERE a.principal_id = %s""", [principal_id])
            stores = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM {self._relation('stores')}")
            total = cur.fetchone()[0]
            fingerprint = self._fingerprint(cur, principal_id)
        roles = sorted({r[0] for r in rows})
        return {"roles": roles, "stores": stores, "full_population": stores == total,
                "manager_full_scope": roles == ["manager"] and stores == total, "scope_fingerprint": fingerprint}

    def schema(self):
        """Logical (source) column types per present view, with the PostgreSQL storage type as metadata."""
        return {name: {"columns": [{"name": c, "type": t, "backend_type": pg_type(t)} for c, t in self._columns[name]],
                       "store_scoped": definition[2], "date_column": definition[1],
                       "estimate_release_status": "Candidate_not_approved" if name in ESTIMATED_VIEWS else "Not an estimate view"}
                for name, definition in VIEWS.items() if name in self._views}
