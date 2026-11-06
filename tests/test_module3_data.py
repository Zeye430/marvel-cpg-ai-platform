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


if __name__ == "__main__":
    unittest.main()
