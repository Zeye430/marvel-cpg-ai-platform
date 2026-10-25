"""Shared PostgreSQL serving against a real disposable local PostgreSQL (synthetic data only).

Skipped unless SHARED_POSTGRES_TEST_DATABASE_URL names the marked test server
(compose.postgres-test.yml); with SHARED_POSTGRES_REQUIRE_TEST_DB=1 a missing
server fails instead. No mocks stand in for the database here.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

try:
    from tests import postgres_fixtures as fx
except ImportError:                                    # discovered with -s tests
    import postgres_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]


def _same(value):
    """Python equality, with NaN made comparable. (DuckDB's ORDER BY ALL returns -0.0 as 0.0;
    exact sign/bit parity is asserted separately through canonical digests.)"""
    return "NaN" if isinstance(value, float) and value != value else value


def setUpModule():
    fx.require_database()


def _modules():
    import duckdb
    from data_platform import postgres_publisher as pub
    from data_platform.access import DataUnavailable, ServingReader
    from data_platform.artifacts import verified_artifact
    from data_platform.postgres_reader import PostgresServingReader
    from data_platform.postgres_schema import canonical_value, from_postgres, load_contract, order_by, quote_ident
    return locals()


class PgCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        globals().update(_modules())
        cls.sources = fx.synthetic_sources()
        cls.db = fx.TestDatabase()
        cls.addClassCleanup(cls.db.drop)
        cls.legacy_before = cls.db.legacy_snapshot()
        cls.contract = load_contract()
        pub.initialize_postgres(cls.db.publisher, apply=True)

    def publish(self, key, *, activate=True):
        result = pub.load_postgres_candidate(self.sources[key], self.db.publisher, apply=True)
        self.assertIn(result["status"], ("Validated", "Reused_validated_release"), result.get("checks_failed"))
        if activate:
            pub.activate_postgres_release(self.db.publisher, result["release_id"], apply=True)
        return result

    def scopes(self, assignments, version):
        path = self.sources["root"] / f"scope-{self.db.suffix}-{version}.json"
        fx.scope_file(path, assignments, version)
        return pub.configure_postgres_scopes(self.db.publisher, path, apply=True)

    def demo_scopes(self, extra=(), version="demo-v1"):
        return self.scopes(self.sources["scope"]["assignments"] + list(extra), version)

    def sql(self, query, params=()):
        with self.db.admin() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall() if cur.description else None

    def canonical_duck(self, artifact, name):
        spec = self.contract["views"][name]
        _, database, _ = verified_artifact(artifact, kind="serving")
        cols = ", ".join(quote_ident(c) for c, _ in spec["columns"])
        with duckdb.connect(str(database), read_only=True) as con:
            rows = con.execute(f"SELECT {cols} FROM serving.vw_{name} ORDER BY {order_by(spec)}").fetchall()
        return [[canonical_value(v, t) for v, (_, t) in zip(r, spec["columns"])] for r in rows]

    def canonical_pg(self, release_id, name):
        spec = self.contract["views"][name]
        cols = ", ".join(quote_ident(c) for c, _ in spec["columns"])
        rows = self.sql(f"SELECT {cols} FROM platform_release_{release_id}.vw_{name} ORDER BY {order_by(spec)}")
        return [[canonical_value(from_postgres(v, t), t) for v, (_, t) in zip(r, spec["columns"])] for r in rows]


class PublicationTests(PgCase):
    """AC-01/02/03/05/09/13/15/19/20: content parity, exclusions, edge values, idempotency, isolation."""

    def test_01_every_view_round_trips_with_typed_content_parity(self):
        result = self.publish("model")
        self.assertEqual(result["checks_failed"], [])
        self.assertEqual(sorted(result["views"]), sorted(self.contract["views"]))
        for name in result["views"]:
            with self.subTest(view=name):
                duck, pg = self.canonical_duck(self.sources["model"], name), self.canonical_pg(result["release_id"], name)
                self.assertEqual(len(duck), result["rows"][name])
                self.assertEqual(duck, pg)                                   # every row, every typed value
        types = {r[0]: r[1] for r in self.sql("""SELECT column_name, data_type || coalesce('(' || numeric_precision || ','
            || numeric_scale || ')', '') FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'mart_negotiation_context'""", [f"platform_release_{result['release_id']}"])}
        self.assertEqual(types["sell_in_units_reported_28d"], "numeric(39,0)")
        self.assertEqual(types["reference_list_price"], "numeric(18,2)")
        self.assertEqual(types["inventory_observed"], "double precision")

    def test_02_edge_values_survive_exactly(self):
        result = self.publish("edge", activate=False)
        release = result["release_id"]
        for name in ("stores", "products", "metric_definitions", "store_sku_daily", "store_day", "negotiation_context"):
            with self.subTest(view=name):
                self.assertEqual(self.canonical_duck(self.sources["edge"], name), self.canonical_pg(release, name))
        rows = self.sql(f"""SELECT inventory_observed, inventory_data_status FROM platform_release_{release}.vw_store_sku_daily
            WHERE store_id = 'S-EDGE-ü' ORDER BY date""")
        floats = [r[0] for r in rows]
        self.assertEqual(str(floats[0]), "-0.0")
        self.assertTrue(floats[2] != floats[2])                               # NaN
        self.assertEqual(floats[3:7], [float("inf"), float("-inf"), 1e308, 5e-324])
        self.assertEqual([r[1] for r in rows][:5], ["", "null", "NULL", "\\N", "\\"])
        nulls = self.sql(f"""SELECT region IS NULL, city, store_name FROM platform_release_{release}.vw_stores
            WHERE store_id = 'S-EDGE-ü'""")[0]
        self.assertEqual(nulls[:2], (True, ""))
        self.assertIn("東京 🛒\nquote \"double\" 'single'", nulls[2])
        huge = self.sql(f"""SELECT sell_in_units_reported_28d, gross_sales_reported_28d
            FROM platform_release_{release}.vw_negotiation_context WHERE store_id = 'S-EDGE-ü'""")[0]
        self.assertEqual((int(huge[0]), str(huge[1])), (2 ** 100 + 7, "-0.01"))

    def test_03_private_objects_and_scope_rows_are_never_exported(self):
        result = self.publish("edge", activate=False)
        text = json.dumps(result, default=str)
        self.assertNotIn(fx.PRIVATE_SENTINEL, text)
        self.assertNotIn(str(self.sources["root"]), text)                 # no local paths in output/manifest
        schema = f"platform_release_{result['release_id']}"
        relations = {r[0] for r in self.sql("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [schema])}
        expected = {s["relation"] for s in self.contract["views"].values()} | {f"vw_{n}" for n in self.contract["views"]}
        self.assertTrue(relations <= expected, relations - expected)
        self.assertNotIn("account_scope", relations)
        found = self.sql(f"""SELECT count(*) FROM information_schema.columns WHERE table_schema LIKE 'platform%%'
            AND column_name IN ('target_oos_7d', 'secret', 'label_source')""")[0][0]
        self.assertEqual(found, 0)
        for table in [s["relation"] for s in self.contract["views"].values()]:
            hits = self.sql(f"SELECT count(*) FROM {schema}.{table} t WHERE t::text LIKE %s", [f"%{fx.PRIVATE_SENTINEL}%"])
            self.assertEqual(hits[0][0], 0, table)
        manifest = self.sql("SELECT manifest::text FROM platform_meta.releases WHERE release_id = %s", [result["release_id"]])[0][0]
        self.assertNotIn(fx.PRIVATE_SENTINEL, manifest)
        self.assertNotIn("/", json.loads(manifest).get("artifact_id", ""))

    def test_04_identical_input_is_idempotent_and_conflicts_refused(self):
        first = self.publish("model", activate=False)
        before = self.sql("SELECT release_id, state, content_key FROM platform_meta.releases ORDER BY 1")
        schemas = self.sql(r"SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'platform\_release\_%%'")[0][0]
        again = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        self.assertEqual(again["status"], "Reused_validated_release")
        self.assertEqual(again["release_id"], first["release_id"])
        self.assertEqual(self.sql("SELECT release_id, state, content_key FROM platform_meta.releases ORDER BY 1"), before)
        self.assertEqual(self.sql(r"SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'platform\_release\_%%'")[0][0], schemas)
        pub.activate_postgres_release(self.db.publisher, first["release_id"], apply=True)
        again_noop = pub.activate_postgres_release(self.db.publisher, first["release_id"], apply=True)
        self.assertEqual(again_noop["status"], "Already_current")
        self.assertEqual(self.sql("SELECT count(*) FROM platform_meta.publications WHERE release_id = %s",
                                  [first["release_id"]])[0][0], 1)
        # Same source artifact ID, different bytes: conflicting identity, refused.
        self.sql("""UPDATE platform_meta.releases SET source_database_sha256 = repeat('0', 64)
            WHERE release_id = %s""", [first["release_id"]])
        try:
            with self.assertRaisesRegex(pub.PublishError, "conflicting source identity"):
                pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        finally:
            self.sql("UPDATE platform_meta.releases SET source_database_sha256 = %s WHERE release_id = %s",
                     [first["source"]["database_sha256"], first["release_id"]])
        # A different verified input becomes a distinct candidate.
        other = self.publish("no_health", activate=False)
        self.assertNotEqual(other["release_id"], first["release_id"])
        self.assertNotIn("model_health", other["views"])

    def test_05_dry_runs_change_nothing_and_preflight_is_read_only(self):
        def state():
            return (self.sql("SELECT nspname FROM pg_namespace ORDER BY 1"),
                    self.sql("SELECT release_id, state FROM platform_meta.releases ORDER BY 1"),
                    self.sql("SELECT count(*) FROM platform_meta.publications"),
                    self.sql("SELECT count(*) FROM platform_access.account_scope"))
        before = state()
        dry = pub.load_postgres_candidate(self.sources["base"], self.db.publisher)
        self.assertEqual(dry["status"], "Dry_run")
        self.assertEqual(pub.initialize_postgres(self.db.publisher)["status"], "Already_initialized")
        report = pub.preflight_postgres(self.db.publisher)
        self.assertEqual(report["platform"]["status"], "Initialized")
        self.assertEqual(report["server_version_num"] // 10000, 17)
        self.assertGreater(report["size"]["totals"]["database_bytes"], 0)
        self.assertNotIn(self.db.passwords[self.db.publisher_role], json.dumps(report, default=str))
        self.assertEqual(state(), before)

    def test_99_legacy_objects_unchanged(self):
        self.assertEqual(self.db.legacy_snapshot(), self.legacy_before)


class FailureTests(PgCase):
    """AC-04/06/07: verified inputs only, failures never change current, atomic serialized switching."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        cls.first_pub = pub.activate_postgres_release(cls.db.publisher, cls.first["release_id"], apply=True)

    def current(self):
        return self.sql("SELECT publication_id, release_id FROM platform_meta.current_release")[0]

    def stable(self):
        return self.sql("SELECT count(*), max(store_id) FROM platform_serving.vw_stores")[0]

    def test_01_unverified_sources_fail_before_any_release_row(self):
        import shutil
        bad_root = self.sources["root"] / f"bad-{self.db.suffix}"
        cases = {}
        for case in ("bytes", "quality", "status", "schema", "type", "grain"):
            target = bad_root / case
            shutil.copytree(self.sources["model"], target)
            run = json.loads((target / "run.json").read_text())
            if case == "bytes":
                with open(target / "business.duckdb", "ab") as handle:
                    handle.write(b"\0")
            elif case == "quality":
                (target / "quality.json").write_text("[]")
            elif case == "status":
                run["status"] = "Failed"
            else:
                with duckdb.connect(str(target / "business.duckdb")) as con:
                    if case == "schema":
                        con.execute("CREATE OR REPLACE VIEW serving.vw_products AS SELECT *, 1 AS extra FROM gold.dim_products")
                    elif case == "type":
                        con.execute("CREATE OR REPLACE VIEW serving.vw_stores AS SELECT * REPLACE (now() AS city) FROM gold.dim_stores")
                    else:
                        con.execute("INSERT INTO gold.dim_products SELECT * FROM gold.dim_products LIMIT 1")
                from data_platform.ingestion import digest
                run["database_sha256"] = digest(target / "business.duckdb")
            run["directory"] = str(target)
            (target / "run.json").write_text(json.dumps(run))
            cases[case] = target
        before = (self.sql("SELECT count(*) FROM platform_meta.releases"), self.current(), self.stable())
        for case, target in cases.items():
            with self.subTest(case=case), self.assertRaises((ValueError, pub.PublishError)):
                pub.load_postgres_candidate(target, self.db.publisher, apply=True)
        self.assertEqual((self.sql("SELECT count(*) FROM platform_meta.releases"), self.current(), self.stable()), before)

    def test_02_failures_between_tables_or_in_validation_keep_current(self):
        before = (self.current(), self.stable())
        reader = PostgresServingReader(self.db.app)
        self.scopes_ready()
        calls = {"n": 0}
        real = pub._copy_view

        def failing(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("injected failure between tables")
            return real(*args, **kwargs)
        with patch.object(pub, "_copy_view", failing), self.assertRaisesRegex(RuntimeError, "injected"):
            pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        release = pub.inspect_serving_source(self.sources["edge"])["release_id"]
        self.assertEqual(self.sql("SELECT state FROM platform_meta.releases WHERE release_id = %s", [release])[0][0], "Failed")
        self.assertEqual(self.sql("SELECT count(*) FROM pg_namespace WHERE nspname = %s", [f"platform_release_{release}"])[0][0], 0)
        self.assertEqual((self.current(), self.stable()), before)
        self.assertEqual(reader.read("stores", "demo:manager", limit=100), PostgresServingReader(self.db.app).read("stores", "demo:manager", limit=100))
        # Validation failure: payload kept for inspection, state Failed, never selectable.
        real_check = pub._check_release

        def broken(*args, **kwargs):
            checks, failures = real_check(*args, **kwargs)
            return checks + [{"check": "injected", "passed": False}], failures + ["injected"]
        with patch.object(pub, "_check_release", broken), self.assertRaisesRegex(pub.PublishError, "failed verification"):
            pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        self.assertEqual(self.sql("SELECT state FROM platform_meta.releases WHERE release_id = %s", [release])[0][0], "Failed")
        with self.assertRaisesRegex(pub.PublishError, "Only Validated"):
            pub.activate_postgres_release(self.db.publisher, release, apply=True)
        self.assertEqual((self.current(), self.stable()), before)
        # Retry restarts only its own unselected candidate and succeeds.
        retry = pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        self.assertEqual((retry["status"], retry["action"]), ("Validated", "Restarted_own_candidate"))
        self.assertEqual((self.current(), self.stable()), before)

    def scopes_ready(self):
        assignments = self.sources["scope"]["assignments"] + [{"principal_id": "demo:manager", "store_id": "S-EDGE-ü", "role": "manager"}]
        if self.sql("SELECT count(*) FROM platform_access.account_scope")[0][0] == 0:
            self.scopes([a for a in assignments if a["store_id"] != "S-EDGE-ü"], "demo-v1")

    def test_03_killed_loader_leaves_current_intact_and_retry_recovers(self):
        before = (self.current(), self.stable())
        script = f"""
import sys, time
sys.path.insert(0, {str(ROOT)!r})
from data_platform import postgres_publisher as pub
from data_platform.postgres_config import PostgresConfig
real = pub._copy_view
def slow(*a, **k):
    out = real(*a, **k)
    print("copied", a[3], flush=True)
    time.sleep(0.5)
    return out
pub._copy_view = slow
pub.load_postgres_candidate({str(self.sources['no_health'])!r},
    PostgresConfig.from_url({self.db.publisher_url!r}, purpose="publisher", local_test=True), apply=True)
"""
        process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(3):
            line = process.stdout.readline()
            self.assertIn("copied", line, process.stderr.read() if not line else "")
        # A concurrent publisher is refused while the loader holds the writer lock.
        with self.assertRaisesRegex(pub.PublishError, "writer lock"):
            pub.activate_postgres_release(self.db.publisher, self.first["release_id"], apply=True)
        process.send_signal(signal.SIGKILL)
        process.wait(30)
        process.stdout.close()
        process.stderr.close()
        release = pub.inspect_serving_source(self.sources["no_health"])["release_id"]
        time.sleep(0.5)
        self.assertEqual(self.sql("SELECT state FROM platform_meta.releases WHERE release_id = %s", [release])[0][0], "Loading")
        self.assertEqual(self.sql("SELECT count(*) FROM pg_namespace WHERE nspname = %s", [f"platform_release_{release}"])[0][0], 0)
        self.assertEqual((self.current(), self.stable()), before)
        with self.assertRaisesRegex(pub.PublishError, "Only Validated"):
            pub.activate_postgres_release(self.db.publisher, release, apply=True)
        retry = pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        self.assertEqual((retry["status"], retry["action"]), ("Validated", "Restarted_own_candidate"))

class ReaderTests(PgCase):
    """AC-08/10/11/12/14/16/19: pinning, least privilege, scope, staleness, API and service parity."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.model["release_id"], apply=True)
        path = cls.sources["root"] / f"scope-{cls.db.suffix}.json"
        fx.scope_file(path, cls.sources["scope"]["assignments"], cls.sources["scope"]["version"])
        pub.configure_postgres_scopes(cls.db.publisher, path, apply=True)
        cls.duck = ServingReader(cls.sources["model"])

    def reader(self, **kwargs):
        reader = PostgresServingReader(self.db.app, **kwargs)
        self.addCleanup(reader.close)
        return reader

    def test_01_read_and_pages_match_duckdb_for_every_view_and_principal(self):
        pg = self.reader()
        self.assertEqual((pg.record["artifact_id"], pg.record["source_artifact_id"], pg.record["backend"]),
                         (self.model["release_id"], self.duck.record["artifact_id"], "postgres"))
        for key in ("as_of", "dataset_hash", "source_build_id", "model_outputs_status", "estimate_views_status", "model_outputs"):
            self.assertEqual(pg.record[key], self.duck.record[key], key)
        principals = ["demo:manager", "demo:rep-a", "demo:rep-b", "demo:partial-manager"]
        from data_platform.access import PAGE_VIEWS
        from data_platform.serving import ESTIMATED_VIEWS, VIEWS
        for principal in principals:
            self.assertEqual({k: v for k, v in pg.principal_scope(principal).items()},
                             self.duck.principal_scope(principal), principal)
            for name in VIEWS:
                kwargs = {"allow_candidate_estimates": True} if name in ESTIMATED_VIEWS else {}
                with self.subTest(principal=principal, view=name):
                    expected = self._result(lambda: self.duck.read(name, principal, limit=10000, **kwargs))
                    self.assertEqual(self._result(lambda: pg.read(name, principal, limit=10000, **kwargs)), expected)
            for name in PAGE_VIEWS:
                with self.subTest(principal=principal, paged=name):
                    self.assertEqual(self._pages(pg, name, principal), self._pages(self.duck, name, principal))
        self.assertEqual(self._result(lambda: pg.read("store_sku_daily", "demo:rep-a", start="2026-06-29", end=date(2026, 6, 30))),
                         self._result(lambda: self.duck.read("store_sku_daily", "demo:rep-a", start=date(2026, 6, 29), end=date(2026, 6, 30))))
        schema_pg, schema_duck = pg.schema(), self.duck.schema()
        self.assertEqual(set(schema_pg), set(schema_duck))
        for name in schema_duck:
            self.assertEqual([(c["name"], c["type"]) for c in schema_pg[name]["columns"]],
                             [(c["name"], c["type"]) for c in schema_duck[name]["columns"]])

    @staticmethod
    def _result(call):
        try:
            rows = call()
            return ("rows", [{k: _same(v) for k, v in r.items()} for r in rows])
        except (PermissionError, ValueError, DataUnavailable) as error:
            return (type(error).__name__, str(error))

    def _pages(self, reader, name, principal, **filters):
        out, cursor = [], None
        try:
            while True:
                size = 1000 if name == "store_sku_daily" else 7
                page = reader.read_page(name, principal, after=cursor, page_size=size, **filters)
                out.append([{k: _same(v) for k, v in r.items()} for r in page["rows"]])
                cursor = page["next_cursor"]
                if cursor is None:
                    return out
        except (PermissionError, ValueError, DataUnavailable) as error:
            return (type(error).__name__, str(error))

    def test_02_negative_requests_match_duckdb_error_categories(self):
        pg = self.reader()
        bad = [("read", ("no_such_view", "demo:manager"), {}),
               ("read", ("stores", "demo:manager"), {"limit": 0}),
               ("read", ("stores", "demo:manager"), {"limit": 10001}),
               ("read", ("stores", "demo:manager"), {"limit": "5"}),
               ("read", ("products", "demo:manager"), {"start": date(2026, 6, 1)}),
               ("read_page", ("store_day", "demo:manager"), {}),
               ("read_page", ("stores", "demo:manager"), {"sku_id": "x"}),
               ("read_page", ("store_sku_daily", "demo:manager"), {"start": "2026-06-01"}),
               ("read_page", ("store_sku_daily", "demo:manager"), {"page_size": 1001}),
               ("read_page", ("stores", "demo:manager"), {"after": "garbage"}),
               ("read", ("estimated_store_day", "demo:manager"), {}),
               ("read", ("stores", "nobody"), {}),
               ("read", ("stores", ""), {}),
               ("read", ("stores", "demo:rep-a' OR '1'='1"), {}),
               ("read_page", ("model_health", "demo:rep-a"), {}),
               ("read_page", ("model_health", "demo:partial-manager"), {})]
        for method, args, kwargs in bad:
            with self.subTest(method=method, args=args, kwargs=kwargs):
                duck_error = self._error(lambda: getattr(self.duck, method)(*args, **kwargs))
                self.assertIsNotNone(duck_error)
                self.assertEqual(self._error(lambda: getattr(pg, method)(*args, **kwargs)), duck_error)
        page = pg.read_page("stores", "demo:rep-a", page_size=1)
        self.assertEqual(len(page["rows"]), 1)
        for other in (("stores", "demo:rep-b"), ("products", "demo:rep-a")):
            with self.assertRaises(ValueError):
                pg.read_page(*other, after=page["next_cursor"], page_size=1)
        with self.assertRaises(ValueError):                                     # another reader's cursor
            self.reader().read_page("stores", "demo:rep-a", after=page["next_cursor"], page_size=1)
        with self.assertRaises(ValueError):
            pg.read_page("stores", "demo:rep-a", after=page["next_cursor"][:-2] + "00", page_size=1)

    @staticmethod
    def _error(call):
        try:
            call()
            return None
        except (PermissionError, ValueError, DataUnavailable) as error:
            return type(error).__name__

    def test_03_reader_pins_release_across_activation(self):
        old = self.reader()
        page = old.read_page("store_sku_daily", "demo:manager", page_size=50)
        other = pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        try:
            pub.activate_postgres_release(self.db.publisher, other["release_id"], apply=True)
            following = old.read_page("store_sku_daily", "demo:manager", after=page["next_cursor"], page_size=50)
            self.assertEqual(following["artifact_id"], self.model["release_id"])
            self.assertFalse(any(r["store_id"] == "S-EDGE-ü" for r in old.read("stores", "demo:manager", limit=100)))
            fresh = self.reader()
            self.assertEqual(fresh.record["artifact_id"], other["release_id"])
            self.assertEqual(old.record["artifact_id"], self.model["release_id"])
            with self.assertRaises(ValueError):                                 # cross-release cursor
                fresh.read_page("store_sku_daily", "demo:manager", after=page["next_cursor"], page_size=50)
            pinned = self.reader(release_id=self.model["release_id"])
            self.assertEqual(pinned.record["artifact_id"], self.model["release_id"])
        finally:
            current = self.sql("SELECT publication_id FROM platform_meta.publications WHERE release_id = %s ORDER BY published_at LIMIT 1",
                               [self.model["release_id"]])[0][0]
            pub.rollback_postgres_release(self.db.publisher, current, apply=True)
        candidate = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
        with self.assertRaises(DataUnavailable):                                # validated but never published
            self.reader(release_id=candidate["release_id"])

    def test_04_application_role_is_least_privilege(self):
        import psycopg2
        candidate = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)["release_id"]
        release = f"platform_release_{self.model['release_id']}"
        statements = [
            f"SELECT * FROM platform_release_{candidate}.vw_stores",
            f"SELECT * FROM {release}.dim_stores",
            "SELECT * FROM platform_meta.releases",
            "SELECT * FROM platform_meta.publications",
            "SELECT * FROM public.fact_sales_sentinel",
            "SELECT * FROM teammate_app.private_notes",
            f"INSERT INTO platform_access.account_scope VALUES ('x', 'y', 'manager', 'v')",
            "DELETE FROM platform_access.account_scope",
            f"UPDATE {release}.dim_stores SET store_name = 'x'",
            "CREATE TABLE platform_serving.hack (id int)",
            "CREATE TABLE public.hack (id int)",
            "CREATE SCHEMA hack",
            f"DROP VIEW {release}.vw_stores",
            "DROP TABLE public.fact_sales_sentinel",
            "CREATE ROLE hacker",
            f"SET ROLE {self.db.publisher_role}",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                conn = psycopg2.connect(self.db.app_url)
                try:
                    with conn.cursor() as cur, self.assertRaises(psycopg2.Error):
                        cur.execute(statement)
                finally:
                    conn.close()
        conn = psycopg2.connect(self.db.app_url)
        try:
            with conn.cursor() as cur:
                for allowed in (f"SELECT count(*) FROM {release}.vw_stores", "SELECT count(*) FROM platform_serving.vw_stores",
                                "SELECT count(*) FROM platform_meta.published_releases", "SELECT count(*) FROM platform_meta.current_release"):
                    cur.execute(allowed)
                    self.assertGreaterEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user")
                self.assertEqual(cur.fetchone(), (False, False, False))
                cur.execute("SELECT count(*) FROM platform_meta.published_releases WHERE release_id = %s", [candidate])
                self.assertEqual(cur.fetchone()[0], 0)                          # candidates invisible
        finally:
            conn.close()
        with self.db.admin() as admin, admin.cursor() as cur:
            cur.execute("""SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = 'PUBLIC'
                AND table_schema LIKE 'platform%%'""")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_05_scope_change_revocation_and_service_cache(self):
        from modules.module_2.service import Module2Service, Principal
        service = Module2Service(reader=self.reader())
        duck_service = Module2Service(self.sources["model"])
        rep_a, manager = Principal("demo:rep-a", True), Principal("demo:manager", True)
        warm = service.get_priorities(rep_a)
        self.assertEqual(warm.to_dict(), duck_service.get_priorities(rep_a).to_dict())
        self.assertEqual(service.get_model_health(manager).status, "Ok")
        page = service.reader.read_page("stores", "demo:rep-a", page_size=1)
        reduced = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] != "demo:rep-a"]
        partial_manager = [a for a in reduced if a["principal_id"] != "demo:manager"] + \
            [a for a in reduced if a["principal_id"] == "demo:manager"][:2]
        try:
            self.scopes(partial_manager, "revoked-v2")
            self.assertEqual(service.get_priorities(rep_a).status, "Permission_denied")      # warm cache not used
            self.assertEqual(service.get_population(rep_a).status, "Permission_denied")
            self.assertEqual(service.get_model_health(manager).status, "Permission_denied")  # manager now partial
            with self.assertRaises(PermissionError):
                service.reader.read_page("stores", "demo:rep-a", after=page["next_cursor"], page_size=1)
            # Switching releases and rolling back does not restore revoked scopes.
            model_publication = self.sql("SELECT publication_id FROM platform_meta.current_release")[0][0]
            base = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
            pub.activate_postgres_release(self.db.publisher, base["release_id"], apply=True)
            back = pub.rollback_postgres_release(self.db.publisher, model_publication, apply=True)
            self.assertEqual(back["status"], "Rolled_back")
            self.assertEqual(self.reader().principal_scope("demo:manager")["stores"], 2)
            with self.assertRaises(PermissionError):
                self.reader().principal_scope("demo:rep-a")
        finally:
            path = self.sources["root"] / f"scope-{self.db.suffix}.json"
            pub.configure_postgres_scopes(self.db.publisher, path, apply=True)
        self.assertEqual(service.get_priorities(rep_a).to_dict(), warm.to_dict())
        self.assertEqual(pub.configure_postgres_scopes(self.db.publisher, path, apply=True)["status"], "Unchanged")

    def test_06_module2_service_parity_on_both_backends(self):
        from modules.module_2.service import Module2Service, Principal
        pg = Module2Service(reader=self.reader(), latest_source_as_of=date(2026, 6, 30))
        duck = Module2Service(self.sources["model"], latest_source_as_of=date(2026, 6, 30))
        for pid in ("demo:manager", "demo:rep-a", "demo:partial-manager", "demo:nobody"):
            principal = Principal(pid, True)
            with self.subTest(principal=pid):
                for call in (lambda s: s.get_priorities(principal, page_size=5),
                             lambda s: s.get_priorities(principal, {"alert_only": True}, page_size=200),
                             lambda s: s.get_population(principal), lambda s: s.get_model_health(principal),
                             lambda s: s.get_monitoring(principal)):
                    self.assertEqual(call(pg).to_dict(), call(duck).to_dict())
                stores = duck.reader.read_page("stores", pid, page_size=2)["rows"] if pid != "demo:nobody" else []
                for store in stores:
                    self.assertEqual(pg.get_store_detail(principal, store["store_id"]).to_dict(),
                                     duck.get_store_detail(principal, store["store_id"]).to_dict())
                if stores:
                    self.assertEqual(pg.export_visit_plan(principal), duck.export_visit_plan(principal))
        with self.assertRaises(Exception):
            Module2Service(self.sources["model"], reader=self.reader())          # exactly one source

    def test_08_connections_are_bounded_and_closed(self):
        from modules.module_2.service import Module2Service, Principal
        reader = PostgresServingReader(self.db.app)
        service = Module2Service(reader=reader)
        errors = []

        def work():
            try:
                for pid in ("demo:manager", "demo:rep-a", "demo:rep-b"):
                    service.get_priorities(Principal(pid, True))
                    reader.read_page("store_sku_daily", "demo:manager", page_size=1000)
            except Exception as error:                                          # noqa: BLE001
                errors.append(error)
        threads = [threading.Thread(target=work) for _ in range(6)]
        peak = []

        def sample():
            while any(t.is_alive() for t in threads):
                peak.append(self.sql("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", [self.db.app_role])[0][0])
                time.sleep(0.01)
        [t.start() for t in threads]
        sampler = threading.Thread(target=sample)
        sampler.start()
        [t.join() for t in threads]
        sampler.join()
        self.assertEqual(errors, [])
        self.assertLessEqual(max(peak + [0]), 2)
        idle = self.sql("SELECT state FROM pg_stat_activity WHERE usename = %s", [self.db.app_role])
        self.assertTrue(all(s[0] == "idle" for s in idle), idle)                # no idle-in-transaction
        reader.close()
        time.sleep(0.2)
        self.assertEqual(self.sql("SELECT count(*) FROM pg_stat_activity WHERE usename = %s", [self.db.app_role])[0][0], 0)

    def test_09_configured_service_opt_in_and_sanitized_failure(self):
        from modules.module_2.server import configured_service
        env = {"SHARED_DATA_BACKEND": "postgres", "SHARED_DATA_DATABASE_URL": self.db.app_url, "SHARED_DATA_LOCAL_TEST": "1"}
        with patch.dict(os.environ, env):
            service, reason = configured_service()
        self.assertIsNone(reason)
        self.assertEqual(service.reader.record["backend"], "postgres")
        service.reader.close()
        bad = {**env, "SHARED_DATA_DATABASE_URL": self.db.app_url.replace(self.db.passwords[self.db.app_role], "wrong-secret-xyz")}
        with patch.dict(os.environ, bad):
            service, reason = configured_service()
        self.assertIsNone(service)
        self.assertNotIn("wrong-secret-xyz", reason)
        self.assertNotIn("127.0.0.1", reason)
        with patch.dict(os.environ, {**env, "SHARED_DATA_LOCAL_TEST": ""}):        # remote rules without CA: refused
            self.assertIsNone(configured_service()[0])
        with patch.dict(os.environ, {"MODULE2_SERVING_ROOT": str(self.sources["root"] / "candidate"), "SHARED_DATA_BACKEND": "duckdb"}):
            service, reason = configured_service()
        self.assertEqual((reason, service.reader.backend), (None, "duckdb"))


class ActivationTests(PgCase):
    """AC-07: serialized atomic activation, append-only rollback, bounded retention."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.first["release_id"], apply=True)
        path = cls.sources["root"] / f"scope-{cls.db.suffix}.json"
        fx.scope_file(path, cls.sources["scope"]["assignments"], "demo-v1")
        pub.configure_postgres_scopes(cls.db.publisher, path, apply=True)

    def current(self):
        return self.sql("SELECT publication_id, release_id FROM platform_meta.current_release")[0]

    def test_03_failed_candidate_then_second_candidate(self):
        with patch.object(pub, "_finish_release", side_effect=RuntimeError("injected before validation")), \
                self.assertRaisesRegex(RuntimeError, "injected"):
            pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        self.failed = pub.inspect_serving_source(self.sources["edge"])["release_id"]
        self.assertEqual(self.sql("SELECT state, error FROM platform_meta.releases WHERE release_id = %s", [self.failed])[0],
                         ("Failed", "RuntimeError: injected before validation"))
        self.assertEqual(pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)["status"],
                         "Validated")

    def test_04_activation_rollback_serialized_and_append_only(self):
        second = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
        history_before = self.sql("SELECT * FROM platform_meta.publications ORDER BY published_at")
        current = self.current()
        with self.assertRaisesRegex(pub.PublishError, "Invalid release"):
            pub.activate_postgres_release(self.db.publisher, "r" + "0" * 23, apply=True)
        with self.assertRaisesRegex(pub.PublishError, "Unknown release"):
            pub.activate_postgres_release(self.db.publisher, "r" + "0" * 24, apply=True)
        with self.assertRaisesRegex(pub.PublishError, "stale"):
            pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True,
                                          expected_current_publication_id="f" * 32)
        results, errors = [], []

        def attempt():
            try:
                results.append(pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True))
            except pub.PublishError as error:
                errors.append(str(error))
        threads = [threading.Thread(target=attempt) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        activated = [r for r in results if r["status"] == "Activated"]
        self.assertEqual(len(activated), 1, (results, errors))
        self.assertTrue(all("writer lock" in e for e in errors))
        self.assertEqual(self.current()[1], second["release_id"])
        self.assertEqual(self.sql("SELECT count(*) FROM platform_serving.vw_model_health")[0][0], 0)
        # Rollback appends a new publication; old history rows are untouched; scopes unchanged.
        scopes = self.sql("SELECT * FROM platform_access.account_scope ORDER BY 1, 2")
        back = pub.rollback_postgres_release(self.db.publisher, current[0], apply=True)
        self.assertEqual((back["status"], back["release_id"]), ("Rolled_back", current[1]))
        history = self.sql("SELECT * FROM platform_meta.publications ORDER BY published_at")
        self.assertEqual(history[:len(history_before)], history_before)
        self.assertEqual(history[-1][2], f"rollback:{current[0]}")
        self.assertEqual(self.sql("SELECT * FROM platform_access.account_scope ORDER BY 1, 2"), scopes)
        self.assertEqual(self.current()[1], current[1])
        with self.assertRaisesRegex(pub.PublishError, "Unknown publication"):
            pub.rollback_postgres_release(self.db.publisher, "a" * 32, apply=True)

    def test_05_retention_limit_refuses_further_distinct_loads(self):
        retained = self.sql("SELECT count(*) FROM platform_meta.releases WHERE state IN ('Imported', 'Validated')")[0][0]
        if retained < 3:
            for key in ("base", "no_health", "edge"):
                try:
                    pub.load_postgres_candidate(self.sources[key], self.db.publisher, apply=True)
                except pub.PublishError:
                    break
        self.assertGreaterEqual(self.sql("SELECT count(*) FROM platform_meta.releases WHERE state IN ('Imported', 'Validated')")[0][0], 3)
        import shutil
        target = self.sources["root"] / f"fourth-{self.db.suffix}"
        if not target.exists():
            fourth = fx.edge_serving(self.sources["base"], target, edge_rows=False)
        else:
            fourth = next((target / "builds").iterdir())
        before = self.sql("SELECT release_id, state FROM platform_meta.releases ORDER BY 1")
        with self.assertRaisesRegex(pub.PublishError, "Storage bound reached"):
            pub.load_postgres_candidate(fourth, self.db.publisher, apply=True)
        with self.assertRaisesRegex(pub.PublishError, "Storage bound reached"):     # restarting a Failed one too
            pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        self.assertEqual(self.sql("SELECT release_id, state FROM platform_meta.releases ORDER BY 1"), before)
        self.assertIn(("Failed",), [(state,) for _, state in before])



class TransferTests(PgCase):
    """AC-19/20: bounded transfer chunks and the size report."""

    def test_size_report_and_bounded_transfer(self):
        calls = []
        real = pub._copy_view

        class Counting:
            def __init__(self, cur, name):
                self.cur, self.name = cur, name

            def copy_expert(self, sql, buffer, *a, **k):
                calls.append((self.name, buffer.getvalue().count("\n")))    # escaped newlines are "\\n"
                return self.cur.copy_expert(sql, buffer, *a, **k)

        def spy(duck, cur, schema, name, spec, chunk_rows):
            return real(duck, Counting(cur, name), schema, name, spec, chunk_rows)
        with patch.object(pub, "_copy_view", spy):
            result = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True, chunk_rows=1000)
        self.assertEqual(result["status"], "Validated")
        sku_chunks = [n for v, n in calls if v == "store_sku_daily"]
        self.assertGreater(len(sku_chunks), 1)
        self.assertTrue(all(n <= 1000 for _, n in calls))
        size = result["size"]
        release = size["releases"][result["release_id"]]
        self.assertGreater(release["bytes"], 0)
        self.assertGreater(size["schemas"][f"platform_release_{result['release_id']}"]["indexes"], 0)
        with self.assertRaises(pub.PublishError):
            pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True, chunk_rows=10001)


class AvailabilityTests(PgCase):
    """AC-13: unreleased outputs, opt-in estimates and absent model views stay distinct from empty data."""

    def test_07_unavailable_outputs_estimates_and_missing_health_are_distinct(self):
        base = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
        no_health = pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        pub.activate_postgres_release(self.db.publisher, base["release_id"], apply=True)
        path = self.sources["root"] / f"scope-{self.db.suffix}.json"
        fx.scope_file(path, self.sources["scope"]["assignments"], "demo-v1")
        pub.configure_postgres_scopes(self.db.publisher, path, apply=True)
        for release in (base, no_health):
            pub.activate_postgres_release(self.db.publisher, release["release_id"], apply=True)
            reader = PostgresServingReader(self.db.app)
            self.addCleanup(reader.close)
            with self.assertRaisesRegex(DataUnavailable, "not zero risk"):
                reader.read_page("oos_predictions", "demo:manager")
            with self.assertRaisesRegex(DataUnavailable, "explicit opt-in"):
                reader.read("estimated_store_sku_daily", "demo:manager")
            rows = reader.read("estimated_store_sku_daily", "demo:manager", limit=5, allow_candidate_estimates=True)
            self.assertTrue(rows and all("inventory_data_status" in r and "estimator_version" in r for r in rows))
            if release is no_health:
                with self.assertRaisesRegex(DataUnavailable, "no model report views"):
                    reader.read_page("model_health", "demo:manager")
                self.assertNotIn("model_health", reader.schema())
            else:
                self.assertEqual(reader.read_page("model_health", "demo:manager")["rows"], [])   # empty, not unavailable


class CliTests(PgCase):
    """Operator CLI: dry-run default, explicit confirmation, JSON without secrets (AC-15/20/21)."""

    def cli(self, *args, url=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SHARED_DATA_", "DATABASE_URL"))}
        env.update(SHARED_DATA_PUBLISH_DATABASE_URL=url or self.db.publisher_url, SHARED_DATA_LOCAL_TEST="1",
                   PYTHONPATH=str(ROOT))
        done = subprocess.run([sys.executable, "-m", "data_platform.postgres_cli", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=300)
        return done.returncode, json.loads(done.stdout), done.stdout + done.stderr

    def test_cli_flow(self):
        secret = self.db.passwords[self.db.publisher_role]
        code, out, raw = self.cli("preflight")
        self.assertEqual((code, out["platform"]["status"]), (0, "Initialized"))
        code, out, raw = self.cli("load", "--artifact-dir", str(self.sources["model"]))
        self.assertEqual((code, out["status"]), (0, "Dry_run"))
        self.assertEqual(self.sql("SELECT count(*) FROM platform_meta.releases")[0][0], 0)
        code, out, raw = self.cli("load", "--artifact-dir", str(self.sources["model"]), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("confirm-database", out["error"])
        code, out, raw = self.cli("load", "--artifact-dir", str(self.sources["model"]), "--apply", "--confirm-database", "other")
        self.assertEqual(code, 1)
        code, out, raw = self.cli("load", "--artifact-dir", str(self.sources["model"]), "--apply", "--confirm-database", self.db.name)
        self.assertEqual((code, out["status"]), (0, "Validated"), raw[-2000:])
        release = out["release_id"]
        code, out, raw = self.cli("validate", "--release-id", release)
        self.assertEqual((code, out["status"]), (0, "Validated"))
        code, out, raw = self.cli("activate", "--release-id", release, "--apply", "--confirm-database", self.db.name)
        self.assertEqual((code, out["status"]), (0, "Activated"))
        code, out, raw = self.cli("configure-scopes", "--policy-file", str(self.sources["scope_file"]))
        self.assertEqual((code, out["status"]), (0, "Dry_run"))
        code, out, raw = self.cli("configure-scopes", "--policy-file", str(self.sources["scope_file"]), "--apply",
                                  "--confirm-database", self.db.name)
        self.assertEqual((code, out["status"]), (0, "Configured"))
        bad = self.sources["root"] / f"bad-scope-{self.db.suffix}.json"
        fx.scope_file(bad, [{"principal_id": "p", "store_id": "UNKNOWN", "role": "sales"}], "v")
        code, out, raw = self.cli("configure-scopes", "--policy-file", str(bad), "--apply", "--confirm-database", self.db.name)
        self.assertEqual(code, 1)
        wrong = self.db.publisher_url.replace(secret, "not-the-password-123")
        code, out, raw = self.cli("preflight", url=wrong)
        self.assertEqual(code, 1)
        self.assertNotIn("not-the-password-123", raw)
        for output in (raw,):
            self.assertNotIn(secret, output)
        code, out, raw = self.cli("preflight", url=self.db.publisher_url.replace("127.0.0.1", "localhost"))
        self.assertEqual(code, 1)
        self.assertIn("loopback", out["error"])
        # The public read example uses only the read-only application settings.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SHARED_DATA_", "DATABASE_URL"))}
        env.update(SHARED_DATA_DATABASE_URL=self.db.app_url, SHARED_DATA_LOCAL_TEST="1", PYTHONPATH=str(ROOT))
        example = subprocess.run([sys.executable, "-m", "examples.read_postgres_data", "--principal-id", "demo:rep-a",
                                  "--view", "stores"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(example.returncode, 0, example.stderr)
        shown = json.loads(example.stdout)
        self.assertEqual((shown["backend"], shown["release_id"]), ("postgres", release))
        self.assertTrue(0 < shown["row_count"] < len(self.sources["stores"]))
        self.assertNotIn(self.db.passwords[self.db.app_role], example.stdout + example.stderr)
        denied = subprocess.run([sys.executable, "-m", "examples.read_postgres_data", "--principal-id", "nobody"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(denied.returncode, 1)
        unavailable = subprocess.run([sys.executable, "-m", "examples.read_postgres_data", "--principal-id", "demo:manager",
                                      "--view", "estimated_store_day"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(unavailable.returncode, 3)
        self.assertEqual(self.db.legacy_snapshot(), self.legacy_before)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------- review findings R-01..R-05

def _cli(db, *args):
    from data_platform.postgres_cli import run
    return run(list(args), environ={"SHARED_DATA_PUBLISH_DATABASE_URL": db.publisher_url, "SHARED_DATA_LOCAL_TEST": "1"})


class _Base(PgCase):
    def state(self, release_id):
        return self.sql("SELECT state FROM platform_meta.releases WHERE release_id = %s", [release_id])[0][0]

    def history(self):
        return (self.sql("SELECT publication_id, release_id, action, previous_publication_id, published_at "
                         "FROM platform_meta.publications ORDER BY published_at"),
                self.sql("SELECT publication_id FROM platform_meta.current_publication"))

    def payload_exists(self, release_id):
        return bool(self.sql("SELECT 1 FROM pg_namespace WHERE nspname = %s", [f"platform_release_{release_id}"]))

    def corrupt_value(self, release_id, table="mart_store_sku_daily", column="inventory_observed", guard_back=True):
        schema = f"platform_release_{release_id}"
        with self.db.admin() as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {schema}.{table} DISABLE TRIGGER platform_release_write_guard")
            cur.execute(f"""UPDATE {schema}.{table} SET {column} = {column} + 12345 WHERE ctid = (
                SELECT ctid FROM {schema}.{table} WHERE {column} IS NOT NULL LIMIT 1)""")
            self.assertEqual(cur.rowcount, 1)
            if guard_back:
                cur.execute(f"ALTER TABLE {schema}.{table} ENABLE TRIGGER platform_release_write_guard")

    def configure_demo(self):
        path = self.sources["root"] / f"scope-{self.db.suffix}.json"
        fx.scope_file(path, self.sources["scope"]["assignments"], "demo-v1")
        pub.configure_postgres_scopes(self.db.publisher, path, apply=True)
        return path


class R01ScopeCheckTests(_Base):
    """A failed or unverifiable scope lookup ends the request and never admits cached rows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.model["release_id"], apply=True)

    def setUp(self):
        self.scope_path = self.configure_demo()

    def service(self):
        from modules.module_2.service import Module2Service
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        return Module2Service(reader=reader)

    def assert_no_rows(self, response, status):
        self.assertEqual(response.status, status)
        self.assertEqual((response.rows, response.totals, response.detail), ([], {}, {}))

    def cached_for(self, service, pid):
        return [k for k in service._cache if pid in k]

    def test_transient_failure_recovery_then_revocation(self):
        from modules.module_2.service import PermissionDenied, Principal
        service = self.service()
        manager, rep = Principal("demo:manager", True), Principal("demo:rep-a", True)
        store = service.reader.read_page("stores", "demo:rep-a", page_size=1)["rows"][0]["store_id"]
        warm_health, warm_rep = service.get_model_health(manager), service.get_priorities(rep)
        self.assertEqual((warm_health.status, warm_rep.status), ("Ok", "Ok"))
        self.assertTrue(self.cached_for(service, "demo:manager") and self.cached_for(service, "demo:rep-a"))
        outage = DataUnavailable("synthetic transient authorization lookup failure")
        with patch.object(service.reader, "principal_scope", side_effect=outage):
            self.assert_no_rows(service.get_model_health(manager), "Unavailable")
            self.assert_no_rows(service.get_priorities(rep), "Unreleased")
            self.assert_no_rows(service.get_population(rep), "Unreleased")
            self.assert_no_rows(service.get_monitoring(manager), "Unavailable")
            with self.assertRaises(DataUnavailable):
                service.get_store_detail(rep, store)
            with self.assertRaises(DataUnavailable):
                service.export_visit_plan(rep)
            self.assertEqual(self.cached_for(service, "demo:manager") + self.cached_for(service, "demo:rep-a"), [])
        # Recovered database: served again and cached under the verified fingerprint only.
        self.assertEqual(service.get_model_health(manager).to_dict(), warm_health.to_dict())
        self.assertEqual(service.get_priorities(rep).to_dict(), warm_rep.to_dict())
        fingerprints = {k[1] for k in service._cache}
        self.assertTrue(fingerprints and all(isinstance(f, str) and len(f) == 64 for f in fingerprints))
        # Real revocation afterwards: nothing cached is returned.
        remaining = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] not in ("demo:manager", "demo:rep-a")]
        self.scopes(remaining, "revoked-r01")
        self.assert_no_rows(service.get_model_health(manager), "Permission_denied")
        self.assert_no_rows(service.get_priorities(rep), "Permission_denied")
        self.assert_no_rows(service.get_population(rep), "Permission_denied")
        with self.assertRaises(PermissionError):
            service.get_store_detail(rep, store)
        with self.assertRaises(PermissionDenied):
            service.export_visit_plan(rep)
        self.assertEqual(self.cached_for(service, "demo:manager") + self.cached_for(service, "demo:rep-a"), [])

    def test_failure_during_outage_then_revocation_never_hits_cache(self):
        """The reviewer's sequence: lookup fails while data reads work, then real revocation."""
        from modules.module_2.service import Principal
        service = self.service()
        manager, rep = Principal("demo:manager", True), Principal("demo:rep-a", True)
        with patch.object(service.reader, "principal_scope",
                          side_effect=DataUnavailable("synthetic transient authorization lookup failure")):
            first, first_rep = service.get_model_health(manager), service.get_priorities(rep)
        self.assert_no_rows(first, "Unavailable")
        self.assert_no_rows(first_rep, "Unreleased")
        self.assertEqual(service._cache, {})
        remaining = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] not in ("demo:manager", "demo:rep-a")]
        self.scopes(remaining, "revoked-r01b")
        self.assert_no_rows(service.get_model_health(manager), "Permission_denied")
        self.assert_no_rows(service.get_priorities(rep), "Permission_denied")

    def test_missing_or_malformed_fingerprint_is_not_authorization(self):
        from modules.module_2.service import Module2Service, Module2Error, Principal
        service = self.service()
        manager = Principal("demo:manager", True)
        self.assertEqual(service.get_model_health(manager).status, "Ok")
        for bad in ({"roles": ["manager"]}, {"scope_fingerprint": None}, {"scope_fingerprint": "abc"}, None):
            with self.subTest(bad=bad), patch.object(service.reader, "principal_scope", return_value=bad):
                self.assert_no_rows(service.get_model_health(manager), "Unavailable")
                self.assertEqual(self.cached_for(service, "demo:manager"), [])

        class NoScope:
            record = service.reader.record

            def read_page(self, *a, **k):
                raise AssertionError("must not be reached")
        with self.assertRaises(Module2Error):
            Module2Service(reader=NoScope())

    def test_duckdb_backend_has_the_same_guarantee(self):
        from modules.module_2.service import Module2Service, Principal
        service = Module2Service(self.sources["model"])
        rep = Principal("demo:rep-a", True)
        self.assertEqual(service.get_priorities(rep).status, "Ok")
        with patch.object(service.reader, "principal_scope", side_effect=PermissionError("No configured account scope")):
            self.assert_no_rows(service.get_priorities(rep), "Permission_denied")
        self.assertEqual(self.cached_for(service, "demo:rep-a"), [])


class R02CandidateIntegrityTests(_Base):
    """Verification failures are durable, CLI-visible and block activation of stale Validated payloads."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.model["release_id"], apply=True)

    def test_01_write_guard_blocks_plain_writes(self):
        import psycopg2
        schema = f"platform_release_{self.model['release_id']}"
        for statement in (f"UPDATE {schema}.dim_stores SET store_name = 'x'", f"DELETE FROM {schema}.dim_products",
                          f"TRUNCATE {schema}.dim_products", f"INSERT INTO {schema}.dim_products (sku_id) VALUES ('x')"):
            with self.subTest(statement=statement):
                conn = psycopg2.connect(self.db.publisher_url)
                try:
                    with conn.cursor() as cur, self.assertRaisesRegex(psycopg2.Error, "immutable"):
                        cur.execute(statement)
                finally:
                    conn.close()

    def test_02_same_count_corruption_after_validation_is_durable_and_cli_fails(self):
        base = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
        self.assertEqual(self.state(base["release_id"]), "Validated")
        before = self.history()
        self.corrupt_value(base["release_id"])
        code, out = _cli(self.db, "validate", "--release-id", base["release_id"])
        self.assertEqual((code, out["status"]), (1, "Failed"))
        self.assertIn("content_digest:store_sku_daily", out["error"])
        self.assertEqual(self.state(base["release_id"]), "Failed")                         # persisted
        stored = self.sql("SELECT checks::text, error FROM platform_meta.releases WHERE release_id = %s", [base["release_id"]])[0]
        self.assertIn("content_digest:store_sku_daily", stored[1])
        with self.assertRaisesRegex(pub.PublishError, "Only Validated"):
            pub.activate_postgres_release(self.db.publisher, base["release_id"], apply=True)
        code, out = _cli(self.db, "activate", "--release-id", base["release_id"], "--apply", "--confirm-database", self.db.name)
        self.assertEqual(code, 1)
        self.assertEqual(self.history(), before)
        self.assertTrue(self.payload_exists(base["release_id"]))                           # kept for inspection

    def test_03_stale_validated_payload_is_rechecked_at_activation(self):
        candidate = pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        release = candidate["release_id"]
        schema = f"platform_release_{release}"
        before = self.history()
        with self.db.admin() as conn, conn.cursor() as cur:                                # altered view, same columns/count
            cur.execute(f"""CREATE OR REPLACE VIEW {schema}.vw_stores AS SELECT store_id, upper(store_name) AS store_name,
                channel, region, state, segment_tier, city FROM {schema}.dim_stores""")
            cur.execute(f"ALTER TABLE {schema}.dim_products DISABLE TRIGGER platform_release_write_guard")
        self.assertEqual(self.state(release), "Validated")                                # stale success on record
        with self.assertRaisesRegex(pub.PublishError, "failed verification") as caught:
            pub.activate_postgres_release(self.db.publisher, release, apply=True)
        for failure in ("view_definition:stores", "write_guard:products"):
            self.assertIn(failure, str(caught.exception))
        self.assertIn("current publication is unchanged", str(caught.exception))
        self.assertEqual((self.state(release), self.history()), ("Failed", before))
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        self.assertEqual(reader.record["artifact_id"], self.model["release_id"])


class R02PublishedCorruptionTests(_Base):
    """A published payload that fails verification fails closed; recovery appends history only."""

    def test_published_corruption_and_recovery(self):
        import psycopg2
        first = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        pub1 = pub.activate_postgres_release(self.db.publisher, first["release_id"], apply=True)["publication_id"]
        second = pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        pub2 = pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True)["publication_id"]
        self.configure_demo()
        pinned = PostgresServingReader(self.db.app)
        self.addCleanup(pinned.close)
        self.assertTrue(pinned.read("stores", "demo:manager"))
        history = self.history()
        self.corrupt_value(second["release_id"], table="mart_store_day", column="oos_sku_count")
        code, out = _cli(self.db, "validate", "--release-id", second["release_id"])
        self.assertEqual(code, 1)
        self.assertIn("It was published", out["error"])
        self.assertEqual((self.state(second["release_id"]), self.history()), ("Failed", history))
        with self.assertRaises(DataUnavailable):
            pinned.read("stores", "demo:manager")                                          # pinned reader fails closed
        with self.assertRaises(DataUnavailable):
            PostgresServingReader(self.db.app)                                             # new readers too
        conn = psycopg2.connect(self.db.app_url)
        try:
            with conn.cursor() as cur, self.assertRaises(psycopg2.Error):
                cur.execute("SELECT count(*) FROM platform_serving.vw_stores")            # stable views too
        finally:
            conn.close()
        with self.assertRaisesRegex(pub.PublishError, "Only Validated"):
            pub.rollback_postgres_release(self.db.publisher, pub2, apply=True)
        with self.assertRaisesRegex(pub.PublishError, "never reloaded in place"):
            pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        back = pub.rollback_postgres_release(self.db.publisher, pub1, apply=True)          # documented recovery
        self.assertEqual(back["status"], "Rolled_back")
        after = self.history()
        self.assertEqual(after[0][:len(history[0])], history[0])                           # history appended only
        self.assertEqual(len(after[0]), len(history[0]) + 1)
        fresh = PostgresServingReader(self.db.app)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.record["artifact_id"], first["release_id"])
        self.assertTrue(fresh.read("stores", "demo:manager"))
        conn = psycopg2.connect(self.db.app_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM platform_serving.vw_stores")
                self.assertGreater(cur.fetchone()[0], 0)
        finally:
            conn.close()


class R03CollisionTests(_Base):
    """Pre-existing release-named schemas are never claimed or deleted, on first attempt or retry."""

    def foreign_schema(self, release_id, *, as_publisher):
        schema = f"platform_release_{release_id}"
        import psycopg2
        url = self.db.publisher_url if as_publisher else self.db.admin_url
        conn = psycopg2.connect(url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA {schema}")
                cur.execute(f"CREATE TABLE {schema}.keep_me (id int, note text)")
                cur.execute(f"INSERT INTO {schema}.keep_me VALUES (1, 'foreign sentinel')")
        finally:
            conn.close()
        with self.db.admin() as conn, conn.cursor() as cur:                               # foreign dependent object
            cur.execute(f"CREATE VIEW teammate_app.depends_{release_id} AS SELECT * FROM {schema}.keep_me")
        return schema

    def sentinel(self, schema, release_id):
        return (self.sql(f"SELECT note FROM {schema}.keep_me"),
                self.sql(f"SELECT count(*) FROM teammate_app.depends_{release_id}"))

    def test_same_owner_and_other_owner_collisions_survive_first_attempt_and_retry(self):
        for key, as_publisher in (("edge", True), ("no_health", False)):
            release = pub.inspect_serving_source(self.sources[key])["release_id"]
            schema = self.foreign_schema(release, as_publisher=as_publisher)
            with self.subTest(owner="publisher" if as_publisher else "other"):
                for attempt in range(2):
                    with self.assertRaisesRegex(pub.PublishError, "left untouched"):
                        pub.load_postgres_candidate(self.sources[key], self.db.publisher, apply=True)
                    self.assertEqual(self.sentinel(schema, release), ([("foreign sentinel",)], [(1,)]))
                    self.assertEqual(self.sql("SELECT count(*) FROM platform_meta.releases WHERE release_id = %s", [release])[0][0], 0)

    def test_registry_row_without_recorded_ownership_does_not_authorize_deletion(self):
        source = pub.inspect_serving_source(self.sources["base"])
        release = source["release_id"]
        with self.db.admin() as conn, conn.cursor() as cur:                               # e.g. an earlier failed CREATE race
            cur.execute("""INSERT INTO platform_meta.releases (release_id, content_key, state, source_artifact_id,
                source_database_sha256, source_run_sha256, source_quality_sha256, contract_sha256, manifest, views)
                VALUES (%s, %s, 'Failed', %s, %s, %s, %s, %s, '{}'::jsonb, '[]'::jsonb)""",
                        [release, source["content_key"], source["source"]["artifact_id"], source["_hashes"]["database"],
                         source["_hashes"]["run"], source["_hashes"]["quality"], source["contract_sha256"]])
        schema = self.foreign_schema(release, as_publisher=True)
        for attempt in range(2):
            with self.assertRaisesRegex(pub.PublishError, "not created by this publisher"):
                pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
            self.assertEqual(self.sentinel(schema, release), ([("foreign sentinel",)], [(1,)]))

    def test_own_failed_candidate_retry_is_recoverable_but_never_cascades(self):
        real = pub._check_release

        def failing(cur, release):
            checks, failures = real(cur, release)
            return checks + [{"check": "injected", "passed": False}], failures + ["injected"]
        with patch.object(pub, "_check_release", failing), self.assertRaisesRegex(pub.PublishError, "failed verification"):
            pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        release = pub.inspect_serving_source(self.sources["model"])["release_id"]
        schema = f"platform_release_{release}"
        self.assertEqual(self.state(release), "Failed")
        with self.db.admin() as conn, conn.cursor() as cur:                               # someone depends on our candidate
            cur.execute(f"CREATE VIEW teammate_app.uses_candidate AS SELECT store_id FROM {schema}.dim_stores")
        with self.assertRaisesRegex(pub.PublishError, "dependent or foreign objects; nothing was removed"):
            pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        self.assertTrue(self.sql("SELECT count(*) FROM teammate_app.uses_candidate")[0][0] > 0)
        with self.db.admin() as conn, conn.cursor() as cur:
            cur.execute("DROP VIEW teammate_app.uses_candidate")
        retry = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        self.assertEqual((retry["status"], retry["action"]), ("Validated", "Restarted_own_candidate"))
        self.assertEqual(self.db.legacy_snapshot()["data"], self.legacy_before["data"])


class R04SourceIdentityTests(_Base):
    """The source run record is part of the release identity; reuse reports what is stored."""

    def mutated_copy(self, key, name, change):
        import shutil
        target = self.sources["root"] / f"{name}-{self.db.suffix}"
        shutil.copytree(self.sources[key], target)
        run = json.loads((target / "run.json").read_text())
        change(run)
        (target / "run.json").write_text(json.dumps(run))
        return target

    def test_metadata_only_mutations_are_conflicts_not_reuse(self):
        original = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        cases = {"as_of": lambda r: r.update(as_of="2026-06-29"),
                 "model_status": lambda r: r.update(model_outputs_status="Unavailable_model_not_released"),
                 "qualification": lambda r: r["model_outputs"].update(eligibility_status="Eligible_for_review")}
        for name, change in cases.items():
            copy = self.mutated_copy("model", name, change)
            with self.subTest(case=name):
                inspected = pub.inspect_serving_source(copy)
                self.assertNotEqual(inspected["release_id"], original["release_id"])      # identity binds run bytes
                with self.assertRaisesRegex(pub.PublishError, "conflicting source identity"):
                    pub.load_postgres_candidate(copy, self.db.publisher, apply=True)
        persisted = self.sql("""SELECT manifest->>'as_of', manifest->>'run_sha256' FROM platform_meta.releases
            WHERE source_artifact_id = %s""", [original["source"]["artifact_id"]])
        self.assertEqual(persisted, [("2026-06-30", original["source"]["run_sha256"])])
        reused = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        self.assertEqual(reused["status"], "Reused_validated_release")
        stored = self.sql("SELECT manifest FROM platform_meta.releases WHERE release_id = %s", [original["release_id"]])[0][0]
        self.assertEqual(reused["source"], {k: v for k, v in stored.items() if k != "kpi_components"})
        self.assertEqual(reused["as_of"], stored["as_of"])

    def test_new_source_identity_is_a_distinct_truthful_release(self):
        copy = self.mutated_copy("model", "renamed", lambda r: r.update(artifact_id=r["artifact_id"] + "-copy"))
        result = pub.load_postgres_candidate(copy, self.db.publisher, apply=True)
        pub.activate_postgres_release(self.db.publisher, result["release_id"], apply=True)
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        self.assertTrue(reader.record["source_artifact_id"].endswith("-copy"))
        self.assertEqual(reader.record["run_sha256"], result["source"]["run_sha256"])

    def test_inputs_changing_during_import_fail_without_payload(self):
        for victim in ("run.json", "quality.json"):
            copy = self.mutated_copy("base", f"midway-{victim.split('.')[0]}",
                                     lambda r: r.update(artifact_id=r["artifact_id"] + "-" + victim.split(".")[0]))
            release = pub.inspect_serving_source(copy)["release_id"]
            real = pub._copy_view
            calls = {"n": 0}

            def mutate_after_first(*args, **kwargs):
                out = real(*args, **kwargs)
                calls["n"] += 1
                if calls["n"] == 1:
                    with open(copy / victim, "a") as handle:
                        handle.write(" ")
                return out
            with self.subTest(victim=victim), patch.object(pub, "_copy_view", mutate_after_first), \
                    self.assertRaisesRegex(pub.PublishError, "changed during import"):
                pub.load_postgres_candidate(copy, self.db.publisher, apply=True)
            self.assertEqual(self.state(release), "Failed")
            self.assertFalse(self.payload_exists(release))


class R05CapacityTests(_Base):
    """Every stored payload counts against the bound, whatever its state; nothing is auto-deleted."""

    def inject_failure(self):
        real = pub._check_release

        def failing(cur, release):
            checks, failures = real(cur, release)
            return checks + [{"check": "synthetic_injected_failure", "passed": False}], failures + ["synthetic_injected_failure"]
        return patch.object(pub, "_check_release", failing)

    def test_mixed_success_failure_and_interrupted_payloads_are_bounded(self):
        base = pub.load_postgres_candidate(self.sources["base"], self.db.publisher, apply=True)
        pub1 = pub.activate_postgres_release(self.db.publisher, base["release_id"], apply=True)["publication_id"]
        model = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        pub2 = pub.activate_postgres_release(self.db.publisher, model["release_id"], apply=True)["publication_id"]
        with patch.object(pub, "_verify", side_effect=RuntimeError("interrupted before validation")), \
                self.assertRaisesRegex(RuntimeError, "interrupted"):
            pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)
        edge = pub.inspect_serving_source(self.sources["edge"])["release_id"]
        self.assertEqual(self.state(edge), "Imported")                                   # interrupted leftover holds storage
        before = (self.history(), self.sql("SELECT release_id, state FROM platform_meta.releases ORDER BY 1"))
        with self.inject_failure(), self.assertRaisesRegex(pub.PublishError, "Storage bound reached: 3") as caught:
            pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        for release in (base["release_id"], model["release_id"], edge):
            self.assertIn(release, str(caught.exception))
        self.assertIn("bytes", str(caught.exception))
        self.assertEqual((self.history(), self.sql("SELECT release_id, state FROM platform_meta.releases ORDER BY 1")), before)
        for release in (base["release_id"], model["release_id"], edge):
            self.assertTrue(self.payload_exists(release))                                # current/rollback/leftover kept
        self.assertEqual(self.history()[1], [(pub2,)])
        retry = pub.load_postgres_candidate(self.sources["edge"], self.db.publisher, apply=True)   # same-source retry
        self.assertEqual((retry["status"], retry["action"]), ("Validated", "Restarted_own_candidate"))
        self.assertIn(pub1, [r[0] for r in self.history()[0]])


class R05RepeatedFailureTests(_Base):
    def test_repeated_distinct_validation_failures_cannot_exceed_the_bound(self):
        real = pub._check_release

        def failing(cur, release):
            checks, failures = real(cur, release)
            return checks + [{"check": "synthetic_injected_failure", "passed": False}], failures + ["synthetic_injected_failure"]
        outcomes = {}
        for key in ("base", "model", "edge", "no_health"):
            with patch.object(pub, "_check_release", failing):
                try:
                    pub.load_postgres_candidate(self.sources[key], self.db.publisher, apply=True)
                except pub.PublishError as error:
                    outcomes[key] = "bound" if "Storage bound" in str(error) else "failed"
        self.assertEqual(outcomes, {"base": "failed", "model": "failed", "edge": "failed", "no_health": "bound"})
        payloads = self.sql(r"SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'platform\_release\_%%'")[0][0]
        self.assertEqual(payloads, 3)
        self.assertEqual(self.sql("SELECT count(*) FROM platform_meta.releases")[0][0], 3)   # refused before any claim
        report = pub.preflight_postgres(self.db.publisher)["size"]
        self.assertEqual(report["totals"]["failed_bytes"] > 0, True)


class R06ExportTests(_Base):
    """A multi-page export is complete under one verified scope or fails without a file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.model["release_id"], apply=True)

    def setUp(self):
        self.configure_demo()

    def service(self, backend="postgres"):
        from modules.module_2.service import Module2Service
        if backend == "duckdb":
            return Module2Service(self.sources["model"])
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        return Module2Service(reader=reader)

    def one_row_pages(self, service, before_second_page=None):
        """Transparent wrapper: same service method, page size 1 so the small fixture spans many pages."""
        real, calls = service.get_priorities, {"n": 0}

        def paged(principal, filters=None, cursor=None, page_size=50):
            calls["n"] += 1
            if calls["n"] == 2 and before_second_page:
                before_second_page()
            return real(principal, filters, cursor=cursor, page_size=1)
        service.get_priorities = paged
        return calls

    def test_unchanged_scope_multi_page_export_is_complete_on_both_backends(self):
        from modules.module_2.service import Principal
        manager = Principal("demo:manager", True)
        for backend in ("postgres", "duckdb"):
            with self.subTest(backend=backend):
                expected = self.service(backend).export_visit_plan(manager)
                service = self.service(backend)
                calls = self.one_row_pages(service)
                exported = service.export_visit_plan(manager)
                self.assertEqual(exported, expected)
                self.assertEqual(calls["n"], len(expected.strip().splitlines()) - 1)        # one page per store
                self.assertGreater(calls["n"], 1)
                filtered = service.export_visit_plan(manager, {"alert_only": True})
                self.assertEqual(filtered, self.service(backend).export_visit_plan(manager, {"alert_only": True}))

    def test_revocation_before_a_later_page_aborts_without_csv(self):
        from modules.module_2.service import PermissionDenied, Principal
        service = self.service()
        keep = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] != "demo:manager"]
        self.one_row_pages(service, lambda: self.scopes(keep, "revoked-mid-export"))
        with self.assertRaises(PermissionDenied):
            service.export_visit_plan(Principal("demo:manager", True))
        self.assertEqual([k for k in service._cache if "demo:manager" in k], [])

    def test_unavailable_later_page_aborts_without_csv(self):
        from modules.module_2.service import Principal
        for backend in ("postgres", "duckdb"):
            with self.subTest(backend=backend):
                service = self.service(backend)
                outage = patch.object(service.reader, "principal_scope", side_effect=DataUnavailable("synthetic outage"))
                self.addCleanup(outage.stop)
                self.one_row_pages(service, outage.start)
                with self.assertRaises(DataUnavailable):
                    service.export_visit_plan(Principal("demo:manager", True))
                outage.stop()

    def test_narrowed_scope_mid_export_is_not_combined(self):
        from modules.module_2.service import PermissionDenied, Principal
        service = self.service()
        narrowed = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] != "demo:manager"] + \
            [a for a in self.sources["scope"]["assignments"] if a["principal_id"] == "demo:manager"][:3]
        self.one_row_pages(service, lambda: self.scopes(narrowed, "narrowed-mid-export"))
        with self.assertRaisesRegex(PermissionDenied, "scope changed during export"):
            service.export_visit_plan(Principal("demo:manager", True))
        fresh = self.service().export_visit_plan(Principal("demo:manager", True))       # restart under the new scope
        self.assertEqual(len(fresh.strip().splitlines()) - 1, 3)


class R07AlreadyCurrentTests(_Base):
    """Idempotent activation/rollback of the current release is verified, not assumed."""

    def publication_count(self):
        return self.sql("SELECT count(*) FROM platform_meta.publications")[0][0]

    def test_already_current_is_verified_and_fails_closed(self):
        import psycopg2
        first = pub.load_postgres_candidate(self.sources["model"], self.db.publisher, apply=True)
        pub1 = pub.activate_postgres_release(self.db.publisher, first["release_id"], apply=True)["publication_id"]
        second = pub.load_postgres_candidate(self.sources["no_health"], self.db.publisher, apply=True)
        pub2 = pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True)["publication_id"]
        self.configure_demo()
        # Healthy idempotent calls: verified, no new publication.
        count = self.publication_count()
        self.assertEqual(pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True)["status"],
                         "Already_current")
        self.assertEqual(pub.rollback_postgres_release(self.db.publisher, pub2, apply=True)["status"], "Already_current")
        code, out = _cli(self.db, "activate", "--release-id", second["release_id"], "--apply", "--confirm-database", self.db.name)
        self.assertEqual((code, out["status"]), (0, "Already_current"))
        self.assertEqual(self.publication_count(), count)
        verified = self.sql("SELECT last_verified_at IS NOT NULL FROM platform_meta.releases WHERE release_id = %s",
                            [second["release_id"]])[0][0]
        self.assertTrue(verified)
        with self.assertRaisesRegex(pub.PublishError, "stale"):                            # CAS still enforced
            pub.activate_postgres_release(self.db.publisher, second["release_id"], apply=True,
                                          expected_current_publication_id="f" * 32)
        # Corrupt the current payload: the idempotent call now detects it and fails closed.
        pinned = PostgresServingReader(self.db.app)
        self.addCleanup(pinned.close)
        history = self.history()
        self.corrupt_value(second["release_id"])
        code, out = _cli(self.db, "activate", "--release-id", second["release_id"], "--apply", "--confirm-database", self.db.name)
        self.assertEqual(code, 1)
        self.assertIn("content_digest:store_sku_daily", out["error"])
        self.assertIn("It was published", out["error"])
        self.assertEqual((self.state(second["release_id"]), self.history()), ("Failed", history))
        with self.assertRaises(DataUnavailable):
            pinned.read("stores", "demo:manager")
        with self.assertRaises(DataUnavailable):
            PostgresServingReader(self.db.app)
        conn = psycopg2.connect(self.db.app_url)
        try:
            with conn.cursor() as cur, self.assertRaises(psycopg2.Error):
                cur.execute("SELECT count(*) FROM platform_serving.vw_stores")
        finally:
            conn.close()
        with self.assertRaisesRegex(pub.PublishError, "Only Validated"):
            pub.rollback_postgres_release(self.db.publisher, pub2, apply=True)
        back = pub.rollback_postgres_release(self.db.publisher, pub1, apply=True)        # documented recovery
        self.assertEqual(back["status"], "Rolled_back")
        self.assertEqual(self.history()[0][:len(history[0])], history[0])
        fresh = PostgresServingReader(self.db.app)
        self.addCleanup(fresh.close)
        self.assertTrue(fresh.read("stores", "demo:manager"))
        # The rollback path's already-current branch is verified too.
        self.corrupt_value(first["release_id"], table="mart_store_day", column="oos_sku_count")
        history = self.history()
        with self.assertRaisesRegex(pub.PublishError, "failed verification"):
            pub.rollback_postgres_release(self.db.publisher, back["publication_id"], apply=True)
        self.assertEqual((self.state(first["release_id"]), self.history()), ("Failed", history))


class R06bConcurrentExportTests(_Base):
    """Export pages are bound to the scope identity carried by each response, not to shared service state."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = pub.load_postgres_candidate(cls.sources["model"], cls.db.publisher, apply=True)
        pub.activate_postgres_release(cls.db.publisher, cls.model["release_id"], apply=True)

    def setUp(self):
        self.configure_demo()

    def service(self, backend="postgres"):
        from modules.module_2.service import Module2Service
        if backend == "duckdb":
            return Module2Service(self.sources["model"])
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        return Module2Service(reader=reader)

    def interleave(self, service, principal, *, after_page, narrow):
        """Wrap the real get_priorities (page size 1). After page ``after_page`` is built — before the
        export consumes it — optionally narrow the scope, then run a real second request for the same
        principal on another thread and wait for it (deterministic: no sleeps)."""
        from concurrent.futures import ThreadPoolExecutor
        real, seen = service.get_priorities, {"pages": [], "second": None, "excluded": None}

        def page(p, filters=None, cursor=None, page_size=50):
            out = real(p, filters, cursor=cursor, page_size=1)
            seen["pages"].append([r["store_id"] for r in out.rows])
            if len(seen["pages"]) == after_page:
                if narrow:
                    current = seen["pages"][-1][0]
                    allowed = [s for s in self.sources["stores"] if s != current][:3]
                    seen["excluded"] = current
                    keep = [a for a in self.sources["scope"]["assignments"] if a["principal_id"] != principal.principal_id]
                    keep += [{"principal_id": principal.principal_id, "store_id": s, "role": "manager"} for s in allowed]
                    self.scopes(keep, f"concurrent-narrowed-{after_page}")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    second = pool.submit(real, p, page_size=1).result()
                    population = pool.submit(service.get_population, p).result()
                seen["second"] = (second.status, second.scope_identity, population.status)
            return out
        service.get_priorities = page
        return seen

    def test_scope_change_refreshed_by_another_request_before_first_page_is_consumed(self):
        from modules.module_2.service import PermissionDenied, Principal
        service, manager = self.service(), Principal("demo:manager", True)
        seen = self.interleave(service, manager, after_page=1, narrow=True)
        with self.assertRaisesRegex(PermissionDenied, "scope changed during export"):
            service.export_visit_plan(manager)
        # The other request really ran under the new scope and refreshed the shared state ...
        self.assertEqual(seen["second"][0], "Ok")
        self.assertEqual(service._fingerprints["demo:manager"], seen["second"][1])
        # ... yet the export kept the identity of the page it actually received and aborted.
        self.assertEqual(len(seen["pages"]), 2)

    def test_scope_change_between_later_pages_aborts(self):
        from modules.module_2.service import PermissionDenied, Principal
        service, manager = self.service(), Principal("demo:manager", True)
        seen = self.interleave(service, manager, after_page=2, narrow=True)
        with self.assertRaisesRegex(PermissionDenied, "scope changed during export"):
            service.export_visit_plan(manager)
        self.assertEqual(len(seen["pages"]), 3)

    def test_concurrent_reads_without_scope_change_keep_export_complete(self):
        from modules.module_2.service import Principal
        manager = Principal("demo:manager", True)
        for backend in ("postgres", "duckdb"):
            for after_page in (1, 2):
                with self.subTest(backend=backend, after_page=after_page):
                    expected = self.service(backend).export_visit_plan(manager)
                    service = self.service(backend)
                    seen = self.interleave(service, manager, after_page=after_page, narrow=False)
                    self.assertEqual(service.export_visit_plan(manager), expected)
                    self.assertEqual(seen["second"][0], "Ok")
                    self.assertEqual(len(seen["pages"]), len(expected.strip().splitlines()) - 1)

    def test_page_without_verified_identity_aborts(self):
        from modules.module_2.service import Principal
        service = self.service()
        real = service.get_priorities

        def unverified(p, filters=None, cursor=None, page_size=50):
            out = real(p, filters, cursor=cursor, page_size=1)
            out.scope_identity = None
            return out
        service.get_priorities = unverified
        with self.assertRaises(DataUnavailable):
            service.export_visit_plan(Principal("demo:manager", True))

    def test_scope_identity_is_internal_metadata_only(self):
        from modules.module_2.service import Principal
        response = self.service().get_priorities(Principal("demo:manager", True))
        self.assertRegex(response.scope_identity, "^[0-9a-f]{64}$")
        self.assertNotIn("scope_identity", response.to_dict())
        clone = type(response)(**{**response.to_dict(), "scope_identity": "0" * 64})
        self.assertEqual(clone, response)                                              # not part of equality
