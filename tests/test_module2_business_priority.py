"""Proposed business-priority projection on hand-built SYNTHETIC data (no model, no client data).

Stores: A current MSL stockout of a low-value SKU; B three low-value prevention alerts;
C one high-value prevention alert; D no reported trade price; E unranked. Every value is
a fixture input; the service never recomputes stored ranks, scores or thresholds.
"""

import csv
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import duckdb

from modules.module_2 import business_priority as bp
from modules.module_2.contracts import Module2Error
from modules.module_2.service import Module2Service, Principal

try:
    from tests.test_module2_business import CONTRACT, columns, reader
except ImportError:                                    # discovered with -s tests
    from test_module2_business import CONTRACT, columns, reader

D = date(2026, 6, 30)
RUN = "score-2026-06-30-synthetic"
TABLES = {"stores": "dim_stores", "products": "dim_products", "visit_priority": "mart_visit_priority",
          "oos_predictions": "predictions_oos", "store_sku_daily": "mart_store_sku_daily",
          "negotiation_context": "mart_negotiation_context"}
MSL = {"P1"}


def drivers(oos, inventory, velocity, scheduled, msl, receipts=0.0):
    values = {"oos_observed": oos, "inventory_observed": inventory, "in_stock_velocity_28d": velocity,
              "scheduled_units_next7d": scheduled, "is_msl": msl, "receipt_units_7d": receipts,
              "days_until_next_scheduled_receipt": 2 if scheduled else None}
    return {"prediction_start": "2026-07-01", "prediction_end": "2026-07-07", "threshold_route": "segment",
            "evidence": [{"feature": k, "label": k, "value": v, "data_status": "Unknown" if v is None else "Observed",
                          "as_of": str(D)} for k, v in values.items()], "limitations": []}


# store -> [(sku, probability, alert, oos, inventory, velocity, scheduled)]
SKUS = {
    "A": [("P1", 0.90, True, True, 0.0, 1.0, 0.0), ("P2", 0.10, False, False, 50.0, 1.0, 0.0)],
    "B": [("P2", 0.80, True, False, 5.0, 1.0, 0.0), ("P3", 0.80, True, False, 5.0, 1.0, 0.0),
          ("P4", 0.80, True, False, 5.0, 1.0, 0.0)],
    "C": [("P5", 0.70, True, False, 10.0, 20.0, 0.0)],
    "D": [("P2", 0.60, True, False, 1.0, 2.0, 0.0)],
}
STORED = ["A", "B", "C", "D"]                     # stored visit order (fixture input)
PRICES = {"P1": 2.0, "P2": 2.0, "P3": 2.0, "P4": 2.0, "P5": 10.0}
AUDIT = {("A", "P1"): (D - timedelta(days=3), False), ("C", "P5"): (D - timedelta(days=40), False),
         ("B", "P2"): (D - timedelta(days=1), True)}


def insert(con, view, rows):
    names = [n for n, _ in columns(view)]
    for row in rows:
        assert not set(row) - set(names), set(row) - set(names)
        con.execute(f"INSERT INTO gold.{TABLES[view]} VALUES ({','.join('?' * len(names))})",
                    [row.get(n) for n in names])


def build(path):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving")
        for view, table in TABLES.items():
            con.execute(f"CREATE TABLE gold.{table} ({', '.join(f'{n} {t}' for n, t in columns(view))})")
            con.execute(f"CREATE VIEW serving.vw_{view} AS SELECT * FROM gold.{table}")
        con.execute("CREATE TABLE gold.account_scope (principal_id VARCHAR, store_id VARCHAR, role VARCHAR, "
                    "scope_version VARCHAR)")
        stores = ["A", "B", "C", "D", "E"]
        con.executemany("INSERT INTO gold.account_scope VALUES (?,?,?,'v1')",
                        [("mgr", s, "manager") for s in stores] + [("rep", s, "sales") for s in ("A", "C", "E")])
        insert(con, "stores", [{"store_id": s, "store_name": f"SYNTHETIC {s}", "channel": "Modern Trade",
                                "region": "West" if s in "AB" else "East", "state": "CA", "segment_tier": "Tier 1",
                                "city": "X"} for s in stores])
        insert(con, "products", [{"sku_id": f"P{i}", "product_name": f"SYNTHETIC P{i}", "category": "Laundry",
                                  "brand": "B", "is_msl": f"P{i}" in MSL, "unit_cost": 1,
                                  "list_price": PRICES[f"P{i}"], "consumer_price": 3} for i in range(1, 6)])
        for rank, s in enumerate(STORED, start=1):
            rows = SKUS[s]
            msl_gap = sum(1 for k, _, _, oos, *_ in rows if k in MSL and oos)
            alerts = sum(1 for r in rows if r[2])
            reason = {"bucket": 1 if msl_gap else 2, "bucket_label": "x", "msl_gap_count": msl_gap,
                      "msl_unknown_count": 0, "alert_count": alerts, "inventory_unknown_count": 0,
                      "sku_count": len(rows), "reference_sales_proxy": {"status": "Complete", "known_sum": 1.0},
                      "impact_fallback_to_risk": False, "top_skus": []}
            insert(con, "visit_priority", [{"scoring_run_id": RUN, "scoring_date": D, "store_id": s,
                                            "priority_rank": rank, "risk_score": max(r[1] for r in rows),
                                            "msl_gap_count": msl_gap, "ranking_policy_version": "visit_priority.v1",
                                            "reason_json": json.dumps(reason)}])
            for sku, p, alert, oos, inv, vel, sched in rows:
                insert(con, "oos_predictions", [{
                    "scoring_run_id": RUN, "scoring_date": D, "store_id": s, "sku_id": sku, "probability": p,
                    "threshold": 0.5, "is_predicted_oos": alert,
                    "drivers_json": json.dumps(drivers(oos, inv, vel, sched, sku in MSL)),
                    "model_version": "m", "feature_version": "f", "input_status": "Observed_context"}])
                audit = AUDIT.get((s, sku))
                insert(con, "negotiation_context", [{
                    "as_of_date": D, "store_id": s, "sku_id": sku, "is_msl": sku in MSL,
                    "reference_list_price": PRICES[sku],
                    "reported_net_trade_price_28d": None if s == "D" else 0.95 * PRICES[sku],
                    "last_invoice_date": D - timedelta(days=1), "last_audit_date": audit[0] if audit else None,
                    "planogram_compliance_latest": audit[1] if audit else None}])


class PolicyUnitTests(unittest.TestCase):
    def prediction(self, **drv):
        base = {"oos": False, "inventory": 10.0, "velocity": 3.0, "scheduled": 5.0, "msl": False}
        base.update(drv)
        return {"sku_id": "P1", "probability": 0.7, "is_predicted_oos": True,
                "drivers_json": json.dumps(drivers(base["oos"], base["inventory"], base["velocity"],
                                                   base["scheduled"], base["msl"], receipts=99.0))}

    def context(self, **over):
        row = {"as_of_date": D, "reference_list_price": 4.0, "reported_net_trade_price_28d": 3.8,
               "last_invoice_date": D - timedelta(days=2)}
        row.update(over)
        return row

    def test_scenario_units_and_value_without_double_counting_receipts(self):
        est = bp.sku_estimate(self.prediction(), self.context(), D)
        self.assertEqual(est["units_short"], 7 * 3.0 - 10.0 - 5.0)      # receipts_7d (99) are not added
        self.assertAlmostEqual(est["sales_at_risk"], 6.0 * 3.8)
        covered = bp.sku_estimate(self.prediction(inventory=100.0), self.context(), D)
        self.assertEqual(covered["sales_at_risk"], 0.0)                  # known inputs, no shortfall

    def test_arrival_timing_and_zero_shortfall_without_price(self):
        # Out of stock now, 2 units/day, 100 units arriving on window day 3: days 1-2 are uncovered.
        self.assertEqual(bp.shortfall_units(0.0, 2.0, 100.0, 3), 4.0)
        self.assertEqual(bp.shortfall_units(0.0, 2.0, 100.0, 1), 0.0)            # arrives on the first day
        self.assertEqual(bp.shortfall_units(5.0, 2.0, 0.0, None), 9.0)           # nothing scheduled: 14 - 5
        self.assertEqual(bp.shortfall_units(0.0, 2.0, 100.0, 9), 14.0)           # outside the window: not counted
        self.assertIsNone(bp.shortfall_units(0.0, 2.0, 100.0, None))             # scheduled, date unknown
        covered = bp.sku_estimate(self.prediction(inventory=100.0), self.context(reported_net_trade_price_28d=None), D)
        self.assertEqual(covered["sales_at_risk"], 0.0)                          # 0 units x any price
        self.assertTrue(covered["estimable"])

    def test_every_missing_input_is_unavailable_never_zero(self):
        cases = {"inventory": "No current inventory report", "velocity": "No prior in-stock sales rate",
                 "scheduled": "Scheduled receipts unknown"}
        for field, reason in cases.items():
            est = bp.sku_estimate(self.prediction(**{field: None}), self.context(), D)
            self.assertIsNone(est["sales_at_risk"], field)
            self.assertIn(reason, est["reasons"])
        no_price = bp.sku_estimate(self.prediction(), self.context(reported_net_trade_price_28d=None), D)
        self.assertIsNone(no_price["sales_at_risk"])
        self.assertIsNotNone(no_price["units_short"])                    # units known, value not
        self.assertIsNone(bp.sku_estimate(self.prediction(), None, D)["sales_at_risk"])

    def test_case_unit_price_and_future_or_misdated_context_are_rejected(self):
        case = bp.sku_estimate(self.prediction(), self.context(reported_net_trade_price_28d=45.6), D)  # 12-pack
        self.assertIsNone(case["sales_at_risk"])
        self.assertTrue(any("unit check" in r for r in case["reasons"]))
        future = bp.sku_estimate(self.prediction(), self.context(last_invoice_date=D + timedelta(days=1)), D)
        self.assertIsNone(future["sales_at_risk"])
        other_day = bp.sku_estimate(self.prediction(), self.context(as_of_date=D - timedelta(days=1)), D)
        self.assertIsNone(other_day["sales_at_risk"])

    def test_audit_freshness_boundary_and_future_audit(self):
        def state(days):
            return bp.audit_state({"last_audit_date": D - timedelta(days=days), "planogram_compliance_latest": False}, D)
        self.assertEqual(state(0)["status"], "Fresh")                    # same-day audit is known at end of day
        self.assertEqual(state(bp.AUDIT_MAX_AGE_DAYS)["status"], "Fresh")
        self.assertEqual(state(bp.AUDIT_MAX_AGE_DAYS + 1)["status"], "Expired")
        self.assertIsNone(state(bp.AUDIT_MAX_AGE_DAYS + 1)["compliant"])
        self.assertEqual(state(-1)["status"], "Future audit excluded")

    def test_duplicate_predictions_are_not_counted(self):
        p = self.prediction()
        comp = bp.store_components([p, dict(p)], {"P1": self.context()}, D, 0, 1)
        self.assertEqual(comp["sales_at_risk"]["status"], "Unavailable")
        self.assertIn("Duplicate prediction rows; not counted", comp["skus"][0]["reasons"])

    def test_components_msl_shelf_and_urgency(self):
        msl_oos = self.prediction(oos=True, inventory=0.0, msl=True)
        comp = bp.store_components([msl_oos], {"P1": self.context(last_audit_date=D, planogram_compliance_latest=False)},
                                   D, 1, 0)
        self.assertEqual(comp["msl_risk"], 100.0)
        self.assertEqual(comp["shelf_risk"], 100.0)
        self.assertEqual(comp["urgency"], 3)
        no_audit = bp.store_components([self.prediction()], {"P1": self.context()}, D, 0, 1)
        self.assertIsNone(no_audit["shelf_risk"])                        # no audit is unavailable, not 0
        self.assertEqual(no_audit["msl_risk"], 0.0)                      # no MSL SKU listed: no exposure
        self.assertEqual(no_audit["urgency"], 2)

    def test_score_requires_impact_and_rescales_missing_secondary(self):
        self.assertEqual(bp.priority_score(None, 50, 50), (None, []))
        score, missing = bp.priority_score(1.0, None, 0.0)
        self.assertEqual(missing, ["shelf"])
        self.assertAlmostEqual(score, 100 * 0.8 / 0.9)
        pct = bp.impact_percentiles({"a": 0.0, "b": 10.0, "c": 30.0, "d": None, "e": 10.0})
        self.assertEqual((pct["a"], pct["c"], pct["d"]), (0.0, 1.0, None))
        self.assertEqual(pct["b"], pct["e"])

    def test_sort_both_directions_missing_last_and_ties_by_rank(self):
        rows = [{"store_id": s, "overall_rank": r, "priority_score": v, "sales_at_risk_usd": v, "shelf_risk": v,
                 "msl_risk": v, "urgency": v} for s, r, v in (("a", 1, 5.0), ("b", 2, None), ("c", 3, 5.0),
                                                                ("d", 4, 9.0))]
        for key in ("sales_at_risk", "shelf_risk", "msl_risk", "urgency", "score"):
            self.assertEqual([r["store_id"] for r in bp.sort_rows(rows, key, True)], ["d", "a", "c", "b"], key)
            self.assertEqual([r["store_id"] for r in bp.sort_rows(rows, key, False)], ["a", "c", "d", "b"], key)
        self.assertEqual([r["store_id"] for r in bp.sort_rows(rows, "rank", True)], ["d", "c", "b", "a"])
        with self.assertRaises(ValueError):
            bp.sort_rows(rows, "component_rank")


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic.duckdb"
        build(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        r = reader(self.path)
        r.record = {**r.record, "rows": {"stores": 5}}
        self.service = Module2Service(reader=r)
        self.mgr, self.rep = Principal("mgr", True), Principal("rep", True)

    def rows(self, principal, sort=None, **filters):
        return self.service.get_priorities(principal, {"view": "all", **filters}, page_size=200, sort=sort)

    def test_high_exposure_outranks_more_low_value_alerts(self):
        rows = {r["store_id"]: r for r in self.rows(self.mgr).rows}
        self.assertEqual(rows["B"]["prevention_alert_count"], 3)
        self.assertEqual(rows["C"]["prevention_alert_count"], 1)
        self.assertAlmostEqual(rows["C"]["sales_at_risk_usd"], (7 * 20 - 10) * 9.5)
        self.assertAlmostEqual(rows["B"]["sales_at_risk_usd"], 3 * (7 - 5) * 1.9)
        self.assertLess(rows["C"]["overall_rank"], rows["B"]["overall_rank"])
        self.assertLess(rows["B"]["display_rank"], rows["C"]["display_rank"])    # stored v1 order unchanged
        self.assertEqual([r["store_id"] for r in self.rows(self.mgr).rows][:2], ["C", "A"])

    def test_missing_price_and_unranked_are_unavailable_and_last(self):
        response = self.rows(self.mgr)
        rows = {r["store_id"]: r for r in response.rows}
        self.assertIsNone(rows["D"]["sales_at_risk_usd"])
        self.assertEqual(rows["D"]["sales_at_risk_status"], "Unavailable")
        self.assertIsNone(rows["D"]["priority_score"])
        self.assertIsNone(rows["E"]["priority_score"])
        self.assertEqual([r["store_id"] for r in response.rows][-2:], ["D", "E"])
        self.assertEqual(rows["E"]["urgency_label"], "Unavailable")
        self.assertEqual(response.totals["stats"]["sales_at_risk"]["status"], "Lower bound")
        self.assertEqual(response.totals["stats"]["sales_at_risk"]["incomplete_stores"], 2)
        self.assertIsNone(rows["C"]["shelf_risk"])                    # only audit is 40 days old
        self.assertEqual(rows["A"]["shelf_risk"], 100.0)

    def test_sorting_whole_set_across_pages_never_reranks(self):
        ranks = {r["store_id"]: r["overall_rank"] for r in self.rows(self.mgr).rows}
        for key in ("sales_at_risk", "shelf_risk", "msl_risk", "urgency", "score"):
            for direction in ("asc", "desc"):
                ids, cursor = [], None
                while True:
                    page = self.service.get_priorities(self.mgr, {"view": "all"}, cursor=cursor, page_size=2,
                                                       sort=(key, direction))
                    ids += [(r["store_id"], r["overall_rank"]) for r in page.rows]
                    cursor = page.next_cursor
                    if not cursor:
                        break
                full = [(r["store_id"], r["overall_rank"]) for r in self.rows(self.mgr, sort=(key, direction)).rows]
                self.assertEqual(ids, full, (key, direction))
                self.assertTrue(all(ranks[s] == rank for s, rank in ids))
        by_sales = [r["store_id"] for r in self.rows(self.mgr, sort=("sales_at_risk", "asc")).rows]
        self.assertEqual(by_sales[-2:], ["D", "E"])                   # unavailable last in ascending order too
        with self.assertRaises(Module2Error):
            self.rows(self.mgr, sort=("score", "up"))

    def test_partial_scope_scores_within_own_stores_only(self):
        rep = {r["store_id"]: r for r in self.rows(self.rep).rows}
        self.assertEqual(set(rep), {"A", "C", "E"})
        self.assertEqual([rep[s]["overall_rank"] for s in ("C", "A", "E")], [1, 2, 3])
        self.assertTrue(all(r["global_rank"] is None for r in rep.values()))
        self.assertEqual([g["region"] for g in self.rows(self.rep).totals["regions"]], ["East", "West"])
        self.assertEqual(sum(g["stores"] for g in self.rows(self.rep).totals["regions"]), 3)

    def test_focus_counts_reconcile_and_urgency_filter(self):
        totals = self.rows(self.mgr).totals
        self.assertEqual(totals["focus"], {"urgent": 1, "prevention": 3, "verify_data": 1})  # A's only alert is already out of stock
        self.assertEqual(totals["stats"]["msl_gap_skus"], 1)
        urgent = self.rows(self.mgr, urgency="urgent").rows
        self.assertEqual([r["store_id"] for r in urgent], ["A"])
        with self.assertRaises(Module2Error):
            self.rows(self.mgr, urgency="critical")

    def test_detail_and_export_reconcile_with_list(self):
        rows = {r["store_id"]: r for r in self.rows(self.mgr).rows}
        detail = self.service.get_store_detail(self.mgr, "C").detail
        self.assertEqual(detail["store"]["priority_score"], rows["C"]["priority_score"])
        self.assertEqual(detail["store"]["overall_rank"], rows["C"]["overall_rank"])
        self.assertAlmostEqual(sum(s["sales_at_risk"] for s in detail["business"]["skus"].values()),
                               rows["C"]["sales_at_risk_usd"])
        self.assertIn(detail["business"]["confidence"], ("High", "Medium", "Low"))
        self.assertTrue(any("Confirm the next delivery" in a for a in detail["business"]["actions"]))
        text = self.service.export_visit_plan(self.mgr, {"view": "all"}, sort=("sales_at_risk", "desc"))
        exported = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual([r["store_id"] for r in exported],
                         [r["store_id"] for r in self.rows(self.mgr, sort=("sales_at_risk", "desc")).rows])
        by_id = {r["store_id"]: r for r in exported}
        self.assertEqual(float(by_id["C"]["sales_at_risk_usd"]), round(rows["C"]["sales_at_risk_usd"], 2))
        self.assertEqual(by_id["D"]["sales_at_risk_usd"], "")
        self.assertEqual(by_id["E"]["overall_rank"], "5")
        self.assertNotIn("confidence", exported[0])                    # confidence is detail-only

    def test_no_shelf_loss_dollars_anywhere(self):
        detail = self.service.get_store_detail(self.mgr, "A").detail["business"]
        self.assertTrue(all("dollar" not in k and "usd" not in k for a in detail["audits"].values() for k in a))
        self.assertTrue(any("no dollar value" in a for a in detail["assumptions"]))


if __name__ == "__main__":
    unittest.main()
