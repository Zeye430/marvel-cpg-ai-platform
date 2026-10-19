"""Small deterministic fixtures for the first, isolated OOS feature slice."""

import unittest
from datetime import date

import duckdb

from data_platform.features import build_oos_state_features
from data_platform.pipeline import PACKAGE
from data_platform.quality import Quality, QualityError


class OosFeatureTests(unittest.TestCase):
    def connection(self):
        con = duckdb.connect()
        self.addCleanup(con.close)
        con.execute((PACKAGE / "sql/meta.sql").read_text())
        con.execute("""
            CREATE SCHEMA feature_input;
            CREATE TABLE feature_input.daily_state AS
            SELECT 'build' AS build_id, 'hash' AS dataset_hash, 'v3' AS data_version,
                   cast(day AS DATE) AS date, 'S1' AS store_id, 'P1' AS sku_id,
                   'candidate-v1' AS estimator_version,
                   day + INTERVAL 1 DAY - INTERVAL 1 MICROSECOND AS as_of_timestamp,
                   'same_day_end_simulation_v1' AS availability_assumption_id,
                   cast(day AS VARCHAR) AS source_lineage_set_id,
                   true AS assortment_is_confirmed_asof,
                   'Modern Trade' AS channel, 'Tier 1' AS segment_tier,
                   'East' AS region, 'Dishwashing' AS category, true AS is_msl,
                   100::BIGINT AS inventory_observed, NULL::DOUBLE AS inventory_estimated,
                   'Observed' AS inventory_data_status,
                   2::BIGINT AS units_sold_observed, NULL::DOUBLE AS units_sold_estimated,
                   'Observed' AS sales_data_status, false AS oos_observed,
                   cast(day AS DATE) AS last_report_date, 0::BIGINT AS days_since_inventory_report,
                   'Candidate' AS estimation_release_status
            FROM generate_series(DATE '2025-01-01', DATE '2025-02-10', INTERVAL 1 DAY) d(day);
        """)
        return con

    def build(self, con, cutoff=date(2025, 2, 10)):
        return build_oos_state_features(con, cutoff, Quality(con, "fixture"))

    def row(self, con, columns, day="2025-02-01"):
        return con.execute(f"SELECT {columns} FROM gold.features_oos_daily WHERE scoring_date=?", [day]).fetchone()

    def hide(self, con, start, end, estimated=False):
        con.execute("""
            UPDATE feature_input.daily_state SET inventory_observed=NULL,
                units_sold_observed=NULL, oos_observed=NULL,
                inventory_data_status=?, sales_data_status=?,
                inventory_estimated=?, units_sold_estimated=?
            WHERE date BETWEEN ? AND ?
        """, ["Estimated" if estimated else "Unknown"] * 2 +
                    [90.0 if estimated else None, 999.0 if estimated else None, start, end])

    def test_calendar_windows_and_current_day_exclusion(self):
        con = self.connection()
        con.execute("UPDATE feature_input.daily_state SET units_sold_observed=1000 WHERE date='2025-02-01'")
        result = self.build(con)
        self.assertEqual(result["rows"], 41)
        self.assertFalse(result["application_ready"])
        self.assertEqual(self.row(con, "expected_days_7d, observed_days_7d, sales_units_observed_7d, in_stock_velocity_7d, in_stock_velocity_28d, sales_acceleration_ratio, days_cover_observed"),
                         (7, 7, 1012, 2.0, 2.0, 1.0, 50.0))
        self.assertEqual(self.row(con, "expected_days_28d, velocity_days_28d"), (28, 28))

    def test_missing_is_not_zero_and_candidates_do_not_enter_history(self):
        con = self.connection()
        self.hide(con, "2025-01-29", "2025-02-01", estimated=True)
        self.build(con)
        self.assertEqual(self.row(con, "inventory_observed, inventory_candidate, sales_observed, sales_candidate, inventory_data_status, observed_days_7d, sales_units_observed_7d, in_stock_velocity_7d, days_cover_candidate, inventory_change_7d_observed"),
                         (None, 90.0, None, 999.0, "Estimated", 3, 6, 2.0, 45.0, None))

    def test_all_unknown_windows_and_zero_velocity_are_na(self):
        con = self.connection()
        self.hide(con, "2025-01-01", "2025-02-10")
        self.build(con)
        self.assertEqual(self.row(con, "sales_units_observed_7d, oos_days_observed_7d, observed_days_7d, days_cover_observed"), (None, None, 0, None))
        zero = self.connection()
        zero.execute("UPDATE feature_input.daily_state SET units_sold_observed=0")
        self.build(zero)
        self.assertEqual(self.row(zero, "in_stock_velocity_28d, days_cover_observed, sales_acceleration_ratio"), (0.0, None, None))

    def test_oos_sales_excluded_and_minimum_history_applies(self):
        con = self.connection()
        con.execute("UPDATE feature_input.daily_state SET inventory_observed=0, oos_observed=true, units_sold_observed=99 WHERE date BETWEEN '2025-01-25' AND '2025-01-29'")
        self.build(con)
        self.assertEqual(self.row(con, "velocity_days_7d, in_stock_velocity_7d, oos_days_observed_7d"), (2, None, 4))
        self.assertEqual(self.row(con, "velocity_days_28d, in_stock_velocity_28d", "2025-01-05"), (4, None))

    def test_cold_start_and_exact_seven_day_inventory_anchor(self):
        con = self.connection()
        con.execute("UPDATE feature_input.daily_state SET assortment_is_confirmed_asof=false WHERE date<'2025-01-10'")
        con.execute("UPDATE feature_input.daily_state SET inventory_observed=60 WHERE date='2025-02-01'")
        self.build(con)
        self.assertEqual(con.execute("SELECT min(scoring_date) FROM gold.features_oos_daily").fetchone()[0], date(2025, 1, 10))
        self.assertEqual(self.row(con, "expected_days_7d, observed_days_7d", "2025-01-11"), (2, 2))
        self.assertEqual(self.row(con, "inventory_change_7d_observed"), (-40,))
        self.assertEqual(self.row(con, "inventory_change_7d_observed", "2025-01-12"), (None,))

    def test_future_mutation_and_truncated_replay(self):
        full, changed, truncated = self.connection(), self.connection(), self.connection()
        changed.execute("UPDATE feature_input.daily_state SET units_sold_observed=999999, inventory_observed=0, oos_observed=true WHERE date>'2025-02-01'")
        truncated.execute("DELETE FROM feature_input.daily_state WHERE date>'2025-02-01'")
        self.build(full)
        self.build(changed)
        self.build(truncated, date(2025, 2, 1))
        sql = "SELECT * FROM gold.features_oos_daily WHERE scoring_date<='2025-02-01' ORDER BY scoring_date"
        expected = full.execute(sql).fetchall()
        self.assertEqual(changed.execute(sql).fetchall(), expected)
        self.assertEqual(truncated.execute(sql).fetchall(), expected)

    def test_sparse_input_uses_calendar_not_last_rows(self):
        con = self.connection()
        con.execute("DELETE FROM feature_input.daily_state WHERE date BETWEEN '2025-01-20' AND '2025-01-31'")
        self.build(con)
        self.assertEqual(self.row(con, "expected_days_7d, velocity_days_7d, in_stock_velocity_7d"), (1, 0, None))

    def test_wrong_artifact_duplicate_and_june_are_rejected(self):
        source = self.connection()
        source.execute("CREATE TABLE bronze.secret AS SELECT 1 x")
        with self.assertRaisesRegex(ValueError, "isolated"):
            self.build(source)
        duplicate = self.connection()
        duplicate.execute("INSERT INTO feature_input.daily_state SELECT * FROM feature_input.daily_state LIMIT 1")
        with self.assertRaises(QualityError):
            self.build(duplicate)
        with self.assertRaisesRegex(ValueError, "June"):
            self.build(self.connection(), date(2026, 6, 1))

    def test_narrow_projection_excludes_label_columns(self):
        con = self.connection()
        con.execute("ALTER TABLE feature_input.daily_state ADD COLUMN target_oos_7d BIGINT DEFAULT 1")
        self.build(con)
        names = [row[0] for row in con.execute("DESCRIBE gold.features_oos_daily").fetchall()]
        self.assertNotIn("target_oos_7d", names)

    def test_store_histories_are_separate_and_mixed_builds_rejected(self):
        con = self.connection()
        con.execute("INSERT INTO feature_input.daily_state SELECT * REPLACE ('S2' AS store_id, 20 AS units_sold_observed) FROM feature_input.daily_state")
        self.build(con)
        self.assertEqual(con.execute("SELECT store_id, in_stock_velocity_28d FROM gold.features_oos_daily WHERE scoring_date='2025-02-01' ORDER BY store_id").fetchall(), [("S1", 2.0), ("S2", 20.0)])
        mixed = self.connection()
        mixed.execute("UPDATE feature_input.daily_state SET build_id='other' WHERE date='2025-02-01'")
        with self.assertRaises(QualityError):
            self.build(mixed)


if __name__ == "__main__":
    unittest.main()
