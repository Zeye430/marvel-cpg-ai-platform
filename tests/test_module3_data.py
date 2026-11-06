"""Module 3 inputs from the shared publication, scoped per principal.

SYNTHETIC local artifacts only (tests/postgres_fixtures: the generated serving build with demo scopes),
read through the same per-principal scoped copy as Module 1. No client data, cloud access or model.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Module3DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tests"))
        from tests import postgres_fixtures as fx
        from shared.runtime import SharedRuntime
        cls.sources = fx.synthetic_sources()
        cls.runtime = SharedRuntime({"SHARED_DATA_ARTIFACT_DIR": str(cls.sources["model"])})

    def data(self, pid):
        from modules.module_3.data import Module3Data
        return Module3Data(self.runtime.module1_pool().get(pid))

    def test_accounts_follow_the_principal_scope(self):
        manager, rep = self.data("demo:manager"), self.data("demo:rep-a")
        all_stores, rep_stores = set(manager.accounts()["store_id"]), set(rep.accounts()["store_id"])
        self.assertTrue(rep_stores)
        self.assertLess(rep_stores, all_stores)
        self.assertLessEqual(len(rep_stores), rep.scope["stores"])
        outside = sorted(all_stores - rep_stores)[0]
        sku = manager.products_for(outside)["sku_id"].iloc[0]
        self.assertIsNotNone(manager.context(outside, sku))
        self.assertIsNone(rep.context(outside, sku))                 # another rep's store: no rows, not a denial leak
        self.assertTrue(rep.products_for(outside).empty)

    def test_unassigned_principal_is_refused(self):
        with self.assertRaises(PermissionError):
            self.data("demo:nobody")

    def test_context_feeds_a_checkable_deal(self):
        from modules.module_3 import calc
        from modules.module_3.server import deal_from
        rep = self.data("demo:rep-a")
        store = rep.accounts()["store_id"].iloc[0]
        sku = rep.products_for(store)["sku_id"].iloc[0]
        row = rep.context(store, sku)
        self.assertEqual(row["policy_status"], "Requires review")
        deal = deal_from(row)
        self.assertIsNotNone(deal.cap)
        self.assertIsNotNone(deal.list_price)
        verdicts = {calc.scenario(deal, a)["verdict"] for a in (0, 50)}
        self.assertNotIn(calc.NOT_ASSESSED, verdicts)

    def test_price_change_inputs(self):
        from modules.module_3.server import weighted_unit
        rep = self.data("demo:rep-a")
        category = rep.categories()[0]
        base = rep.baseline("category", category)
        self.assertFalse(base.empty)
        self.assertTrue((base["units"] >= 0).all())
        unit = weighted_unit(base)
        self.assertIsNotNone(unit)
        self.assertGreater(unit["unit_net"], unit["unit_cost"])
        sku = base["sku_id"].iloc[0]
        self.assertEqual(list(rep.baseline("sku", sku)["sku_id"]), [sku])
        e = rep.elasticity(category)
        self.assertEqual(e["category"], category)
        self.assertGreaterEqual(e["n"], 0)
        if e["estimate"] is not None:                                 # enough priced in-stock days
            self.assertLessEqual(e["low"], e["estimate"])
            self.assertLessEqual(e["estimate"], e["high"])
        with self.assertRaises(ValueError):
            rep.baseline("region", "x")

    def test_price_change_reads_retail_accounts_only(self):
        manager = self.data("demo:manager")
        category = manager.categories()[0]
        sql = """SELECT sum(units_sold_observed) AS units FROM store_sku_daily WHERE category = $category {}
                   AND inventory_observed > 0 AND units_sold_observed IS NOT NULL
                   AND date > (SELECT max(date) FROM store_sku_daily) - INTERVAL 91 DAY"""
        everyone = manager.q(sql.format(""), {"category": category})["units"].iloc[0]
        retail = manager.q(sql.format("AND channel <> 'Distributor'"), {"category": category})["units"].iloc[0]
        self.assertGreater(everyone, retail)
        self.assertEqual(manager.baseline("category", category)["units"].sum(), retail)


class FakeItem:
    """A scoped copy whose access check can be revoked at a chosen check (counts every check)."""

    def __init__(self, revoke_at=None):
        import pandas as pd
        self.pd, self.checks, self.queries, self.revoke_at = pd, 0, 0, revoke_at
        self.record, self.scope, self.manager = {"as_of": "2026-06-30"}, {"stores": 1}, False

    def check(self):
        self.checks += 1
        if self.revoke_at is not None and self.checks >= self.revoke_at:
            raise PermissionError("scope revoked")
        return self.scope

    def query(self, sql, params=None):
        self.queries += 1
        return self.pd.DataFrame({"category": ["Laundry"], "n": [0], "slope": [None], "sxx": [None], "syy": [None],
                                  "sxy": [None], "lo": [None], "hi": [None]})


class AccessCheckTests(unittest.TestCase):
    """Codex TASK-0017 R-03: every read is between two access checks, cached results are re-checked, and a
    revocation before, during or after a read returns nothing."""

    def data(self, **kwargs):
        from modules.module_3.data import Module3Data
        item = FakeItem(**kwargs)
        return Module3Data(item), item

    def test_every_read_is_checked_before_and_after(self):
        data, item = self.data()
        data.categories()
        self.assertEqual((item.checks, item.queries), (2, 1))

    def test_revocation_before_or_during_a_read_returns_nothing(self):
        for revoke_at, queries in ((1, 0), (2, 1)):          # before the query; after it (in flight)
            with self.subTest(revoke_at=revoke_at):
                data, item = self.data(revoke_at=revoke_at)
                with self.assertRaises(PermissionError):
                    data.categories()
                self.assertEqual(item.queries, queries)

    def test_cached_results_are_rechecked_and_not_served_after_revocation(self):
        data, item = self.data()
        first = data.elasticity("Laundry")
        reads = item.queries
        self.assertEqual(data.elasticity("Laundry"), first)  # cached: no new query, but a fresh check
        self.assertEqual(item.queries, reads)
        item.revoke_at = item.checks + 1
        with self.assertRaises(PermissionError):
            data.elasticity("Laundry")


class IncompleteBaselineTests(unittest.TestCase):
    """Codex TASK-0017 R-04: unknown price, cost or discount is excluded and counted, never filled."""

    def frame(self, rows):
        import pandas as pd
        columns = ["sku_id", "units", "list_price", "unit_cost", "trade_discount", "stores", "first_day", "last_day"]
        return pd.DataFrame([dict(zip(columns, r)) for r in rows])

    def test_unequal_volumes_are_weighted(self):
        from modules.module_3.server import weighted_unit
        unit = weighted_unit(self.frame([("A", 300, 4.0, 2.0, 10.0, 5, "2026-04-01", "2026-06-30"),
                                         ("B", 100, 8.0, 3.0, 20.0, 4, "2026-04-01", "2026-06-30")]))
        self.assertAlmostEqual(unit["unit_net"], (300 * 4.0 * 0.9 + 100 * 8.0 * 0.8) / 400)
        self.assertAlmostEqual(unit["unit_cost"], (300 * 2.0 + 100 * 3.0) / 400)
        self.assertAlmostEqual(unit["trade_discount"], (300 * 10 + 100 * 20) / 400)
        self.assertEqual((unit["excluded_skus"], unit["excluded_units"]), (0, 0.0))

    def test_partly_missing_inputs_are_excluded_and_disclosed(self):
        from modules.module_3.server import weighted_unit
        nan = float("nan")
        rows = [("A", 300, 4.0, 2.0, 10.0, 5, "2026-04-01", "2026-06-30"),
                ("B", 100, nan, 3.0, 20.0, 4, "2026-04-01", "2026-06-30"),       # no list price
                ("C", 50, 6.0, nan, 15.0, 4, "2026-04-01", "2026-06-30"),        # no cost
                ("D", 25, 6.0, 2.5, None, 4, "2026-04-01", "2026-06-30")]        # no recent discount
        unit = weighted_unit(self.frame(rows))
        self.assertEqual((unit["units"], unit["skus"], unit["excluded_skus"], unit["excluded_units"]),
                         (300.0, 1, 3, 175.0))
        self.assertAlmostEqual(unit["unit_net"], 4.0 * 0.9)              # only the complete product
        self.assertAlmostEqual(unit["trade_discount"], 10.0)             # the missing discount is not 0

    def test_wholly_missing_inputs_give_no_baseline(self):
        from modules.module_3.server import weighted_unit
        nan = float("nan")
        self.assertIsNone(weighted_unit(self.frame([("A", 300, nan, 2.0, 10.0, 5, "2026-04-01", "2026-06-30"),
                                                    ("B", 100, 8.0, 3.0, None, 4, "2026-04-01", "2026-06-30")])))
        self.assertIsNone(weighted_unit(self.frame([("A", 0, 4.0, 2.0, 10.0, 5, "2026-04-01", "2026-06-30")])))


if __name__ == "__main__":
    unittest.main()
