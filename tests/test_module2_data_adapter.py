"""Lightweight readers tested against real temporary shared-platform artifacts."""

import importlib
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from data_platform.feature_runner import build_features
from data_platform.ingestion import digest
from data_platform.labels import build_labels
from data_platform.pipeline import build
from data_platform.temporal_plan import TemporalPlan, plan_evaluation
from modules.module_2 import data_adapter as adapter
import test_rolling_labels as fixtures


class Module2ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.RollingLabelTests()
        cls.addClassCleanup(cls.fixture.doCleanups)
        cls.fixture.setUp()
        cls.labels = cls.fixture.labels()
        options = dict(temporal_plan=cls.fixture.plan, assume_complete_snapshot=True,
                       threads=1, memory_limit="256MB")
        cls.features = build_features(cls.fixture.source_dir, date(2026, 5, 31),
                                      cls.fixture.root/"reader-features", **options)
        cls.scoring = build_features(cls.fixture.source_dir, date(2026, 6, 30),
                                     cls.fixture.root/"reader-scoring", purpose="scoring", **options)
        cls.predictors = ["inventory_observed", "in_stock_velocity_28d", "scheduled_units_next7d"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def development(self, **overrides):
        options = dict(fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        options.update(overrides)
        return adapter.load_development_partition(self.features["directory"], self.labels["directory"], **options)

    def copy_artifact(self, record, name="copy"):
        destination = self.root/name
        shutil.copytree(record["directory"], destination)
        return destination

    def edit_record(self, directory, **changes):
        path = directory/"run.json"
        record = json.loads(path.read_text())
        record.update(changes)
        path.write_text(json.dumps(record))

    def edit_database(self, directory, sql):
        record = json.loads((directory/"run.json").read_text())
        database = directory/record["database"]
        with duckdb.connect(str(database)) as con:
            con.execute(sql)
        self.edit_record(directory, database_sha256=digest(database))

    def test_development_alignment_order_values_and_coverage(self):
        for fold in ("rolling_dev_1", "rolling_dev_2"):
            for partition in ("train", "validation"):
                with self.subTest(fold=fold, partition=partition):
                    batch = self.development(fold_id=fold, partition=partition)
                    self.assertGreater(len(batch.X), 0)
                    self.assertEqual(list(batch.X), self.predictors)
                    self.assertEqual(str(batch.y.dtype), "int64")
                    self.assertEqual(batch.y.name, "target_oos_7d")
                    self.assertEqual(len(batch.X), len(batch.y))
                    self.assertEqual(batch.metadata["returned_rows"], len(batch.X))
                    self.assertGreaterEqual(batch.metadata["candidate_rows"], len(batch.X))
                    self.assertFalse(batch.row_keys.duplicated(list(adapter.KEYS)).any())
                    for frame in (batch.row_keys, batch.X, batch.y, batch.context):
                        self.assertTrue(frame.index.equals(pd.RangeIndex(len(batch.X))))
                    sorted_keys = batch.row_keys.sort_values(["scoring_date", "store_id", "sku_id"]).reset_index(drop=True)
                    pd.testing.assert_frame_equal(batch.row_keys, sorted_keys)
                    with duckdb.connect(str(Path(self.features["directory"])/"features.duckdb"), read_only=True) as con:
                        actual = con.execute("SELECT inventory_observed FROM gold.features_oos_daily WHERE scoring_date=? AND store_id=? AND sku_id=?",
                                             batch.row_keys.iloc[0][["scoring_date", "store_id", "sku_id"]].tolist()).fetchone()[0]
                    observed = batch.X.iloc[0]["inventory_observed"]
                    self.assertTrue(pd.isna(observed) if actual is None else observed == actual)

    def test_current_missing_and_current_oos_are_not_filtered(self):
        train = self.development(partition="train")
        self.assertTrue(train.X["inventory_observed"].isna().any())
        latest = adapter.load_scoring_inputs(self.scoring["directory"], predictors=self.predictors)
        self.assertTrue(latest.context["oos_observed"].any())
        copied = self.copy_artifact(self.scoring)
        self.edit_database(copied, """UPDATE gold.features_oos_daily
            SET inventory_observed=NULL,inventory_data_status='Unknown'
            WHERE scoring_date=DATE '2026-06-30'""")
        missing = adapter.load_scoring_inputs(copied, predictors=self.predictors)
        self.assertEqual(len(missing.X), len(latest.X))
        self.assertTrue(missing.X["inventory_observed"].isna().all())

    def test_outer_test_legacy_and_bad_partition_refused_before_reads(self):
        for fold, partition in [("rolling_validation", "train"), ("june_test", "validation"),
                                ("march_development", "train"), ("rolling_dev_1", "test")]:
            with self.subTest(fold=fold, partition=partition), patch.object(adapter, "verified_artifact", side_effect=AssertionError("Opened artifact")):
                with self.assertRaises(ValueError):
                    self.development(fold_id=fold, partition=partition)

    def test_predictors_and_candidate_opt_in(self):
        for predictors in ([], "inventory_observed", {"inventory_observed"}, ["target_oos_7d"],
                           ["store_id"], ["inventory_observed", "inventory_observed"], ["1; DROP TABLE x"], [["nested"]]):
            with self.subTest(predictors=predictors), self.assertRaises(ValueError):
                self.development(predictors=predictors)
        for flag in (1, None, "true"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.development(include_candidate_estimates=flag)
        with self.assertRaises(ValueError):
            self.development(predictors=["inventory_candidate"])
        batch = self.development(predictors=["inventory_candidate", "inventory_observed"], include_candidate_estimates=True)
        self.assertEqual(list(batch.X), ["inventory_candidate", "inventory_observed"])
        self.assertTrue(batch.metadata["include_candidate_estimates"])

    def test_wrong_source_plan_cutoff_purpose_and_version_rejected(self):
        for field, value in (("source_build_id", "another"), ("purpose", "test"),
                             ("as_of", "2026-05-30"), ("feature_version", "unknown"),
                             ("temporal_plan", None)):
            with self.subTest(field=field):
                copied = self.copy_artifact(self.features, field)
                self.edit_record(copied, **{field: value})
                with self.assertRaises(ValueError):
                    adapter.load_development_partition(copied, self.labels["directory"],
                        fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        with self.assertRaises(ValueError):
            adapter.load_scoring_inputs(self.features["directory"], predictors=self.predictors)
        with self.assertRaises(ValueError):
            adapter.load_development_partition(self.scoring["directory"], self.labels["directory"],
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)

    def test_different_individually_valid_source_plan_rejected(self):
        copied = self.copy_artifact(self.labels)
        changed = replace(TemporalPlan.from_dict(self.fixture.plan), source_build_id="other-source")
        self.edit_record(copied, temporal_plan=changed.to_dict(), source_build_id=changed.source_build_id)
        with self.assertRaisesRegex(ValueError, "must match"):
            adapter.load_development_partition(self.features["directory"], copied,
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)

    def test_midmonth_manifest_controls_dates_without_month_snapping(self):
        source = build(replace(self.fixture.fixture.config, as_of=date(2026, 6, 15),
                               output_root=self.root/"base", include_kpi_marts=True))
        source_dir = Path(source["database"]).parent
        plan = plan_evaluation(source_dir)
        features = build_features(source_dir, date(2026, 5, 16), self.root/"features",
                                  temporal_plan=plan, threads=1, memory_limit="256MB")
        labels = build_labels(source_dir, date(2026, 5, 16), self.root/"labels",
                              temporal_plan=plan, threads=1, memory_limit="256MB")
        scoring = build_features(source_dir, date(2026, 6, 15), self.root/"scoring",
                                 temporal_plan=plan, purpose="scoring", threads=1, memory_limit="256MB")
        batch = adapter.load_development_partition(features["directory"], labels["directory"],
            fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        self.assertEqual(batch.metadata["cutoff"], "2026-05-16")
        start, end = TemporalPlan.from_dict(plan).development_folds()[1][2:4]
        self.assertTrue(batch.row_keys["scoring_date"].between(pd.Timestamp(start), pd.Timestamp(end)).all())
        latest = adapter.load_scoring_inputs(scoring["directory"], predictors=self.predictors)
        self.assertTrue(latest.row_keys["scoring_date"].eq(pd.Timestamp("2026-06-15")).all())

    def test_short_history_fold_unavailable_before_labels_are_opened(self):
        minimal = replace(TemporalPlan.from_dict(self.fixture.plan), history_start=date(2026, 4, 24))
        with patch.object(adapter, "_feature_artifact", return_value=(self.features, None, None, minimal)), \
             patch.object(adapter, "verified_artifact", side_effect=AssertionError("Opened labels")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.development()

    def test_integrity_and_failed_quality_are_not_bypassed(self):
        copied = self.copy_artifact(self.features)
        self.edit_record(copied, database_sha256="0"*64)
        with self.assertRaisesRegex(ValueError, "integrity"):
            adapter.load_development_partition(copied, self.labels["directory"],
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        copied = self.copy_artifact(self.scoring, "quality")
        quality = copied/"quality.json"
        checks = json.loads(quality.read_text())
        next(c for c in checks if c["severity"] == "error").update(passed=False, failed_count=1)
        quality.write_text(json.dumps(checks))
        self.edit_record(copied, quality_sha256=digest(quality))
        with self.assertRaisesRegex(ValueError, "quality"):
            adapter.load_scoring_inputs(copied, predictors=self.predictors)

    def test_missing_input_and_required_column_fail_without_rebuild(self):
        with self.assertRaisesRegex(ValueError, "accepted features"):
            adapter.load_scoring_inputs(self.root/"absent", predictors=self.predictors)
        copied = self.copy_artifact(self.scoring)
        self.edit_database(copied, "ALTER TABLE gold.features_oos_daily DROP COLUMN inventory_observed")
        with self.assertRaisesRegex(ValueError, "columns"):
            adapter.load_scoring_inputs(copied, predictors=self.predictors)

    def test_duplicate_missing_and_wrong_row_identity_rejected(self):
        mutations = {
            "duplicate": "INSERT INTO gold.features_oos_daily SELECT * FROM gold.features_oos_daily WHERE scoring_date=DATE '2026-04-10'",
            "missing": "DELETE FROM gold.features_oos_daily WHERE scoring_date=DATE '2026-04-10'",
            "identity": "UPDATE gold.features_oos_daily SET dataset_hash='wrong' WHERE scoring_date=DATE '2026-04-10'",
            "null_key": "UPDATE gold.features_oos_daily SET sku_id=NULL WHERE scoring_date=DATE '2026-04-10'",
        }
        for name, sql in mutations.items():
            with self.subTest(name=name):
                copied = self.copy_artifact(self.features, name)
                self.edit_database(copied, sql)
                with self.assertRaises(ValueError):
                    adapter.load_development_partition(copied, self.labels["directory"],
                        fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)

    def test_nonbinary_target_and_changed_fold_rejected(self):
        copied = self.copy_artifact(self.labels)
        self.edit_database(copied, "UPDATE evaluation.labels_oos_next7 SET target_oos_7d=2 WHERE scoring_date=DATE '2026-04-10'")
        with self.assertRaisesRegex(ValueError, "binary"):
            adapter.load_development_partition(self.features["directory"], copied,
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        copied = self.copy_artifact(self.labels, "fold")
        self.edit_database(copied, "UPDATE evaluation.temporal_folds SET validation_start=validation_start+1 WHERE fold_id='rolling_dev_2'")
        with self.assertRaisesRegex(ValueError, "fold bounds"):
            adapter.load_development_partition(self.features["directory"], copied,
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)

    def test_scoring_latest_only_no_labels_and_extra_columns_ignored(self):
        copied = self.copy_artifact(self.scoring)
        self.edit_database(copied, "ALTER TABLE gold.features_oos_daily ADD COLUMN future_target INTEGER DEFAULT 1")
        original = adapter.verified_artifact
        def verify(directory, *, kind):
            self.assertEqual(Path(directory), copied)
            self.assertEqual(kind, "features")
            return original(directory, kind=kind)
        with patch.object(adapter, "verified_artifact", side_effect=verify):
            batch = adapter.load_scoring_inputs(copied, predictors=self.predictors)
        self.assertFalse(hasattr(batch, "y"))
        self.assertTrue(batch.row_keys["scoring_date"].eq(pd.Timestamp("2026-06-30")).all())
        self.assertFalse(any("label" in name for name in batch.metadata))
        self.assertNotIn("future_target", batch.X)
        with self.assertRaises(ValueError):
            adapter.load_scoring_inputs(copied, predictors=["future_target"])

    def test_empty_partitions_and_latest_date_are_unavailable(self):
        copied = self.copy_artifact(self.labels)
        self.edit_database(copied, "DELETE FROM evaluation.labels_oos_next7 WHERE scoring_date BETWEEN DATE '2026-04-02' AND DATE '2026-04-24'")
        with self.assertRaises(RuntimeError):
            adapter.load_development_partition(self.features["directory"], copied,
                fold_id="rolling_dev_2", partition="validation", predictors=self.predictors)
        copied = self.copy_artifact(self.scoring, "scoring")
        self.edit_database(copied, "DELETE FROM gold.features_oos_daily WHERE scoring_date=DATE '2026-06-30'")
        with self.assertRaises(RuntimeError):
            adapter.load_scoring_inputs(copied, predictors=self.predictors)

    def test_reads_are_immutable_and_results_detached(self):
        files = [Path(record["directory"])/name for record in (self.features, self.labels, self.scoring)
                 for name in (record["database"], "run.json", "quality.json")]
        before = {str(p): digest(p) for p in files}
        original = self.development()
        changed = self.development()
        changed.X.iloc[0, 0] = -999
        changed.y.iloc[0] = 99
        changed.context.iloc[0, 0] = "changed"
        replay = self.development()
        pd.testing.assert_frame_equal(original.X, replay.X)
        pd.testing.assert_series_equal(original.y, replay.y)
        pd.testing.assert_frame_equal(original.context, replay.context)
        adapter.load_scoring_inputs(self.scoring["directory"], predictors=self.predictors)
        self.assertEqual(before, {str(p): digest(p) for p in files})


class Module2ImportTests(unittest.TestCase):
    def test_offline_import_has_no_ui_or_network_dependencies(self):
        script = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('shiny','modules.module_2.ui','modules.module_2.server','shared.data','shared.auth')):
            raise AssertionError('Offline import loaded '+fullname)
sys.meta_path.insert(0, Block())
def audit(event, args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('Network activity during import')
sys.addaudithook(audit)
from modules.module_2.data_adapter import load_development_partition, load_scoring_inputs
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_exports_preserve_original_callables(self):
        package = importlib.import_module("modules.module_2")
        ui_module = types.ModuleType("modules.module_2.ui")
        server_module = types.ModuleType("modules.module_2.server")
        ui_module.module_2_ui = lambda dataset: dataset
        server_module.module_2_server = lambda *args: args
        with patch.dict(sys.modules, {ui_module.__name__: ui_module, server_module.__name__: server_module}):
            self.assertEqual(package.__all__, ["module_2_ui", "module_2_server"])
            self.assertIs(package.module_2_ui, ui_module.module_2_ui)
            self.assertIs(package.module_2_server, server_module.module_2_server)
        with self.assertRaises(AttributeError):
            getattr(package, "unknown_export")


if __name__ == "__main__":
    unittest.main()
