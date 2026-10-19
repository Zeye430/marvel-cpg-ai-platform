"""Projection isolation and provenance, using synthetic source builds only."""

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb

from data_platform.feature_handoff import export_feature_input
from data_platform.features import INPUT_COLUMNS, build_oos_state_features
from data_platform.ingestion import digest
from data_platform.quality import Quality
from data_platform.replenishment_features import ORDER_COLUMNS, RECEIPT_COLUMNS, build_oos_replenishment_features
import test_oos_features as feature_fixtures
import test_replenishment_features as replenishment_fixtures


class FeatureHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        helper = feature_fixtures.OosFeatureTests()
        fixture = helper.connection()
        self.addCleanup(helper.doCleanups)
        # Preserve native DuckDB types, including nullable integer observations.
        parquet = self.source / "fixture.parquet"
        fixture.execute("COPY feature_input.daily_state TO ? (FORMAT PARQUET)", [str(parquet)])
        with duckdb.connect(str(self.source / "build.duckdb")) as con:
            con.execute("CREATE SCHEMA gold; CREATE SCHEMA bronze; CREATE SCHEMA evaluation")
            con.execute("CREATE TABLE gold.mart_store_sku_daily AS SELECT * FROM read_parquet(?)", [str(parquet)])
            con.execute("ALTER TABLE gold.mart_store_sku_daily ADD COLUMN target_oos_7d BIGINT DEFAULT 1")
            con.execute("CREATE TABLE bronze.secret AS SELECT 'private' AS secret_text")
            con.execute("CREATE TABLE evaluation.labels AS SELECT 1 AS label_value")
        self.report = {
            "status": "Accepted", "build_id": "build", "dataset_hash": "hash",
            "configuration": {"as_of": "2025-02-10"}, "daily_state": {"rows": 41},
        }
        self.checks = [{"severity": "error", "passed": True, "failed_count": 0}]
        self.write_manifest()

    def write_manifest(self):
        (self.source / "quality.json").write_text(json.dumps(self.checks))
        self.report["database_sha256"] = digest(self.source / "build.duckdb")
        self.report["quality_sha256"] = digest(self.source / "quality.json")
        (self.source / "run.json").write_text(json.dumps(self.report))

    def export(self, cutoff=date(2025, 2, 1)):
        return export_feature_input(self.source, self.root / "projections", cutoff)

    def add_replenishment_sources(self):
        helper = replenishment_fixtures.ReplenishmentFeatureTests()
        self.addCleanup(helper.doCleanups)
        fixture = helper.connection()
        helper.order(fixture,"FUTURE","2025-01-31","2025-02-05",40)
        helper.receipt(fixture,"FUTURE","2025-02-06",39)
        helper.order(fixture,"LATE","2025-01-20","2025-01-23",9,known="2025-02-02 09:00:00")
        helper.receipt(fixture,"LATE","2025-01-24",9,known="2025-02-03 09:00:00")
        with duckdb.connect(str(self.source / "build.duckdb")) as source:
            source.execute("CREATE SCHEMA silver")
            for target, name in (("order_schedule_events","order_schedule_events"),
                                 ("replenishment_events","receipt_events")):
                path = self.source / (target+".parquet")
                fixture.execute(f"COPY feature_input.{name} TO ? (FORMAT PARQUET)",[str(path)])
                source.execute(f"CREATE TABLE silver.{target} AS SELECT * FROM read_parquet(?)",[str(path)])
            # Deliberately unapproved source columns must never reach the input.
            source.execute("ALTER TABLE silver.order_schedule_events ADD COLUMN actual_delivery DATE DEFAULT '2025-02-06'")
            source.execute("ALTER TABLE silver.order_schedule_events ADD COLUMN units_delivered BIGINT DEFAULT 999999")
        self.write_manifest()

    def test_projection_is_allowlisted_bounded_and_usable(self):
        before = digest(self.source / "build.duckdb")
        result = self.export()
        directory = Path(result["directory"])
        self.assertEqual(result["status"], "Accepted")
        self.assertFalse(result["application_ready"])
        self.assertEqual(result["rows"], 32)
        self.assertEqual(digest(self.source / "build.duckdb"), before)
        projected_db = directory / result["database"]
        self.assertEqual(digest(projected_db), result["database_sha256"])
        # Inspect read-only, then build on a disposable copy so the accepted
        # projection stays byte-immutable and native nullable types survive.
        with duckdb.connect(str(projected_db), read_only=True) as source:
            names = [r[0] for r in source.execute("DESCRIBE feature_input.daily_state").fetchall()]
            self.assertEqual(names, list(INPUT_COLUMNS))
            self.assertEqual(source.execute("SELECT max(date) FROM feature_input.daily_state").fetchone()[0], date(2025, 2, 1))
            self.assertEqual(source.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('bronze','silver','evaluation')").fetchone()[0], 0)
        working_db = self.root / "working.duckdb"
        shutil.copyfile(projected_db, working_db)
        with duckdb.connect(str(working_db)) as work:
            built = build_oos_state_features(work, date(2025, 2, 1), Quality(work, "fixture"))
            self.assertEqual(built["rows"], 32)
        self.assertEqual(digest(projected_db), result["database_sha256"])

    def test_tampered_database_or_failed_checks_block_export(self):
        with duckdb.connect(str(self.source / "build.duckdb")) as con:
            con.execute("UPDATE gold.mart_store_sku_daily SET units_sold_observed=99")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.export()
        self.checks[0].update(passed=False, failed_count=1)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "passing required"):
            self.export()

    def test_wrong_identity_retains_failed_candidate(self):
        self.report["build_id"] = "wrong-build"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "identity"):
            self.export()
        runs = list((self.root / "projections").glob(".pending-*/run.json"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(json.loads(runs[0].read_text())["status"], "Failed")
        self.assertEqual(list((self.root / "projections").glob("feature-input-*")), [])

    def test_lock_cutoff_failed_status_and_source_path(self):
        with self.assertRaisesRegex(ValueError, "June"):
            self.export(date(2026, 6, 1))
        with self.assertRaisesRegex(ValueError, "cutoff"):
            self.export(date(2025, 2, 11))
        with self.assertRaisesRegex(ValueError, "source build directory"):
            export_feature_input(self.source, self.source / "projection", date(2025, 2, 1))
        self.report["status"] = "Failed"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "accepted build"):
            self.export()

    def test_unconfirmed_pairs_excluded_and_nullable_types_preserved(self):
        with duckdb.connect(str(self.source / "build.duckdb")) as con:
            con.execute("UPDATE gold.mart_store_sku_daily SET assortment_is_confirmed_asof=false WHERE date<'2025-01-03'")
            con.execute("UPDATE gold.mart_store_sku_daily SET inventory_observed=NULL, units_sold_observed=NULL, oos_observed=NULL, inventory_data_status='Unknown', sales_data_status='Unknown' WHERE date='2025-02-01'")
        self.write_manifest()
        result = self.export()
        self.assertEqual(result["rows"], 30)
        with duckdb.connect(str(Path(result["directory"]) / result["database"]), read_only=True) as con:
            self.assertEqual(con.execute("SELECT inventory_observed, units_sold_observed FROM feature_input.daily_state WHERE date='2025-02-01'").fetchone(), (None, None))
            types = dict((r[0], r[1]) for r in con.execute("DESCRIBE feature_input.daily_state").fetchall())
            self.assertEqual(types["inventory_observed"], "BIGINT")

    def test_quality_tampering_and_repeated_exports_do_not_overwrite(self):
        first, second = self.export(), self.export()
        self.assertNotEqual(first["directory"], second["directory"])
        self.assertEqual(digest(Path(first["directory"]) / first["database"]), first["database_sha256"])
        (self.source / "quality.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "quality hash mismatch"):
            self.export()

    def test_optional_replenishment_projection_is_known_at_and_allowlisted(self):
        self.add_replenishment_sources()
        result = export_feature_input(self.source,self.root/"projections",date(2025,2,1),include_replenishment=True)
        self.assertEqual(result["replenishment_rows"],{"order_schedule_events":6,"receipt_events":2})
        self.assertEqual(result["projection_version"],"oos_state_replenishment_input.v2")
        working = self.root/"replen-working.duckdb"
        shutil.copyfile(Path(result["directory"])/result["database"],working)
        with duckdb.connect(str(working)) as con:
            for table,columns in (("order_schedule_events",ORDER_COLUMNS),("receipt_events",RECEIPT_COLUMNS)):
                self.assertEqual([r[0] for r in con.execute(f"DESCRIBE feature_input.{table}").fetchall()],list(columns))
            self.assertEqual(con.execute("SELECT scheduled_delivery FROM feature_input.order_schedule_events WHERE delivery_id='FUTURE'").fetchone()[0],date(2025,2,5))
            build_oos_state_features(con,date(2025,2,1),Quality(con,"state"))
            built = build_oos_replenishment_features(con,date(2025,2,1),Quality(con,"replen"),assume_complete_snapshot=True)
            self.assertEqual(built["rows"],32)

    def test_future_receipt_changes_cannot_change_earlier_exported_inputs(self):
        self.add_replenishment_sources()
        first = export_feature_input(self.source,self.root/"projections",date(2025,2,1),include_replenishment=True)
        with duckdb.connect(str(self.source/"build.duckdb")) as con:
            con.execute("UPDATE silver.replenishment_events SET receipt_date='2025-02-08',source_known_at='2025-02-08 23:59:59',receipt_units=999999 WHERE delivery_id='FUTURE'")
            con.execute("UPDATE silver.order_schedule_events SET actual_delivery='2025-02-08',units_delivered=999999")
        self.write_manifest()
        second = export_feature_input(self.source,self.root/"projections",date(2025,2,1),include_replenishment=True)
        for table in ("daily_state","order_schedule_events","receipt_events"):
            with duckdb.connect(str(Path(first["directory"])/first["database"]),read_only=True) as a:
                before = a.execute(f"SELECT * FROM feature_input.{table} ORDER BY ALL").fetchall()
            with duckdb.connect(str(Path(second["directory"])/second["database"]),read_only=True) as b:
                self.assertEqual(before,b.execute(f"SELECT * FROM feature_input.{table} ORDER BY ALL").fetchall())

    def test_missing_replenishment_sources_fail_instead_of_assuming_empty(self):
        with self.assertRaises(duckdb.CatalogException):
            export_feature_input(self.source,self.root/"projections",date(2025,2,1),include_replenishment=True)
        runs = list((self.root/"projections").glob(".pending-*/run.json"))
        self.assertEqual(json.loads(runs[0].read_text())["status"],"Failed")


if __name__ == "__main__":
    unittest.main()
