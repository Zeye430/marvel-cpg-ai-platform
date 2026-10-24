"""First-experiment policies; ML integrations run in the declared ML runtime."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from data_platform.temporal_plan import TemporalPlan
from modules.module_2 import training as t

HAS_ML = all(importlib.util.find_spec(name) for name in ("lightgbm", "sklearn", "pyarrow"))


def frame(rows, start, seed=650):
    random = np.random.default_rng(seed)
    data = pd.DataFrame({c: random.random(rows) for c in t.NUMERIC})
    for name in t.CATEGORICAL:
        data[name] = "known"
    data["build_id"] = "synthetic"
    data["scoring_date"] = pd.Timestamp(start)
    data["store_id"] = [f"S{i}" for i in range(rows)]
    data["sku_id"] = "P1"
    data["target_oos_7d"] = np.arange(rows) % 2
    data["oos_observed"] = pd.array([True, False, None] * (rows//3) + [False] * (rows % 3), dtype="boolean")
    return data


class TrainingPolicyTests(unittest.TestCase):
    def test_feature_configuration_and_train_only_categories(self):
        self.assertEqual(len(t.FEATURES), 47)
        self.assertEqual(len(t.FEATURES), len(set(t.FEATURES)))
        self.assertFalse(any("candidate" in c or "target" in c for c in t.FEATURES))
        train, valid = frame(6, "2026-01-01"), frame(6, "2026-02-01")
        valid["channel"] = "unseen"
        train.loc[0, "inventory_observed"] = np.nan
        X, V, levels = t.prepare_categories(train, valid)
        self.assertEqual(levels["channel"], ["known"])
        self.assertTrue(V["channel"].isna().all())
        self.assertTrue(pd.isna(X.loc[0, "inventory_observed"]))

    def test_infinite_numeric_inputs_fail(self):
        train, valid = frame(6, "2026-01-01"), frame(6, "2026-02-01")
        valid.loc[0, "inventory_observed"] = np.inf
        with self.assertRaises(ValueError):
            t.prepare_categories(train, valid)

    def test_segment_support_and_unseen_group_identity(self):
        self.assertFalse(t.supported([0]*2000, 1000))
        self.assertFalse(t.supported([0]*999+[1], 1000))
        self.assertTrue(t.supported([0]*500+[1]*500, 1000))
        data = frame(3, "2026-01-01")
        data.loc[0, "channel"] = None
        keys = t.segment_keys(data)
        self.assertEqual(json.loads(keys.iloc[0]), ["<Unknown>", "known"])


@unittest.skipUnless(HAS_ML, "Requires the declared existing LightGBM/sklearn/pyarrow runtime")
class TrainingMLTests(unittest.TestCase):
    def test_threshold_and_zero_alert_undefined_metrics(self):
        y = np.array([0, 0, 1, 1])
        score = np.array([.1, .2, .8, .9])
        threshold = t.choose_threshold(y, score)
        result = t.evaluate(y, score, threshold)
        self.assertEqual((result["precision"], result["recall"], result["roc_auc"]), (1, 1, 1))
        empty = t.evaluate(y, score, 1.1)
        self.assertIsNone(empty["precision"])
        self.assertEqual(empty["recall"], 0)
        self.assertIsNone(t.evaluate([1], [.9], .5)["roc_auc"])
        self.assertEqual(t.evaluate([], [], [])['rows'], 0)

    def test_small_segment_thresholds_fall_back(self):
        y = np.array([0]*20+[1]*20)
        score = np.linspace(0, 1, len(y))
        policy = t.threshold_policy(y, score, pd.Series(["small"]*40))
        self.assertEqual(policy["segments"], {})
        with self.assertRaises(ValueError):
            t.choose_threshold([1, 1], [.3, .8])

    def test_actual_segment_and_unseen_pooled_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            train, valid = frame(1200, "2026-01-01"), frame(40, "2026-02-01")
            valid.loc[20:, "channel"] = "unseen"
            pooled, segmented, routes = t._fit_fold(train, valid, Path(temporary)/"models")
            self.assertTrue(np.isfinite(pooled).all())
            self.assertTrue(np.isfinite(segmented).all())
            self.assertTrue((routes[:20] == "segment_model").all())
            self.assertTrue((routes[20:] == "pooled_fallback").all())

    def test_full_two_fold_experiment_and_temporal_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root/"inputs"; inputs.mkdir()
            plan = TemporalPlan(date(2026, 6, 30), date(2025, 1, 1), "a"*64, "synthetic", "b"*64)
            manifest = {"version": "module2.inner_training_inputs.v1", "status": "Accepted",
                        "test": "Locked", "outer_validation": "Locked", "features": list(t.FEATURES),
                        "parameters": t.PARAMETERS, "cohort": "synthetic_all_eligible", "files": [],
                        "temporal_plan": plan.to_dict()}
            for fold, train_last, start, end, _ in plan.development_folds()[:2]:
                for partition in ("train", "validation"):
                    data = frame(80, train_last if partition == "train" else start)
                    path = inputs/f"{fold}-{partition}.parquet"
                    data.to_parquet(path,index=False)
                    manifest["files"].append({"fold": fold, "partition": partition, "file": path.name,
                        "sha256": t._hash(path), "rows": len(data), "metadata": {
                            "plan_sha256": plan.to_dict()["plan_sha256"], "source_build_id": "synthetic",
                            "purpose": "development", "cutoff": "2026-05-31", "fold_id": fold, "partition": partition}})
            t._save(inputs/"inputs.json",manifest)
            report = t.fit_experiment(inputs,root/"results")
            self.assertEqual(report["status"],"Completed_not_promoted")
            self.assertEqual(report["test"],"Locked")
            thresholds = json.loads((root/"results/thresholds.json").read_text())
            self.assertEqual(thresholds["selected_on"],"rolling_dev_1")
            later = [r for r in report["results"] if r["fold"] == "rolling_dev_2"]
            self.assertTrue(all(r["operating_point"] == "frozen_from_earlier_fold" for r in later))
            self.assertTrue(any(r["scope"] == "current_unknown" for r in later))
            row = manifest["files"][0]
            path = inputs/row["file"]
            bad = pd.read_parquet(path)
            bad["scoring_date"] = pd.Timestamp("2026-06-01")
            bad.to_parquet(path,index=False)
            row["sha256"] = t._hash(path)
            t._save(inputs/"inputs.json",manifest)
            with self.assertRaisesRegex(ValueError,"outside"):
                t.fit_experiment(inputs,root/"bad-results")


if __name__ == "__main__":
    unittest.main()
