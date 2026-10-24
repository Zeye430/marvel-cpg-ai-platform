"""Shared PostgreSQL serving: contract, codecs, configuration and offline guarantees (no database)."""

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from data_platform import postgres_schema as schema
from data_platform.postgres_config import ConfigError, PostgresConfig, configured_backend, sanitize
from data_platform.serving import VIEWS

try:
    from tests import postgres_fixtures as fx
except ImportError:
    import postgres_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
BLOCKER = r"""
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('psycopg2', 'shiny', 'shinywidgets', 'plotly', 'dotenv', 'sqlalchemy') \
                or name in ('shared.data', 'shared.auth'):
            raise ImportError('blocked import: ' + name)
sys.meta_path.insert(0, Block())
def audit(event, args):
    if event == 'socket.connect':
        raise RuntimeError('network blocked')
sys.addaudithook(audit)
"""


def blocked(code, *args, env=None):
    return subprocess.run([sys.executable, "-c", BLOCKER + code, *args], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}, timeout=120)


class ContractTests(unittest.TestCase):
    def test_contract_covers_exactly_the_serving_views(self):
        contract = schema.load_contract()
        self.assertEqual(set(contract["views"]), set(VIEWS))
        self.assertEqual(contract["interface_version"], "shared_postgres_serving.v1")
        self.assertEqual({n for n, s in contract["views"].items() if s["optional"]}, {"model_health", "model_monitoring"})
        for name, spec in contract["views"].items():
            self.assertTrue(spec["key"], name)
            for _, logical in spec["columns"]:
                schema.pg_type(logical)
        self.assertEqual(contract["transfer_chunk_rows"], 10000)
        self.assertNotIn("account_scope", json.dumps(contract["views"]))

    def test_type_mapping_is_lossless_or_refused(self):
        self.assertEqual(schema.pg_type("VARCHAR"), 'TEXT COLLATE "C"')
        self.assertEqual(schema.pg_type("DECIMAL(18,2)"), "NUMERIC(18,2)")
        self.assertEqual(schema.pg_type("HUGEINT"), "NUMERIC(39,0)")
        self.assertEqual(schema.pg_type("DOUBLE"), "DOUBLE PRECISION")
        for unsupported in ("TIMESTAMP", "BLOB", "FLOAT", "DECIMAL(40,2)", "DECIMAL(4,6)", "VARCHAR[]", "JSON", "UUID"):
            with self.subTest(type=unsupported), self.assertRaises(ValueError):
                schema.pg_type(unsupported)
        with self.assertRaises(ValueError):
            schema.quote_ident('x"; DROP TABLE y; --')


class CodecTests(unittest.TestCase):
    def test_copy_text_escapes_and_nulls(self):
        cases = {(None, "VARCHAR"): "\\N", ("", "VARCHAR"): "", ("\\N", "VARCHAR"): "\\\\N", ("null", "VARCHAR"): "null",
                 ("a\tb\nc\rd\\e", "VARCHAR"): "a\\tb\\nc\\rd\\\\e", ("東京 🛒", "VARCHAR"): "東京 🛒",
                 (True, "BOOLEAN"): "t", (False, "BOOLEAN"): "f", (date(2026, 6, 30), "DATE"): "2026-06-30",
                 (2 ** 100, "HUGEINT"): str(2 ** 100), (-0.0, "DOUBLE"): "-0.0", (float("nan"), "DOUBLE"): "NaN",
                 (float("inf"), "DOUBLE"): "Infinity", (float("-inf"), "DOUBLE"): "-Infinity", (5e-324, "DOUBLE"): "5e-324",
                 (0.1 + 0.2, "DOUBLE"): "0.30000000000000004", (Decimal("-0.50"), "DECIMAL(18,2)"): "-0.50"}
        for (value, logical), expected in cases.items():
            with self.subTest(value=value, logical=logical):
                self.assertEqual(schema.copy_value(value, logical), expected)
        self.assertEqual(float(schema.copy_value(0.1 + 0.2, "DOUBLE")), 0.1 + 0.2)
        with self.assertRaises(schema.UnsupportedValue):
            schema.copy_value("nul\x00byte", "VARCHAR")
        self.assertEqual(schema.copy_row(["a", None, 1], ["VARCHAR", "VARCHAR", "BIGINT"]), "a\t\\N\t1\n")

    def test_canonical_values_are_distinct_and_typed(self):
        texts = [None, "", "null", "NULL", "\\N", " ", "0"]
        encoded = [schema.canonical_value(v, "VARCHAR") for v in texts]
        self.assertEqual(len(set(encoded)), len(texts))
        floats = [-0.0, 0.0, float("nan"), float("inf"), float("-inf"), 5e-324]
        self.assertEqual(len({schema.canonical_value(v, "DOUBLE") for v in floats}), len(floats))
        self.assertEqual(schema.canonical_value(Decimal("1.2"), "DECIMAL(18,2)"),
                         schema.canonical_value(Decimal("1.20"), "DECIMAL(18,2)"))
        with self.assertRaises(schema.UnsupportedValue):
            schema.canonical_value(Decimal("1.234"), "DECIMAL(18,2)")
        self.assertEqual(schema.canonical_value(Decimal(7), "HUGEINT"), schema.canonical_value(7, "HUGEINT"))
        with self.assertRaises(schema.UnsupportedValue):
            schema.canonical_value(1, "BOOLEAN")
        self.assertEqual(schema.from_postgres(Decimal("12"), "HUGEINT"), 12)
        self.assertIs(type(schema.from_postgres(Decimal("12"), "HUGEINT")), int)
        a, b = schema.RowDigest(), schema.RowDigest()
        a.update(["x", None], ["VARCHAR", "VARCHAR"])
        b.update(["x", ""], ["VARCHAR", "VARCHAR"])
        self.assertNotEqual(a.hexdigest(), b.hexdigest())
        c, d = schema.RowDigest(), schema.RowDigest()
        c.update(["ab", "c"], ["VARCHAR", "VARCHAR"])
        d.update(["a", "bc"], ["VARCHAR", "VARCHAR"])
        self.assertNotEqual(c.hexdigest(), d.hexdigest())

    def test_order_uses_unique_key_without_float_columns(self):
        spec = schema.load_contract()["views"]["store_sku_daily"]
        self.assertEqual(schema.order_by(spec), '"date" ASC NULLS LAST, "store_id" ASC NULLS LAST, "sku_id" ASC NULLS LAST')


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ca = Path(temp.name) / "ca.pem"
        self.ca.write_text("synthetic CA placeholder")

    def test_remote_requires_verified_tls_and_ca(self):
        url = "postgresql://reader:s3cret-value@db.example.invalid:12345/serving"
        self.assertEqual(PostgresConfig.from_url(url, sslrootcert=str(self.ca)).sslmode, "verify-full")   # secure default
        for bad in (url + "?sslmode=require", url + "?sslmode=disable", url + "?sslmode=verify-ca", url + "?sslmode=prefer"):
            with self.subTest(url=bad), self.assertRaises(ConfigError):
                PostgresConfig.from_url(bad, sslrootcert=str(self.ca))
        with self.assertRaises(ConfigError):
            PostgresConfig.from_url(url + "?sslmode=verify-full")                         # no CA file
        with self.assertRaises(ConfigError):
            PostgresConfig.from_url(url + "?sslmode=verify-full", sslrootcert=str(self.ca) + ".missing")
        config = PostgresConfig.from_url(url + "?sslmode=verify-full", sslrootcert=str(self.ca))
        self.assertEqual((config.sslmode, config.port, config.local_test), ("verify-full", 12345, False))
        self.assertEqual(config.connect_kwargs()["sslrootcert"], str(self.ca))
        self.assertIn("statement_timeout=5000", config.connect_kwargs()["options"])

    def test_local_test_mode_only_for_numeric_loopback_or_socket(self):
        for host in ("localhost", "10.0.0.5", "db.local", "127.0.0.1.evil.example", "0.0.0.0"):
            with self.subTest(host=host), self.assertRaises(ConfigError):
                PostgresConfig.from_url(f"postgresql://u:p@{host}:5432/db", local_test=True)
        for host in ("127.0.0.1", "[::1]"):
            self.assertTrue(PostgresConfig.from_url(f"postgresql://u:p@{host}:5432/db", local_test=True).local_test)
        socket = PostgresConfig.from_url("postgresql://u@x/db?host=/tmp/pgsock", local_test=True)
        self.assertEqual(socket.host, "/tmp/pgsock")
        with self.assertRaises(ConfigError):
            PostgresConfig.from_url("postgresql://u:p@127.0.0.1/db", local_test="yes")

    def test_secrets_never_appear_in_descriptions_or_errors(self):
        config = PostgresConfig.from_url("postgresql://u:VerySecret123@127.0.0.1:5432/db", local_test=True)
        for text in (repr(config), json.dumps(config.describe()), str(config.describe())):
            self.assertNotIn("VerySecret123", text)

        class FakeError(Exception):
            pgcode = "28P01"
            pgerror = 'FATAL: password authentication failed for "u" using postgresql://u:VerySecret123@h/db password=VerySecret123\nDETAIL: more'
        message = sanitize(FakeError(), config)
        self.assertNotIn("VerySecret123", message)
        self.assertNotIn("DETAIL", message)
        self.assertIn("28P01", message)

    def test_only_explicit_environment_names_are_used(self):
        legacy = {"DATABASE_URL": "postgresql://legacy:pw@db.example.invalid/legacy"}
        with self.assertRaises(ConfigError):
            PostgresConfig.from_env("app", legacy)
        env = {"SHARED_DATA_DATABASE_URL": "postgresql://app:pw@127.0.0.1:5/a", "SHARED_DATA_LOCAL_TEST": "1",
               "SHARED_DATA_PUBLISH_DATABASE_URL": "postgresql://pub:pw@127.0.0.1:5/p",
               "SHARED_DATA_PUBLISH_STATEMENT_TIMEOUT_SECONDS": "120"}
        self.assertEqual(PostgresConfig.from_env("app", env).dbname, "a")
        publisher = PostgresConfig.from_env("publisher", env)
        self.assertEqual((publisher.dbname, publisher.statement_timeout_ms), ("p", 120000))
        for bad in ("0", "abc", "999999"):
            with self.subTest(timeout=bad), self.assertRaises(ConfigError):
                PostgresConfig.from_env("publisher", {**env, "SHARED_DATA_PUBLISH_STATEMENT_TIMEOUT_SECONDS": bad})
        with self.assertRaises(ConfigError):
            PostgresConfig.from_url("postgresql://u:p@127.0.0.1/db?options=-c%20role%3Dadmin", local_test=True)
        self.assertEqual(configured_backend({}), "duckdb")
        self.assertEqual(configured_backend({"SHARED_DATA_BACKEND": "postgres"}), "postgres")
        with self.assertRaises(ConfigError):
            configured_backend({"SHARED_DATA_BACKEND": "aiven"})


class OfflineTests(unittest.TestCase):
    def test_imports_are_lazy_and_offline(self):
        modules = ["data_platform.readers", "data_platform.postgres_config", "data_platform.postgres_schema",
                   "data_platform.postgres_publisher", "data_platform.postgres_reader", "data_platform.postgres_cli",
                   "data_platform.access"]
        result = blocked("import importlib\nfor m in " + repr(modules) + ": importlib.import_module(m)\nprint('ok')")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_factory_needs_explicit_backend_inputs(self):
        from data_platform.readers import open_business_reader
        for kwargs in ({"backend": "aiven"}, {"backend": "duckdb"}, {"backend": "postgres"},
                       {"backend": "postgres", "artifact_dir": "x"},
                       {"backend": "duckdb", "artifact_dir": "x", "release_id": "r" + "0" * 24}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfigError):
                open_business_reader(**kwargs)

    def test_cli_defaults_have_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            for args in (["--help"], ["load", "--help"], ["activate", "--help"]):
                result = blocked("import runpy, sys; sys.argv=['postgres_cli', *sys.argv[1:]]; "
                                 "runpy.run_module('data_platform.postgres_cli', run_name='__main__')", *args)
                self.assertEqual(result.returncode, 0, result.stderr[-1500:])
            run = ("import json, sys\nfrom data_platform.postgres_cli import run\n"
                   "code, out = run(sys.argv[1:])\nprint(json.dumps(out)); sys.exit(code)")
            missing = blocked(run, "preflight", env={"SHARED_DATA_PUBLISH_DATABASE_URL": ""})
            self.assertEqual(missing.returncode, 1, missing.stderr[-1500:])
            self.assertIn("SHARED_DATA_PUBLISH_DATABASE_URL", json.loads(missing.stdout)["error"])
            env = {"SHARED_DATA_PUBLISH_DATABASE_URL": "postgresql://p:TopSecret9@127.0.0.1:1/target",
                   "SHARED_DATA_LOCAL_TEST": "1", "DATABASE_URL": "postgresql://legacy:x@db.example.invalid/x"}
            unconfirmed = blocked(run, "init", "--apply", env=env)        # refused before any connection
            self.assertEqual(unconfirmed.returncode, 1)
            self.assertIn("confirm-database", unconfirmed.stdout)
            self.assertNotIn("TopSecret9", unconfirmed.stdout + unconfirmed.stderr)
            no_db = blocked(run, "inspect-source", "--artifact-dir", str(Path(temporary) / "absent"))
            self.assertEqual(no_db.returncode, 1)
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), [])

    def test_validate_is_documented_as_a_recorded_operation_not_a_dry_run(self):
        result = blocked("import runpy, sys; sys.argv=['postgres_cli', '--help']; "
                         "runpy.run_module('data_platform.postgres_cli', run_name='__main__')")
        self.assertIn("validate records its result", " ".join(result.stdout.split()))
        sub = blocked("import runpy, sys; sys.argv=['postgres_cli', 'validate', '--help']; "
                      "runpy.run_module('data_platform.postgres_cli', run_name='__main__')")
        self.assertEqual(sub.returncode, 0)
        from data_platform import postgres_cli
        self.assertIn("validate has no dry run", " ".join(postgres_cli.__doc__.split()))
        readme = " ".join((ROOT / "data_platform/README.md").read_text().split())
        self.assertIn("`inspect-source` and `preflight` are read-only", readme)
        self.assertIn("`validate` has no dry run", readme)
        self.assertNotIn("Every mutating command is a **dry run**", readme)
        contract = json.loads((ROOT / "data_platform/contracts/v3/module-data-contract.json").read_text())
        self.assertIn("validate records its result", contract["postgres_serving"]["publisher"])

    def test_read_example_validates_before_connecting(self):
        run = "import sys\nfrom examples.read_postgres_data import main\nsys.exit(main(sys.argv[1:]))"
        for args, code in ((["--help"], 0), ([], 2), (["--principal-id", "p", "--limit", "0"], 2),
                           (["--principal-id", "p"], 1)):
            with self.subTest(args=args):
                result = blocked(run, *args, env={"SHARED_DATA_DATABASE_URL": "", "DATABASE_URL": "postgresql://x:y@h/z"})
                self.assertEqual(result.returncode, code, result.stderr[-800:])
        result = blocked(run, "--principal-id", "p",
                         env={"SHARED_DATA_DATABASE_URL": "postgresql://r:Pw55@db.example.invalid/x?sslmode=require"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("verify-full", result.stderr)
        self.assertNotIn("Pw55", result.stderr + result.stdout)

    def test_module2_backend_selection_is_explicit_and_sanitized(self):
        code = ("import os\nfrom modules.module_2.server import configured_service\n"
                "print(configured_service()[1])\nprint(configured_service('duckdb')[1])\n")
        env = {"SHARED_DATA_BACKEND": "postgres", "SHARED_DATA_DATABASE_URL": "postgresql://a:Hidden77@db.example.invalid/x",
               "MODULE2_SERVING_ROOT": "/nonexistent-root"}
        stub = ("import sys, types\nfor n in ('shiny', 'htmltools'):\n"
                "    m = types.ModuleType(n); m.reactive = m.render = m.ui = m; m.TagList = m.tags = object\n"
                "    sys.modules[n] = m\n")
        result = subprocess.run([sys.executable, "-c", stub + code], cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(ROOT), **env}, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        postgres_line, duck_line = result.stdout.strip().splitlines()
        self.assertTrue(postgres_line.startswith("Shared PostgreSQL serving unavailable"), postgres_line)
        self.assertNotIn("Hidden77", result.stdout)
        self.assertNotIn("example.invalid", result.stdout)
        self.assertTrue(duck_line.startswith("Module 2 serving candidate unavailable"), duck_line)  # no fallback
        unsupported = subprocess.run([sys.executable, "-c", stub + code], cwd=ROOT, capture_output=True, text=True,
                                     env={**os.environ, "PYTHONPATH": str(ROOT), "SHARED_DATA_BACKEND": "sqlite"}, timeout=120)
        self.assertIn("Unsupported SHARED_DATA_BACKEND", unsupported.stdout)


class FixtureGuardTests(unittest.TestCase):
    def test_test_database_guards(self):
        good = "postgresql://a:b@127.0.0.1:55437/shared_serving_synthetic_test"
        fx.check_test_url(good)
        for bad in ("", "postgresql://a:b@localhost:55437/shared_serving_synthetic_test",
                    "postgresql://a:b@db.example.invalid:5432/shared_serving_synthetic_test",
                    "postgresql://a:b@127.0.0.1:55437/production", "mysql://a:b@127.0.0.1/shared_serving_synthetic_test"):
            with self.subTest(url=bad), self.assertRaises(fx.UnsafeTarget):
                fx.check_test_url(bad)

        class Cursor:
            def __init__(self, marker):
                self.marker = marker

            def execute(self, *args):
                pass

            def fetchone(self):
                return (self.marker, "shared_serving_synthetic_test")
        self.assertEqual(fx.check_marker(Cursor(fx.MARKER_VALUE)), "shared_serving_synthetic_test")
        for marker in (None, "", "production"):
            with self.subTest(marker=marker), self.assertRaises(fx.UnsafeTarget):
                fx.check_marker(Cursor(marker))

    def test_required_mode_fails_instead_of_skipping(self):
        env = {**os.environ, fx.TEST_URL_ENV: "postgresql://a:b@127.0.0.1:1/shared_serving_synthetic_test",
               fx.REQUIRE_ENV: "1", "PYTHONPATH": str(ROOT)}
        result = subprocess.run([sys.executable, "-m", "unittest", "tests.test_postgres_integration"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=300)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("skipped", result.stderr.splitlines()[-1] if result.stderr else "")
        optional = subprocess.run([sys.executable, "-m", "unittest", "tests.test_postgres_integration"], cwd=ROOT,
                                  env={**env, fx.REQUIRE_ENV: ""}, capture_output=True, text=True, timeout=300)
        self.assertEqual(optional.returncode, 0, optional.stderr[-1500:])
        self.assertIn("skipped", optional.stderr)


if __name__ == "__main__":
    unittest.main()
