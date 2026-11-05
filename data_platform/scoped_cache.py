"""Trusted complete-view extraction for application analytics, not a SQL API.

Only verified ServingReader/PostgresServingReader instances and fixed named
views are accepted. All rows are copied in batches into a private DuckDB cache;
the caller receives it only after extraction and a fresh scope check complete.
This adapts published data, never raw CSVs, labels, features or internal marts.
Do not expose this connection or database credentials to model-written SQL.
"""

import re
import uuid
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from .access import DataUnavailable, ServingReader
from .postgres_schema import from_postgres, quote_ident
from .serving import VIEWS

CATALOG = "scoped"
CHECKPOINT_ROWS = 250_000
COPY_STORE_CHUNK = 60
_TYPE = re.compile(r"(?:VARCHAR|BOOLEAN|DATE|INTEGER|BIGINT|HUGEINT|DOUBLE|DECIMAL\(\d+,\d+\))\Z")


@dataclass
class ScopedCache:
    reader: object = field(repr=False)
    principal_id: str
    scope_fingerprint: str
    record: dict
    con: object = field(repr=False)
    row_counts: dict
    closed: bool = False

    def check_access(self):
        if self.closed:
            raise DataUnavailable("Scoped cache is closed")
        try:
            scope = self.reader.principal_scope(self.principal_id)
            if scope["scope_fingerprint"] != self.scope_fingerprint:
                raise PermissionError("Account scope changed; rebuild the scoped cache")
            # Also checks a pinned PostgreSQL release has not been withdrawn.
            self.reader.read("stores", self.principal_id, limit=1)
            return scope
        except Exception:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            self.con.close()


def _batches(reader, principal_id, name, batch_rows, allow_candidate_estimates):
    """Shared PostgreSQL release: stream typed rows in batches inside one read-only transaction."""
    from .postgres_reader import PostgresServingReader
    if not isinstance(reader, PostgresServingReader):
        raise TypeError("A verified shared business reader is required")
    with reader._pool.transaction() as guard:
        reader._guard(guard, name, principal_id, allow_candidate_estimates)
        where = " WHERE store_id IN (SELECT store_id FROM platform_access.account_scope WHERE principal_id=%s)" if VIEWS[name][2] else ""
        # Named cursor avoids buffering a complete cloud view in the driver.
        with guard.connection.cursor(name="scoped_" + uuid.uuid4().hex) as cursor:
            cursor.itersize = batch_rows
            cursor.execute(f"SELECT * FROM {reader._relation(name)}" + where,
                           [principal_id] if where else [])
            types = [t for _, t in reader._columns[name]]
            while rows := cursor.fetchmany(batch_rows):
                yield [tuple(from_postgres(v, t) for v, t in zip(row, types)) for row in rows]


def _copy_local(con, reader, principal_id, names, schema, max_rows, allow_candidate_estimates):
    """Verified local artifact: copy scoped rows inside DuckDB (exact types, no Python row loop).

    The source is attached READ_ONLY only for the copy and detached before the
    cache is returned; every view passes the reader's own guard first.
    """
    from .ingestion import literal
    with duckdb.connect(str(reader.database), read_only=True) as source:
        for name in names:
            reader._guard(source, name, principal_id, allow_candidate_estimates)
    con.execute(f"ATTACH {literal(str(reader.database))} AS _scoped_source (READ_ONLY)")
    try:
        stores = [r[0] for r in con.execute("SELECT DISTINCT store_id FROM _scoped_source.gold.account_scope "
                                            "WHERE principal_id=? ORDER BY store_id", [principal_id]).fetchall()]
        scoped = " WHERE store_id IN (SELECT store_id FROM _scoped_source.gold.account_scope WHERE principal_id=?)"
        total, counts = 0, {}
        for name in names:
            relation = f"_scoped_source.serving.{quote_ident('vw_' + name)}"
            params = [principal_id] if VIEWS[name][2] else []
            total += con.execute(f"SELECT count(*) FROM {relation}" + (scoped if params else ""), params).fetchone()[0]
            if total > max_rows:
                raise DataUnavailable("Complete scoped extraction exceeds the row budget; no partial data returned")
        for name in names:
            relation = f"_scoped_source.serving.{quote_ident('vw_' + name)}"
            columns = ", ".join(quote_ident(c["name"]) for c in schema[name]["columns"])
            # Store chunks bound the uncompressed in-flight data; each chunk is compressed at checkpoint.
            chunks = [stores[i:i + COPY_STORE_CHUNK] for i in range(0, len(stores), COPY_STORE_CHUNK)] \
                if VIEWS[name][2] else [None]
            for chunk in chunks:
                if chunk is None:
                    con.execute(f"INSERT INTO {quote_ident(name)} SELECT {columns} FROM {relation}")
                else:
                    con.execute(f"INSERT INTO {quote_ident(name)} SELECT {columns} FROM {relation}" + scoped +
                                " AND list_contains(?, store_id)", [principal_id, chunk])
                con.execute(f"CHECKPOINT {CATALOG}")
            counts[name] = con.execute(f"SELECT count(*) FROM {quote_ident(name)}").fetchone()[0]
        return counts
    finally:
        con.execute("DETACH _scoped_source")


def materialize_business_views(reader, principal_id, names, *, batch_rows=10000,
                               max_rows=15_000_000, memory_limit="1GB",
                               allow_candidate_estimates=False):
    """Build one private, release-pinned cache or fail; never return a partial total.

    max_rows is a hard failure budget, not a truncation limit. The returned
    connection is for trusted module adapters only; consumers re-check access
    before use and discard on identity/scope change. No cross-user cache reuse.
    """
    from .postgres_reader import PostgresServingReader
    if not isinstance(reader, (ServingReader, PostgresServingReader)):
        raise TypeError("A verified shared business reader is required")
    if not isinstance(principal_id, str) or not principal_id:
        raise PermissionError("An authenticated principal is required")
    names = tuple(names)
    if not names or len(set(names)) != len(names) or any(n not in VIEWS for n in names):
        raise ValueError("Use distinct approved business view names")
    if type(batch_rows) is not int or not 1 <= batch_rows <= 10000 or type(max_rows) is not int or max_rows < 1:
        raise ValueError("Invalid extraction budget")
    scope = reader.principal_scope(principal_id)
    schema = reader.schema()
    con = duckdb.connect(":memory:", config={"threads": 2, "memory_limit": memory_limit,
                                           "autoload_known_extensions": False,
                                           "autoinstall_known_extensions": False})
    cache = ScopedCache(reader, principal_id, scope["scope_fingerprint"], dict(reader.record), con, {})
    total = 0
    try:
        # Compressed in-memory catalog (repeated identifiers/hashes compress well): a fraction
        # of the plain in-memory footprint. Unqualified table names resolve here.
        con.execute(f"ATTACH ':memory:' AS {CATALOG} (COMPRESS)")
        con.execute(f"USE {CATALOG}")
        for name in names:
            if name not in schema:
                raise DataUnavailable(f"Published view unavailable: {name}")
            columns = schema[name]["columns"]
            if any(not _TYPE.fullmatch(c["type"]) for c in columns):
                raise ValueError("Unsupported source type")
            ddl = ", ".join(quote_ident(c["name"]) + " " + c["type"] for c in columns)
            con.execute(f"CREATE TABLE {quote_ident(name)} ({ddl})")
            cache.row_counts[name] = 0
        if isinstance(reader, ServingReader):
            cache.row_counts.update(_copy_local(con, reader, principal_id, names, schema, max_rows,
                                                allow_candidate_estimates))
            names = ()
        for name in names:
            columns = schema[name]["columns"]
            batches = _batches(reader, principal_id, name, batch_rows, allow_candidate_estimates)
            try:
                for rows in batches:
                    total += len(rows)
                    if total > max_rows:
                        raise DataUnavailable("Complete scoped extraction exceeds the row budget; no partial data returned")
                    # Route numerics through exact strings before the declared
                    # target cast: dataframe adapters otherwise turn NaN into
                    # NULL or large Python integers into lossy doubles.
                    encoded = [tuple(str(value) if value is not None and
                                     (column["type"] in {"DOUBLE", "INTEGER", "BIGINT", "HUGEINT"}
                                      or column["type"].startswith("DECIMAL(")) else value
                                     for value, column in zip(row, columns)) for row in rows]
                    frame = pd.DataFrame(encoded, columns=[c["name"] for c in columns], dtype=object)
                    con.register("_batch", frame)
                    try:
                        con.execute(f"INSERT INTO {quote_ident(name)} SELECT * FROM _batch")
                    finally:
                        con.unregister("_batch")
                    cache.row_counts[name] += len(rows)
                    if cache.row_counts[name] % CHECKPOINT_ROWS < len(rows):
                        con.execute(f"CHECKPOINT {CATALOG}")  # bound the uncompressed in-flight data
            finally:
                batches.close()
        # Compression of the in-memory catalog is applied at checkpoint (~15x smaller here).
        con.execute(f"CHECKPOINT {CATALOG}")
        cache.check_access()
        return cache
    except Exception:
        cache.close()
        raise
