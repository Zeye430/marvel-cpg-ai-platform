"""Integrated Modules 1, 2 and 4 on the marked, disposable synthetic local PostgreSQL only.

Skipped unless the test database is configured (see compose.postgres-test.yml); fails instead of
skipping when SHARED_POSTGRES_REQUIRE_TEST_DB=1. No cloud connection, client data or real model.
"""

import csv
import io
import unittest

from tests import postgres_fixtures as fx
from tests.test_postgres_integration import PgCase


def chart_payload(markup):
    """A rendered Module 1 chart with floats at 9 significant digits (sum order differs by backend)."""
    import json
    import re

    def rounded(value):
        if isinstance(value, float):
            return float(f"{value:.9g}")
        if isinstance(value, list):
            return [rounded(v) for v in value]
        if isinstance(value, dict):
            return {k: rounded(v) for k, v in value.items()}
        return value
    text = str(markup)
    return re.sub(r"var f=\{.*?\};var el=", "", text, flags=re.S), \
        [rounded(json.loads(m)) for m in re.findall(r"var f=(\{.*?\});var el=", text, flags=re.S)]


def setUpModule():
    fx.require_database()


class IntegratedPgTests(PgCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.release = cls("runTest").publish("model")

    def setUp(self):
        self.demo_scopes()

    def runtime(self):
        from shared.runtime import SharedRuntime
        runtime = SharedRuntime({"SHARED_DATA_BACKEND": "postgres", "SHARED_DATA_DATABASE_URL": self.db.app_url,
                                 "SHARED_DATA_LOCAL_TEST": "1"})
        self.addCleanup(lambda: runtime._reader is not None and runtime._reader.close())
        return runtime

    def without(self, principal_id, store_id=None):
        return [r for r in self.sources["scope"]["assignments"]
                if not (r["principal_id"] == principal_id and (store_id is None or r["store_id"] == store_id))]

    def test_both_modules_share_the_release_and_scope_and_match_local(self):
        from modules.module_2.service import Principal
        runtime = self.runtime()
        status = runtime.status()
        self.assertTrue(status["ready"], status)
        self.assertEqual((status["identity"]["backend"], status["identity"]["release_id"]),
                         ("postgres", self.release["release_id"]))
        reader = runtime.reader()
        service, reason = runtime.module2_service()
        copilot = runtime.copilot()
        self.assertIsNone(reason)
        self.assertIs(service.reader, reader)
        self.assertIs(copilot.reader, reader)
        self.assertNotIn(self.db.passwords[self.db.app_role], repr(status))
        from shared.runtime import SharedRuntime
        local = SharedRuntime({"SHARED_DATA_ARTIFACT_DIR": str(self.sources["model"])}).copilot().pool
        for pid in ("demo:manager", "demo:rep-a", "demo:partial-manager"):
            with self.subTest(principal=pid):
                stores = reader.principal_scope(pid)["stores"]
                response = service.get_priorities(Principal(pid, True), {"view": "all"}, page_size=200)
                self.assertEqual(response.totals["scoped_stores"], stores)
                pg_box, local_box = copilot.pool.get(pid), local.get(pid)
                self.assertEqual(pg_box.store_count, stores)
                # Every curated table, including predictions, stored and Module 2 business ranking.
                self.assertEqual(pg_box.tables, local_box.tables)
                self.assertTrue({"sku_risk", "store_priority", "business_priority"} <= set(pg_box.tables))
                self.assertEqual(pg_box.model, local_box.model)
                for table in pg_box.tables:
                    query = f"SELECT * FROM {table} ORDER BY ALL"
                    self.assertEqual(pg_box.values(f"SELECT count(*) FROM {table}"),
                                     local_box.values(f"SELECT count(*) FROM {table}"), table)
                    with pg_box.lock, local_box.lock:              # raw rows: NULL stays None (no NaN)
                        self.assertEqual(pg_box.con.execute(query).fetchall(),
                                         local_box.con.execute(query).fetchall(), table)

    def test_module1_kpis_and_charts_match_local_and_postgres(self):
        from datetime import date
        from data_platform.access import ServingReader
        from modules.module_1 import server as m1
        from modules.module_1.data import Filters, Module1Pool
        runtime = self.runtime()
        pg_pool = runtime.module1_pool()
        self.assertIs(pg_pool.reader, runtime.reader())               # the shared reader, not a second source
        local_pool = Module1Pool(ServingReader(self.sources["model"]))
        for principal in ("demo:manager", "demo:rep-b"):
            local, cloud = local_pool.get(principal), pg_pool.get(principal)
            self.addCleanup(local.close)
            self.addCleanup(cloud.close)
            self.assertEqual(local.choices(), cloud.choices())
            info = local.choices()
            colors = {"channel": m1.palette(info["channels"]), "region": m1.palette(info["regions"]),
                      "tier": m1.palette(info["tiers"]), "category": m1.palette(info["categories"])}
            skus = tuple(info["products"])
            cases = [Filters(tuple(info["channels"]), tuple(info["regions"]), skus, True,
                             date(2026, 4, 6), date(2026, 6, 28)),
                     Filters(tuple(info["channels"][:1]), tuple(info["regions"]), skus[:2], False,
                             date(2026, 5, 4), date(2026, 6, 28))]
            for f in cases:
                with self.subTest(principal=principal, filters=f):
                    self.assertEqual(local.kpis(f), cloud.kpis(f))
                    for name, call in (("latest", lambda d: d.latest_inventory(f)),
                                       ("episodes", lambda d: d.oos_episodes(f, "channel")),
                                       ("trade", lambda d: d.trade_terms(f)),
                                       ("flow", lambda d: d.flow_by_channel(f)),
                                       ("score", lambda d: d.scorecard(f, "region")),
                                       ("states", lambda d: d.state_execution(f))):
                        a, b = call(local), call(cloud)
                        keys = list(a.columns)  # total order: several rows can share leading keys
                        self.assertEqual(a.sort_values(keys).reset_index(drop=True).to_dict("records"),
                                         b.sort_values(keys).reset_index(drop=True).to_dict("records"), name)
                    for output_id in m1.CHARTS:                     # the rendered charts match too
                        self.assertEqual(chart_payload(m1.render_chart(output_id, local, f, None, colors)),
                                         chart_payload(m1.render_chart(output_id, cloud, f, None, colors)), output_id)

    def test_module1_postgres_cache_fails_closed_after_scope_revocation(self):
        pool = self.runtime().module1_pool()
        data = pool.get("demo:rep-a")
        self.addCleanup(data.close)
        self.scopes(self.without("demo:rep-a"), "revoked-m1")
        with self.assertRaises(PermissionError):
            data.check()
        self.assertTrue(data.closed)
        with self.assertRaises(PermissionError):
            pool.get("demo:rep-a")

    def test_module2_scope_change_during_load_fails_closed(self):
        from modules.module_2.service import Module2Service, Principal
        from data_platform.postgres_reader import PostgresServingReader
        reader = PostgresServingReader(self.db.app)
        self.addCleanup(reader.close)
        service = Module2Service(reader=reader)
        rep = Principal("demo:rep-a", True)
        removed = sorted(r["store_id"] for r in self.sources["scope"]["assignments"]
                         if r["principal_id"] == "demo:rep-a")[0]
        real, fired = reader.read, {"n": 0}

        def intercept(name, *args, **kwargs):
            out = real(name, *args, **kwargs)
            if name == "negotiation_context" and not fired["n"]:
                fired["n"] = 1
                self.scopes(self.without("demo:rep-a", removed), "rep-a-narrowed")
            return out
        reader.read = intercept
        response = service.get_priorities(rep, {"view": "all"}, page_size=200)
        self.assertEqual(fired["n"], 1)
        self.assertEqual((response.status, response.rows), ("Unavailable", []))
        self.assertFalse([k for k in service._cache if "demo:rep-a" in k])
        again = service.get_priorities(rep, {"view": "all"}, page_size=200)
        self.assertEqual(again.status, "Ok")
        self.assertNotIn(removed, {r["store_id"] for r in again.rows})
        exported = list(csv.DictReader(io.StringIO(service.export_visit_plan(rep, {"view": "all"}))))
        self.assertNotIn(removed, {r["store_id"] for r in exported})
        self.scopes(self.without("demo:rep-a"), "rep-a-revoked")
        self.assertEqual(service.get_priorities(rep).status, "Permission_denied")

    def test_module4_revocation_while_answering_is_denied(self):
        from modules.module_4.agent import SCOPE_CHANGED
        runtime = self.runtime()
        copilot = runtime.copilot()
        copilot.pool.get("demo:rep-a")

        class Result:
            status, answer, sql, truncated, attempts, frame = "ok", "rows", "SELECT 1", False, [], None

        def answer_then_revoke(question, sandbox, **kwargs):
            self.scopes(self.without("demo:rep-a"), "rep-a-revoked-m4")
            return Result()
        copilot.nlq.answer = answer_then_revoke
        out = copilot.ask("How many units were sold by channel?", "demo:rep-a", "sales")
        self.assertEqual((out.status, out.answer, out.sql), ("denied", SCOPE_CHANGED, None))
        again = copilot.ask("How many units were sold by channel?", "demo:rep-a", "sales")
        self.assertEqual(again.status, "denied")                  # no sandbox for a revoked scope

    def test_prediction_answers_match_local_and_revocation_mid_answer_is_denied(self):
        from modules.module_4.agent import SCOPE_CHANGED
        from shared.runtime import SharedRuntime
        from tests.test_module4_predictions import ALERTS_SQL, BUSINESS_SQL, STORED_SQL, FakeLLM, fenced

        def ask(copilot, sql, question, pid):
            copilot.llm = copilot.nlq.llm = FakeLLM([fenced(sql), "Summary."] * 2)
            return copilot.ask(question, pid, "manager" if pid == "demo:manager" else "sales")
        pg = self.runtime().copilot()
        local = SharedRuntime({"SHARED_DATA_ARTIFACT_DIR": str(self.sources["model"])}).copilot()
        for pid in ("demo:manager", "demo:partial-manager"):
            for sql, question in ((ALERTS_SQL, "Which SKUs in my stores have a stockout alert?"),
                                  (STORED_SQL, "Show the visit priority ranking of my stores"),
                                  (BUSINESS_SQL, "What is the business priority ranking in Module 2?")):
                with self.subTest(principal=pid, sql=sql):
                    a, b = ask(pg, sql, question, pid), ask(local, sql, question, pid)
                    self.assertEqual(a.status, "ok")
                    self.assertEqual((a.status, a.notes), (b.status, b.notes))
                    self.assertEqual(a.frame.astype(object).where(a.frame.notna(), None).values.tolist(),
                                     b.frame.astype(object).where(b.frame.notna(), None).values.tolist())
                    self.assertEqual({k: v for k, v in a.provenance["model_outputs"].items()},
                                     b.provenance["model_outputs"])
                    self.assertEqual(a.provenance["backend"], "postgres")
        pg.pool.get("demo:rep-a")
        answer = pg.nlq.answer

        def answer_then_revoke(question, sandbox, **kwargs):
            result = answer(question, sandbox)
            self.scopes(self.without("demo:rep-a"), "rep-a-revoked-predictions")
            return result
        pg.llm = pg.nlq.llm = FakeLLM([fenced(STORED_SQL), "Summary."])
        pg.nlq.answer = answer_then_revoke
        out = pg.ask("Show the visit priority ranking of my stores", "demo:rep-a", "sales")
        self.assertEqual((out.status, out.answer, out.frame, out.notes), ("denied", SCOPE_CHANGED, None, []))


if __name__ == "__main__":
    unittest.main()
