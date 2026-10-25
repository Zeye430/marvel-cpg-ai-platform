"""Shared-plan feature/workflow integration using temporary synthetic data."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import duckdb

from data_platform.__main__ import main
from data_platform.access import ServingReader, DataUnavailable
from data_platform.artifacts import verified_artifact
from data_platform.feature_handoff import export_feature_input
from data_platform.feature_runner import build_features
from data_platform.ingestion import digest
from data_platform.temporal_plan import TemporalPlan
from data_platform.time_policy import validate_feature_cutoff
from data_platform.workflow import build_all
import test_rolling_labels as label_fixtures


class RollingFeatureWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Reuse the synthetic source setup, not another test's results/assertions.
        self.fixture = label_fixtures.RollingLabelTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.root
        self.source_dir = self.fixture.source_dir
        self.plan = self.fixture.plan
        self.config = self.fixture.fixture.config
        self.policy = self.root/"scope.json"
        self.policy.write_text(json.dumps({"version": "synthetic-rolling-v1", "assignments": [
            {"principal_id": "alice", "store_id": "S1", "role": "sales"},
        ]}))

    def features(self, name="rolling-features", **overrides):
        options = {"temporal_plan": self.plan, "assume_complete_snapshot": True,
                   "memory_limit": "256MB", "threads": 1, **overrides}
        return build_features(self.source_dir, date(2026, 5, 31), self.root/name, **options)

    def workflow(self, cutoff=date(2026, 6, 30), **overrides):
        options = {"source_dir": self.config.source_dir, "snapshot_root": self.config.snapshot_root,
                   "access_policy": self.policy, "rolling_evaluation": True,
                   "assume_complete_snapshot": True, "memory_limit": "256MB", "threads": 1, **overrides}
        return build_all(cutoff, self.root/"workflow", **options)

    def test_feature_plan_propagation_and_exact_legacy_numeric_parity(self):
        before = self.fixture.fingerprints()
        rolling = self.features()
        legacy = self.features("legacy-features", temporal_plan=None)
        record, db, _ = verified_artifact(rolling["directory"], kind="features")
        self.assertEqual(record["temporal_plan"], self.plan)
        self.assertEqual(record["projection"]["temporal_plan"], self.plan)
        self.assertEqual(record["source_database_sha256"], self.plan["source"]["source_database_sha256"])
        for component in ("state", "replenishment", "context"):
            self.assertIn(component, record)
        with duckdb.connect(str(db), read_only=True) as con, duckdb.connect(str(Path(legacy["directory"])/"features.duckdb"), read_only=True) as old:
            sql = "SELECT * FROM gold.features_oos_daily ORDER BY scoring_date,store_id,sku_id"
            self.assertEqual(con.execute(sql).fetchall(), old.execute(sql).fetchall())
            self.assertEqual(con.execute("SELECT max(scoring_date) FROM gold.features_oos_daily").fetchone(), (date(2026, 5, 31),))
            self.assertEqual(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='evaluation'").fetchone(), (0,))
        self.assertEqual(self.fixture.fingerprints(), before)

    def test_wrong_feature_plan_source_date_and_purpose_fail_without_sql(self):
        original = TemporalPlan.from_dict(self.plan)
        cases = [
            (date(2026, 5, 30), "development", self.plan),
            (date(2026, 6, 29), "scoring", self.plan),
            (date(2026, 5, 31), "test", self.plan),
            (date(2026, 5, 31), "development", replace(original, source_build_id="other").to_dict()),
        ]
        altered = deepcopy(self.plan); altered["test_access"] = "Approved"
        cases.append((date(2026, 5, 31), "development", altered))
        for index, (cutoff, purpose, plan) in enumerate(cases):
            for builder in ("complete", "projection"):
                output = self.root/f"invalid-{index}-{builder}"
                with self.subTest(index=index, builder=builder), patch("duckdb.connect", side_effect=AssertionError("SQL opened")), self.assertRaises(ValueError):
                    if builder == "complete":
                        build_features(self.source_dir, cutoff, output, purpose=purpose, temporal_plan=plan)
                    else:
                        export_feature_input(self.source_dir, output, cutoff, purpose=purpose, temporal_plan=plan)
                self.assertFalse(output.exists())

    def test_plan_feature_verification_rejects_root_and_projection_downgrades(self):
        result = self.features()
        path = Path(result["directory"])/"run.json"
        cases = []
        for key, value in (("purpose", "test"), ("as_of", "2026-05-30"),
                           ("source_build_id", "other"), ("source_database_sha256", "a"*64),
                           ("feature_version", "unknown"), ("folds", []), ("test_release", {})):
            r = deepcopy(result); r[key] = value; cases.append(r)
        for key in ("temporal_plan", "projection"):
            r = deepcopy(result); del r[key]; cases.append(r)
        for key, value in (("temporal_plan", None), ("source_build_id", "other"),
                           ("purpose", "scoring"), ("as_of", "2026-06-30")):
            r = deepcopy(result); r["projection"][key] = value; cases.append(r)
        for r in cases:
            path.write_text(json.dumps(r, default=str))
            with self.subTest(record=r), self.assertRaises(ValueError):
                verified_artifact(result["directory"], kind="features")

    def test_rolling_workflow_has_one_plan_and_latest_business_without_test(self):
        with patch("data_platform.test_release.authorize_test_release", side_effect=AssertionError("test release requested")):
            result = self.workflow()
        self.assertEqual(result["status"], "Accepted")
        self.assertEqual(result["evaluation_profile"], "trailing_30d")
        self.assertIn("Locked", result["test_status"])
        plan = result["temporal_plan"]
        saved = self.root/"workflow/runs"/result["run_id"]/"temporal-plan.json"
        self.assertEqual(json.loads(saved.read_text()), plan)
        c = result["components"]
        self.assertEqual(set(c), {"base", "features", "labels", "scoring_features", "serving"})
        for name, purpose, cutoff in (("features", "development", "2026-05-31"),
                                      ("labels", "development", "2026-05-31"),
                                      ("scoring_features", "scoring", "2026-06-30")):
            record, _, _ = verified_artifact(c[name]["directory"])
            self.assertEqual((record["purpose"], record["as_of"], record["temporal_plan"]), (purpose, cutoff, plan))
        self.assertEqual(c["serving"]["as_of"], "2026-06-30")
        reader = ServingReader(c["serving"]["directory"])
        rows = reader.read("store_sku_daily", "alice", start="2026-06-30", end="2026-06-30")
        self.assertEqual(len(rows), 1)
        with self.assertRaises(DataUnavailable):
            reader.read("oos_predictions", "alice")
        with duckdb.connect(str(Path(c["serving"]["directory"])/"business.duckdb"), read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('evaluation','feature_input')").fetchone(), (0,))

    def test_midmonth_workflow_and_unknown_latest_input_are_preserved(self):
        self.fixture.fixture.rows["fact_sell_out"] = [r for r in self.fixture.fixture.rows["fact_sell_out"] if r["date"] != "2026-06-15"]
        self.fixture.fixture.write_sources()
        result = self.workflow(date(2026, 6, 15))
        c = result["components"]
        self.assertEqual((c["features"]["as_of"], c["labels"]["as_of"]), ("2026-05-16", "2026-05-16"))
        self.assertEqual((c["scoring_features"]["as_of"], c["serving"]["as_of"]), ("2026-06-15", "2026-06-15"))
        with duckdb.connect(str(Path(c["scoring_features"]["directory"])/"features.duckdb"), read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*),count(inventory_observed) FROM gold.features_oos_daily WHERE scoring_date='2026-06-15'").fetchone(), (1, 0))

    def test_rolling_failure_leaves_prior_synthetic_publication_unchanged(self):
        self.workflow(publish=True)
        pointer = self.root/"workflow/serving/published/serving.json"
        before = pointer.read_bytes()
        with patch("data_platform.workflow.build_labels", side_effect=RuntimeError("synthetic label failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic label failure"):
                self.workflow(publish=True)
        self.assertEqual(pointer.read_bytes(), before)
        reports = [json.loads(p.read_text()) for p in (self.root/"workflow/runs").glob("*/run.json")]
        failed = [r for r in reports if r["status"] == "Failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["evaluation_profile"], "trailing_30d")
        self.assertIn("temporal_plan", failed[0])

    def test_legacy_profile_and_invalid_flag_keep_safe_boundaries(self):
        result = self.workflow(rolling_evaluation=False)
        self.assertEqual(result["evaluation_profile"], "legacy_fixed_v3")
        self.assertNotIn("temporal_plan", result)
        self.assertIn("june_test_status", result)
        self.assertNotIn("temporal_plan", result["components"]["features"])
        for value in (1, "true", None):
            output = self.root/f"invalid-flag-{value}"
            with self.subTest(flag=value), self.assertRaises(ValueError):
                build_all(date(2026, 6, 30), output, rolling_evaluation=value)
            self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "available"):
            validate_feature_cutoff(date(2026, 8, 31), "scoring", temporal_plan=self.plan)

    def test_short_history_retains_requested_profile_without_legacy_fallback(self):
        with self.assertRaisesRegex(ValueError, "Source metadata") as caught:
            self.workflow(date(2025, 1, 7))
        self.assertIn("Insufficient", str(caught.exception.__cause__))
        reports = [json.loads(p.read_text()) for p in (self.root/"workflow/runs").glob("*/run.json")]
        self.assertEqual(len(reports), 1)
        self.assertEqual((reports[0]["status"], reports[0]["evaluation_profile"]), ("Failed", "trailing_30d"))
        self.assertEqual(set(reports[0]["components"]), {"base"})
        self.assertFalse((self.root/"workflow/serving/published/serving.json").exists())

    def test_cli_plan_flags_and_rolling_dispatch(self):
        plan_path = self.root/"plan.json"
        plan_path.write_text(json.dumps(self.plan))
        for command, module, method in (("build-features", "feature_runner", "build_features"),
                                         ("export-feature-input", "feature_handoff", "export_feature_input")):
            with self.subTest(command=command), patch(f"data_platform.{module}.{method}", return_value={"status": "Accepted"}) as called:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main([command, "--source-build-dir", "source", "--as-of", "2026-05-31", "--temporal-plan", str(plan_path)])
                self.assertEqual(code, 0)
                self.assertEqual(called.call_args.kwargs["temporal_plan"], self.plan)
        with patch("data_platform.workflow.build_all", return_value={"status": "Accepted"}) as called:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["build-all", "--as-of", "2026-06-30", "--rolling-evaluation"])
            self.assertEqual(code, 0)
            self.assertIs(called.call_args.kwargs["rolling_evaluation"], True)
        plan_path.write_text("invalid JSON")
        with patch("data_platform.feature_runner.build_features") as called:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["build-features", "--source-build-dir", "source", "--as-of", "2026-05-31", "--temporal-plan", str(plan_path)])
            self.assertEqual(code, 1)
            called.assert_not_called()


if __name__ == "__main__":
    unittest.main()
