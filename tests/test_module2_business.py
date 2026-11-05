"""Module 2 business views on a hand-built SYNTHETIC serving fixture (no model, no client data).

Stores cover every display case: a current MSL gap (S1), a prevention alert
outside the top five reasons (S2), unknown inventory (S3), routine (S4), an
unranked scoped store (S5) and a prediction/ranking source mismatch (S6).
Ranks, scores and thresholds in the fixture are never recomputed by the service.
"""

import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb

from data_platform.access import CursorCodec, ServingReader
from modules.module_2.contracts import Module2Error
from modules.module_2.service import Module2Service, Principal

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "data_platform/contracts/v3/postgres-serving.json").read_text())["views"]
D = date(2026, 6, 30)
RUN = "score-2026-06-30-synthetic"
TABLES = {"stores": "dim_stores", "products": "dim_products", "visit_priority": "mart_visit_priority",
          "oos_predictions": "predictions_oos", "store_sku_daily": "mart_store_sku_daily"}


def columns(view):
    return [(c[0], c[1]) if isinstance(c, list) else (c["name"], c["type"]) for c in CONTRACT[view]["columns"]]


def insert(con, view, rows):
    names = [n for n, _ in columns(view)]
    for row in rows:
        unknown = set(row) - set(names)
        assert not unknown, unknown
        con.execute(f"INSERT INTO gold.{TABLES[view]} VALUES ({','.join('?' * len(names))})",
                    [row.get(n) for n in names])


def evidence(oos, inventory):
    return [{"feature": "oos_observed", "label": "Reported OOS today", "value": oos, "data_status": "Observed" if oos is not None else "Unknown", "as_of": str(D)},
            {"feature": "inventory_observed", "label": "Reported inventory", "value": inventory, "data_status": "Observed" if inventory is not None else "Unknown", "as_of": str(D)},
            {"feature": "days_since_inventory_report", "label": "Report age", "value": 0, "data_status": "Observed", "as_of": str(D)}]


def prediction(store, sku, probability, alert, oos, inventory, run=RUN):
    drivers = {"prediction_start": "2026-07-01", "prediction_end": "2026-07-07", "threshold_route": "segment",
               "evidence": evidence(oos, inventory), "limitations": []}
    return {"scoring_run_id": run, "scoring_date": D, "store_id": store, "sku_id": sku, "probability": probability,
            "threshold": 0.5, "is_predicted_oos": alert, "drivers_json": json.dumps(drivers),
            "model_version": "synthetic-model", "feature_version": "f1", "input_status": "Observed_context"}


# store -> [(sku, probability, alert, oos_observed, inventory_observed)]
PREDICTIONS = {
    "S1": [("P1", 0.80, True, True, 0.0), ("P3", 0.70, True, False, 4.0), ("P2", 0.10, False, False, 9.0)],
    "S2": [("P3", 0.95, True, True, 0.0), ("P4", 0.94, True, True, 0.0), ("P5", 0.93, True, True, 0.0),
           ("P6", 0.92, True, None, None), ("P7", 0.91, True, None, None), ("P8", 0.90, True, False, 6.0),
           ("P1", 0.10, False, False, 5.0), ("P2", 0.10, False, False, 5.0)],
    "S3": [("P1", 0.20, False, None, None), ("P3", 0.10, False, False, 3.0)],
    "S4": [("P1", 0.05, False, False, 8.0), ("P3", 0.04, False, False, 8.0)],
    "S6": [("P3", 0.60, True, False, 2.0)],
}
BUCKETS = {"S1": 1, "S2": 2, "S6": 2, "S3": 3, "S4": 4}
LABELS = {1: "Observed MSL gap", 2: "Predicted OOS alert", 3: "Data follow-up: inventory/MSL unknown", 4: "Monitor"}


def reason(store, rows):
    msl = {"P1", "P2"}
    top = sorted(rows, key=lambda r: (-r[1], r[0]))[:5]
    return json.dumps({
        "bucket": BUCKETS[store], "bucket_label": LABELS[BUCKETS[store]],
        "msl_gap_count": sum(1 for s, _, _, oos, _ in rows if s in msl and oos),
        "msl_unknown_count": sum(1 for s, _, _, oos, _ in rows if s in msl and oos is None),
        "alert_count": sum(1 for r in rows if r[2]),
        "inventory_unknown_count": sum(1 for r in rows if r[4] is None), "sku_count": len(rows),
        "reference_sales_proxy": {"status": "Complete", "known_sum": 1.0}, "impact_fallback_to_risk": False,
        "top_skus": [{"sku_id": s, "probability": p, "threshold": 0.5, "alert": a} for s, p, a, _, _ in top]})


def build(path):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving")
        for view, table in TABLES.items():
            con.execute(f"CREATE TABLE gold.{table} ({', '.join(f'{n} {t}' for n, t in columns(view))})")
            con.execute(f"CREATE VIEW serving.vw_{view} AS SELECT * FROM gold.{table}")
        con.execute("CREATE TABLE gold.account_scope (principal_id VARCHAR, store_id VARCHAR, role VARCHAR, scope_version VARCHAR)")
        stores = ["S1", "S2", "S3", "S4", "S5", "S6"]
        con.executemany("INSERT INTO gold.account_scope VALUES (?,?,?,'v1')",
                        [("mgr", s, "manager") for s in stores] + [("rep", s, "sales") for s in ("S1", "S2", "S5", "S6")])
        insert(con, "stores", [{"store_id": s, "store_name": f"SYNTHETIC Store {s}", "channel": "Modern Trade" if s in ("S1", "S2", "S3") else "Distributor",
                                "region": "West", "state": "CA", "segment_tier": "Tier 1", "city": "X"} for s in stores])
        insert(con, "products", [{"sku_id": f"P{i}", "product_name": f"SYNTHETIC Product {i}", "category": "Laundry",
                                  "brand": "B", "is_msl": i in (1, 2), "unit_cost": 1, "list_price": 2, "consumer_price": 3}
                                 for i in range(1, 9)])
        order = ["S1", "S2", "S6", "S3", "S4"]
        insert(con, "visit_priority", [{"scoring_run_id": RUN, "scoring_date": D, "store_id": s, "priority_rank": i,
                                        "risk_score": max(r[1] for r in PREDICTIONS[s]),
                                        "msl_gap_count": json.loads(reason(s, PREDICTIONS[s]))["msl_gap_count"],
                                        "ranking_policy_version": "visit_priority.v1", "reason_json": reason(s, PREDICTIONS[s])}
                                       for i, s in enumerate(order, start=1)])
        insert(con, "oos_predictions", [prediction(s, sku, p, a, oos, inv, run="older-run" if s == "S6" else RUN)
                                        for s, rows in PREDICTIONS.items() for sku, p, a, oos, inv in rows])
        insert(con, "store_sku_daily", [{"date": D, "store_id": "S5", "sku_id": "P1", "inventory_observed": 7.0,
                                         "oos_observed": False, "inventory_data_status": "Observed", "is_msl": True}])


def reader(path):
    r = ServingReader.__new__(ServingReader)
    r.database = path
    r._cursors = CursorCodec()
    r.record = {"artifact_id": "synthetic-m2", "as_of": str(D), "dataset_hash": "synthetic",
                "model_outputs_status": "Available", "rows": {"stores": 6},
                "model_outputs": {"model_version": "synthetic-model", "scoring_run_id": RUN,
                                  "publication_kind": "Synthetic_demo", "synthetic": True}}
    return r


class BusinessViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic.duckdb"
        build(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.service = Module2Service(reader=reader(self.path))
        self.mgr, self.rep = Principal("mgr", True), Principal("rep", True)

    def view(self, principal, view, **filters):
        return self.service.get_priorities(principal, {"view": view, **filters}, page_size=200)

    def test_summary_counts_and_views_reconcile(self):
        response = self.view(self.mgr, "all")
        self.assertEqual(response.totals["summary"], {"stores": 6, "current_msl": 1, "prevention": 2, "verify_data": 4})
        ids = {v: [r["store_id"] for r in self.view(self.mgr, v).rows]
               for v in ("all", "current_msl", "prevention", "verify_data")}
        self.assertEqual(ids["all"], ["S1", "S2", "S6", "S3", "S4", "S5"])  # unranked last
        self.assertEqual(ids["current_msl"], ["S1"])
        self.assertEqual(ids["prevention"], ["S1", "S2"])
        self.assertEqual(ids["verify_data"], ["S2", "S6", "S3", "S5"])  # S2 has unreported inventory
        for v in ("current_msl", "prevention", "verify_data"):
            self.assertEqual(len(ids[v]), response.totals["summary"][v])
            self.assertEqual(self.view(self.mgr, v).totals["filtered_stores"], len(ids[v]))

    def test_prevention_uses_all_predictions_not_top_five_and_excludes_oos_unknown(self):
        rows = {r["store_id"]: r for r in self.view(self.mgr, "all").rows}
        self.assertEqual(rows["S2"]["prevention_alert_count"], 1)  # P8 is outside the five top reasons
        self.assertNotIn("P8", [s["sku_id"] for s in rows["S2"]["top_skus"]])
        self.assertEqual(rows["S2"]["alert_count"], 6)               # legacy count unchanged
        self.assertEqual(rows["S1"]["prevention_alert_count"], 1)   # P1 is currently out of stock
        self.assertEqual(rows["S4"]["prevention_alert_count"], 0)

    def test_source_mismatch_is_unavailable_not_zero(self):
        rows = {r["store_id"]: r for r in self.view(self.mgr, "all").rows}
        self.assertIsNone(rows["S6"]["prevention_alert_count"])
        self.assertTrue(any("does not match" in t for t in rows["S6"]["data_check_reasons"]))

    def test_unranked_store_is_explicit_and_ranks_never_change(self):
        rows = {r["store_id"]: r for r in self.view(self.mgr, "verify_data").rows}
        self.assertIsNone(rows["S5"]["display_rank"])
        self.assertIsNone(rows["S5"]["risk_score"])
        self.assertEqual(rows["S5"]["data_check_reasons"], ["No usable model score for this store"])
        self.assertEqual(rows["S6"]["display_rank"], 3)  # filtering does not renumber
        self.assertEqual(rows["S3"]["data_check_reasons"], ["Inventory not reported for 1 SKU(s)",
                                                            "MSL status unknown for 1 SKU(s)"])

    def test_objectives_follow_bucket_and_view(self):
        rows = {r["store_id"]: r["visit_objective"] for r in self.view(self.mgr, "all").rows}
        self.assertEqual(rows["S1"], "Check shelf stock and replenishment")
        self.assertEqual(rows["S2"], "Confirm stock and upcoming replenishment")
        self.assertTrue(rows["S3"].startswith("Verify"))
        self.assertEqual(rows["S4"], "Routine store review")
        self.assertTrue(rows["S5"].startswith("Verify"))
        emphasized = {r["store_id"]: r["visit_objective"] for r in self.view(self.mgr, "prevention").rows}
        self.assertEqual(emphasized["S1"], "Confirm stock and upcoming replenishment")

    def test_partial_scope_sees_only_its_stores_and_no_global_rank(self):
        response = self.view(self.rep, "all")
        self.assertEqual([r["store_id"] for r in response.rows], ["S1", "S2", "S6", "S5"])
        self.assertEqual([r["display_rank"] for r in response.rows], [1, 2, 3, None])
        self.assertTrue(all(r["global_rank"] is None for r in response.rows))
        self.assertEqual(response.totals["summary"]["stores"], 4)

    def test_filters_apply_before_views_and_search_is_shared(self):
        mt = self.view(self.mgr, "all", channel="Modern Trade")
        self.assertEqual(mt.totals["summary"], {"stores": 3, "current_msl": 1, "prevention": 2, "verify_data": 2})
        self.assertEqual([r["store_id"] for r in self.view(self.mgr, "verify_data", search="s5").rows], ["S5"])

    def test_legacy_filters_unchanged_and_conflicts_rejected(self):
        legacy = self.service.get_priorities(self.mgr, {"alert_only": True}, page_size=200)
        self.assertEqual([r["store_id"] for r in legacy.rows], ["S1", "S2", "S6"])
        self.assertNotIn("S5", [r["store_id"] for r in self.service.get_priorities(self.mgr, {}, page_size=200).rows])
        with self.assertRaises(Module2Error):
            self.view(self.mgr, "prevention", alert_only=True)
        with self.assertRaises(Module2Error):
            self.view(self.mgr, "everything")

    def test_export_matches_view_and_keeps_legacy_columns_first(self):
        text = self.service.export_visit_plan(self.mgr, {"view": "verify_data"})
        rows = list(csv.reader(io.StringIO(text)))
        legacy = ["display_rank", "store_id", "store_name", "channel", "region", "segment_tier", "urgency",
                  "risk_score", "msl_gap_count", "alert_skus", "unknown_inventory_skus", "top_skus",
                  "scoring_date", "model_version", "publication_kind"]
        self.assertEqual(rows[0][:len(legacy)], legacy)
        self.assertEqual(rows[0][len(legacy):len(legacy) + 3], ["visit_objective", "prevention_alert_skus", "data_checks"])
        self.assertEqual(rows[0][-1], "ranking_policy_version")     # business-priority columns appended last
        self.assertEqual([r[1] for r in rows[1:]], ["S2", "S6", "S3", "S5"])
        unranked = rows[-1]
        self.assertEqual((unranked[0], unranked[7]), ("", ""))  # no manufactured rank or risk

    def test_unranked_store_detail_does_not_fail(self):
        detail = self.service.get_store_detail(self.mgr, "S5")
        self.assertEqual(detail.detail["skus"], [])
        self.assertEqual(detail.detail["reported_now"][0]["sku_id"], "P1")


if __name__ == "__main__":
    unittest.main()
