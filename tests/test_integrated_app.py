"""Integrated Module 1 + 2 + 4 app: one pinned shared source, demo identities, explicit failures.

SYNTHETIC local artifacts only (tests/postgres_fixtures builds them in a temporary
directory with a stub scoring bundle, no model fitting); no cloud connection, client
data or real language model.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLEAN_PREFIXES = ("SHARED_DATA_", "MODULE2_", "MODULE4_", "DATABASE_URL", "APP_STATE_")
FORBIDDEN = ("modules.module_3", "shared.data", "shared.auth", "shinywidgets")


def clean_env(**extra):
    return {**{k: v for k, v in os.environ.items() if not k.startswith(CLEAN_PREFIXES)}, **extra}


class EntryImportTests(unittest.TestCase):
    """The entry imports neither Module 3 nor the legacy CSV loader / sign-in, and opens nothing."""

    def run_import(self, env):
        code = ("import sys, json, integrated_app\n"
                "opened = integrated_app.RUNTIME._reader is not None\n"
                "status = integrated_app.RUNTIME.status()\n"
                f"bad = sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r}))\n"
                "print(json.dumps({'opened_at_import': opened, 'ready': status['ready'], 'reason': status['reason'],"
                " 'forbidden': bad, 'modules': [s.key for s in integrated_app.MODULES]}))\n")
        with tempfile.TemporaryDirectory() as cwd:      # no default publication or CSV next to the process
            done = subprocess.run([sys.executable, "-c", code], cwd=cwd,
                                  env={**clean_env(**env), "PYTHONPATH": str(ROOT)},
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_entry_imports_modules_1_2_and_4_only(self):
        result = self.run_import({})
        self.assertEqual(result["modules"], ["module_1", "module_2", "module_4"])   # Business Performance opens first
        self.assertEqual(result["forbidden"], [])
        self.assertFalse(result["opened_at_import"])
        self.assertFalse(result["ready"])                     # no publication configured: unavailable, no fallback

    def test_unconfigured_postgres_and_unknown_backend_are_unavailable(self):
        self.assertEqual(self.run_import({"SHARED_DATA_BACKEND": "postgres"})["reason"],
                         "Shared business data unavailable (postgres: ConfigError)")
        self.assertIn("ValueError", self.run_import({"SHARED_DATA_BACKEND": "csv"})["reason"])

    def test_unreachable_postgres_reason_is_sanitized(self):
        secret = "postgresql://someone:very-secret-pw@203.0.113.9:5432/db?sslmode=verify-full"
        reason = self.run_import({"SHARED_DATA_BACKEND": "postgres", "SHARED_DATA_DATABASE_URL": secret,
                                  "SHARED_DATA_SSLROOTCERT": "/nonexistent/ca.pem"})["reason"]
        self.assertNotIn("very-secret-pw", reason)
        self.assertNotIn("203.0.113.9", reason)


class ShellTests(unittest.TestCase):
    def test_demo_identities_are_server_owned_and_labelled(self):
        from shared.shell import DEMO_ACCOUNTS
        self.assertEqual([a.principal_id for a in DEMO_ACCOUNTS], ["demo:manager", "demo:rep-a", "demo:rep-b"])
        self.assertTrue(all("(demo)" in a.name for a in DEMO_ACCOUNTS))
        self.assertEqual({a.role for a in DEMO_ACCOUNTS}, {"Regional Manager", "Field Rep"})

    def test_module_specs_must_be_distinct(self):
        from shared.shell import ModuleSpec, build_app
        spec = ModuleSpec("module_2", "x", "Module 2", lambda c: None, lambda *a: None)
        with self.assertRaises(ValueError):
            build_app([spec, spec], runtime=None)
        with self.assertRaises(ValueError):
            build_app([], runtime=None)

    def test_one_sidebar_names_every_module_and_disables_those_not_built(self):
        from shared.shell import MODULE_CATALOG, ModuleSpec, _app_ui
        seen = {}
        specs = (ModuleSpec("module_2", "Stockout Risk", "Module 2", lambda c: seen.setdefault("m2", c) and "",
                            lambda *a: None, lambda: "PAGES-OF-MODULE-2"),
                 ModuleSpec("module_4", "Commercial Copilot", "Module 4", lambda c: seen.setdefault("m4", c) and "",
                            lambda *a: None))
        contexts = {s.key: {"hosted": True, "module_label": s.label} for s in specs}
        html = str(_app_ui(specs, contexts, "t"))
        self.assertEqual([m.name for m in MODULE_CATALOG],
                         ["Business Performance", "Stockout Risk", "Negotiation Simulator", "Commercial Copilot"])
        nav = html[html.index('id="shell-nav"'):html.index("Other</p>")]
        for key, disabled in (("module_1", True), ("module_2", False), ("module_3", True), ("module_4", False)):
            tag = nav[nav.rindex("<button", 0, nav.index(f'data-module="{key}"')):nav.index(f'data-module="{key}"')]
            self.assertEqual('aria-disabled="true"' in tag, disabled, key)
            self.assertEqual("wsOpenModule" in tag, not disabled, key)
        self.assertLess(nav.index("Business Performance"), nav.index("Stockout Risk"))
        self.assertIn("PAGES-OF-MODULE-2", nav)                        # the module's own pages, under it
        self.assertNotIn("shell-bar", html)                            # no top module switcher any more
        self.assertEqual(html.count('id="shell_signout"'), 1)
        self.assertEqual(seen["m2"]["module_label"], "Stockout Risk")
        self.assertTrue(seen["m4"]["hosted"])

    def test_integrated_build_enables_business_performance_and_keeps_module_3_disabled(self):
        from shared.shell import _app_ui
        sys.modules.pop("integrated_app", None)
        import integrated_app
        contexts = {s.key: {"runtime": integrated_app.RUNTIME, "hosted": True, "module_label": s.label}
                    for s in integrated_app.MODULES}
        html = str(_app_ui(integrated_app.MODULES, contexts, "t"))
        nav = html[html.index('id="shell-nav"'):html.index("Other</p>")]
        for key, disabled in (("module_1", False), ("module_2", False), ("module_3", True), ("module_4", False)):
            tag = nav[nav.rindex("<button", 0, nav.index(f'data-module="{key}"')):nav.index(f'data-module="{key}"')]
            self.assertEqual('aria-disabled="true"' in tag, disabled, key)
        self.assertIn('id="m1_nav"', nav)                              # Module 1's pages, under it
        self.assertIn('aria-current="page"', nav[max(0, nav.index('data-module="module_1"') - 400):
                                                 nav.index('data-module="module_1"')])   # landing: Business Performance
        ids = [i for i in __import__("re").findall(r' id="([^"]+)"', html)]
        self.assertEqual(len(ids), len(set(ids)), sorted({i for i in ids if ids.count(i) > 1}))
        m1 = html[html.index('id="m1-root"'):]
        for legacy in ("sleek-card", "badge-telemetry-live", "LIVE TELEMETRY", "ambient-glow", "user_role_state"):
            self.assertNotIn(legacy, m1)

    def test_module_1_without_the_shared_runtime_reads_nothing(self):
        from modules.module_1 import module_1_server, module_1_ui
        html = str(module_1_ui({"channels": ["x"]}))                   # the legacy root app's dataset dict
        self.assertIn("runs in the integrated workspace", html)
        self.assertNotIn("m1_dates", html)
        self.assertIsNone(module_1_server(None, None, None, {"channels": ["x"]}, {}))

    def test_hosted_module_2_uses_the_app_sidebar_and_both_modules_use_the_shared_design(self):
        from modules.module_2.ui import module_2_ui
        from modules.module_4.ui import module_4_ui
        tags = module_2_ui({}), module_2_ui({"hosted": True}), module_4_ui({"module_label": "Commercial Copilot"})
        standalone, hosted, copilot = map(str, tags)
        self.assertIn('id="m2_sidebar"', standalone)
        self.assertNotIn('id="m2_sidebar"', hosted)
        self.assertNotIn('id="m2_signout"', hosted)                     # one sign-out: the shell's
        for tag in tags:
            self.assertIn("ws-kit", [d.name for d in tag.get_dependencies()])
        self.assertIn("ws-topbar", copilot)
        self.assertIn('ws-btn primary', copilot)
        for legacy in ("sleek-card", "btn-luxury-primary", "badge-telemetry-live", "📉"):
            self.assertNotIn(legacy, copilot)


class SharedSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tests"))
        from tests import postgres_fixtures as fx
        cls.sources = fx.synthetic_sources()

    def runtime(self, **env):
        from shared.runtime import SharedRuntime
        return SharedRuntime({"SHARED_DATA_ARTIFACT_DIR": str(self.sources["model"]), **env})

    def test_both_modules_read_one_reader_identity_and_scope(self):
        from modules.module_2.service import Principal
        runtime = self.runtime()
        status = runtime.status()
        self.assertTrue(status["ready"], status)
        reader = runtime.reader()
        service, reason = runtime.module2_service()
        copilot = runtime.copilot()
        self.assertIsNone(reason)
        self.assertIs(service.reader, reader)
        self.assertIs(copilot.reader, reader)
        self.assertIs(runtime.module1_pool().reader, reader)
        self.assertEqual(copilot.status()["artifact_id"], service.record["artifact_id"])
        self.assertEqual(copilot.status()["as_of"], service.record["as_of"])
        self.assertEqual(status["identity"]["model_publication_kind"], "Synthetic_demo")
        for pid in ("demo:manager", "demo:rep-a", "demo:rep-b"):
            with self.subTest(principal=pid):
                expected = reader.principal_scope(pid)["stores"]
                response = service.get_priorities(Principal(pid, True), {"view": "all"}, page_size=200)
                self.assertEqual(response.totals["scoped_stores"], expected)
                sandbox = copilot.pool.get(pid)
                self.assertEqual(sandbox.store_count, expected)
                self.assertEqual(sandbox.artifact_id, service.record["artifact_id"])
                m1 = runtime.module1_pool().get(pid)
                self.assertEqual(m1.choices()["store_count"], expected)
                self.assertEqual(m1.record["artifact_id"], service.record["artifact_id"])
        self.assertLess(reader.principal_scope("demo:rep-a")["stores"],
                        reader.principal_scope("demo:manager")["stores"])

    def test_unknown_principal_is_denied_in_both_modules(self):
        from modules.module_2.service import Principal
        runtime = self.runtime()
        service, _ = runtime.module2_service()
        self.assertEqual(service.get_priorities(Principal("demo:nobody", True)).status, "Permission_denied")
        answer = runtime.copilot().ask("How many units were sold by channel?", "demo:nobody", "sales")
        self.assertEqual((answer.status, answer.frame), ("denied", None))

    def test_split_module_sources_are_unavailable_not_mixed(self):
        runtime = self.runtime(MODULE4_SERVING_ARTIFACT_DIR=str(self.sources["base"]))
        copilot = runtime.copilot()
        self.assertIsNone(copilot.reader)
        self.assertIn("different publication", copilot.data_reason)
        other = self.runtime(MODULE2_SERVING_ROOT=str(Path(self.sources["root"]) / "no-such-root"))
        service, reason = other.module2_service()
        self.assertIsNone(service)
        self.assertIn("could not be verified", reason)

    def test_base_publication_shows_unavailable_predictions_not_zero_risk(self):
        from modules.module_2.service import Principal
        service, reason = self.runtime(SHARED_DATA_ARTIFACT_DIR=str(self.sources["base"])).module2_service()
        self.assertIsNone(reason)
        # No released predictions drives the "Predictions not released ... not zero risk" banner.
        self.assertEqual(service.record["model_outputs_status"], "Unavailable_model_not_released")
        response = service.get_priorities(Principal("demo:manager", True))
        self.assertEqual(response.rows, [])                      # never zero-risk rows
        self.assertEqual(response.status, "Permission_denied")   # this base fixture has no account scopes
        self.assertEqual(response.publication_kind, "Base_serving_no_model_outputs")

    def test_failed_open_is_sanitized_retried_and_never_falls_back(self):
        from shared.runtime import SharedRuntime, SourceUnavailable
        calls = []

        def opener(env):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("password=hunter2 host=10.0.0.1")
            from shared.business import open_reader
            return open_reader(env)

        runtime = SharedRuntime({"SHARED_DATA_ARTIFACT_DIR": str(self.sources["model"])}, opener=opener,
                                retry_seconds=0.2)
        with self.assertRaises(SourceUnavailable) as caught:
            runtime.reader()
        self.assertNotIn("hunter2", str(caught.exception))
        self.assertEqual(runtime.module2_service()[0], None)
        self.assertIsNone(runtime.copilot().reader)
        with self.assertRaises(SourceUnavailable):
            runtime.module1_pool()                              # unavailable, never another source
        self.assertEqual(len(calls), 1)                         # within the retry window: no new attempt
        time.sleep(0.25)
        self.assertIsNotNone(runtime.reader())
        self.assertIsNotNone(runtime.module2_service()[0])     # rebuilt after the source recovered
        self.assertIsNotNone(runtime.copilot().reader)
        self.assertEqual(len(calls), 2)

    def test_local_settings_are_exclusive(self):
        runtime = self.runtime(SHARED_DATA_PUBLICATION=str(Path(self.sources["root"]) / "candidate" / "published"
                                                           / "serving.json"))
        self.assertFalse(runtime.status()["ready"])


class CopilotFailureTests(unittest.TestCase):
    """Module 4: no source keeps document questions available; no model is explicit; scope changes fail closed."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tests"))
        from tests import postgres_fixtures as fx
        cls.sources = fx.synthetic_sources()

    def copilot(self, reader=None, reason=None):
        from modules.module_4.agent import Copilot
        from modules.module_4.llm import ChatModel, LLMConfig
        llm = ChatModel(LLMConfig(provider="openai", base_url="http://127.0.0.1:9", timeout_s=1))  # closed port
        return Copilot(llm=llm, reader=reader, data_reason=reason)

    def test_no_source_data_unavailable_documents_still_retrieved(self):
        bot = self.copilot(reason="Shared business data unavailable (duckdb: FileNotFoundError)")
        data = bot.ask("How many units were sold by channel last month?", "demo:rep-a", "sales")
        self.assertEqual(data.status, "unavailable")
        self.assertIn("Shared business data unavailable", data.answer)
        with patch.dict(os.environ, {"MODULE4_RAG_DENSE": "0"}):
            docs = bot.ask("What discount can I offer without manager approval?", "demo:rep-a", "sales")
        self.assertEqual(docs.route, "knowledge")
        self.assertEqual(docs.status, "unavailable")            # no model: no generated answer...
        self.assertTrue(docs.sources)                           # ...but the permitted passages are shown
        self.assertFalse(bot.status()["model_ready"])

    def test_scope_change_while_answering_discards_the_answer(self):
        from data_platform.access import ServingReader
        from modules.module_4.agent import SCOPE_CHANGED
        reader = ServingReader(self.sources["model"])
        bot = self.copilot(reader=reader)
        bot.pool.get("demo:rep-a")                              # sandbox built under the current scope
        real_scope = reader.principal_scope

        def changed(principal_id):
            scope = dict(real_scope(principal_id))
            scope["scope_fingerprint"] = "0" * 64              # as if the assignment changed mid-answer
            return scope

        class Result:
            status, answer, sql, truncated, attempts = "ok", "rows", "SELECT 1", False, []

            def __init__(self):
                import pandas as pd
                self.frame = pd.DataFrame({"x": [1]})

        def answer_then_change(question, sandbox, **kwargs):
            reader.principal_scope = changed
            return Result()
        bot.nlq.answer = answer_then_change
        out = bot.ask("How many units were sold by channel?", "demo:rep-a", "sales")
        self.assertEqual((out.status, out.answer, out.frame, out.sql), ("denied", SCOPE_CHANGED, None, None))
        reader.principal_scope = real_scope

        def answer_then_revoke(question, sandbox, **kwargs):
            def revoked(principal_id):
                raise PermissionError("No configured account scope")
            reader.principal_scope = revoked
            return Result()
        bot.nlq.answer = answer_then_revoke
        out = bot.ask("How many units were sold by channel?", "demo:rep-a", "sales")
        self.assertEqual((out.status, out.frame), ("denied", None))


if __name__ == "__main__":
    unittest.main()
