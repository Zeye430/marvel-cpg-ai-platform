"""Plan-bound test labels through the v2 gate and ledger, on temporary synthetic sources."""

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import duckdb

import module2_release_fixtures as fx
import test_rolling_labels as fixtures
from data_platform.artifacts import verified_artifact
from data_platform.labels import build_labels
from data_platform.pipeline import build
from data_platform.temporal_plan import TemporalPlan, plan_evaluation
from data_platform import test_exposure as ledger


class PlannedTestLabelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RollingLabelTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.root
        self.plan = self.fixture.plan
        self.ledger_path = self.root / "governance" / "ledger.jsonl"
        self.header = ledger.initialize_test_exposure_ledger(
            self.ledger_path, initialized_by="synthetic test", initialized_at=datetime.now(timezone.utc).isoformat(),
            history_note="SYNTHETIC", prior_exposures=[])

    def release(self, name="evidence", plan=None, **kwargs):
        return fx.build(self.root / name, plan or self.plan, self.header["ledger_id"], **kwargs)

    def labels(self, release, name="test-labels", plan=None, source=None, **kwargs):
        p = plan or self.plan
        return build_labels(source or self.fixture.source_dir, TemporalPlan.from_dict(p).source_as_of, self.root / name,
                            purpose="test", temporal_plan=p, test_release=release,
                            test_exposure_ledger=self.ledger_path, threads=1, memory_limit="256MB", **kwargs)

    def test_test_only_fold_pending_null_and_isolated_projection(self):
        before = self.fixture.fingerprints()
        result = self.labels(self.release())
        record, db, cutoff = verified_artifact(result["directory"], kind="labels")
        self.assertEqual(cutoff, date(2026, 6, 30))
        self.assertEqual(record["folds"], [["rolling_test", None, "2026-06-01", "2026-06-23", "2026-06-30"]])
        self.assertEqual(record["fold_policy_version"], "trailing30.test_only.v1")
        self.assertEqual(record["test_exposure"]["artifact_id"], record["artifact_id"])
        with duckdb.connect(str(db), read_only=True) as con:
            self.assertEqual(con.execute("SELECT DISTINCT partition FROM evaluation.fold_samples").fetchall(), [("test",)])
            self.assertEqual(con.execute("SELECT min(scoring_date),max(scoring_date) FROM evaluation.labels_oos_next7").fetchone(),
                             (date(2026, 6, 1), date(2026, 6, 30)))
            self.assertEqual(con.execute("SELECT count(target_oos_7d) FROM evaluation.labels_oos_next7 WHERE scoring_date>'2026-06-23'").fetchone(), (0,))
            eligible = con.execute("SELECT min(scoring_date),max(scoring_date) FROM evaluation.fold_samples WHERE eligible").fetchone()
            self.assertGreaterEqual(eligible[0], date(2026, 6, 1)); self.assertLessEqual(eligible[1], date(2026, 6, 23))
            schemas = {r[0] for r in con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()}
            self.assertTrue(schemas.isdisjoint({"gold", "silver", "bronze", "feature_input", "serving"}))
            obs = str(Path(result["directory"]) / "observations.parquet")
            self.assertGreater(con.execute("SELECT min(date) FROM read_parquet(?)", [obs]).fetchone()[0], date(2026, 6, 1))
            cal = str(Path(result["directory"]) / "calendar.parquet")
            self.assertEqual(con.execute("SELECT min(date) FROM read_parquet(?)", [cal]).fetchone()[0], date(2026, 6, 1))
        self.assertEqual(self.fixture.fingerprints(), before)
        _, entries = ledger.read_ledger(self.ledger_path)
        self.assertEqual([e["artifact_id"] for e in entries], [record["artifact_id"]])

    def test_verification_is_repeatable_without_appending_and_refuses_tampering(self):
        release = self.release()
        result = self.labels(release)
        size = self.ledger_path.stat().st_size
        for _ in range(3):
            verified_artifact(result["directory"], kind="labels")
        self.assertEqual(self.ledger_path.stat().st_size, size)
        report = release.parent / json.loads(release.read_text())["evidence"]["validation_report"]["path"]
        report.write_text(report.read_text() + " ")
        with self.assertRaises(ValueError):
            verified_artifact(result["directory"], kind="labels")

    def test_second_primary_build_is_refused_before_truth_sql(self):
        self.labels(self.release())
        second = self.release("evidence-2")
        with patch("duckdb.connect", side_effect=AssertionError("truth SQL opened")):
            with self.assertRaises(ValueError):
                self.labels(second, name="second")

    def test_missing_ledger_or_release_refuses_before_sql(self):
        release = self.release()
        with patch("duckdb.connect", side_effect=AssertionError("source SQL opened")):
            with self.assertRaises(ValueError):
                build_labels(self.fixture.source_dir, date(2026, 6, 30), self.root / "x", purpose="test",
                             temporal_plan=self.plan, test_release=release, threads=1)
            with self.assertRaises(ValueError):
                build_labels(self.fixture.source_dir, date(2026, 5, 31), self.root / "y", purpose="development",
                             temporal_plan=self.plan, test_exposure_ledger=self.ledger_path, threads=1)
            with self.assertRaises(ValueError):
                self.labels(self.release("bad", mutate=lambda n, r: n == "release" and r.update(status="Pending")), name="z")
        self.assertEqual(ledger.read_ledger(self.ledger_path)[1], [])

    def test_failure_after_reservation_keeps_the_exposure(self):
        with patch("data_platform.quality.Quality.require_pass", side_effect=ValueError("synthetic quality failure")):
            with self.assertRaises(ValueError):
                self.labels(self.release())
        _, entries = ledger.read_ledger(self.ledger_path)
        self.assertEqual(len(entries), 1)                                  # never refunded
        failed = json.loads(next((self.root / "test-labels" / "builds").glob("*/run.json")).read_text())
        self.assertEqual(failed["status"], "Failed")
        self.assertEqual(failed["test_exposure"]["sequence"], entries[0]["sequence"])

    def test_midmonth_source_uses_its_own_window(self):
        source = build(replace(self.fixture.fixture.config, as_of=date(2026, 6, 15), include_kpi_marts=True))
        source_dir = Path(source["database"]).parent
        plan = plan_evaluation(source_dir)
        result = self.labels(self.release("mid", plan=plan), name="mid-labels", plan=plan, source=source_dir)
        record, _, cutoff = verified_artifact(result["directory"], kind="labels")
        self.assertEqual(cutoff, date(2026, 6, 15))
        self.assertEqual(record["folds"], [["rolling_test", None, "2026-05-17", "2026-06-08", "2026-06-15"]])

    def test_build_all_still_never_produces_test_labels(self):
        from data_platform import workflow
        import inspect
        self.assertNotIn("purpose=\"test\"", inspect.getsource(workflow))


if __name__ == "__main__":
    unittest.main()
