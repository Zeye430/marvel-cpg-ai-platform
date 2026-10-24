"""Plan-aware observed development truth; every source is a temporary fixture."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import duckdb

from data_platform.__main__ import main
from data_platform.artifacts import verified_artifact
from data_platform.ingestion import digest
from data_platform.labels import build_labels, FOLDS
from data_platform.pipeline import build
from data_platform.temporal_plan import TemporalPlan, plan_evaluation
import test_data_platform as fixtures


class RollingFoldTests(unittest.TestCase):
    def plan(self, cutoff, history=date(2025, 1, 1)):
        return TemporalPlan(cutoff, history, "a" * 64, "synthetic", "b" * 64)

    def test_relative_folds_and_plan_identity_stable(self):
        cases = {
            date(2026, 6, 30): ("2026-04-24", "2026-05-02", "2026-05-24", "2026-05-31"),
            date(2026, 8, 31): ("2026-06-25", "2026-07-03", "2026-07-25", "2026-08-01"),
            date(2026, 6, 15): ("2026-04-09", "2026-04-17", "2026-05-09", "2026-05-16"),
        }
        for d, expected in cases.items():
            with self.subTest(cutoff=d):
                plan = self.plan(d)
                before = plan.to_dict()
                folds = plan.development_folds()
                self.assertEqual([f[0] for f in folds], ["rolling_dev_1", "rolling_dev_2", "rolling_validation"])
                self.assertEqual(folds[-1][1:], expected)
                self.assertEqual(plan.to_dict(), before)
                for _, train, start, last, outcome in folds:
                    parse = date.fromisoformat
                    self.assertLess(parse(train) + timedelta(days=7), parse(start))
                    self.assertEqual(parse(last) + timedelta(days=7), parse(outcome))
                    self.assertEqual((parse(outcome) - parse(start)).days + 1, 30)
                self.assertLess(date.fromisoformat(folds[1][-1]), date.fromisoformat(folds[2][2]))

    def test_insufficient_inner_history_does_not_move_outer_fold(self):
        full = self.plan(date(2026, 6, 30)).development_folds()
        minimal = self.plan(date(2026, 6, 30), date(2026, 4, 24)).development_folds()
        self.assertEqual(minimal, (full[-1],))
        near_date_min = self.plan(date(1, 3, 9), date.min).development_folds()
        self.assertEqual(len(near_date_min), 1)
        self.assertEqual(near_date_min[0][0], "rolling_validation")


class RollingLabelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlatformTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.root
        self.missing = date(2026, 5, 12)
        d, inventory = date(2026, 1, 1), 150
        while d <= date(2026, 6, 30):
            refill = 7 if d == date(2026, 6, 2) else 0
            inventory += refill
            sold = min(1, inventory)
            inventory -= sold
            if d != self.missing:
                self.fixture.rows["fact_sell_out"].append(
                    self.fixture.sellout(str(d), sold, inventory, refill, "D-FUTURE" if refill else "")
                )
            d += timedelta(days=1)
        self.fixture.write_sources()
        self.source = build(replace(self.fixture.config, as_of=date(2026, 6, 30), include_kpi_marts=True))
        self.source_dir = Path(self.source["database"]).parent
        self.plan = plan_evaluation(self.source_dir)

    def labels(self, name="rolling", **kwargs):
        return build_labels(self.source_dir, date(2026, 5, 31), self.root/name,
                            temporal_plan=self.plan, threads=1, memory_limit="256MB", **kwargs)

    def fingerprints(self):
        return {name: digest(self.source_dir/name) for name in ("run.json", "quality.json", "build.duckdb")}

    def test_actual_build_embeds_exact_plan_and_excludes_reserved_test_truth(self):
        before = self.fingerprints()
        result = self.labels()
        record, db, cutoff = verified_artifact(result["directory"], kind="labels")
        self.assertEqual(record["temporal_plan"], self.plan)
        self.assertEqual(cutoff, date(2026, 5, 31))
        self.assertEqual(record["folds"], [list(row) for row in TemporalPlan.from_dict(self.plan).development_folds()])
        with duckdb.connect(str(db), read_only=True) as con:
            folds = con.execute("SELECT fold_id,cast(train_last AS VARCHAR),cast(validation_start AS VARCHAR),cast(validation_end AS VARCHAR),cast(outcome_end AS VARCHAR) FROM evaluation.temporal_folds ORDER BY validation_start").fetchall()
            self.assertEqual(folds, list(TemporalPlan.from_dict(self.plan).development_folds()))
            self.assertEqual(con.execute("SELECT bool_and(outcomes_available) FROM evaluation.temporal_folds").fetchone(), (True,))
            actual = con.execute("""SELECT partition,min(scoring_date),max(scoring_date)
                FROM evaluation.fold_samples WHERE fold_id='rolling_validation' AND eligible
                GROUP BY partition ORDER BY partition""").fetchall()
            # A currently unreported day can still have seven observed future
            # days; label eligibility must not silently require today's report.
            self.assertEqual(actual, [("train", date(2025, 12, 31), date(2026, 4, 24)),
                                      ("validation", date(2026, 5, 2), date(2026, 5, 24))])
            self.assertEqual(con.execute("""SELECT observed_future_days,target_oos_7d FROM evaluation.labels_oos_next7
                WHERE scoring_date='2025-12-31'""").fetchone(), (7, 0))
            self.assertEqual(con.execute("SELECT max(scoring_date) FROM evaluation.labels_oos_next7").fetchone(), (date(2026, 5, 31),))
            schemas = {r[0] for r in con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()}
            self.assertTrue(schemas.isdisjoint({"gold", "silver", "bronze", "feature_input", "serving"}))
            for filename in ("calendar.parquet", "observations.parquet"):
                path = str(Path(result["directory"])/filename)
                self.assertLessEqual(con.execute("SELECT max(date) FROM read_parquet(?)", [path]).fetchone()[0], date(2026, 5, 31))
        self.assertEqual(self.fingerprints(), before)

    def test_partial_immature_and_complete_positive_labels(self):
        result = self.labels()
        with duckdb.connect(str(Path(result["directory"])/"labels.duckdb"), read_only=True) as con:
            partial = con.execute("SELECT target_oos_7d,observed_future_days,label_source FROM evaluation.labels_oos_next7 WHERE scoring_date='2026-05-08'").fetchone()
            self.assertEqual(partial, (None, 6, "partial_window_unknown"))
            self.assertEqual(con.execute("SELECT target_oos_7d FROM evaluation.labels_oos_next7 WHERE scoring_date='2026-05-24'").fetchone(), (1,))
            self.assertEqual(con.execute("SELECT count(target_oos_7d) FROM evaluation.labels_oos_next7 WHERE scoring_date>='2026-05-25'").fetchone(), (0,))

    def test_midmonth_source_build_uses_its_own_relative_dates(self):
        source = build(replace(self.fixture.config, as_of=date(2026, 6, 15), include_kpi_marts=True))
        source_dir = Path(source["database"]).parent
        plan = plan_evaluation(source_dir)
        result = build_labels(source_dir, date(2026, 5, 16), self.root/"midmonth-labels",
                              temporal_plan=plan, threads=1, memory_limit="256MB")
        record, db, cutoff = verified_artifact(result["directory"], kind="labels")
        self.assertEqual(cutoff, date(2026, 5, 16))
        self.assertEqual(record["temporal_plan"], plan)
        with duckdb.connect(str(db), read_only=True) as con:
            self.assertEqual(con.execute("""SELECT train_last,validation_start,validation_end,outcome_end
                FROM evaluation.temporal_folds WHERE fold_id='rolling_validation'""").fetchone(),
                (date(2026, 4, 9), date(2026, 4, 17), date(2026, 5, 9), date(2026, 5, 16)))

    def test_late_posted_training_truth_is_not_eligible(self):
        # Deliberate temporary fixture variation; never mutate real accepted data.
        with duckdb.connect(self.source["database"]) as con:
            con.execute("UPDATE silver.fact_sell_out SET source_known_at=TIMESTAMP '2026-05-03' WHERE date='2026-04-27'")
        self.source["database_sha256"] = digest(self.source["database"])
        (self.source_dir/"run.json").write_text(json.dumps(self.source, default=str))
        self.plan = plan_evaluation(self.source_dir)
        result = self.labels()
        with duckdb.connect(str(Path(result["directory"])/"labels.duckdb"), read_only=True) as con:
            self.assertEqual(con.execute("""SELECT target_oos_7d,eligible FROM evaluation.fold_samples
                WHERE fold_id='rolling_validation' AND scoring_date='2026-04-24'""").fetchone(), (0, False))
            self.assertEqual(con.execute("""SELECT count(*) FROM evaluation.fold_samples s
                JOIN evaluation.temporal_folds f USING(fold_id)
                WHERE eligible AND partition='train' AND
                  (label_window_end>=validation_start OR outcome_known_at>=cast(validation_start AS TIMESTAMP))""").fetchone(), (0,))

    def test_wrong_plan_or_cutoff_fails_before_source_sql_or_output(self):
        original = TemporalPlan.from_dict(self.plan)
        bad_plans = [replace(original, **change).to_dict() for change in (
            {"source_build_id": "other"}, {"dataset_hash": "a"*64},
            {"source_database_sha256": "b"*64}, {"history_start": date(2025, 1, 2)},
            {"source_as_of": date(2026, 6, 29)},
        )]
        tampered = deepcopy(self.plan); tampered["test_access"] = "Approved"; bad_plans.append(tampered)
        for index, plan in enumerate(bad_plans):
            output = self.root/f"invalid-{index}"
            with self.subTest(index=index), patch("duckdb.connect", side_effect=AssertionError("source SQL opened")), self.assertRaises(ValueError):
                build_labels(self.source_dir, date(2026, 5, 31), output, temporal_plan=plan)
            self.assertFalse(output.exists())
        output = self.root/"wrong-cutoff"
        with patch("duckdb.connect", side_effect=AssertionError("source SQL opened")), self.assertRaises(ValueError):
            build_labels(self.source_dir, date(2026, 5, 30), output, temporal_plan=self.plan)
        self.assertFalse(output.exists())

    def test_plan_cannot_unlock_test_even_with_legacy_evidence(self):
        output = self.root/"denied-test"
        with patch("duckdb.connect", side_effect=AssertionError("source SQL opened")), self.assertRaisesRegex(ValueError, "locked"):
            build_labels(self.source_dir, date(2026, 6, 30), output, purpose="test",
                         temporal_plan=self.plan, test_release="not-read.json")
        self.assertFalse(output.exists())

    def test_artifact_metadata_tampering_never_falls_back_to_legacy(self):
        result = self.labels()
        manifest = Path(result["directory"])/"run.json"
        mutations = []
        for key, value in (("temporal_plan", None), ("purpose", "test"), ("restricted", 1),
                           ("source_build_id", "other"), ("source_database_sha256", "a"*64),
                           ("dataset_hash", "b"*64), ("fold_policy_version", "unknown"),
                           ("folds", []), ("test_release", {"fake": True}),
                           ("as_of", "2026-05-30"), ("label_version", "unknown")):
            record = deepcopy(result); record[key] = value; mutations.append(record)
        record = deepcopy(result); del record["temporal_plan"]; mutations.append(record)
        for record in mutations:
            manifest.write_text(json.dumps(record, default=str))
            with self.subTest(keys=record), self.assertRaises(ValueError):
                verified_artifact(result["directory"], kind="labels")

    def test_cli_plan_file_and_legacy_calls(self):
        plan_path = self.root/"temporal-plan.json"
        plan_path.write_text(json.dumps(self.plan))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["build-labels", "--source-build-dir", str(self.source_dir),
                         "--as-of", "2026-05-31", "--output-root", str(self.root/"cli-labels"),
                         "--threads", "1", "--memory-limit", "256MB", "--temporal-plan", str(plan_path)])
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads(out.getvalue())["temporal_plan"], self.plan)
        plan_path.write_text("invalid JSON")
        for path in (plan_path, self.root/"missing-plan.json"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["build-labels", "--source-build-dir", str(self.source_dir),
                             "--as-of", "2026-05-31", "--output-root", str(self.root/"invalid-cli"),
                             "--temporal-plan", str(path)])
            self.assertEqual(code, 1)
            self.assertFalse((self.root/"invalid-cli").exists())
        legacy = build_labels(self.source_dir, date(2026, 5, 31), self.root/"legacy-labels", threads=1, memory_limit="256MB")
        self.assertNotIn("temporal_plan", legacy)
        verified_artifact(legacy["directory"], kind="labels")
        with duckdb.connect(str(Path(legacy["directory"])/"labels.duckdb"), read_only=True) as con:
            ids = {r[0] for r in con.execute("SELECT fold_id FROM evaluation.temporal_folds").fetchall()}
            self.assertEqual(ids, {row[0] for row in FOLDS})


if __name__ == "__main__":
    unittest.main()
