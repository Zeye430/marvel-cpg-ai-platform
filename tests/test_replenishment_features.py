"""Point-in-time receipts, pending plans and explicit completeness semantics."""

import unittest
from datetime import date

from data_platform.features import build_oos_state_features
from data_platform.quality import Quality, QualityError
from data_platform.replenishment_features import (
    ORDER_COLUMNS, RECEIPT_COLUMNS, build_oos_replenishment_features,
)
import test_oos_features as state_fixtures


class ReplenishmentFeatureTests(unittest.TestCase):
    def connection(self, seeded=True):
        helper = state_fixtures.OosFeatureTests()
        self.addCleanup(helper.doCleanups)
        con = helper.connection()
        con.execute("""
            CREATE TABLE feature_input.order_schedule_events (
                delivery_id VARCHAR,store_id VARCHAR,sku_id VARCHAR,order_date DATE,
                scheduled_delivery DATE,units_ordered BIGINT,source_known_at TIMESTAMP,
                schedule_version VARCHAR,availability_assumption_id VARCHAR,dataset_hash VARCHAR,
                data_version VARCHAR,build_id VARCHAR,source_file_hash VARCHAR,source_row_number BIGINT
            );
            CREATE TABLE feature_input.receipt_events (
                delivery_id VARCHAR,store_id VARCHAR,sku_id VARCHAR,receipt_date DATE,
                receipt_units BIGINT,source_known_at TIMESTAMP,availability_assumption_id VARCHAR,
                dataset_hash VARCHAR,data_version VARCHAR,build_id VARCHAR,source_file_hash VARCHAR,
                source_row_number BIGINT
            );
        """)
        if seeded:
            for row in (("D1","2025-01-10","2025-01-15",40),
                        ("D2","2025-01-12","2025-01-18",20),
                        ("D3","2025-01-14","2025-01-14",5),
                        ("D4","2025-01-17","2025-01-22",10),
                        ("D5","2025-01-11","2025-01-23",7)):
                self.order(con, *row)
            self.receipt(con,"D1","2025-01-16",30)
            self.receipt(con,"D2","2025-01-20",20)
        return con

    def order(self, con, delivery, day, scheduled, units, known=None):
        values = (delivery,"S1","P1",day,scheduled,units,known or day+" 00:00:00",
                  "initial_snapshot_v1","initial_schedule_known_at_order_simulation_v1",
                  "hash","v3","build","source",1)
        con.execute(f"INSERT INTO feature_input.order_schedule_events VALUES ({','.join('?' for _ in ORDER_COLUMNS)})", values)

    def receipt(self, con, delivery, day, units, known=None):
        values = (delivery,"S1","P1",day,units,known or day+" 23:59:59",
                  "same_day_end_simulation_v1","hash","v3","build","source",1)
        con.execute(f"INSERT INTO feature_input.receipt_events VALUES ({','.join('?' for _ in RECEIPT_COLUMNS)})", values)

    def build(self, con, *, complete=True, cutoff=date(2025,2,10)):
        return build_oos_replenishment_features(
            con,cutoff,Quality(con,"replen-fixture"),assume_complete_snapshot=complete,
        )

    def row(self, con, columns, day="2025-01-15"):
        return con.execute(f"SELECT {columns} FROM gold.features_oos_replenishment WHERE scoring_date=?", [day]).fetchone()

    def test_pending_plans_use_known_orders_not_future_actuals(self):
        con = self.connection()
        result = self.build(con)
        self.assertEqual(result["rows"],41)
        self.assertFalse(result["application_ready"])
        self.assertEqual(self.row(con,"pending_order_count,pending_units_ordered,scheduled_units_next7d,overdue_units_ordered,days_until_next_scheduled_receipt"),
                         (4,72,20,45,3))

    def test_short_ship_closes_event_without_inventing_backorder(self):
        con = self.connection()
        self.build(con)
        self.assertEqual(self.row(con,"pending_order_count,pending_units_ordered,receipt_units_7d,days_since_receipt","2025-01-16"),
                         (3,32,30,0))

    def test_calendar_windows_and_no_many_to_many_unit_multiplication(self):
        con = self.connection()
        self.build(con)
        self.assertEqual(self.row(con,"receipt_units_7d,receipt_units_28d,pending_units_ordered,scheduled_units_next7d","2025-01-20"),
                         (50,50,22,17))
        self.assertEqual(self.row(con,"receipt_units_7d,receipt_units_28d,days_since_receipt","2025-01-23"),(20,50,3))
        self.assertEqual(self.row(con,"receipt_units_7d,receipt_units_28d,days_since_receipt","2025-01-27"),(0,50,7))

    def test_empty_feeds_unknown_by_default_and_zero_only_if_assumed(self):
        unknown, complete = self.connection(False), self.connection(False)
        self.build(unknown,complete=False)
        self.build(complete)
        fields="receipt_units_7d,pending_order_count,pending_units_ordered,scheduled_units_next7d,overdue_units_ordered,days_until_next_scheduled_receipt"
        self.assertEqual(self.row(unknown,fields),(None,)*6)
        self.assertEqual(self.row(complete,fields),(0,0,0,0,0,None))
        self.assertNotEqual(self.row(unknown,"feature_version"),self.row(complete,"feature_version"))

    def test_late_known_order_and_late_posted_receipt(self):
        con = self.connection(False)
        self.order(con,"LATE","2025-01-10","2025-01-15",9,known="2025-01-13 09:00:00")
        self.receipt(con,"LATE","2025-01-13",8,known="2025-01-16 10:00:00")
        self.build(con)
        self.assertEqual(self.row(con,"pending_order_count,receipt_units_7d","2025-01-12"),(0,0))
        self.assertEqual(self.row(con,"pending_order_count,receipt_units_7d","2025-01-15"),(1,0))
        self.assertEqual(self.row(con,"pending_order_count,receipt_units_7d,days_since_receipt","2025-01-16"),(0,8,3))

    def test_zero_unit_delivery_closes_the_posted_event(self):
        con = self.connection(False)
        self.order(con,"ZERO","2025-01-10","2025-01-15",8)
        self.receipt(con,"ZERO","2025-01-15",0)
        self.build(con)
        self.assertEqual(self.row(con,"pending_order_count,receipt_units_7d,last_receipt_date"),(0,0,date(2025,1,15)))

    def test_receipt_is_retained_when_sell_out_day_is_missing(self):
        con = self.connection()
        con.execute("UPDATE feature_input.daily_state SET inventory_observed=NULL,units_sold_observed=NULL,oos_observed=NULL,inventory_data_status='Unknown',sales_data_status='Unknown' WHERE date='2025-01-16'")
        self.build(con)
        self.assertEqual(self.row(con,"receipt_units_7d,pending_units_ordered","2025-01-16"),(30,32))

    def test_future_mutation_removal_and_truncated_history(self):
        full, changed, truncated = self.connection(),self.connection(),self.connection()
        changed.execute("UPDATE feature_input.receipt_events SET receipt_date='2025-01-25',source_known_at='2025-01-25 23:59:59',receipt_units=999999 WHERE receipt_date>'2025-01-15'")
        truncated.execute("DELETE FROM feature_input.receipt_events WHERE receipt_date>'2025-01-15'")
        truncated.execute("DELETE FROM feature_input.order_schedule_events WHERE order_date>'2025-01-15'")
        truncated.execute("DELETE FROM feature_input.daily_state WHERE date>'2025-01-15'")
        self.build(full)
        self.build(changed)
        self.build(truncated,cutoff=date(2025,1,15))
        sql="SELECT * FROM gold.features_oos_replenishment WHERE scoring_date<='2025-01-15' ORDER BY scoring_date"
        expected=full.execute(sql).fetchall()
        self.assertEqual(changed.execute(sql).fetchall(),expected)
        self.assertEqual(truncated.execute(sql).fetchall(),expected)

    def test_june_scheduled_plan_known_in_may_is_not_removed(self):
        con = self.connection(False)
        con.execute("""INSERT INTO feature_input.daily_state
            SELECT * REPLACE (DATE '2026-05-31' AS date,TIMESTAMP '2026-05-31 23:59:59' AS as_of_timestamp)
            FROM feature_input.daily_state WHERE date='2025-02-10'""")
        self.order(con,"JUNE","2026-05-30","2026-06-02",40)
        self.receipt(con,"JUNE","2026-06-03",39)
        self.build(con,cutoff=date(2026,5,31))
        self.assertEqual(self.row(con,"pending_order_count,scheduled_units_next7d,days_until_next_scheduled_receipt","2026-05-31"),(1,40,2))

    def test_same_pair_multiple_same_day_receipts_are_counted_once(self):
        con = self.connection()
        self.order(con,"EXTRA","2025-01-18","2025-01-20",5)
        self.receipt(con,"EXTRA","2025-01-20",4)
        self.build(con)
        self.assertEqual(self.row(con,"receipt_units_7d","2025-01-20"),(54,))

    def test_duplicate_mismatched_and_invalid_inputs_are_rejected(self):
        statements = (
            "INSERT INTO feature_input.receipt_events SELECT * FROM feature_input.receipt_events LIMIT 1",
            "UPDATE feature_input.order_schedule_events SET build_id='other'",
            "UPDATE feature_input.receipt_events SET store_id='other' WHERE delivery_id='D1'",
            "UPDATE feature_input.receipt_events SET receipt_units=-1",
            "UPDATE feature_input.order_schedule_events SET source_known_at=NULL",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                con=self.connection()
                con.execute(statement)
                with self.assertRaises(QualityError):
                    self.build(con)

    def test_state_and_replenishment_groups_can_coexist_without_fanout(self):
        con = self.connection()
        build_oos_state_features(con,date(2025,2,10),Quality(con,"state-fixture"))
        self.build(con)
        self.assertEqual(con.execute("""SELECT count(*) FROM gold.features_oos_daily d
            JOIN gold.features_oos_replenishment r USING(build_id,scoring_date,store_id,sku_id)""").fetchone()[0],41)
        names=[r[0] for r in con.execute("DESCRIBE gold.features_oos_replenishment").fetchall()]
        self.assertNotIn("actual_delivery",names)
        self.assertNotIn("units_delivered",names)

    def test_isolation_and_june_cutoff_remain_enforced(self):
        con=self.connection()
        con.execute("CREATE TABLE bronze.secret AS SELECT 1 AS secret_field")
        with self.assertRaisesRegex(ValueError,"isolated"):
            self.build(con)
        with self.assertRaisesRegex(ValueError,"June"):
            self.build(self.connection(),cutoff=date(2026,6,1))


if __name__ == "__main__":
    unittest.main()
