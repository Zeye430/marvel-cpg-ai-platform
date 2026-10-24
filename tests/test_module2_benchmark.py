"""Optuna benchmark, budget, failure and fair-selection rules on synthetic folds."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from module2_fixtures import synthetic_frame

HAS_ML = all(importlib.util.find_spec(n) for n in ("lightgbm", "xgboost", "sklearn", "joblib", "optuna"))
if HAS_ML:
    from modules.module_2 import benchmark, config
    from modules.module_2.contracts import read_json, sha256_file, write_json


def write_development_inputs(run_dir, rows=1600, seed=10):
    """A synthetic stand-in for inputs.export_development (same manifest shape)."""
    folder = Path(run_dir) / "inputs" / "development"
    folder.mkdir(parents=True)
    manifest = {"version": "module2.development_inputs.v1", "status": "Accepted", "files": {},
                "synthetic": True, "features": list(config.FEATURES)}
    for i, name in enumerate(("rolling_dev_1-train", "rolling_dev_1-validation",
                              "rolling_dev_2-train", "rolling_dev_2-validation")):
        frame = synthetic_frame(rows if name.endswith("train") else rows // 2, seed=seed + i)
        frame.to_parquet(folder / f"{name}.parquet", index=False)
        manifest["files"][name] = {"file": f"{name}.parquet", "sha256": sha256_file(folder / f"{name}.parquet"),
                                   "rows": len(frame)}
    write_json(folder / "manifest.json", manifest)
    return folder


def fake(algorithm, trial, precision, recall, auc, ap=0.5, seconds=10.0, segment=0.7, status="Complete"):
    overall = {"group_kind": "all", "group_id": "all", "precision": precision, "recall": recall,
               "roc_auc": auc, "average_precision": ap}
    return {"algorithm": algorithm, "trial_id": f"{algorithm}-{trial:03d}", "status": status,
            "dev2": {"results": [overall]}, "total_fit_seconds": seconds,
            "min_supported_segment_recall": segment, "constraints": [0.7 - recall, 0.8 - auc]}


@unittest.skipUnless(HAS_ML, "Requires the module2 extra (full Module 2 environment)")
class SelectionRuleTests(unittest.TestCase):
    budget = {"min_complete_trials": 3}

    def test_fair_selection_any_family_can_win_and_ties_are_fixed(self):
        trials = [fake("lightgbm", i, 0.80, 0.72, 0.9) for i in range(3)]
        trials += [fake("xgboost", i, 0.80, 0.72, 0.9, ap=0.6) for i in range(3)]
        trials += [fake("random_forest", i, 0.79, 0.75, 0.9) for i in range(3)]
        trials += [fake("logistic_regression", 0, 0.85, 0.71, 0.85)]
        selection = benchmark.select(trials, self.budget)
        self.assertEqual(selection["winner"], "logistic_regression-000")  # baseline may win on the same rule
        trials = [t for t in trials if t["algorithm"] != "logistic_regression"]
        selection = benchmark.select(trials, self.budget)
        self.assertEqual(selection["winner"], "xgboost-000")               # tie on precision -> higher AP
        self.assertEqual(selection["benchmark_status"], "Complete")

    def test_infeasible_or_incomplete_never_produce_a_fake_winner(self):
        trials = [fake("lightgbm", i, 0.95, 0.60, 0.9) for i in range(3)]          # recall constraint fails
        trials += [fake("xgboost", i, 0.90, 0.71, 0.79) for i in range(3)]         # AUC constraint fails
        trials += [fake("random_forest", i, 0.9, 0.9, 0.9, status="Timeout") for i in range(5)]
        selection = benchmark.select(trials, self.budget)
        self.assertIsNone(selection["winner"])
        self.assertEqual(selection["selection_status"], "No_feasible_candidate")
        self.assertEqual(selection["benchmark_status"], "Benchmark_incomplete")
        self.assertEqual(selection["incomplete_algorithms"], ["random_forest"])
        self.assertIsNotNone(selection["closest_infeasible_diagnostic"])


@unittest.skipUnless(HAS_ML, "Requires the module2 extra (full Module 2 environment)")
class BenchmarkRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"
        write_development_inputs(self.run_dir)

    def test_synthetic_profile_runs_all_families_and_keeps_no_models(self):
        report = benchmark.run_benchmark(self.run_dir, profile="synthetic", run_id="test-run")
        trials = report["trials"]
        for algorithm in config.MAIN_ALGORITHMS:
            mine = [t for t in trials if t["algorithm"] == algorithm]
            self.assertGreaterEqual(len(mine), 3, algorithm)
            self.assertLessEqual(len(mine), config.BUDGETS["synthetic"]["max_trials"])
        rf = sorted((t for t in trials if t["algorithm"] == "random_forest"), key=lambda t: t["trial_id"])
        self.assertEqual([t["params"] for t in rf[:3]], [dict(p) for p in config.RF_STARTING_TRIALS])
        self.assertEqual(len([t for t in trials if t["algorithm"] == "logistic_regression"]), 3)
        for trial in trials:
            if trial["status"] == "Complete":
                self.assertEqual(trial["dev1"]["operating_point"], "tuned_on_this_fold")
                self.assertEqual(trial["dev2"]["operating_point"], "frozen_from_rolling_dev_1")
                self.assertEqual(trial["calibrator"]["method"], "sigmoid")
                self.assertGreater(trial["peak_rss_bytes"], 0)
        model_files = [p for p in self.run_dir.rglob("*") if p.suffix in (".txt", ".ubj", ".joblib", ".npy")]
        self.assertEqual(model_files, [])                                   # no trial model persisted
        self.assertTrue((self.run_dir / "benchmark" / "cleanup.json").exists())
        self.assertIn(report["selection"]["benchmark_status"], ("Complete", "Benchmark_incomplete"))
        again = benchmark.select(benchmark.collect(self.run_dir), config.BUDGETS["synthetic"])
        self.assertEqual(again["winner"], report["selection"]["winner"])    # deterministic

    def test_trial_timeout_kills_only_its_worker_and_is_not_complete(self):
        budget = {**config.BUDGETS["synthetic"], "trial_seconds": 0.01, "max_trials": 1}
        with patch.dict(config.BUDGETS, {"synthetic": budget}):
            report = benchmark.run_benchmark(self.run_dir, profile="synthetic", run_id="timeout-run")
        statuses = {t["status"] for t in report["trials"]}
        self.assertEqual(statuses, {"Timeout"})
        self.assertEqual(report["status"], "Benchmark_incomplete")
        self.assertIsNone(report["selection"]["winner"])

    def test_resource_guard_stops_safely_with_evidence(self):
        limits = {**config.RESOURCE_LIMITS, "free_space_reserve_bytes": 10**18}
        with patch.dict(config.RESOURCE_LIMITS, limits):
            report = benchmark.run_benchmark(self.run_dir, profile="synthetic", run_id="disk-run")
        self.assertEqual(report["status"], "Benchmark_incomplete")
        self.assertIn("Resource_exhausted", report["selection"]["resource_status"])

    def test_trial_reads_development_cache_only(self):
        benchmark.prepare_cache(self.run_dir)
        cache = read_json(self.run_dir / "benchmark" / "cache" / "cache.json")
        self.assertEqual(sorted(cache["folds"]), ["rolling_dev_1", "rolling_dev_2"])
        spec = {"algorithm": "lightgbm", "trial_id": "direct", "output": str(self.run_dir / "direct"),
                "cache": str(self.run_dir / "benchmark" / "cache"),
                "params": {"n_estimators": 150, "learning_rate": 0.1, "num_leaves": 15, "min_child_samples": 20,
                           "colsample_bytree": 0.8, "subsample": 0.8, "reg_lambda": 1.0}}
        result = benchmark.run_trial(spec)
        self.assertEqual(result["status"], "Complete")
        scores = pd.read_parquet(self.run_dir / "direct" / "scores.parquet")
        self.assertEqual(set(scores["fold"]), {"rolling_dev_1", "rolling_dev_2"})
        self.assertTrue(((scores["calibrated"] >= 0) & (scores["calibrated"] <= 1)).all())


if __name__ == "__main__":
    unittest.main()
