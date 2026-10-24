"""Model-agnostic preprocessing, adapters, calibration and thresholds (synthetic)."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from module2_fixtures import synthetic_frame

HAS_ML = all(importlib.util.find_spec(n) for n in ("lightgbm", "xgboost", "sklearn", "joblib", "optuna"))
if HAS_ML:
    from modules.module_2 import calibration, config, evaluation, models
    from modules.module_2.contracts import Module2Error
    from modules.module_2.preprocessing import Preprocessor

SMALL = {"lightgbm": {"n_estimators": 30, "learning_rate": 0.1, "num_leaves": 15, "min_child_samples": 20,
                      "colsample_bytree": 0.8, "subsample": 0.8, "reg_lambda": 1.0},
         "xgboost": {"n_estimators": 30, "learning_rate": 0.1, "max_depth": 3, "min_child_weight": 1.0,
                     "subsample": 0.8, "colsample_bytree": 0.8, "reg_lambda": 1.0},
         "random_forest": {"n_estimators": 20, "max_depth": 8, "min_samples_leaf": 50, "max_features": 0.4,
                           "max_samples": 0.5},
         "logistic_regression": {"C": 1.0}}


@unittest.skipUnless(HAS_ML, "Requires the module2 extra (full Module 2 environment)")
class PreprocessorTests(unittest.TestCase):
    def setUp(self):
        self.train = synthetic_frame(800, seed=1)
        self.valid = synthetic_frame(300, seed=2)

    def test_train_only_vocabulary_unknown_and_missing(self):
        pre = Preprocessor.fit(self.train)
        valid = self.valid.copy()
        valid.loc[0, "channel"] = "Unseen Channel"
        valid.loc[1, "channel"] = None
        X = pre.transform(valid)
        self.assertEqual(X.shape[1], len(pre.columns))
        self.assertEqual(X.dtype, np.float32)
        cols = {c: i for i, c in enumerate(pre.columns)}
        channel_cols = [cols[c] for c in pre.columns if c.startswith("channel=")]
        self.assertEqual(X[0, channel_cols].sum(), 0)          # unseen -> ignored
        self.assertEqual(X[0, cols["channel__missing"]], 0)
        self.assertEqual(X[1, cols["channel__missing"]], 1)    # missing -> explicit
        self.assertNotIn("channel=Unseen Channel", pre.columns)
        self.assertAlmostEqual(pre.unknown_category_rates(valid)["channel"], 1 / 299)

    def test_median_from_training_only_and_indicator(self):
        pre = Preprocessor.fit(self.train)
        median = float(np.nanmedian(self.train["inventory_observed"]))
        self.assertAlmostEqual(pre.state["numeric"]["inventory_observed"]["median"], median)
        frame = self.valid.copy()
        frame["inventory_observed"] = np.nan
        X = pre.transform(frame)
        cols = {c: i for i, c in enumerate(pre.columns)}
        self.assertTrue(np.allclose(X[:, cols["inventory_observed"]], np.float32(median)))
        self.assertTrue((X[:, cols["inventory_observed__missing"]] == 1).all())

    def test_all_missing_column_kept_and_marked(self):
        train = self.train.copy()
        train["consumer_price_observed"] = np.nan
        pre = Preprocessor.fit(train)
        self.assertTrue(pre.state["numeric"]["consumer_price_observed"]["all_missing"])
        self.assertIn("consumer_price_observed", pre.columns)

    def test_infinite_or_text_numeric_fails(self):
        pre = Preprocessor.fit(self.train)
        bad = self.valid.copy()
        bad.loc[0, "receipt_units_7d"] = np.inf
        with self.assertRaises(Module2Error):
            pre.transform(bad)
        bad = self.valid.copy()
        bad["receipt_units_7d"] = bad["receipt_units_7d"].astype(object)
        bad.loc[0, "receipt_units_7d"] = "seven"
        with self.assertRaises(Module2Error):
            pre.transform(bad)

    def test_json_roundtrip_is_identical(self):
        pre = Preprocessor.fit(self.train)
        again = Preprocessor(json.loads(json.dumps(pre.to_dict())))
        self.assertTrue(np.array_equal(pre.transform(self.valid), again.transform(self.valid)))
        self.assertEqual(pre.sha256, again.sha256)
        self.assertEqual(pre.raw_feature("channel=Modern Trade"), "channel")


@unittest.skipUnless(HAS_ML, "Requires the module2 extra (full Module 2 environment)")
class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        train, valid = synthetic_frame(2000, seed=3), synthetic_frame(600, seed=4)
        pre = Preprocessor.fit(train)
        cls.X, cls.y = pre.transform(train), train["target_oos_7d"].to_numpy(int)
        cls.V = pre.transform(valid)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_all_four_families_fit_predict_save_load_identically(self):
        for algorithm in config.ALGORITHMS:
            with self.subTest(algorithm=algorithm):
                adapter = models.fit_candidate(algorithm, SMALL[algorithm], self.X, self.y)
                before = adapter.predict_raw_probability(self.V)
                self.assertTrue(((before >= 0) & (before <= 1)).all())
                meta = adapter.save(self.root / algorithm)
                loaded = models.load_candidate(self.root / algorithm, expected=meta)
                self.assertLess(np.max(np.abs(loaded.predict_raw_probability(self.V) - before)), 1e-10)
                again = models.fit_candidate(algorithm, SMALL[algorithm], self.X, self.y)
                self.assertLess(np.max(np.abs(again.predict_raw_probability(self.V) - before)), 1e-10)

    def test_tampered_mismatched_or_foreign_models_refused(self):
        adapter = models.fit_candidate("lightgbm", SMALL["lightgbm"], self.X, self.y)
        meta = adapter.save(self.root / "m")
        with (self.root / "m" / "model.txt").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(Module2Error):
            models.load_candidate(self.root / "m")
        adapter.save(self.root / "n")
        with self.assertRaises(Module2Error):
            models.load_candidate(self.root / "n", expected={**meta, "params": {}})
        with patch.object(models, "library_versions", return_value={"lightgbm": "0.0"}):
            with self.assertRaises(Module2Error):
                models.load_candidate(self.root / "n")
        with self.assertRaises(Module2Error):
            models.fit_candidate("lstm", {}, self.X, self.y)
        with self.assertRaises(Module2Error):
            models.fit_candidate("lightgbm", SMALL["lightgbm"], self.X, np.zeros(len(self.y)))

    def test_random_forest_reports_bounded_node_count(self):
        adapter = models.fit_candidate("random_forest", SMALL["random_forest"], self.X, self.y)
        self.assertLessEqual(adapter.info["node_count"], 20 * (2 * 1024 - 1))

    def test_xgboost_class_weight_from_training_labels_only(self):
        adapter = models.fit_candidate("xgboost", SMALL["xgboost"], self.X, self.y)
        expected = (len(self.y) - self.y.sum()) / self.y.sum()
        self.assertAlmostEqual(adapter.info["scale_pos_weight"], expected)

    def test_logistic_non_convergence_is_a_recorded_failure(self):
        fixed = {**config.FIXED_PARAMETERS["logistic_regression"], "max_iter": 1}
        with patch.dict(config.FIXED_PARAMETERS, {"logistic_regression": fixed}):
            with self.assertRaises(models.ConvergenceFailure):
                models.fit_candidate("logistic_regression", {"C": 10.0}, self.X * 1000, self.y)


@unittest.skipUnless(HAS_ML, "Requires the module2 extra (full Module 2 environment)")
class CalibrationThresholdTests(unittest.TestCase):
    def test_sigmoid_is_monotone_json_and_rejects_invalid(self):
        rng = np.random.default_rng(5)
        y = rng.integers(0, 2, 2000)
        raw = np.clip(0.3 * y + rng.random(2000) * 0.7, 0, 1)
        cal = calibration.fit_sigmoid(raw, y)
        self.assertGreater(cal["coef"], 0)
        p = calibration.apply(json.loads(json.dumps(cal)), np.sort(raw))
        self.assertTrue((np.diff(p) >= 0).all())
        with self.assertRaises(Module2Error):
            calibration.fit_sigmoid(raw, np.zeros(2000, int))
        with self.assertRaises(Module2Error):
            calibration.fit_sigmoid(1 - raw, y)                  # anti-correlated -> coef <= 0
        with self.assertRaises(Module2Error):
            calibration.apply({**cal, "coef": -1.0}, raw)

    def test_ece_bins_and_empty_bins(self):
        table = calibration.reliability([0, 1], [0.05, 0.95])
        self.assertEqual(len(table), 10)
        self.assertIsNone(table[5]["mean_probability"])
        self.assertAlmostEqual(calibration.ece([0, 1], [0.05, 0.95]), 0.05)

    def test_segment_threshold_support_and_fallback(self):
        frame = synthetic_frame(4000, seed=6)
        from modules.module_2.training import segment_keys
        segments = segment_keys(frame)
        rng = np.random.default_rng(6)
        score = np.clip(frame["target_oos_7d"] * 0.4 + rng.random(len(frame)) * 0.6, 0, 1)
        policy = evaluation.threshold_policy(frame["target_oos_7d"], score, segments)
        small = [s for s in policy["support"] if not s["own_threshold"]]
        for row in policy["support"]:
            self.assertEqual(row["own_threshold"], row["segment"] in policy["segments"])
        values, routes = evaluation.route_thresholds(policy, list(segments) + ['["Unseen","Tier 9"]'])
        self.assertEqual(routes[-1], "global_fallback")
        self.assertEqual(values[-1], policy["global"])
        self.assertIsInstance(small, list)
        with self.assertRaises(Module2Error):
            evaluation.check_thresholds({"global": float("nan"), "segments": {}})
        with self.assertRaises(Module2Error):
            evaluation.check_thresholds({"global": True, "segments": {}})

    def test_undefined_metrics_never_pass(self):
        single = evaluation.scope_metrics([0, 0, 0], [0.1, 0.2, 0.3], [0.5, 0.5, 0.5])
        self.assertIsNone(single["roc_auc"]); self.assertIsNone(single["precision"])
        self.assertFalse(single["meets_numeric_targets"])
        zero_alerts = evaluation.scope_metrics([0, 1, 1, 0], [0.1, 0.2, 0.3, 0.1], [0.9] * 4)
        self.assertIsNone(zero_alerts["precision"]); self.assertEqual(zero_alerts["recall"], 0.0)
        self.assertFalse(zero_alerts["meets_numeric_targets"])
        good = evaluation.scope_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2], [0.5] * 4)
        self.assertTrue(good["meets_numeric_targets"])
        self.assertEqual(good["tp"] + good["fp"] + good["fn"] + good["tn"], 4)

    def test_report_reconciliation_rejects_contradictions(self):
        frame = synthetic_frame(500, seed=7)
        results = evaluation.evaluate_groups(frame, np.full(len(frame), 0.5), np.full(len(frame), 0.4))
        self.assertTrue(evaluation.reconcile(results, len(frame)))
        broken = [dict(r) for r in results]
        broken[0]["tp"] += 1
        with self.assertRaises(Module2Error):
            evaluation.reconcile(broken, len(frame))
        with self.assertRaises(Module2Error):
            evaluation.reconcile(results + [results[0]], len(frame))


if __name__ == "__main__":
    unittest.main()
