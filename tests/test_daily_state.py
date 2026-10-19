"""Tests of historical boundaries and state reconstruction, independent of raw ingestion."""

import unittest
from dataclasses import replace
from datetime import date, timedelta

import duckdb

from data_platform.estimation import EstimatorConfig, build_daily_state
from data_platform.pipeline import PACKAGE
from data_platform.quality import Quality


class DailyStateTests(unittest.TestCase):
    def connection(self, hidden=(), perturb=None):
        con = duckdb.connect()
        self.addCleanup(con.close)
        con.execute((PACKAGE / "sql/meta.sql").read_text())
        con.execute("""
            CREATE TABLE silver.dim_stores AS SELECT 'S1' store_id, 'Modern Trade' channel,
                'East' region, 'Tier 1' segment_tier;
            CREATE TABLE silver.dim_products AS SELECT 'P1' sku_id, 'Dishwashing' category, true is_msl;
            CREATE TABLE silver.store_sku_calendar AS
                SELECT cast(day AS DATE) date, 'S1' store_id, 'P1' sku_id, 'hash' dataset_hash, 'v3' data_version
                FROM generate_series(DATE '2025-01-01', DATE '2025-03-15', INTERVAL 1 DAY) d(day);
            CREATE TABLE silver.fact_sell_out (
                date DATE, store_id VARCHAR, sku_id VARCHAR, units_sold_daily BIGINT, inventory_level BIGINT,
                is_out_of_stock BOOLEAN, restock_units BIGINT, restock_delivery_id VARCHAR,
                consumer_price_actual DECIMAL(18,2), promo_id VARCHAR, promo_discount_pct DOUBLE,
                source_file_hash VARCHAR, source_row_number BIGINT, source_known_at TIMESTAMP, dataset_hash VARCHAR
            );
            CREATE TABLE silver.replenishment_events (
                receipt_date DATE, store_id VARCHAR, sku_id VARCHAR, delivery_id VARCHAR,
                receipt_units BIGINT, source_known_at TIMESTAMP, source_file_hash VARCHAR,
                source_row_number BIGINT, dataset_hash VARCHAR, build_id VARCHAR
            );
            CREATE TABLE silver.promotion_calendar (
                date DATE, channel VARCHAR, category VARCHAR, promo_id VARCHAR, discount_pct DOUBLE,
                source_known_at TIMESTAMP, source_file_hash VARCHAR, source_row_number BIGINT,
                dataset_hash VARCHAR, build_id VARCHAR
            );
            CREATE TABLE silver.fact_execution_audits (
                date DATE, store_id VARCHAR, sku_id VARCHAR, audit_id VARCHAR,
                source_known_at TIMESTAMP, source_file_hash VARCHAR, source_row_number BIGINT
            );
        """)
        start, end = date(2025, 1, 1), date(2025, 3, 15)
        rows = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if day in hidden:
                continue
            sales = 2 if perturb is None or day not in perturb else perturb[day]
            rows.append((day, "S1", "P1", sales, 100 - offset * 2, False, 0, None, 15,
                         None, 0, "source", offset + 1, str(day) + " 23:59:59", "hash"))
        # Keep inventory physically nonnegative for later fixture observations.
        rows = [row[:4] + (max(row[4], 1),) + row[5:] for row in rows]
        con.executemany("INSERT INTO silver.fact_sell_out VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return con

    def estimate(self, con, config=None):
        return build_daily_state(con, "fixture", date(2025, 3, 15), Quality(con, "fixture"), config)

    def test_four_day_limit_and_report_reset(self):
        hidden = [date(2025, 2, d) for d in range(10, 15)]
        con = self.connection(hidden)
        self.estimate(con)
        rows = con.execute("""SELECT date, inventory_data_status, units_sold_estimated, inventory_estimated
            FROM gold.mart_store_sku_daily WHERE date BETWEEN '2025-02-10' AND '2025-02-15' ORDER BY date""").fetchall()
        self.assertEqual([r[1] for r in rows], ["Estimated"] * 4 + ["Unknown", "Observed"])
        self.assertEqual([r[2] for r in rows[:4]], [2.0] * 4)
        self.assertIsNone(rows[4][3])
        self.assertIsNone(rows[5][2])

    def test_missing_leading_history_and_no_future_audit(self):
        con = self.connection([date(2025, 1, 1), date(2025, 1, 2)])
        con.execute("INSERT INTO silver.fact_execution_audits VALUES ('2025-02-01','S1','P1','A1','2025-02-01 23:59:59','audit',1)")
        self.estimate(con)
        self.assertEqual(con.execute("SELECT inventory_data_status, assortment_is_confirmed_asof FROM gold.mart_store_sku_daily WHERE date='2025-01-01'").fetchone(), ("Unknown", False))
        self.assertIsNone(con.execute("SELECT last_audit_id FROM gold.mart_store_sku_daily WHERE date='2025-01-31'").fetchone()[0])
        self.assertEqual(con.execute("SELECT last_audit_id FROM gold.mart_store_sku_daily WHERE date='2025-02-01'").fetchone()[0], "A1")

    def test_receipts_count_once_and_do_not_reset_report_age(self):
        con = self.connection([date(2025, 2, 10), date(2025, 2, 11)])
        con.execute("INSERT INTO silver.replenishment_events VALUES ('2025-02-10','S1','P1','D1',10,'2025-02-10 23:59:59','delivery',1,'hash','fixture')")
        self.estimate(con)
        anchor = con.execute("SELECT inventory_observed FROM gold.mart_store_sku_daily WHERE date='2025-02-09'").fetchone()[0]
        rows = con.execute("SELECT inventory_estimated, days_since_inventory_report FROM gold.mart_store_sku_daily WHERE date BETWEEN '2025-02-10' AND '2025-02-11' ORDER BY date").fetchall()
        self.assertEqual(rows, [(anchor + 10 - 2, 1), (anchor + 10 - 4, 2)])

    def test_whole_block_and_future_changes_cannot_leak(self):
        hidden = [date(2025, 2, 10), date(2025, 2, 11)]
        left = self.connection(hidden)
        right = self.connection(hidden, {date(2025, 2, 12): 100000})
        self.estimate(left)
        self.estimate(right)
        sql = "SELECT date, units_sold_estimated, inventory_estimated, sales_history_max_date FROM gold.mart_store_sku_daily WHERE date <= '2025-02-11' ORDER BY date"
        self.assertEqual(left.execute(sql).fetchall(), right.execute(sql).fetchall())
        for con in (left, right):
            maximum = con.execute("SELECT max(sales_history_max_date) FROM gold.mart_store_sku_daily WHERE date='2025-02-11'").fetchone()[0]
            self.assertLess(maximum, date(2025, 2, 10))

    def test_promotion_fit_not_broadcast_back_to_earlier_dates(self):
        con = self.connection([date(2025, 2, 10)])
        con.execute("UPDATE silver.fact_sell_out SET promo_id='P', units_sold_daily=4 WHERE date BETWEEN '2025-01-15' AND '2025-01-25'")
        config = replace(EstimatorConfig(), promotion_fit_cutoff=date(2025, 1, 31), promotion_min_count=2)
        self.estimate(con, config)
        self.assertEqual(con.execute("SELECT DISTINCT current_promotion_factor, promotion_factor_fit_cutoff, promotion_fit_status FROM gold.mart_store_sku_daily WHERE date <= '2025-01-31'").fetchall(), [(1.0, None, "Warm-up; factor unavailable")])
        self.assertEqual(con.execute("SELECT factor FROM gold.estimator_promotion_factors").fetchone()[0], 2.0)

    def test_frequency_median_matches_direct_historical_median(self):
        con = self.connection(perturb={date(2025, 1, 3): 10, date(2025, 1, 4): 6})
        self.estimate(con)
        for day in ("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-02-01"):
            direct = con.execute("SELECT median(units_sold_daily) FROM silver.fact_sell_out WHERE date < ? AND NOT is_out_of_stock", [day]).fetchone()[0]
            compact = con.execute("SELECT group_raw FROM _group_fallback_daily WHERE date=?", [day]).fetchone()[0]
            self.assertEqual(compact, direct, day)

    def test_cumulative_floor_convention_after_depletion_and_receipt(self):
        con = self.connection([date(2025, 2, 10), date(2025, 2, 11)])
        con.execute("UPDATE silver.fact_sell_out SET inventory_level=1 WHERE date='2025-02-09'")
        con.execute("INSERT INTO silver.replenishment_events VALUES ('2025-02-11','S1','P1','D1',3,'2025-02-11 23:59:59','delivery',1,'hash','fixture')")
        self.estimate(con)
        rows = con.execute("SELECT inventory_unclipped, inventory_estimated FROM gold.mart_store_sku_daily WHERE date BETWEEN '2025-02-10' AND '2025-02-11' ORDER BY date").fetchall()
        self.assertEqual(rows, [(-1., 0.), (0., 0.)])

    def test_known_receipt_cannot_replace_missing_sales_history(self):
        con = self.connection([date(2025, 1, 2)])
        con.execute("INSERT INTO silver.replenishment_events VALUES ('2025-01-02','S1','P1','D1',100,'2025-01-02 23:59:59','delivery',1,'hash','fixture')")
        self.estimate(con)
        self.assertEqual(con.execute("SELECT inventory_data_status, inventory_model_state, receipt_units_from_deliveries FROM gold.mart_store_sku_daily WHERE date='2025-01-02'").fetchone(), ("Unknown", None, 100.))


if __name__ == "__main__":
    unittest.main()
