"""One-shot publication of a verified serving artifact into PostgreSQL.

Flow: inspect (local, no database) -> init (platform namespaces, once) ->
load (isolated candidate schema, Loading -> Imported -> Validated/Failed) ->
activate (atomic pointer + stable views) -> rollback (append-only history).
init/load/activate/rollback/configure-scopes are dry runs unless ``apply=True``;
validate is an explicit operation that records its result (a failure marks the
release Failed and withdraws reader access). Writers are serialized
with a PostgreSQL advisory lock. Only objects in the fixed platform namespaces
are created or changed; other schemas (for example legacy public tables) are
never read, altered or dropped. Store scopes are configured separately and
are never copied from a data release.
"""

import hashlib
import io
import json
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import duckdb

from .artifacts import verified_artifact
from .ingestion import digest
from .postgres_config import sanitize
from .postgres_schema import (INTERFACE_VERSION, RowDigest, contract_sha256, copy_row, from_postgres, load_contract,
                              order_by, pg_type, quote_ident)

SQL_PATH = Path(__file__).resolve().parent / "sql" / "postgres_serving.sql"
META, ACCESS, SERVING, RELEASE_PREFIX = "platform_meta", "platform_access", "platform_serving", "platform_release_"
NAMESPACES = (META, ACCESS, SERVING)
READER_ROLE = "platform_serving_reader"
PUBLISH_LOCK, SCOPE_LOCK = 730_120_261_022_001, 730_120_261_022_002
OPTIONAL_GROUP = ("model_health", "model_monitoring")       # present together or absent together
SOURCE_MEMORY_LIMIT = "1GB"
_RELEASE_ID = re.compile(r"r[0-9a-f]{24}")
_PUBLICATION_ID = re.compile(r"[0-9a-f]{32}")
# Portable provenance copied from the source run record (no paths, scopes or credentials).
PORTABLE_FIELDS = ("artifact_id", "kind", "as_of", "dataset_hash", "source_build_id", "feature_artifact_id",
                   "metric_view", "feature_purpose", "estimate_policy", "estimate_views_status",
                   "available_metric_views", "model_outputs_status", "model_outputs", "readiness", "rows",
                   "application_ready", "database_sha256", "quality_sha256", "code_revision", "code_tree_hash",
                   "base_serving_artifact_id", "base_serving_database_sha256")
RANGE_CHECKS = {
    "oos_predictions": "probability IS NULL OR probability < 0 OR probability > 1 OR threshold < 0 OR threshold > 1",
    "visit_priority": "priority_rank < 1",
    "model_health": "precision < 0 OR precision > 1 OR recall < 0 OR recall > 1 OR roc_auc < 0 OR roc_auc > 1",
}


class PublishError(RuntimeError):
    """A refused or failed publisher operation (message is safe to log)."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _schema(release_id):
    if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
        raise PublishError("Invalid release ID")
    return RELEASE_PREFIX + release_id


# ------------------------------------------------------------------ source inspection (no database)

def inspect_serving_source(artifact_dir):
    """Verify a local serving artifact against the contract; never connects to PostgreSQL.

    The release identity binds the source run record, quality report and database
    bytes (and the contract); any later change to one of them is a different source.
    """
    directory = Path(artifact_dir).resolve()
    run_sha256 = digest(directory / "run.json")
    record, database, cutoff = verified_artifact(artifact_dir, kind="serving")
    if not record.get("application_ready"):
        raise PublishError("Only application-ready serving artifacts can be published")
    contract = load_contract()
    views, rows, grains = [], {}, {}
    with duckdb.connect(str(database), read_only=True) as con:
        present = {r[0][3:] for r in con.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema='serving' AND table_name LIKE 'vw\\_%' ESCAPE '\\'").fetchall()}
        others = con.execute("""SELECT table_name FROM information_schema.tables
            WHERE table_schema='serving' AND table_name NOT LIKE 'vw\\_%' ESCAPE '\\'""").fetchall()
        unexpected = sorted(present - set(contract["views"])) + [r[0] for r in others]
        if unexpected:
            raise PublishError(f"Serving schema contains objects outside the contract: {unexpected}")
        missing = sorted(n for n, s in contract["views"].items() if not s["optional"] and n not in present)
        if missing:
            raise PublishError(f"Required serving views are missing: {missing}")
        if len({n in present for n in OPTIONAL_GROUP}) != 1:
            raise PublishError("Model health and monitoring views must be present together or absent together")
        for name, spec in contract["views"].items():
            if name not in present:
                continue
            columns = [[r[0], r[1]] for r in con.execute(f"DESCRIBE serving.{quote_ident('vw_' + name)}").fetchall()]
            if columns != spec["columns"]:
                raise PublishError(f"View {name} columns/types differ from the PostgreSQL serving contract")
            views.append(name)
            rows[name] = con.execute(f"SELECT count(*) FROM serving.{quote_ident('vw_' + name)}").fetchone()[0]
            key = ", ".join(quote_ident(c) for c in spec["key"])
            grains[name] = con.execute(f"""SELECT count(*) FROM (SELECT {key} FROM serving.{quote_ident('vw_' + name)}
                GROUP BY ALL HAVING count(*) > 1)""").fetchone()[0]
        kpi = list(con.execute("SELECT count(inventory_observed), count(*) FILTER (WHERE oos_observed) FROM serving.vw_store_sku_daily").fetchone()) \
            + list(con.execute("SELECT sum(observed_inventory_sku_count), sum(oos_sku_count) FROM serving.vw_store_day").fetchone())
    violations = {n: c for n, c in grains.items() if c}
    if violations:
        raise PublishError(f"Source violates declared grains: {violations}")
    hashes = {"run": run_sha256, "quality": record["quality_sha256"], "database": record["database_sha256"]}
    if _source_hashes(directory, database) != hashes:
        raise PublishError("Source artifact changed during inspection")
    manifest = {**{k: record[k] for k in PORTABLE_FIELDS if k in record}, "run_sha256": run_sha256}
    text = _canonical(manifest)
    if str(directory) in text or str(database) in text:
        raise PublishError("Source provenance would expose a local path")
    identity = {"interface": INTERFACE_VERSION, "contract_sha256": contract_sha256(), "namespace": "platform",
                "source": {**{k: record.get(k) for k in ("artifact_id", "database_sha256", "quality_sha256",
                                                         "dataset_hash", "source_build_id")},
                           "run_sha256": run_sha256}}
    content_key = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return {"command": "inspect-source", "status": "Verified", "source": manifest, "as_of": str(cutoff),
            "views": views, "rows": rows, "kpi_components": [int(v or 0) for v in kpi],
            "content_key": content_key, "release_id": "r" + content_key[:24],
            "contract_sha256": contract_sha256(), "interface_version": INTERFACE_VERSION,
            "_database": database, "_directory": directory, "_hashes": hashes}


def _source_hashes(directory, database):
    return {"run": digest(directory / "run.json"), "quality": digest(directory / "quality.json"),
            "database": digest(database)}


def _public(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


# ------------------------------------------------------------------ connection helpers

def _connect(config):
    conn = config.connect()
    conn.autocommit = False
    return conn


@contextmanager
def _writer_lock(conn, key, what="publisher"):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cur.fetchone()[0]
    conn.commit()
    if not acquired:
        raise PublishError(f"Another {what} operation holds the writer lock; retry after it finishes")
    try:
        yield
    finally:
        if not conn.closed:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [key])
            conn.commit()


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _platform_state(cur):
    cur.execute("SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)", [list(NAMESPACES)])
    present = sorted(r[0] for r in cur.fetchall())
    version = None
    if META in present and _one(cur, "SELECT to_regclass('platform_meta.schema_version') IS NOT NULL"):
        version = _one(cur, "SELECT interface_version FROM platform_meta.schema_version")
    return present, version


def _require_initialized(cur):
    present, version = _platform_state(cur)
    if version != INTERFACE_VERSION or len(present) != len(NAMESPACES):
        raise PublishError("Platform namespaces are not initialized (run init) or have an unknown version")


def size_report(cur):
    """Relation/index sizes for platform namespaces only; no application data is read."""
    cur.execute(r"""SELECT n.nspname,
            count(*) FILTER (WHERE c.relkind = 'r'), count(*) FILTER (WHERE c.relkind = 'i'),
            count(*) FILTER (WHERE c.relkind = 'v'),
            coalesce(sum(pg_relation_size(c.oid)) FILTER (WHERE c.relkind IN ('r', 't', 'm')), 0),
            coalesce(sum(pg_relation_size(c.oid)) FILTER (WHERE c.relkind = 'i'), 0),
            coalesce(sum(pg_total_relation_size(c.oid)) FILTER (WHERE c.relkind = 'r'), 0)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname LIKE 'platform\_%' GROUP BY 1 ORDER BY 1""")
    schemas = {r[0]: {"tables": r[1], "indexes": r[2], "views": r[3], "table_bytes": int(r[4]),
                      "index_bytes": int(r[5]), "total_bytes": int(r[6])} for r in cur.fetchall()}
    releases = {}
    if _one(cur, "SELECT to_regclass('platform_meta.releases') IS NOT NULL"):
        current = _one(cur, "SELECT release_id FROM platform_meta.current_release") \
            if _one(cur, "SELECT to_regclass('platform_meta.current_release') IS NOT NULL") else None
        cur.execute("SELECT release_id, state FROM platform_meta.releases ORDER BY created_at")
        for release_id, state in cur.fetchall():
            total = schemas.get(RELEASE_PREFIX + release_id, {}).get("total_bytes", 0)
            releases[release_id] = {"state": state, "bytes": total, "current": release_id == current}
    totals = {"current_bytes": sum(r["bytes"] for r in releases.values() if r["current"]),
              "retained_validated_bytes": sum(r["bytes"] for r in releases.values() if r["state"] == "Validated"),
              "candidate_bytes": sum(r["bytes"] for r in releases.values() if r["state"] in ("Loading", "Imported")),
              "failed_bytes": sum(r["bytes"] for r in releases.values() if r["state"] == "Failed"),
              "platform_bytes": sum(s["total_bytes"] for s in schemas.values()),
              "database_bytes": int(_one(cur, "SELECT pg_database_size(current_database())"))}
    return {"schemas": schemas, "releases": releases, "totals": totals}


# ------------------------------------------------------------------ preflight / init

def preflight_postgres(config):
    """SELECT-only diagnostics: version, privileges, platform state and sizes."""
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            info = {"server_version": _one(cur, "SHOW server_version"),
                    "server_version_num": int(_one(cur, "SHOW server_version_num")),
                    "current_user": _one(cur, "SELECT current_user"), "database": _one(cur, "SELECT current_database()"),
                    "superuser": _one(cur, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"),
                    "can_create_schemas": _one(cur, "SELECT has_database_privilege(current_database(), 'CREATE')"),
                    "can_create_roles": _one(cur, "SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user"),
                    "reader_role_exists": bool(_one(cur, "SELECT 1 FROM pg_roles WHERE rolname = %s", [READER_ROLE]))}
            present, version = _platform_state(cur)
            cur.execute(r"""SELECT nspname FROM pg_namespace WHERE nspname LIKE 'platform\_%%'
                AND nspname <> ALL(%s) AND nspname NOT LIKE 'platform\_release\_%%'""", [list(NAMESPACES)])
            unknown = [r[0] for r in cur.fetchall()]
            info["platform"] = {"namespaces_present": present, "interface_version": version,
                                "unknown_platform_namespaces": unknown,
                                "status": ("Initialized" if version == INTERFACE_VERSION and len(present) == 3
                                           else "Not_initialized" if not present and not unknown else "Collision_or_unknown_version")}
            if info["platform"]["status"] == "Initialized":
                cur.execute("""SELECT r.release_id, r.state, r.source_artifact_id, r.manifest->>'as_of'
                    FROM platform_meta.releases r ORDER BY r.created_at""")
                info["releases"] = [{"release_id": a, "state": b, "source_artifact_id": c, "as_of": d}
                                    for a, b, c, d in cur.fetchall()]
                cur.execute("SELECT publication_id, release_id, published_at FROM platform_meta.current_release")
                row = cur.fetchone()
                info["current"] = {"publication_id": row[0], "release_id": row[1], "published_at": str(row[2])} if row else None
            info["size"] = size_report(cur)
        return {"command": "preflight", "status": "Ok", "target": config.describe(), **info}
    finally:
        conn.rollback()
        conn.close()


def initialize_postgres(config, *, apply=False):
    """Create the fixed platform namespaces once; compatible re-runs are no-ops."""
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            present, version = _platform_state(cur)
            role = bool(_one(cur, "SELECT 1 FROM pg_roles WHERE rolname = %s", [READER_ROLE]))
            can_role = _one(cur, "SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user")
        conn.rollback()
        if present and version == INTERFACE_VERSION and len(present) == len(NAMESPACES):
            return {"command": "init", "status": "Already_initialized", "interface_version": version,
                    "target": config.describe()}
        if present:
            raise PublishError(f"Platform namespaces exist without a compatible version marker ({present}, {version}); "
                               "refusing to change them")
        plan = {"create_schemas": list(NAMESPACES), "reader_role": READER_ROLE,
                "create_reader_role": not role}
        if not role and not can_role:
            raise PublishError(f"Role {READER_ROLE} is missing and this login cannot create roles; "
                               "ask the database administrator to create it (NOLOGIN)")
        if not apply:
            return {"command": "init", "status": "Dry_run", "plan": plan, "target": config.describe()}
        with _writer_lock(conn, PUBLISH_LOCK):
            with conn.cursor() as cur:
                if _platform_state(cur)[0]:
                    raise PublishError("Platform namespaces appeared concurrently; re-run init to inspect them")
                if not role:
                    cur.execute(f"CREATE ROLE {READER_ROLE} NOLOGIN")
                cur.execute(SQL_PATH.read_text())
                cur.execute("INSERT INTO platform_meta.schema_version (interface_version) VALUES (%s)", [INTERFACE_VERSION])
                for schema in NAMESPACES:
                    cur.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
                    cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO {READER_ROLE}")
                cur.execute(f"""GRANT SELECT ON platform_meta.published_releases, platform_meta.current_release,
                    platform_access.account_scope, platform_access.scope_policy TO {READER_ROLE}""")
            conn.commit()
        return {"command": "init", "status": "Initialized", "plan": plan, "interface_version": INTERFACE_VERSION,
                "target": config.describe()}
    finally:
        conn.rollback()
        conn.close()


# ------------------------------------------------------------------ load / validate

WRITE_GUARD = "platform_release_write_guard"


def _schema_oid(cur, schema):
    value = _one(cur, "SELECT oid FROM pg_namespace WHERE nspname = %s", [schema])
    return int(value) if value is not None else None


def _owned(cur, release):
    """True if this release's payload schema exists and is the one its import created.

    A schema with the release name that is not provably ours (no recorded OID, or a
    different OID) raises: it may belong to someone else and is never touched.
    """
    schema = _schema(release["release_id"])
    oid = _schema_oid(cur, schema)
    if oid is None:
        return False
    if release.get("payload_schema_oid") is not None and int(release["payload_schema_oid"]) == oid:
        return True
    raise PublishError(f"Schema {schema} exists but was not created by this publisher for this release; "
                       "it is left untouched (resolve the name collision manually)")


def _payloads(cur):
    """Every release payload that currently occupies storage (any lifecycle state)."""
    cur.execute("""SELECT r.release_id, r.state, n.oid IS NOT NULL
        FROM platform_meta.releases r
        LEFT JOIN pg_namespace n ON n.nspname = 'platform_release_' || r.release_id AND n.oid = r.payload_schema_oid
        ORDER BY r.created_at""")
    found = []
    for release_id, state, present in cur.fetchall():
        if present:
            size = _one(cur, """SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'""",
                        [_schema(release_id)])
            found.append({"release_id": release_id, "state": state, "bytes": int(size)})
    return found


def _enforce_capacity(cur, release_id):
    """Refuse a full import if it would exceed the bound on stored payloads; nothing is deleted."""
    limit = load_contract()["max_retained_releases"]
    others = [p for p in _payloads(cur) if p["release_id"] != release_id]
    if len(others) >= limit:
        listing = ", ".join(f"{p['release_id']} ({p['state']}, {p['bytes']} bytes)" for p in others)
        raise PublishError(f"Storage bound reached: {len(others)} stored release payloads (limit {limit}): {listing}. "
                           "No new import until an operator explicitly removes an unneeded payload")


def _drop_own_payload(cur, release):
    """Remove only the objects this release's import created; never CASCADE into other objects."""
    import psycopg2
    contract = load_contract()
    schema = _schema(release["release_id"])
    try:
        for name in release["views"]:
            cur.execute(f"DROP VIEW IF EXISTS {schema}.{quote_ident('vw_' + name)} RESTRICT")
        for name in release["views"]:
            cur.execute(f"DROP TABLE IF EXISTS {schema}.{quote_ident(contract['views'][name]['relation'])} RESTRICT")
        cur.execute(f"DROP SCHEMA {schema} RESTRICT")
    except psycopg2.Error as error:
        raise PublishError(f"Own candidate payload {schema} has other dependent or foreign objects; nothing was "
                           f"removed ({sanitize(error)})") from None


def _create_release(cur, schema, release_id, source):
    contract = load_contract()
    cur.execute(f"CREATE SCHEMA {schema}")
    cur.execute("UPDATE platform_meta.releases SET payload_schema_oid = %s WHERE release_id = %s",
                [_schema_oid(cur, schema), release_id])
    cur.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")
    for name in source["views"]:
        spec = contract["views"][name]
        table = quote_ident(spec["relation"])
        cur.execute(f"CREATE TABLE {schema}.{table} ("
                    + ", ".join(f"{quote_ident(c)} {pg_type(t)}" for c, t in spec["columns"]) + ")")


def _copy_view(duck, cur, schema, name, spec, chunk_rows):
    """Stream one view in contract order; returns its canonical source digest."""
    columns = [c for c, _ in spec["columns"]]
    logicals = [t for _, t in spec["columns"]]
    column_list = ", ".join(quote_ident(c) for c in columns)
    source = duck.execute(f"SELECT {column_list} FROM serving.{quote_ident('vw_' + name)} ORDER BY {order_by(spec)}")
    digest_ = RowDigest()
    target = f"COPY {schema}.{quote_ident(spec['relation'])} ({column_list}) FROM STDIN WITH (FORMAT text)"
    while True:
        rows = source.fetchmany(chunk_rows)
        if not rows:
            break
        buffer = io.StringIO()
        for row in rows:
            buffer.write(copy_row(row, logicals))
            digest_.update(row, logicals)
        buffer.seek(0)
        cur.copy_expert(target, buffer)
    return {"rows": digest_.rows, "sha256": digest_.hexdigest()}


def _view_sha256(cur, relation):
    return hashlib.sha256(_one(cur, "SELECT pg_get_viewdef(%s::regclass)", [relation]).encode()).hexdigest()


def _finish_release(cur, schema, source, digests):
    contract = load_contract()
    for name in source["views"]:
        spec = contract["views"][name]
        table = quote_ident(spec["relation"])
        key = ", ".join(quote_ident(c) for c in spec["key"])
        cur.execute(f"CREATE INDEX ON {schema}.{table} ({key})")
        if spec["store_scoped"] and spec["key"][0] != "store_id":
            extra = ["store_id"] + ([spec["date_column"]] if spec["date_column"] else [])
            cur.execute(f"CREATE INDEX ON {schema}.{table} ({', '.join(quote_ident(c) for c in extra)})")
        view = f"{schema}.{quote_ident('vw_' + name)}"
        cur.execute(f"CREATE VIEW {view} AS SELECT "
                    + ", ".join(quote_ident(c) for c, _ in spec["columns"]) + f" FROM {schema}.{table}")
        cur.execute(f"""CREATE TRIGGER {WRITE_GUARD} BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
            ON {schema}.{table} FOR EACH STATEMENT EXECUTE FUNCTION platform_meta.reject_release_write()""")
        cur.execute(f"ANALYZE {schema}.{table}")
        digests[name]["view_sha256"] = _view_sha256(cur, view)


def _check_release(cur, release):
    """Full verification of one stored payload against its recorded source evidence."""
    contract = load_contract()
    release_id, views = release["release_id"], release["views"]
    row_counts, digests, manifest = release["row_counts"] or {}, release["content_digests"] or {}, release["manifest"]
    schema = _schema(release_id)
    checks, failures = [], []

    def check(name, ok, detail=None):
        checks.append({"check": name, "passed": bool(ok), **({"detail": detail} if detail is not None else {})})
        if not ok:
            failures.append(name)

    oid = _schema_oid(cur, schema)
    check("payload_present", oid is not None)
    check("payload_ownership", oid is not None and release.get("payload_schema_oid") is not None
          and int(release["payload_schema_oid"]) == oid)
    check("evidence_recorded", set(digests) == set(views) and set(row_counts) == set(views))
    if failures:
        return checks, failures
    cur.execute("""SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, collation_name
        FROM information_schema.columns WHERE table_schema = %s ORDER BY table_name, ordinal_position""", [schema])
    actual = {}
    for table, column, data_type, precision, scale, collation in cur.fetchall():
        actual.setdefault(table, []).append((column, data_type, precision, scale, collation))
    for name in views:
        spec = contract["views"][name]
        expected = []
        for column, logical in spec["columns"]:
            mapped = pg_type(logical)
            if mapped.startswith("TEXT"):
                expected.append((column, "text", None, None, "C"))
            elif mapped.startswith("NUMERIC"):
                p, s = re.fullmatch(r"NUMERIC\((\d+),(\d+)\)", mapped).groups()
                expected.append((column, "numeric", int(p), int(s), None))
            else:
                data_type = {"BOOLEAN": "boolean", "DATE": "date", "INTEGER": "integer", "BIGINT": "bigint",
                             "DOUBLE PRECISION": "double precision"}[mapped]
                expected.append((column, data_type, None, None, None))
        got = [(c, t, p if t == "numeric" else None, s if t == "numeric" else None, coll) for c, t, p, s, coll
               in actual.get(spec["relation"], [])]
        check(f"columns:{name}", got == expected)
        view = f"{schema}.{quote_ident('vw_' + name)}"
        present = bool(_one(cur, "SELECT to_regclass(%s) IS NOT NULL", [view]))
        check(f"view_present:{name}", present)
        if present:
            check(f"view_definition:{name}", _view_sha256(cur, view) == digests[name].get("view_sha256"))
        guard = _one(cur, """SELECT tgenabled FROM pg_trigger WHERE tgname = %s
            AND tgrelid = to_regclass(%s)""", [WRITE_GUARD, f"{schema}.{quote_ident(spec['relation'])}"])
        check(f"write_guard:{name}", guard == "O")
    unexpected = sorted(set(actual) - {contract["views"][n]["relation"] for n in views} - {f"vw_{n}" for n in views})
    check("no_unexpected_relations", not unexpected, unexpected or None)
    if failures:
        return checks, failures
    for name in views:
        spec = contract["views"][name]
        relation = f"{schema}.{quote_ident('vw_' + name)}"
        count = _one(cur, f"SELECT count(*) FROM {relation}")
        check(f"row_count:{name}", count == row_counts[name] == digests[name]["rows"],
              {"postgres": count, "source": row_counts[name]})
        key = ", ".join(quote_ident(c) for c in spec["key"])
        dupes = _one(cur, f"SELECT count(*) FROM (SELECT {key} FROM {relation} GROUP BY {key} HAVING count(*) > 1) d")
        check(f"grain:{name}", dupes == 0, dupes or None)
        if name in RANGE_CHECKS:
            check(f"range:{name}", _one(cur, f"SELECT count(*) FROM {relation} WHERE {RANGE_CHECKS[name]}") == 0)
        logicals = [t for _, t in spec["columns"]]
        rows = RowDigest()
        with cur.connection.cursor(name=f"verify_{uuid.uuid4().hex[:12]}") as stream:
            stream.itersize = 10_000
            stream.execute(f"SELECT {', '.join(quote_ident(c) for c, _ in spec['columns'])} FROM {relation} "
                           f"ORDER BY {order_by(spec)}")
            for row in stream:
                rows.update([from_postgres(v, t) for v, t in zip(row, logicals)], logicals)
        check(f"content_digest:{name}", rows.hexdigest() == digests[name]["sha256"] and rows.rows == digests[name]["rows"])
    kpi = [_one(cur, f"SELECT count(inventory_observed) FROM {schema}.vw_store_sku_daily"),
           _one(cur, f"SELECT count(*) FILTER (WHERE oos_observed) FROM {schema}.vw_store_sku_daily"),
           _one(cur, f"SELECT sum(observed_inventory_sku_count) FROM {schema}.vw_store_day"),
           _one(cur, f"SELECT sum(oos_sku_count) FROM {schema}.vw_store_day")]
    kpi = [int(v or 0) for v in kpi]
    check("kpi_components_match_source", kpi == manifest["kpi_components"], {"postgres": kpi})
    check("oos_component_parity", kpi[0] == kpi[2] and kpi[1] == kpi[3])
    return checks, failures


_RELEASE_COLUMNS = ("release_id", "content_key", "state", "manifest", "views", "row_counts", "content_digests",
                    "source_artifact_id", "source_database_sha256", "source_run_sha256", "source_quality_sha256",
                    "payload_schema_oid")


def _release_row(cur, release_id):
    cur.execute(f"SELECT {', '.join(_RELEASE_COLUMNS)} FROM platform_meta.releases WHERE release_id = %s", [release_id])
    row = cur.fetchone()
    return dict(zip(_RELEASE_COLUMNS, row)) if row else None


def _summary(release):
    """What is actually stored for a release (never mixed with a newly inspected source)."""
    manifest = release["manifest"]
    return {"release_id": release["release_id"], "content_key": release["content_key"], "state": release["state"],
            "source": {k: v for k, v in manifest.items() if k != "kpi_components"}, "as_of": manifest.get("as_of"),
            "views": release["views"], "rows": release["row_counts"]}


def _published(cur, release_id):
    return bool(_one(cur, "SELECT 1 FROM platform_meta.publications WHERE release_id = %s LIMIT 1", [release_id]))


def _check_identity(cur, source):
    other = _one(cur, """SELECT release_id FROM platform_meta.releases WHERE source_artifact_id = %s
        AND (source_database_sha256 <> %s OR source_run_sha256 <> %s OR source_quality_sha256 <> %s) LIMIT 1""",
                 [source["source"]["artifact_id"], source["_hashes"]["database"], source["_hashes"]["run"],
                  source["_hashes"]["quality"]])
    if other:
        raise PublishError(f"Release {other} already holds source artifact {source['source']['artifact_id']} with "
                           "different run/quality/database bytes; conflicting source identity")
    existing = _release_row(cur, source["release_id"])
    if existing and existing["content_key"] != source["content_key"]:
        raise PublishError("Release ID collision with different content; refusing")
    return existing


def _lock_payload(cur, release):
    """Block writes to the payload until this transaction ends (verification + switch see one state)."""
    contract = load_contract()
    schema = _schema(release["release_id"])
    if _schema_oid(cur, schema) is None:
        return
    tables = [f"{schema}.{quote_ident(contract['views'][n]['relation'])}" for n in release["views"]
              if _one(cur, "SELECT to_regclass(%s) IS NOT NULL", [f"{schema}.{contract['views'][n]['relation']}"])]
    if tables:
        cur.execute(f"LOCK TABLE {', '.join(tables)} IN SHARE MODE")


def _record_failure(conn, release, checks, failures):
    """Durably mark a payload that failed verification and withdraw reader access to it.

    Publication history and the current pointer are not changed; if the failed release is
    current, the stable platform_serving views stop being readable (fail closed).
    """
    schema = _schema(release["release_id"])
    with conn.cursor() as cur:
        cur.execute("""UPDATE platform_meta.releases SET state = 'Failed', failed_at = now(), checks = %s, error = %s
            WHERE release_id = %s""", [json.dumps(checks), f"Verification failed: {failures}", release["release_id"]])
        if _schema_oid(cur, schema) is not None and release.get("payload_schema_oid") is not None \
                and int(release["payload_schema_oid"]) == _schema_oid(cur, schema):
            cur.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM {READER_ROLE}")
        current = _current(cur)
        if current and current["release_id"] == release["release_id"]:
            cur.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA {SERVING} FROM {READER_ROLE}")
    conn.commit()


def _failure_message(release_id, failures, published):
    recovery = ("It was published: readers can no longer open it. Recover with rollback to an earlier verified "
                "publication or by loading and activating a newly built serving artifact; history is unchanged."
                if published else "It cannot be activated; re-run load for the same source to rebuild this candidate.")
    return f"Release {release_id} failed verification {failures}; marked Failed. {recovery}"


def _verify(conn, release_id):
    """Verify a stored payload under a write lock; failures are recorded and raised."""
    with conn.cursor() as cur:
        release = _release_row(cur, release_id)
        if not release:
            raise PublishError("Unknown release")
        if release["state"] not in ("Imported", "Validated"):
            raise PublishError(f"Release is {release['state']}; only Imported or Validated releases can be verified")
        _lock_payload(cur, release)
        checks, failures = _check_release(cur, release)
        published = _published(cur, release_id)
    if failures:
        conn.rollback()
        _record_failure(conn, release, checks, failures)
        raise PublishError(_failure_message(release_id, failures, published))
    with conn.cursor() as cur:
        cur.execute("""UPDATE platform_meta.releases SET state = 'Validated', checks = %s, error = NULL,
            validated_at = coalesce(validated_at, now()), last_verified_at = now() WHERE release_id = %s""",
                    [json.dumps(checks), release_id])
    conn.commit()
    return {"status": "Validated", "release_id": release_id, "previous_state": release["state"],
            "checks_passed": sum(c["passed"] for c in checks), "checks_failed": []}


def load_postgres_candidate(artifact_dir, config, *, apply=False, chunk_rows=10_000):
    """Import a verified serving artifact as an isolated, unselected candidate release."""
    if type(chunk_rows) is not int or not 1 <= chunk_rows <= 10_000:
        raise PublishError("chunk_rows must be 1..10000")
    source = inspect_serving_source(artifact_dir)
    release_id, schema = source["release_id"], _schema(source["release_id"])
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            _require_initialized(cur)
            existing = _check_identity(cur, source)
            if existing is None and _schema_oid(cur, schema) is not None:
                raise PublishError(f"Schema {schema} already exists and is not a release of this publisher; "
                                   "it is left untouched (resolve the name collision manually)")
            payloads = _payloads(cur)
        conn.rollback()
        if not apply:
            return {**_public(source), "command": "load", "status": "Dry_run",
                    "existing_state": existing["state"] if existing else None, "stored_payloads": payloads,
                    "target": config.describe()}
        with _writer_lock(conn, PUBLISH_LOCK):
            with conn.cursor() as cur:
                existing = _check_identity(cur, source)
            if existing and existing["state"] == "Validated":
                result = _verify(conn, release_id)                       # full recheck; failure recorded and raised
                with conn.cursor() as cur:
                    stored = _summary(_release_row(cur, release_id))
                    size = size_report(cur)
                conn.rollback()
                return {**stored, "command": "load", "status": "Reused_validated_release",
                        "checks_passed": result["checks_passed"], "size": size}
            with conn.cursor() as cur:
                if existing and _published(cur, release_id):
                    raise PublishError("This release was published and later failed verification; it is never "
                                       "reloaded in place. Roll back or load a newly built serving artifact")
                owned = _owned(cur, existing) if existing else (
                    False if _schema_oid(cur, schema) is None else _owned(cur, {"release_id": release_id}))
                _enforce_capacity(cur, release_id)
                if existing:
                    if owned:
                        _drop_own_payload(cur, existing)                  # its own unselected candidate only
                    cur.execute("""UPDATE platform_meta.releases SET state = 'Loading', error = NULL, failed_at = NULL,
                        imported_at = NULL, row_counts = NULL, content_digests = NULL, checks = NULL,
                        payload_schema_oid = NULL WHERE release_id = %s""", [release_id])
                    action = "Restarted_own_candidate"
                else:
                    manifest = {**source["source"], "as_of": source["as_of"], "kpi_components": source["kpi_components"],
                                "contract_sha256": source["contract_sha256"], "interface_version": INTERFACE_VERSION}
                    cur.execute("""INSERT INTO platform_meta.releases (release_id, content_key, state, source_artifact_id,
                        source_database_sha256, source_run_sha256, source_quality_sha256, contract_sha256, manifest, views)
                        VALUES (%s, %s, 'Loading', %s, %s, %s, %s, %s, %s, %s)""",
                                [release_id, source["content_key"], source["source"]["artifact_id"],
                                 source["_hashes"]["database"], source["_hashes"]["run"], source["_hashes"]["quality"],
                                 source["contract_sha256"], json.dumps(manifest), json.dumps(source["views"])])
                    action = "Loaded_new_candidate"
            conn.commit()
            try:
                contract = load_contract()
                with conn.cursor() as cur:
                    _create_release(cur, schema, release_id, source)
                    digests = {}
                    # Bounded source memory; sorts spill to a private temp dir, never beside the artifact.
                    with tempfile.TemporaryDirectory(prefix="pg-export-") as spill, \
                            duckdb.connect(str(source["_database"]), read_only=True,
                                           config={"memory_limit": SOURCE_MEMORY_LIMIT, "threads": 2,
                                                   "temp_directory": spill}) as duck:
                        for name in source["views"]:
                            digests[name] = _copy_view(duck, cur, schema, name, contract["views"][name], chunk_rows)
                    if _source_hashes(source["_directory"], source["_database"]) != source["_hashes"]:
                        raise PublishError("Source run record, quality report or database changed during import")
                    if {n: d["rows"] for n, d in digests.items()} != source["rows"]:
                        raise PublishError("Streamed row counts differ from the inspected source")
                    _finish_release(cur, schema, source, digests)
                    cur.execute("""UPDATE platform_meta.releases SET state = 'Imported', imported_at = now(),
                        row_counts = %s, content_digests = %s WHERE release_id = %s""",
                                [json.dumps(source["rows"]), json.dumps(digests), release_id])
                conn.commit()
            except BaseException as error:
                conn.rollback()
                _mark_failed(conn, release_id, error)
                raise
            result = _verify(conn, release_id)
            with conn.cursor() as cur:
                stored = _summary(_release_row(cur, release_id))
                size = size_report(cur)
            conn.rollback()
            return {**stored, "command": "load", "status": result["status"], "action": action,
                    "checks_passed": result["checks_passed"], "checks_failed": [], "size": size,
                    "contract_sha256": source["contract_sha256"], "interface_version": INTERFACE_VERSION}
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


def _mark_failed(conn, release_id, error):
    if conn.closed:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE platform_meta.releases SET state = 'Failed', failed_at = now(), error = %s
                WHERE release_id = %s AND state IN ('Loading', 'Imported')""",
                        [sanitize(error)[:300], release_id])
        conn.commit()
    except Exception:
        conn.rollback()


def validate_postgres_release(config, release_id):
    """Verify a stored release; a failure is recorded (state Failed, reader access withdrawn) and raised."""
    _schema(release_id)
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            _require_initialized(cur)
        with _writer_lock(conn, PUBLISH_LOCK):
            return {"command": "validate", **_verify(conn, release_id), "target": config.describe()}
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


# ------------------------------------------------------------------ activation / rollback

def _current(cur):
    cur.execute("SELECT publication_id, release_id FROM platform_meta.current_release")
    row = cur.fetchone()
    return {"publication_id": row[0], "release_id": row[1]} if row else None


def _switch(cur, release, action, current):
    contract = load_contract()
    schema = _schema(release["release_id"])
    publication_id = uuid.uuid4().hex
    cur.execute("""INSERT INTO platform_meta.publications (publication_id, release_id, action, previous_publication_id)
        VALUES (%s, %s, %s, %s)""", [publication_id, release["release_id"], action,
                                     current["publication_id"] if current else None])
    cur.execute("""INSERT INTO platform_meta.current_publication (singleton, publication_id) VALUES (TRUE, %s)
        ON CONFLICT (singleton) DO UPDATE SET publication_id = EXCLUDED.publication_id, switched_at = now()""",
                [publication_id])
    cur.execute(f"GRANT USAGE ON SCHEMA {schema} TO {READER_ROLE}")
    for name in contract["views"]:
        stable = f"{SERVING}.{quote_ident('vw_' + name)}"
        cur.execute(f"DROP VIEW IF EXISTS {stable}")
        if name in release["views"]:
            cur.execute(f"GRANT SELECT ON {schema}.{quote_ident('vw_' + name)} TO {READER_ROLE}")
            cur.execute(f"CREATE VIEW {stable} AS SELECT * FROM {schema}.{quote_ident('vw_' + name)}")
            cur.execute(f"GRANT SELECT ON {stable} TO {READER_ROLE}")
    cur.execute("UPDATE platform_meta.releases SET last_verified_at = now() WHERE release_id = %s", [release["release_id"]])
    return publication_id


def _activatable(cur, release_id):
    """Cheap preconditions; the payload itself is fully re-verified inside the switching transaction."""
    release = _release_row(cur, release_id)
    if not release:
        raise PublishError("Unknown release")
    if release["state"] != "Validated":
        raise PublishError(f"Only Validated releases can be activated (state: {release['state']})")
    contract = load_contract()
    missing = [n for n, s in contract["views"].items() if not s["optional"] and n not in release["views"]]
    if missing:
        raise PublishError(f"Release lacks required views: {missing}")
    return release


def _verified_switch(conn, release_id, action, expected_current=None, check_expected=False):
    """Verify the payload and switch to it in one transaction while writes to it are blocked."""
    with conn.cursor() as cur:
        release = _activatable(cur, release_id)
        current = _current(cur)
        if check_expected and (current or {}).get("publication_id") != expected_current:
            raise PublishError("Current publication changed since it was inspected; refusing stale activation")
        already_current = bool(current and current["release_id"] == release_id)
        # Idempotent calls are verified too: an already-current payload is locked and checked
        # before "Already_current" is reported, and a failure takes the same fail-closed path.
        _lock_payload(cur, release)
        checks, failures = _check_release(cur, release)
        published = _published(cur, release_id)
        if not failures:
            if already_current:
                cur.execute("UPDATE platform_meta.releases SET last_verified_at = now() WHERE release_id = %s",
                            [release_id])
                publication_id = None
            else:
                publication_id = _switch(cur, release, action, current)
    if failures:
        conn.rollback()
        _record_failure(conn, release, checks, failures)
        raise PublishError(_failure_message(release_id, failures, published) + " The current publication is unchanged.")
    conn.commit()
    return publication_id, current


def activate_postgres_release(config, release_id, *, apply=False, expected_current_publication_id=None):
    """Atomically point consumers at a Validated, freshly re-verified release (new publication record)."""
    _schema(release_id)
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            _require_initialized(cur)
            _activatable(cur, release_id)
            current = _current(cur)
        conn.rollback()
        if not apply:
            return {"command": "activate", "status": "Dry_run", "release_id": release_id, "current": current,
                    "note": "The payload is fully re-verified when applied", "target": config.describe()}
        with _writer_lock(conn, PUBLISH_LOCK):
            publication_id, current = _verified_switch(
                conn, release_id, "activate", expected_current_publication_id,
                check_expected=expected_current_publication_id is not None)
        if publication_id is None:
            return {"command": "activate", "status": "Already_current", "release_id": release_id,
                    "publication_id": current["publication_id"]}
        return {"command": "activate", "status": "Activated", "release_id": release_id,
                "publication_id": publication_id, "previous_publication_id": (current or {}).get("publication_id")}
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


def rollback_postgres_release(config, publication_id, *, apply=False):
    """Re-point to the release of an earlier publication after re-verifying it; appends history only."""
    if not isinstance(publication_id, str) or not _PUBLICATION_ID.fullmatch(publication_id):
        raise PublishError("Invalid publication ID")
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            _require_initialized(cur)
            release_id = _one(cur, "SELECT release_id FROM platform_meta.publications WHERE publication_id = %s",
                              [publication_id])
            if not release_id:
                raise PublishError("Unknown publication")
            _activatable(cur, release_id)
            current = _current(cur)
        conn.rollback()
        if not apply:
            return {"command": "rollback", "status": "Dry_run", "target_publication_id": publication_id,
                    "release_id": release_id, "current": current, "target": config.describe()}
        with _writer_lock(conn, PUBLISH_LOCK):
            new_id, current = _verified_switch(conn, release_id, f"rollback:{publication_id}")
        if new_id is None:
            return {"command": "rollback", "status": "Already_current", "release_id": release_id,
                    "publication_id": current["publication_id"]}
        return {"command": "rollback", "status": "Rolled_back", "release_id": release_id, "publication_id": new_id,
                "previous_publication_id": (current or {}).get("publication_id"),
                "note": "Data publication only; store scopes are unchanged"}
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


# ------------------------------------------------------------------ scopes

def _scope_policy(policy_file):
    try:
        policy = json.loads(Path(policy_file).read_text())
    except (OSError, ValueError):
        raise PublishError("Scope policy file is unreadable or not JSON") from None
    if not isinstance(policy, dict) or not isinstance(policy.get("version"), str) or not policy["version"].strip() \
            or not isinstance(policy.get("assignments"), list):
        raise PublishError("Scope policy needs a nonblank version and an assignments list")
    rows, seen = [], {}
    for row in policy["assignments"]:
        if not isinstance(row, dict) or set(row) != {"principal_id", "store_id", "role"} \
                or not all(isinstance(row[k], str) and row[k] for k in row) or row["role"] not in ("sales", "manager"):
            raise PublishError("Each assignment needs exactly principal_id, store_id and role (sales/manager)")
        key = (row["principal_id"], row["store_id"])
        if key in seen:
            raise PublishError("Duplicate or conflicting assignment for one principal/store")
        seen[key] = row["role"]
        rows.append((row["principal_id"], row["store_id"], row["role"]))
    rows.sort()
    fingerprint = hashlib.sha256(_canonical({"version": policy["version"], "assignments": rows}).encode()).hexdigest()
    return policy["version"], rows, fingerprint


def configure_postgres_scopes(config, policy_file, *, apply=False):
    """Replace all store-scope assignments atomically (privileged; independent of data releases)."""
    version, rows, fingerprint = _scope_policy(policy_file)
    conn = _connect(config)
    try:
        with conn.cursor() as cur:
            _require_initialized(cur)
            current = _current(cur)
            if not current:
                raise PublishError("No current publication; activate a release before configuring scopes")
            cur.execute(f"SELECT store_id FROM {_schema(current['release_id'])}.vw_stores")
            stores = {r[0] for r in cur.fetchall()}
            unknown = sorted({s for _, s, _ in rows} - stores)
            if unknown:
                raise PublishError(f"{len(unknown)} assignment store IDs are not in the current release")
            previous = _one(cur, "SELECT scope_fingerprint FROM platform_access.scope_policy")
        conn.rollback()
        summary = {"policy_version": version, "assignments": len(rows),
                   "principals": len({p for p, _, _ in rows}), "scope_fingerprint": fingerprint,
                   "previous_fingerprint": previous}
        if not apply:
            return {"command": "configure-scopes", "status": "Dry_run", **summary, "target": config.describe()}
        if previous == fingerprint:
            return {"command": "configure-scopes", "status": "Unchanged", **summary}
        from psycopg2.extras import execute_values
        with _writer_lock(conn, SCOPE_LOCK, "scope configuration"):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM platform_access.account_scope")
                if rows:
                    execute_values(cur, """INSERT INTO platform_access.account_scope (principal_id, store_id, role, scope_version)
                        VALUES %s""", [(p, s, r, version) for p, s, r in rows], page_size=1000)
                cur.execute("""INSERT INTO platform_access.scope_policy (singleton, policy_version, scope_fingerprint, assignment_count)
                    VALUES (TRUE, %s, %s, %s) ON CONFLICT (singleton) DO UPDATE SET policy_version = EXCLUDED.policy_version,
                    scope_fingerprint = EXCLUDED.scope_fingerprint, assignment_count = EXCLUDED.assignment_count,
                    configured_at = now()""", [version, fingerprint, len(rows)])
            conn.commit()
        return {"command": "configure-scopes", "status": "Configured", **summary}
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()
