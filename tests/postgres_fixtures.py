"""Synthetic sources and a guarded disposable PostgreSQL for shared-serving tests.

The test server is named only by SHARED_POSTGRES_TEST_DATABASE_URL (never the
application/operator variables or a legacy DATABASE_URL). Before any setup or
teardown the target must be a numeric loopback address, the marked test
database, and a server started with the disposable-test marker setting
(compose.postgres-test.yml). Each test class gets its own throwaway database
and login roles inside that server; only those are dropped afterwards.
With SHARED_POSTGRES_REQUIRE_TEST_DB=1 an unavailable server is a failure,
otherwise the database tests are skipped.
"""

import atexit
import importlib.util
import ipaddress
import json
import os
import secrets
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

TEST_URL_ENV = "SHARED_POSTGRES_TEST_DATABASE_URL"
REQUIRE_ENV = "SHARED_POSTGRES_REQUIRE_TEST_DB"
ADMIN_DATABASE = "shared_serving_synthetic_test"
MARKER_SETTING, MARKER_VALUE = "shared_serving.synthetic_test_marker", "disposable-synthetic-v1"
TEST_DB_PREFIX = "shared_serving_test_"
HAS_DRIVER = importlib.util.find_spec("psycopg2") is not None
HAS_ML = all(importlib.util.find_spec(n) for n in ("sklearn", "pyarrow"))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


class UnsafeTarget(RuntimeError):
    pass


def check_test_url(url):
    """Static guard: loopback numeric host and the marked database name only."""
    if not url:
        raise UnsafeTarget(f"{TEST_URL_ENV} is not set")
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise UnsafeTarget("Test URL must be postgresql://")
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise UnsafeTarget("Test database must be on a numeric loopback address")
    if parts.path.lstrip("/") != ADMIN_DATABASE:
        raise UnsafeTarget(f"Test URL must name the {ADMIN_DATABASE} database")
    return parts


def check_marker(cur):
    cur.execute("SELECT current_setting(%s, true), current_database()", [MARKER_SETTING])
    value, database = cur.fetchone()
    if value != MARKER_VALUE:
        raise UnsafeTarget("Server lacks the disposable-test marker; refusing to touch it")
    return database


def require_database():
    """Skip (or, with SHARED_POSTGRES_REQUIRE_TEST_DB=1, fail) when the test server is unusable."""
    required = os.environ.get(REQUIRE_ENV) == "1"
    problem = None
    password = urlsplit(os.environ.get(TEST_URL_ENV, "")).password or ""
    if not HAS_DRIVER:
        problem = "psycopg2 is not installed (postgres extra)"
    elif not HAS_ML:
        problem = "synthetic serving fixtures need the module2 extra"
    else:
        try:
            check_test_url(os.environ.get(TEST_URL_ENV))
            import psycopg2
            with psycopg2.connect(os.environ[TEST_URL_ENV], connect_timeout=5) as conn, conn.cursor() as cur:
                check_marker(cur)
            conn.close()
        except Exception as error:                      # report without credentials
            problem = f"{type(error).__name__}: {str(error).splitlines()[0][:200] if str(error) else ''}"
            if password:
                problem = problem.replace(password, "***")
    if problem:
        if required:
            raise AssertionError(f"Required PostgreSQL test database unavailable: {problem}")
        raise unittest.SkipTest(f"PostgreSQL test database unavailable: {problem}")


def _admin():
    import psycopg2
    check_test_url(os.environ.get(TEST_URL_ENV))
    conn = psycopg2.connect(os.environ[TEST_URL_ENV], connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        if check_marker(cur) != ADMIN_DATABASE:
            raise UnsafeTarget("Unexpected admin database")
    return conn


class TestDatabase:
    """One throwaway database plus publisher/app login roles inside the marked server."""

    def __init__(self):
        from data_platform.postgres_config import PostgresConfig
        self.suffix = secrets.token_hex(5)
        self.name = TEST_DB_PREFIX + self.suffix
        self.publisher_role, self.app_role = f"sst_pub_{self.suffix}", f"sst_app_{self.suffix}"
        self.passwords = {self.publisher_role: secrets.token_urlsafe(18), self.app_role: secrets.token_urlsafe(18)}
        admin = _admin()
        try:
            with admin.cursor() as cur:
                cur.execute(f"CREATE DATABASE {self.name}")
                for role in (self.publisher_role, self.app_role):
                    cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE",
                                [self.passwords[role]])
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'platform_serving_reader'")
                if not cur.fetchone():
                    cur.execute("CREATE ROLE platform_serving_reader NOLOGIN")
                cur.execute(f"GRANT platform_serving_reader TO {self.app_role}")
                cur.execute(f"GRANT CREATE, CONNECT ON DATABASE {self.name} TO {self.publisher_role}")
        finally:
            admin.close()
        base = urlsplit(os.environ[TEST_URL_ENV])
        host = f"{base.hostname}:{base.port or 5432}"

        def url(role):
            return f"postgresql://{role}:{self.passwords[role]}@{host}/{self.name}"
        self.publisher_url, self.app_url = url(self.publisher_role), url(self.app_role)
        self.publisher = PostgresConfig.from_url(self.publisher_url, purpose="publisher", local_test=True)
        self.app = PostgresConfig.from_url(self.app_url, purpose="app", local_test=True)
        self.admin_url = f"postgresql://{base.username}:{base.password}@{host}/{self.name}"
        self._legacy()

    def admin(self):
        """Superuser connection to this test database (fixture setup/inspection only)."""
        import psycopg2
        conn = psycopg2.connect(self.admin_url, connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            if check_marker(cur) != self.name or not self.name.startswith(TEST_DB_PREFIX):
                raise UnsafeTarget("Unexpected test database")
        return conn

    def _legacy(self):
        """Pre-existing teammate objects the platform must never change."""
        with self.admin() as conn, conn.cursor() as cur:
            cur.execute("""CREATE TABLE public.fact_sales_sentinel (id int PRIMARY KEY, store_id text, units numeric(12,2));
                INSERT INTO public.fact_sales_sentinel VALUES (1, 'S1', 10.50), (2, 'S2', NULL);
                CREATE TABLE public.dim_store_sentinel (store_id text PRIMARY KEY, name text);
                INSERT INTO public.dim_store_sentinel VALUES ('S1', 'Legacy One');
                CREATE SCHEMA teammate_app;
                CREATE TABLE teammate_app.private_notes (id serial PRIMARY KEY, note text);
                INSERT INTO teammate_app.private_notes (note) VALUES ('teammate private row');
                CREATE VIEW public.legacy_view AS SELECT store_id, units FROM public.fact_sales_sentinel;""")

    def legacy_snapshot(self):
        with self.admin() as conn, conn.cursor() as cur:
            cur.execute("""SELECT table_schema, table_name, column_name, data_type, ordinal_position
                FROM information_schema.columns WHERE table_schema IN ('public', 'teammate_app') ORDER BY 1, 2, 5""")
            columns = cur.fetchall()
            cur.execute("""SELECT n.nspname, c.relname, c.relkind, c.relacl::text FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('public', 'teammate_app') ORDER BY 1, 2""")
            relations = cur.fetchall()
            cur.execute("SELECT nspacl::text FROM pg_namespace WHERE nspname IN ('public', 'teammate_app') ORDER BY nspname")
            acls = cur.fetchall()
            data = {}
            for table in ("public.fact_sales_sentinel", "public.dim_store_sentinel", "teammate_app.private_notes"):
                cur.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY t::text)), count(*) FROM {table} t")
                data[table] = cur.fetchone()
            cur.execute("SELECT pg_get_viewdef('public.legacy_view'::regclass)")
            view = cur.fetchone()[0]
        return {"columns": columns, "relations": relations, "acls": acls, "data": data, "view": view}

    def drop(self):
        admin = _admin()
        try:
            with admin.cursor() as cur:
                if not self.name.startswith(TEST_DB_PREFIX):
                    raise UnsafeTarget("Refusing to drop a non-test database")
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                            [self.name])
                cur.execute(f"DROP DATABASE IF EXISTS {self.name}")
                for role in (self.publisher_role, self.app_role):
                    if role.startswith("sst_"):
                        cur.execute(f"DROP ROLE IF EXISTS {role}")
        finally:
            admin.close()


# ------------------------------------------------------------------ synthetic serving sources

_SOURCES = {}


def _cleanup():
    for value in _SOURCES.values():
        if isinstance(value, tempfile.TemporaryDirectory):
            value.cleanup()


atexit.register(_cleanup)


def synthetic_sources():
    """Build once per process: base serving, a Synthetic_demo serving with model outputs and demo scopes,
    and edge-value / no-health / corrupted variants. All data is generated."""
    if "sources" in _SOURCES:
        return _SOURCES["sources"]
    from unittest.mock import patch
    import duckdb
    import test_module2_serving as m2
    from data_platform.artifacts import verified_artifact
    from data_platform.model_outputs import build_serving_with_model_outputs
    from data_platform.workflow import build_all
    from modules.module_2 import inputs, ranking, scoring, synthetic
    from modules.module_2.contracts import write_json
    from modules.module_2.service import demo_scope

    temp = _SOURCES["temp"] = tempfile.TemporaryDirectory(prefix="shared-pg-fixtures-")
    root = Path(temp.name)
    synthetic.generate(root / "raw", stores_per_segment=1, skus=4)
    write_json(root / "scope0.json", {"version": "none", "assignments": []})
    build_all(date(2026, 6, 30), root / "workflow", source_dir=root / "raw", snapshot_root=root / "snapshots",
              access_policy=root / "scope0.json", rolling_evaluation=True, assume_complete_snapshot=True, threads=1,
              memory_limit="512MB")
    workflow = inputs.load_workflow(next((root / "workflow" / "runs").glob("*/run.json")))
    base = workflow["components"]["serving"]["directory"]
    m2.EVIDENCE["default"] = m2.make_evidence(workflow["plan"].to_dict())
    with patch.object(scoring.bundles, "load_bundle", lambda d: m2.StubBundle(d)):
        scoring.score(workflow, root / "bundle", root / "scoring", synthetic=True)
    _, db, _ = verified_artifact(base, kind="serving")
    with duckdb.connect(str(db), read_only=True) as con:
        products = con.execute("SELECT sku_id,list_price,is_msl FROM gold.dim_products").df()
        stores = con.execute("SELECT store_id FROM gold.dim_stores").df()
    ranking.rank(root / "scoring", products, stores, root / "ranking")
    m2.health_package(root / "health")
    store_ids = sorted(stores["store_id"])
    scope = demo_scope(store_ids)
    scope["assignments"] += [{"principal_id": "demo:partial-manager", "store_id": s, "role": "manager"}
                             for s in store_ids[:3]]
    write_json(root / "scope.json", scope)
    published = build_serving_with_model_outputs(
        base, root / "scoring", root / "ranking", root / "candidate", health_package_dir=root / "health",
        access_policy=root / "scope.json", publish=True, publication_kind="Synthetic_demo")
    sources = {"root": root, "base": base, "model": published["directory"], "scope_file": root / "scope.json",
               "scope": scope, "stores": store_ids}
    sources["edge"] = edge_serving(published["directory"], root / "edge")
    sources["no_health"] = edge_serving(base, root / "no-health", drop_health=True, edge_rows=False)
    _SOURCES["sources"] = sources
    return sources


EDGE_TEXT = ["", "null", "NULL", "\\N", "\\", "tab\there", "line1\nline2\r\nline3", 'quote "double" \'single\'',
             "Café Ünïcode 東京 🛒", "comma,separated", "  padded  ", "SENTINEL-visible-edge"]
EDGE_FLOATS = [-0.0, 0.0, float("nan"), float("inf"), float("-inf"), 1e308, 5e-324, 0.1 + 0.2, -123.456]
PRIVATE_SENTINEL = "SENTINEL-PRIVATE-NOT-FOR-SERVING-7f3a"


def edge_serving(source_dir, output_root, *, drop_health=False, edge_rows=True):
    """A new synthetic serving artifact derived from ``source_dir`` with hard values and private sentinels."""
    import duckdb
    from data_platform.artifacts import artifact_attempt, verified_artifact
    from data_platform.pipeline import atomic_json
    from data_platform.serving import VIEWS
    record, database, _ = verified_artifact(source_dir, kind="serving")
    keep = {k: record[k] for k in ("as_of", "dataset_hash", "source_build_id", "feature_artifact_id", "metric_view",
                                   "feature_purpose", "estimate_policy", "estimate_views_status",
                                   "available_metric_views", "access_policy_version", "model_outputs_status",
                                   "model_outputs", "readiness") if k in record}
    with artifact_attempt(output_root, "serving", {**keep, "synthetic_fixture": "postgres edge values"}) as (directory, new):
        target = directory / "business.duckdb"
        shutil.copyfile(database, target)
        with duckdb.connect(str(target)) as con:
            if edge_rows:
                _insert_edges(con)
            # Private objects that must never be exported.
            con.execute("CREATE SCHEMA IF NOT EXISTS silver")
            con.execute(f"CREATE TABLE silver.private_sentinel AS SELECT '{PRIVATE_SENTINEL}' AS secret")
            con.execute(f"CREATE TABLE gold.features_private AS SELECT '{PRIVATE_SENTINEL}' AS target_oos_7d")
            con.execute(f"INSERT INTO gold.account_scope VALUES ('{PRIVATE_SENTINEL}', (SELECT min(store_id) FROM gold.dim_stores), 'sales', 'fixture')")
            if drop_health:
                con.execute("DROP VIEW serving.vw_model_health; DROP VIEW serving.vw_model_monitoring")
            present = {r[0][3:] for r in con.execute(
                "SELECT table_name FROM information_schema.views WHERE table_schema='serving'").fetchall()}
            rows = {n: con.execute(f"SELECT count(*) FROM gold.{rel}").fetchone()[0]
                    for n, (rel, _, _) in VIEWS.items() if n in present}
        atomic_json(directory / "quality.json", [{"check_id": "synthetic_edge_fixture", "severity": "error",
                                                  "passed": True, "failed_count": 0}])
        new.update(database="business.duckdb", application_ready=True, rows=rows)
    return new["directory"]


def _insert_edges(con):
    store = "S-EDGE-ü"
    con.execute("""INSERT INTO gold.dim_stores (store_id, store_name, channel, region, state, segment_tier, city)
        VALUES (?, ?, 'Modern Trade', NULL, ?, 'Tier 1', ?)""", [store, EDGE_TEXT[8] + "\n" + EDGE_TEXT[7], EDGE_TEXT[3], ""])
    con.execute("""INSERT INTO gold.account_scope SELECT DISTINCT principal_id, ?, role, scope_version FROM gold.account_scope
        WHERE principal_id = 'demo:manager'""", [store])
    con.execute("""INSERT INTO gold.dim_products (sku_id, product_name, category, brand, is_msl, unit_cost, list_price, consumer_price)
        VALUES ('SKU-EDGE-1', 'null', '', NULL, NULL, 0.01, 9999999999999999.99, -0.50)""")
    con.execute("""INSERT INTO gold.metric_definitions SELECT 'edge_metric', 'v-edge', '%', 'n', 'd', ?, 'rel', ?, 'NULL', ?""",
                [EDGE_TEXT[6], EDGE_TEXT[5], EDGE_TEXT[4]])
    template = con.execute("SELECT * FROM gold.mart_store_sku_daily LIMIT 1").fetchone()
    columns = [r[0] for r in con.execute("DESCRIBE gold.mart_store_sku_daily").fetchall()]
    inventory = [v for v in EDGE_FLOATS]
    for i, value in enumerate(inventory):
        row = dict(zip(columns, template))
        row.update(date=date(2026, 6, 1 + i), store_id=store, sku_id="SKU-EDGE-1", inventory_observed=value,
                   units_sold_observed=EDGE_FLOATS[-1 - i], oos_observed=(None if i % 3 == 0 else bool(i % 2)),
                   inventory_data_status=EDGE_TEXT[i % len(EDGE_TEXT)], consumer_price_observed=None if i == 1 else 12.34,
                   is_msl=None)
        con.execute(f"INSERT INTO gold.mart_store_sku_daily ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    [row[c] for c in columns])
    # Matching store-day components keep the OOS/inventory numerator parity intact.
    day_columns = [r[0] for r in con.execute("DESCRIBE gold.mart_store_day").fetchall()]
    day = dict(zip(day_columns, con.execute("SELECT * FROM gold.mart_store_day LIMIT 1").fetchone()))
    added = con.execute("""SELECT count(inventory_observed), count(*) FILTER (WHERE oos_observed)
        FROM gold.mart_store_sku_daily WHERE store_id = ?""", [store]).fetchone()
    day.update(date=date(2026, 6, 30), store_id=store, observed_inventory_sku_count=added[0], oos_sku_count=added[1],
               oos_rate_pct=float("nan"), msl_state=EDGE_TEXT[2])
    con.execute(f"INSERT INTO gold.mart_store_day ({', '.join(day_columns)}) VALUES ({', '.join('?' * len(day_columns))})",
                [day[c] for c in day_columns])
    neg_columns = [r[0] for r in con.execute("DESCRIBE gold.mart_negotiation_context").fetchall()]
    neg = dict(zip(neg_columns, con.execute("SELECT * FROM gold.mart_negotiation_context LIMIT 1").fetchone()))
    neg.update(store_id=store, sku_id="SKU-EDGE-1", sell_in_units_reported_28d=2 ** 100 + 7,
               gross_sales_reported_28d=-0.01, policy_limitation=EDGE_TEXT[6])
    con.execute(f"INSERT INTO gold.mart_negotiation_context ({', '.join(neg_columns)}) VALUES ({', '.join('?' * len(neg_columns))})",
                [neg[c] for c in neg_columns])


def scope_file(path, assignments, version):
    Path(path).write_text(json.dumps({"version": version, "assignments": assignments}))
    return path
