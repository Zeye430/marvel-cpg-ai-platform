"""Controlled outer validation: temporary synthetic evidence, never real test truth."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from data_platform.temporal_plan import TemporalPlan
from modules.module_2 import training as t, validation as v
from test_module2_training import frame, HAS_ML

HAS_DATA = importlib.util.find_spec("duckdb") is not None


def evidence(root, plan, components=None):
    """Minimal explicitly synthetic prior experiment for boundary tests only."""
    workflow = root/"workflow.json"
    dev = root/"development"
    t._save(workflow, {"status": "Accepted", "evaluation_profile": "trailing_30d",
                       "temporal_plan": plan, "components": components or {}})
    inputs = {"status": "Accepted", "features": list(t.FEATURES), "parameters": t.PARAMETERS,
              "cohort": v.COHORT, "temporal_plan": plan, "workflow_sha256": t._hash(workflow)}
    t._save(dev/"inputs/inputs.json", inputs)
    dummy = dev/"results/synthetic-evidence.txt"
    dummy.parent.mkdir(parents=True, exist_ok=True)
    dummy.write_text("Synthetic fixture only; not a fitted model or approval.\n")
    t._save(dev/"results/report.json", {**inputs, "status": "Completed_not_promoted",
        "test": "Locked", "outer_validation": "Locked",
        "input_manifest_sha256": t._hash(dev/"inputs/inputs.json"),
        "model_files": {dummy.name: t._hash(dummy)},
        "results": [{"fold": "rolling_dev_2", "policy": v.POLICY, "scope": "all",
                     "precision": .9, "recall": .75, "roc_auc": .98}]})
    t._save(dev/"results/thresholds.json", {"selected_on": "rolling_dev_1", "policies": {
        "pooled": {"global": .8, "segments": {'["known", "known"]': .7}}}})
    return workflow, dev


def synthetic_candidate(root):
    """Data-export guard fixture only; no claim this text is a real ML candidate."""
    _, phash = v._policy(root)
    folder = root/"candidate"
    folder.mkdir()
    (folder/"model.txt").write_text("Synthetic export-boundary test, not an ML model")
    t._save(folder/"categories.json", {})
    t._save(folder/"manifest.json", {"version": "module2.outer_candidate.v1",
        "status": "Frozen_for_validation", "test_access": "Locked", "policy_sha256": phash,
        "features": list(t.FEATURES), "model_sha256": t._hash(folder/"model.txt"),
        "categories_sha256": t._hash(folder/"categories.json"),
        "training_manifest_sha256": t._hash(root/"train-inputs/manifest.json")})


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = TemporalPlan(date(2026, 6, 30), date(2025, 1, 1), "a"*64, "synthetic", "b"*64)
        self.workflow, self.dev = evidence(self.root, self.plan.to_dict())
        self.run = self.root/"run"

    def freeze(self):
        # Pure policy fixture; actual shared-source verification is tested below.
        source = {"artifact_id": "synthetic", "database_sha256": "b"*64}
        with patch.object(v, "_sources", return_value=(source, None, source, None)):
            return v.freeze_policy(self.workflow, self.dev, self.run)

    def test_freeze_pins_configuration_and_refuses_repeated_output(self):
        p = self.freeze()
        self.assertEqual(p["thresholds"]["global"], .8)
        self.assertEqual(p["test_access"], "Locked")
        self.assertEqual(v._policy(self.run)[0], p)
        with self.assertRaises(FileExistsError):
            self.freeze()

    def test_unpassed_or_undefined_development_metrics_do_not_freeze(self):
        path = self.dev/"results/report.json"
        original = v._read(path)
        for value in (None, True, float("nan"), .74):
            bad = json.loads(json.dumps(original))
            bad["results"][0]["precision"] = value
            path.write_text(json.dumps(bad))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "prerequisites"):
                self.freeze()
        self.assertFalse(self.run.exists())

    def test_workflow_and_model_evidence_mismatch_rejected(self):
        path = self.dev/"results/synthetic-evidence.txt"
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.freeze()
        workflow = v._read(self.workflow)
        workflow["status"] = "Failed"
        t._save(self.workflow, workflow)
        with self.assertRaisesRegex(ValueError, "compatible"):
            self.freeze()

    def test_threshold_policy_code_and_dependency_mutations_rejected(self):
        self.freeze()
        path = self.run/"policy/frozen-policy.json"
        original = v._read(path)
        for field, value in (("code_hashes", {}), ("test_access", "Open"),
                             ("thresholds", {"global": .1, "segments": {}})):
            t._save(path, {**original, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                v._policy(self.run)
        t._save(path, original)
        (self.dev/"results/thresholds.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            v._policy(self.run)

    def test_test_mode_and_pre_candidate_validation_refused_before_source_read(self):
        self.freeze()
        with patch.object(v, "_sources", side_effect=AssertionError("Opened sources")):
            for partition in ("test", "validation"):
                with self.subTest(partition=partition), self.assertRaises(ValueError):
                    v.export_partition(self.run, partition)
        self.assertFalse((self.run/"validation-inputs").exists())
        self.assertFalse((self.run/"access").exists())

    def test_frame_rejects_future_dates_duplicates_and_nonbinary_truth(self):
        p = {"temporal_plan": self.plan.to_dict()}
        good = frame(12, "2026-04-24")
        v._check_frame(good, p, "train")
        for bad in (good.assign(scoring_date=pd.Timestamp("2026-04-25")),
                    pd.concat([good, good]), good.assign(target_oos_7d=2),
                    good.assign(build_id="other")):
            with self.assertRaises(ValueError):
                v._check_frame(bad, p, "train")


@unittest.skipUnless(HAS_DATA, "Requires the locked DuckDB data environment")
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_module2_data_adapter as reader_tests
        cls.fixture = reader_tests.Module2ReaderTests
        cls.fixture.setUpClass()
        cls.addClassCleanup(cls.fixture.doClassCleanups)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        f = self.fixture
        self.workflow, self.dev = evidence(self.root, f.fixture.plan, {
            "features": {"directory": f.features["directory"]},
            "labels": {"directory": f.labels["directory"]}})
        self.run = self.root/"run"
        v.freeze_policy(self.workflow, self.dev, self.run)

    def test_real_temporary_artifacts_outer_exports_and_single_access(self):
        import duckdb
        from modules.module_2 import data_adapter as a
        before = {name: t._hash(Path(r["directory"])/r["database"])
                  for name, r in (("features", self.fixture.features), ("labels", self.fixture.labels))}
        training = v.export_partition(self.run, "train")
        self.assertEqual(training["scoring_end"], "2026-04-24")
        self.assertEqual(training["outcome_end"], "2026-05-01")
        self.assertFalse((self.run/"validation-inputs").exists())
        with patch.object(a, "_query", side_effect=AssertionError("Read labels before freeze")):
            with self.assertRaises(ValueError):
                v.export_partition(self.run, "validation")
        synthetic_candidate(self.run)
        validation = v.export_partition(self.run, "validation")
        self.assertEqual(validation["scoring_start"], "2026-05-02")
        self.assertEqual(validation["scoring_end"], "2026-05-24")
        self.assertEqual(validation["outcome_end"], "2026-05-31")
        self.assertLess(validation["rows"], validation["candidate_rows"])
        with duckdb.connect() as con:
            train = con.execute("SELECT * FROM read_parquet(?)", [str(self.run/"train-inputs/data.parquet")]).df()
            valid = con.execute("SELECT * FROM read_parquet(?)", [str(self.run/"validation-inputs/data.parquet")]).df()
        self.assertTrue(train.inventory_observed.isna().any())
        # This fixture depletes after the scoring window, not on those days.
        self.assertTrue(valid.oos_observed.eq(False).all())
        self.assertFalse(valid.duplicated(v.KEYS).any())
        self.assertTrue(valid.scoring_date.between(pd.Timestamp("2026-05-02"), pd.Timestamp("2026-05-24")).all())
        self.assertEqual(set(valid.target_oos_7d.unique()), {0, 1})
        with self.assertRaises(FileExistsError):
            v.export_partition(self.run, "validation")
        self.assertEqual(before, {name: t._hash(Path(r["directory"])/r["database"])
                  for name, r in (("features", self.fixture.features), ("labels", self.fixture.labels))})
        # The ordinary development API is deliberately not broadened.
        with self.assertRaises(ValueError):
            a.load_development_partition(self.fixture.features["directory"], self.fixture.labels["directory"],
                fold_id="rolling_validation", partition="train", predictors=list(t.FEATURES))

    def test_changed_source_after_freeze_retains_failed_access_attempt(self):
        v.export_partition(self.run, "train")
        synthetic_candidate(self.run)
        p, _ = v._policy(self.run)
        p["source_artifacts"]["features"]["artifact_id"] = "changed"
        t._save(self.run/"policy/frozen-policy.json", p)
        # Rebind the synthetic guard fixture, then fail real source verification.
        candidate = self.run/"candidate/manifest.json"
        t._save(candidate, {**v._read(candidate), "policy_sha256": v._policy(self.run)[1]})
        with self.assertRaisesRegex(ValueError, "source changed"):
            v.export_partition(self.run, "validation")
        self.assertEqual(v._read(self.run/"validation-inputs/manifest.json")["status"], "Failed")
        self.assertTrue((self.run/"access/outer-validation.json").exists())
        with self.assertRaises(FileExistsError):
            v.export_partition(self.run, "validation")


@unittest.skipUnless(HAS_ML, "Requires the declared existing LightGBM/sklearn/pyarrow runtime")
class CandidateTests(unittest.TestCase):
    # Reuse setup/helpers, without duplicating inherited policy test executions.
    setUp = PolicyTests.setUp
    freeze = PolicyTests.freeze

    def train_inputs(self):
        self.freeze()
        p, phash = v._policy(self.run)
        data = frame(240, "2026-04-24")
        folder = self.run/"train-inputs"; folder.mkdir()
        data.to_parquet(folder/"data.parquet", index=False)
        t._save(folder/"manifest.json", {"version": "module2.outer_partition.v1", "status": "Accepted",
            "partition": "train", "policy_sha256": phash, "test_access": "Locked",
            "file": "data.parquet", "data_sha256": t._hash(folder/"data.parquet"),
            "rows": len(data), "candidate_rows": len(data), "scoring_end": "2026-04-24"})
        return p, phash

    def validation_inputs(self, phash):
        _, chash = v._candidate(self.run, phash)
        data = frame(90, "2026-05-02", seed=777)
        data["channel"] = "unseen_validation_category"
        folder = self.run/"validation-inputs"; folder.mkdir()
        data.to_parquet(folder/"data.parquet", index=False)
        record = {"version": "module2.outer_partition.v1", "status": "Accepted", "partition": "validation",
            "policy_sha256": phash, "candidate_manifest_sha256": chash, "test_access": "Locked",
            "file": "data.parquet", "data_sha256": t._hash(folder/"data.parquet"), "rows": len(data),
            "candidate_rows": len(data)+10}
        t._save(folder/"manifest.json", record)
        t._save(self.run/"access/outer-validation.json", record)

    def test_actual_candidate_fit_then_frozen_predict_only_evaluation(self):
        import lightgbm
        _, phash = self.train_inputs()
        v.fit_candidate(self.run)
        self.assertFalse((self.run/"validation-inputs").exists())
        self.assertEqual(v._read(self.run/"candidate/categories.json")["channel"], ["known"])
        frozen_model = t._hash(self.run/"candidate/model.txt")
        self.validation_inputs(phash)
        with patch.object(t, "choose_threshold", side_effect=AssertionError("Retuning")), \
             patch.object(t, "threshold_policy", side_effect=AssertionError("Retuning")), \
             patch.object(lightgbm.LGBMClassifier, "fit", side_effect=AssertionError("Refitting")):
            report = v.evaluate_candidate(self.run)
        self.assertEqual(report["status"], "Evaluated_not_promoted")
        self.assertFalse(report["numeric_gate_passed"])
        self.assertEqual(report["test_access"], "Locked")
        self.assertEqual(report["publication"], "Not_authorized")
        self.assertEqual(report["unknown_segment_rows"], 90)
        self.assertEqual(report["label_coverage"], .9)
        predictions = pd.read_parquet(self.run/"evaluation/validation-predictions.parquet")
        self.assertTrue(predictions.threshold.eq(.8).all())
        groups = [r for r in report["results"] if r["scope"].startswith("current_")]
        self.assertEqual(sum(r["rows"] for r in groups), 90)
        self.assertEqual(t._hash(self.run/"candidate/model.txt"), frozen_model)
        with self.assertRaises(FileExistsError):
            v.evaluate_candidate(self.run)
        with self.assertRaises(FileExistsError):
            v.fit_candidate(self.run)

    def test_mixed_candidate_and_corrupted_model_refused(self):
        _, phash = self.train_inputs()
        v.fit_candidate(self.run)
        self.validation_inputs(phash)
        path = self.run/"access/outer-validation.json"
        t._save(path, {**v._read(path), "candidate_manifest_sha256": "wrong"})
        with self.assertRaisesRegex(ValueError, "different frozen candidate"):
            v.evaluate_candidate(self.run)
        (self.run/"candidate/model.txt").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "frozen candidate"):
            v.evaluate_candidate(self.run)
        self.assertFalse((self.run/"evaluation").exists())

    def test_failed_evaluation_is_retained_not_retried(self):
        import lightgbm
        _, phash = self.train_inputs()
        v.fit_candidate(self.run)
        self.validation_inputs(phash)
        with patch.object(lightgbm.Booster, "predict", side_effect=RuntimeError("synthetic predict failure")):
            with self.assertRaises(RuntimeError):
                v.evaluate_candidate(self.run)
        self.assertEqual(v._read(self.run/"evaluation/report.json")["status"], "Failed")
        with self.assertRaises(FileExistsError):
            v.evaluate_candidate(self.run)


if __name__ == "__main__":
    unittest.main()
