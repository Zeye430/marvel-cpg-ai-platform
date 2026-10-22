"""Daily batch: durable completion records, safe resume and same-date source corrections."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

HAS_ML = all(importlib.util.find_spec(n) for n in ("sklearn", "pyarrow"))
if HAS_ML:
    from data_platform.workflow import build_all
    from modules.module_2 import delivery, inputs, monitoring, publishing, ranking, scoring, synthetic
    from modules.module_2.contracts import sha256_file, write_json
    from modules.module_2.training import segment_keys


class StubBundle:
    """Deterministic frozen-model stand-in; every downstream step is the real code."""

    def __init__(self, directory, source, sha):
        self.directory = Path(directory)
        self.manifest = {"model_version": "m2-stub", "feature_version": "oos_features.v1-complete", "algorithm": "stub",
                         "candidate_id": "stub-000", "source": source,
                         "components": {"drift_reference": {"path": "drift-reference.json"}}}
        self.manifest_sha256 = sha

    def predict(self, frame):
        cover = pd.to_numeric(frame["days_cover_observed"], errors="coerce").fillna(3.0).to_numpy(float)
        p = 1 / (1 + np.exp(cover - 4))
        return {"raw": p, "probability": p, "threshold": np.full(len(frame), 0.5),
                "route": np.full(len(frame), "global_fallback", dtype=object),
                "segment": segment_keys(frame).to_numpy(), "decision": p >= 0.5}

    def eligibility(self):
        return {"status": "Validation_passed"}


def build_workflow(root, seed):
    synthetic.generate(root / "raw", stores_per_segment=1, skus=3, seed=seed)
    write_json(root / "scope0.json", {"version": "none", "assignments": []})
    build_all(date(2026, 6, 30), root / "workflow", source_dir=root / "raw", snapshot_root=root / "snapshots",
              access_policy=root / "scope0.json", rolling_evaluation=True, assume_complete_snapshot=True, threads=1,
              memory_limit="512MB")
    return next((root / "workflow" / "runs").glob("*/run.json"))


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class DailyResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.workflow_a = build_workflow(cls.base / "a", seed=11)
        cls.workflow_b = build_workflow(cls.base / "b", seed=12)            # same date, corrected source

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        run = self.run_dir = Path(temporary.name) / "run"
        plan = inputs.load_workflow(self.workflow_a)["plan"].to_dict()
        source = {"dataset_hash": plan["source"]["dataset_hash"], "source_build_id": plan["source"]["source_build_id"],
                  "plan_sha256": plan["plan_sha256"]}
        write_json(run / "config.json", {"synthetic": True, "plan": plan})
        write_json(run / "benchmark" / "report.json", {"run_id": "stub", "trials": [], "status": "Complete",
                                                       "plan_sha256": plan["plan_sha256"],
                                                       "selection": {"winner": "stub-000"}})
        write_json(run / "inputs" / "development" / "manifest.json", {"files": {}})
        write_json(run / "bundle" / "bundle.json", {"model_version": "m2-stub", "candidate_id": "stub-000",
                                                    "algorithm": "stub", "params": {}, "fit_seconds": 1.0,
                                                    "source": source, "temporal_plan": plan,
                                                    "selection": {"benchmark_report_sha256":
                                                                  sha256_file(run / "benchmark" / "report.json")}})
        write_json(run / "bundle" / "eligibility.json", {"status": "Validation_passed", "history": []})
        write_json(run / "bundle" / "drift-reference.json", {"rows": 10, "numeric": {}, "categorical": {}, "segment_mix": {}})
        sha = sha256_file(run / "bundle" / "bundle.json")
        result = {"group_kind": "all", "group_id": "all", "rows": 1000, "positives": 100, "tp": 80, "fp": 20, "fn": 20,
                  "tn": 880, "alerts": 100, "precision": 0.8, "recall": 0.8, "roc_auc": 0.9, "average_precision": 0.7,
                  "brier": 0.05, "ece": 0.01, "meets_numeric_targets": True}
        write_json(run / "evaluation" / "validation" / "report.json", {
            "version": "module2.evaluation_report.v2", "kind": "iterative_validation", "status": "Evaluated",
            "bundle_manifest_sha256": sha, "model_version": "m2-stub", "algorithm": "stub", "candidate_id": "stub-000",
            **source,
            "eligible_rows": 1000, "candidate_rows": 2000, "numeric_gate_passed": True, "results": [result],
            "window": {"scoring_start": "2026-05-02", "scoring_end": "2026-05-24", "outcome_end": "2026-05-31"},
            "limitations": ["synthetic"]})
        self.stub = StubBundle(run / "bundle", source, sha)
        for target in (delivery.bundles, scoring.bundles):
            patcher = patch.object(target, "load_bundle", lambda directory, **kwargs: self.stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def state(self):
        return json.loads((self.run_dir / "daily-state.json").read_text())

    def issued(self):
        return monitoring.issued_history(self.run_dir / "issued-history")[0]

    def publications(self):
        return sorted((self.run_dir / "serving" / "published" / "history").glob("*.json"))

    def test_empty_scoring_directory_is_not_completion_and_completion_is_idempotent(self):
        label_dirs = self.run_dir / "scoring"
        label_dirs.mkdir(parents=True)
        (label_dirs / "2026-06-30").mkdir()                        # empty leftover directory
        first = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(first["status"], "Processed")
        again = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(again["status"], "No_op_already_processed")
        self.assertEqual((len(self.issued()), len(self.publications())), (1, 1))

    def test_failure_between_scoring_and_ranking_resumes_without_rescoring(self):
        with patch.object(delivery.ranking, "rank", side_effect=RuntimeError("injected crash")):
            with self.assertRaises(RuntimeError):
                delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        record = next(iter(self.state()["runs"].values()))
        self.assertEqual(record["status"], "Failed")
        scored = record["steps"]["scoring"]
        result = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(result["status"], "Processed")
        self.assertEqual(result["scoring"]["manifest_sha256"], scored["manifest_sha256"])
        self.assertEqual(len([d for d in (self.run_dir / "scoring").iterdir()]), 1)
        self.assertEqual((len(self.issued()), len(self.publications())), (1, 1))

    def test_failure_after_issue_does_not_duplicate_issued_history(self):
        with patch.object(delivery.monitoring, "monitor", side_effect=RuntimeError("injected crash")):
            with self.assertRaises(RuntimeError):
                delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(len(self.issued()), 1)
        delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual((len(self.issued()), len(self.publications())), (1, 1))

    def test_crash_after_publication_adopts_it_instead_of_publishing_twice(self):
        real = publishing.publish_candidate

        def publish_then_crash(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError("crash after publication, before the completion record")
        with patch.object(delivery.publishing, "publish_candidate", side_effect=publish_then_crash):
            with self.assertRaises(RuntimeError):
                delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(len(self.publications()), 1)
        result = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(result["status"], "Processed")
        self.assertEqual(len(self.publications()), 1)

    def test_tampered_completed_package_is_not_trusted(self):
        delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        record = next(iter(self.state()["runs"].values()))
        ranking_dir = Path(record["steps"]["ranking"]["dir"])
        (ranking_dir / "ranking.parquet").write_bytes(b"tampered")
        result = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(result["status"], "Processed")
        self.assertNotEqual(result["ranking"]["dir"], str(ranking_dir))
        self.assertEqual(len(self.issued()), 1)

    def completed_record(self):
        delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        return next(iter(self.state()["runs"].values()))

    def test_reviewer_probe_existing_empty_directory_is_not_completion(self):
        empty = self.run_dir / "empty-artifact"
        empty.mkdir(parents=True)
        probe = {"status": "Completed", "steps": {"publication": {"publication_id": "missing", "artifact": str(empty)}}}
        self.assertFalse(delivery._completed(probe, self.run_dir))
        record = self.completed_record()
        self.assertTrue(delivery._completed(record, self.run_dir))
        for broken in ({**record, "required_steps": None}, {**record, "status": "Running"},
                       {**record, "steps": {k: v for k, v in record["steps"].items() if k != "ranking"}},
                       {**record, "key": {**record["key"], "dataset_hash": "0" * 64}}):
            self.assertFalse(delivery._completed(broken, self.run_dir))

    def test_corrupt_published_database_is_recovered_not_trusted(self):
        record = self.completed_record()
        old = record["steps"]["publication"]
        database = Path(old["artifact"]) / "business.duckdb"
        database.write_bytes(database.read_bytes() + b"corrupt")
        self.assertFalse(delivery._completed(record, self.run_dir))
        result = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        self.assertEqual(result["status"], "Processed")
        self.assertNotEqual(result["serving_candidate"]["publication_id"], old["publication_id"])
        state = next(iter(self.state()["runs"].values()))
        self.assertEqual(state["recoveries"][0]["previous_publication_id"], old["publication_id"])
        self.assertEqual(len(self.issued()), 1)                              # no duplicate issued history
        self.assertEqual(result["scoring"]["manifest_sha256"], record["steps"]["scoring"]["manifest_sha256"])
        self.assertEqual(delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)["status"],
                         "No_op_already_processed")

    def test_corrupt_serving_manifest_or_missing_history_is_not_completion(self):
        record = self.completed_record()
        artifact = Path(record["steps"]["publication"]["artifact"])
        quality = artifact / "quality.json"
        quality.write_text(quality.read_text() + " ")
        self.assertFalse(delivery._completed(record, self.run_dir))
        quality.write_text(quality.read_text()[:-1])
        self.assertTrue(delivery._completed(record, self.run_dir))
        history = self.run_dir / "serving" / "published" / "history" / f"{record['steps']['publication']['publication_id']}.json"
        history.rename(history.with_suffix(".moved"))
        self.assertFalse(delivery._completed(record, self.run_dir))

    def test_publication_of_other_packages_is_not_adopted(self):
        record = self.completed_record()
        other = dict(record)
        other["steps"] = dict(record["steps"])
        other["steps"]["health"] = {**record["steps"]["health"], "manifest_sha256": "0" * 64}
        self.assertFalse(delivery._completed(other, self.run_dir))
        self.assertIsNone(delivery.publication_valid(self.run_dir / "serving", record["steps"]["publication"]["publication_id"],
                                                     "0" * 64, record["steps"]["health"]["manifest_sha256"]))

    def test_same_date_corrected_source_is_a_new_key_that_supersedes(self):
        first = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_a)
        corrected = delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_b)
        self.assertEqual(corrected["status"], "Processed")
        self.assertNotEqual(corrected["key_id"], first["key_id"])
        self.assertEqual(corrected["supersedes"], [first["key_id"]])
        self.assertEqual(corrected["scoring_date"], first["scoring_date"])
        self.assertEqual(len(self.issued()), 2)                   # both issued, explicitly linked as a correction
        self.assertEqual(delivery.daily(run_dir=self.run_dir, workflow_run=self.workflow_b)["status"],
                         "No_op_already_processed")


if __name__ == "__main__":
    unittest.main()
