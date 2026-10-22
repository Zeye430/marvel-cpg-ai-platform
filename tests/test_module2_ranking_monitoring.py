"""visit_priority.v1 rules and issued-prediction monitoring (pure synthetic frames)."""

from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

HAS_ML = all(importlib.util.find_spec(n) for n in ("sklearn", "pyarrow"))
if HAS_ML:
    from modules.module_2 import config, monitoring, ranking
    from modules.module_2.contracts import Module2Error


def sku_rows(store, probs, *, msl=None, oos=None, velocity=None, price=10.0, inventory=5.0):
    n = len(probs)
    msl = msl or [False] * n
    oos = oos or [False] * n
    velocity = velocity or [1.0] * n
    return pd.DataFrame({"store_id": store, "sku_id": [f"P{i}" for i in range(n)], "probability": probs,
                         "threshold": [0.5] * n, "is_predicted_oos": [p >= 0.5 for p in probs],
                         "is_msl": pd.array(msl, dtype="boolean"), "oos_observed": pd.array(oos, dtype="boolean"),
                         "inventory_observed": [inventory] * n, "in_stock_velocity_28d": velocity,
                         "list_price": price})


def frame(*parts):
    data = pd.concat(parts, ignore_index=True)
    velocity = pd.to_numeric(data["in_stock_velocity_28d"], errors="coerce")
    data["demand_proxy_units"] = 7 * velocity * data["probability"]
    data["sales_proxy"] = data["demand_proxy_units"] * data["list_price"]
    data.loc[velocity.isna() | data["list_price"].isna(), ["demand_proxy_units", "sales_proxy"]] = np.nan
    msl, oos = data["is_msl"], data["oos_observed"]
    data["msl_gap"] = (msl.fillna(False) & oos.fillna(False)).astype(bool)
    data["msl_unknown"] = (msl.fillna(False) & oos.isna()).astype(bool)
    data["inventory_unknown"] = data["inventory_observed"].isna()
    return data


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class RankingRuleTests(unittest.TestCase):
    def test_percentiles_average_ties_and_single_store(self):
        self.assertEqual(ranking.percentile([0.7]).tolist(), [1.0])
        self.assertEqual(ranking.percentile([0.1, 0.5, 0.5, 0.9]).tolist(), [0.0, 0.5, 0.5, 1.0])

    def test_bucket_order_proxy_status_fallback_and_ties(self):
        data = frame(
            sku_rows("S-MON", [0.2, 0.1]),                                        # monitor
            sku_rows("S-GAP", [0.3, 0.2], msl=[True, False], oos=[True, False]),    # observed MSL gap
            sku_rows("S-ALERT-A", [0.9, 0.1]),                                     # alert
            sku_rows("S-ALERT-B", [0.9, 0.1], velocity=[None, 1.0]),               # alert, partial proxy
            sku_rows("S-UNK", [0.2, 0.1], msl=[True, False], oos=[None, False]),   # MSL unknown -> follow-up
        )
        table = ranking.rank_stores(data, None)
        order = table["store_id"].tolist()
        self.assertEqual(order[0], "S-GAP")
        self.assertEqual(set(order[1:3]), {"S-ALERT-A", "S-ALERT-B"})
        self.assertEqual(order[3:], ["S-UNK", "S-MON"])
        self.assertEqual(table["priority_rank"].tolist(), [1, 2, 3, 4, 5])
        partial = table.set_index("store_id").loc["S-ALERT-B"]
        self.assertEqual(partial["proxy_status"], "Partial")
        self.assertTrue(partial["impact_fallback"])
        self.assertEqual(partial["impact_percentile"], partial["risk_percentile"])
        # equal index inside a bucket: msl gaps, risk, then store_id decide deterministically
        twin = frame(sku_rows("S-B", [0.9]), sku_rows("S-A", [0.9]))
        self.assertEqual(ranking.rank_stores(twin, None)["store_id"].tolist(), ["S-A", "S-B"])

    def test_missing_price_never_becomes_zero_money(self):
        data = frame(sku_rows("S1", [0.9], price=np.nan))
        self.assertTrue(np.isnan(data["sales_proxy"].iloc[0]))
        table = ranking.rank_stores(data, None)
        self.assertEqual(table["proxy_status"].iloc[0], "Unknown")
        self.assertIsNone(json.loads(ranking.reason(next(table.itertuples())))["reference_sales_proxy"]["known_sum"])


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class MonitoringTests(unittest.TestCase):
    def test_outcome_maturity_pending_partial_and_complete(self):
        issued = pd.DataFrame({"scoring_run_id": "r1", "scoring_date": [date(2026, 6, 1)] * 3 + [date(2026, 6, 28)],
                               "store_id": ["S1", "S2", "S3", "S1"], "sku_id": "P1", "probability": 0.6,
                               "threshold": 0.5})
        days = [date(2026, 6, 1) + timedelta(days=i) for i in range(1, 8)]
        daily = pd.DataFrame([{"date": d, "store_id": "S1", "sku_id": "P1", "oos_observed": d == days[3]} for d in days]
                             + [{"date": d, "store_id": "S2", "sku_id": "P1", "oos_observed": False} for d in days]
                             + [{"date": d, "store_id": "S3", "sku_id": "P1", "oos_observed": False} for d in days[:5]])
        out = monitoring.outcomes(issued, daily, date(2026, 6, 30)).set_index(["store_id", "scoring_date"])
        self.assertEqual(out.loc[("S1", date(2026, 6, 1)), "outcome_status"], "Complete")
        self.assertEqual(int(out.loc[("S1", date(2026, 6, 1)), "target"]), 1)
        self.assertEqual(int(out.loc[("S2", date(2026, 6, 1)), "target"]), 0)
        self.assertEqual(out.loc[("S3", date(2026, 6, 1)), "outcome_status"], "Unknown_partial")
        self.assertTrue(pd.isna(out.loc[("S3", date(2026, 6, 1)), "target"]))
        self.assertEqual(out.loc[("S1", date(2026, 6, 28)), "outcome_status"], "Pending")

    def reference(self, rows=5000, seed=1):
        rng = np.random.default_rng(seed)
        values = rng.normal(size=rows)
        edges = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9)))
        share = np.bincount(np.searchsorted(edges, values, side="right"), minlength=len(edges) + 1) / rows
        numeric = {name: {"edges": edges.tolist(), "bin_share": share.tolist(), "missing_share": 0.0}
                   for name in config.NUMERIC}
        categorical = {name: {"levels": {"A": 1.0}, "missing_share": 0.0} for name in config.CATEGORICAL}
        return {"rows": rows, "numeric": numeric, "categorical": categorical, "segment_mix": {}}

    def current(self, rows, shift=0.0, seed=2):
        rng = np.random.default_rng(seed)
        data = pd.DataFrame({name: rng.normal(loc=shift, size=rows) for name in config.NUMERIC})
        for name in config.CATEGORICAL:
            data[name] = "A"
        return data

    def test_psi_and_drift_support_rules(self):
        self.assertAlmostEqual(monitoring.psi([0.5, 0.5], [0.5, 0.5]), 0.0)
        status, reasons, _ = monitoring.drift(self.reference(), self.current(500))
        self.assertEqual((status, reasons), ("Insufficient_support", []))
        status, _, _ = monitoring.drift(self.reference(), self.current(5000))
        self.assertEqual(status, "Stable")
        status, reasons, report = monitoring.drift(self.reference(), self.current(5000, shift=1.5))
        self.assertEqual(status, "Severe")
        self.assertTrue(any("PSI" in r for r in reasons))
        unknown = self.current(5000)
        unknown["channel"] = "New Channel"
        status, reasons, _ = monitoring.drift(self.reference(), unknown)
        self.assertEqual(status, "Warning")
        self.assertTrue(any("Unknown category" in r for r in reasons))

    def test_issued_history_is_idempotent_and_refuses_duplicates(self):
        from modules.module_2.contracts import sha256_file, write_json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def package(name, run_id):
                folder = root / name
                folder.mkdir()
                pd.DataFrame({"build_id": "b", "dataset_hash": "d", "scoring_run_id": run_id,
                              "scoring_date": [date(2026, 6, 30)], "store_id": "S1", "sku_id": "P1",
                              "probability": 0.7, "threshold": 0.5, "is_predicted_oos": True, "drivers_json": "{}",
                              "model_version": "m1", "feature_version": "f", "input_status": "Observed_context"}
                             ).to_parquet(folder / "predictions.parquet", index=False)
                pd.DataFrame({"store_id": ["S1"]}).to_parquet(folder / "scoring-detail.parquet", index=False)
                write_json(folder / "manifest.json", {
                    "version": "module2.scoring_package.v1", "status": "Accepted", "scoring_run_id": run_id,
                    "scoring_date": "2026-06-30", "created_at": "2026-10-21T00:00:00+00:00",
                    "source": {"dataset_hash": "d", "source_build_id": "b"},
                    "bundle": {"model_version": "m1", "manifest_sha256": "f" * 64},
                    "files": {n: sha256_file(folder / n) for n in ("predictions.parquet", "scoring-detail.parquet")}})
                return folder
            first = package("p1", "run-1")
            entry = monitoring.record_issued(first, root / "history")
            self.assertEqual(monitoring.record_issued(first, root / "history"), entry)      # idempotent
            with self.assertRaises(Module2Error):
                monitoring.record_issued(package("p2", "run-2"), root / "history")          # same model/date again
            entries, issued = monitoring.issued_history(root / "history")
            self.assertEqual((len(entries), len(issued)), (1, 1))


if __name__ == "__main__":
    unittest.main()
