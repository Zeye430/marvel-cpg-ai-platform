"""Integration fixtures for data loss, receipt semantics and publication failures."""

import csv
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb

from data_platform.ingestion import digest, literal
from data_platform.pipeline import BuildConfig, build, writer_lock
from data_platform.quality import QualityError


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = BuildConfig(source_dir=self.root / "raw", output_root=self.root / "platform",
                                  snapshot_root=self.root / "snapshots", as_of=date(2025, 1, 7),
                                  memory_limit="256MB", threads=1)
        self.config.source_dir.mkdir()
        self.contract = json.loads(self.config.contract_path.read_text())
        self.rows = {
            "dim_stores": [dict(store_id="S1", store_name='Store, "One"', channel="Modern Trade",
                                region="East", state="NY", segment_tier="Tier 1", city="Test")],
            "dim_products": [dict(sku_id="P1", product_name="Product", category="Dishwashing",
                                  brand="Marvel", is_msl=True, unit_cost=5, list_price=10, consumer_price=15)],
            "dim_guardrails": [dict(channel="Modern Trade", category="Dishwashing", max_discount_pct=25,
                                    min_target_margin=20, required_exec_tradeoff="Secondary display")],
            "dim_promotions": [dict(promo_id=f"PRO-{i}", promo_name="Campaign", promo_type="TPR",
                                    category="Dishwashing", channel="Modern Trade", start_date="2025-01-01",
                                    end_date="2025-01-31", discount_pct=10, min_volume_units=5) for i in (1, 2)],
            "fact_sell_in": [dict(transaction_id="T1", date="2025-01-06", store_id="S1", sku_id="P1",
                                  units_sold=3, gross_sales=30, discount_pct=10, extra_discount_pct=1,
                                  net_sales=27, promo_id="", guardrail_violation=False)],
            "fact_deliveries": [
                self.delivery("D1", "2025-01-01", "2025-01-02", 10),
                self.delivery("D2", "2025-01-02", "2025-01-04", 5),
                self.delivery("D-FUTURE", "2025-01-04", "2026-06-02", 7),
            ],
            "fact_execution_audits": [dict(audit_id="A1", date="2025-01-01", store_id="S1", sku_id="P1",
                                           planogram_compliance=True, shelf_share_pct=100, secondary_display=False)],
            "fact_sell_out": [self.sellout("2025-01-01", 1, 5), self.sellout("2025-01-03", 2, 13),
                              self.sellout("2025-01-04", 3, 15, 5, "D2"), self.sellout("2025-01-05", 15, 0)],
        }
        self.write_sources()

    @staticmethod
    def delivery(delivery_id, order, actual, units):
        return dict(delivery_id=delivery_id, store_id="S1", sku_id="P1", order_date=order,
                    scheduled_delivery=actual, actual_delivery=actual, delay_days=0,
                    delivery_status="On Time", units_ordered=units, units_delivered=units, fill_rate_pct=100)

    @staticmethod
    def sellout(day, sales, inventory, restock=0, delivery=""):
        return dict(date=day, store_id="S1", sku_id="P1", units_sold_daily=sales,
                    inventory_level=inventory, is_out_of_stock=inventory == 0, consumer_price_actual=15,
                    promo_id="", promo_discount_pct=0, restock_units=restock, restock_delivery_id=delivery)

    def write_sources(self):
        for source in self.contract["sources"]:
            with (self.config.source_dir / source["filename"]).open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[c["name"] for c in source["columns"]])
                writer.writeheader()
                writer.writerows(self.rows[source["name"]])

    def open_build(self, result):
        connection = duckdb.connect(result["database"], read_only=True)
        self.addCleanup(connection.close)
        return connection

    def test_receipts_missingness_and_future_projection(self):
        result = build(self.config, publish_silver=True)
        connection = self.open_build(result)
        self.assertFalse(result["application_ready"])
        self.assertEqual(connection.execute("SELECT store_name FROM bronze.dim_stores").fetchone()[0], 'Store, "One"')
        self.assertEqual(connection.execute("SELECT promo_id FROM bronze.fact_sell_out LIMIT 1").fetchone()[0], "")
        self.assertEqual(connection.execute("SELECT units_sold_observed, inventory_observed, oos_observed FROM silver.store_sku_calendar WHERE date='2025-01-02'").fetchone(), (None, None, None))
        self.assertEqual(connection.execute("SELECT receipt_units, reconciliation_status FROM silver.replenishment_events WHERE delivery_id='D1'").fetchone(), (10, "Sell-out not reported"))
        self.assertEqual(connection.execute("SELECT count(*) FROM silver.replenishment_events").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT count(*) FROM silver.order_schedule_events WHERE delivery_id='D-FUTURE'").fetchone()[0], 1)
        columns = {r[0] for r in connection.execute("DESCRIBE silver.order_schedule_events").fetchall()}
        self.assertTrue(columns.isdisjoint({"actual_delivery", "units_delivered", "delivery_status", "delay_days", "fill_rate_pct"}))
        self.assertEqual(connection.execute("SELECT count(*) FROM silver.fact_deliveries WHERE delivery_id='D-FUTURE'").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT sum(abs(residual_units)) FROM silver.inventory_reconciliation").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM silver.promotion_calendar WHERE date='2025-01-02' AND has_overlap").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT DISTINCT rule_status FROM silver.dim_guardrails").fetchone()[0], "Requires review")
        self.assertEqual(connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='gold'").fetchone()[0], 0)

    def test_repeat_load_is_idempotent_and_old_build_immutable(self):
        first = build(self.config, publish_silver=True)
        second = build(self.config, publish_silver=True)
        self.assertEqual(first["dataset_hash"], second["dataset_hash"])
        self.assertEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(first["silver_rows"], second["silver_rows"])
        self.assertNotEqual(first["build_id"], second["build_id"])
        self.assertEqual(second["previous_publication_id"], first["publication_id"])
        self.assertEqual(digest(first["database"]), first["database_sha256"])
        connection = self.open_build(second)
        self.assertEqual(connection.execute("SELECT count(*) FROM bronze.fact_sell_out").fetchone()[0], 4)
        connection.execute(f"ATTACH {literal(first['database'])} AS previous (READ_ONLY)")
        for table in second["silver_rows"]:
            columns = [r[0] for r in connection.execute(f'DESCRIBE silver."{table}"').fetchall()]
            business = ", ".join(f'"{column}"' for column in columns
                                 if column not in {"build_id", "ingestion_run_id", "ingested_at"})
            delta = connection.execute(f"""
                SELECT count(*) FROM (
                    (SELECT {business} FROM silver."{table}"
                     EXCEPT ALL SELECT {business} FROM previous.silver."{table}")
                    UNION ALL
                    (SELECT {business} FROM previous.silver."{table}"
                     EXCEPT ALL SELECT {business} FROM silver."{table}")
                )
            """).fetchone()[0]
            self.assertEqual(delta, 0, table)

    def test_failed_receipt_preserves_previous_publication(self):
        first = build(self.config, publish_silver=True)
        manifest = self.config.output_root / "published/silver.json"
        accepted = manifest.read_bytes()
        self.rows["fact_sell_out"][2]["restock_units"] = 6
        self.write_sources()
        with self.assertRaises(QualityError):
            build(self.config, publish_silver=True)
        self.assertEqual(manifest.read_bytes(), accepted)
        reports = list((self.config.output_root / "builds").glob("*/run.json"))
        failed = [json.loads(p.read_text()) for p in reports if json.loads(p.read_text())["status"] == "Failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("restock_event_match", failed[0]["error"])
        self.assertTrue(Path(first["database"]).exists())

    def test_duplicate_keys_and_fractional_units_are_not_silently_fixed(self):
        self.rows["fact_sell_out"].append(dict(self.rows["fact_sell_out"][0]))
        self.rows["fact_sell_out"][1]["units_sold_daily"] = "2.5"
        self.write_sources()
        with self.assertRaises(QualityError):
            build(self.config)
        report = json.loads(next((self.config.output_root / "builds").glob("*/quality.json")).read_text())
        failures = {r["check_id"] for r in report if not r["passed"]}
        self.assertTrue({"natural_key", "type_units_sold_daily"}.issubset(failures))

    def test_changed_header_rejected_and_snapshot_retained(self):
        path = self.config.source_dir / "fact_sell_out.csv"
        path.write_text(path.read_text().replace("inventory_level", "inventory_changed", 1))
        with self.assertRaises(QualityError):
            build(self.config)
        self.assertEqual(len(list(self.config.snapshot_root.glob("*/fact_sell_out.csv"))), 1)
        self.assertFalse((self.config.output_root / "published/silver.json").exists())

    def test_june_locked_and_second_writer_rejected(self):
        with self.assertRaisesRegex(ValueError, "June outcomes"):
            build(replace(self.config, as_of=date(2026, 6, 1)))
        with writer_lock(self.config.output_root / ".writer.lock"):
            with self.assertRaisesRegex(RuntimeError, "Another writer"):
                build(self.config)

    def test_inventory_equation_failure_is_retained(self):
        self.rows["fact_sell_out"][3]["inventory_level"] = 1
        self.rows["fact_sell_out"][3]["is_out_of_stock"] = False
        self.write_sources()
        with self.assertRaisesRegex(QualityError, "consecutive_inventory_identity"):
            build(self.config)
        run_path = next((self.config.output_root / "builds").glob("*/run.json"))
        connection = self.open_build(json.loads(run_path.read_text()))
        self.assertEqual(connection.execute("SELECT residual_units FROM silver.inventory_reconciliation WHERE date='2025-01-05'").fetchone()[0], 1)

    def test_snapshot_tampering_blocks_reuse(self):
        first = build(self.config, publish_silver=True)
        path = Path(first["snapshot"]) / "dim_stores.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(QualityError, "immutable_snapshot"):
            build(self.config, publish_silver=True)
        manifest = json.loads((self.config.output_root / "published/silver.json").read_text())
        self.assertEqual(manifest["build_id"], first["build_id"])

    def test_interrupted_build_retains_terminal_run_status(self):
        with patch('data_platform.pipeline.shared_relations',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                build(self.config,publish_silver=True)
        report=json.loads(next((self.config.output_root/'builds').glob('*/run.json')).read_text())
        self.assertEqual(report['status'],'Interrupted')
        connection=self.open_build(report)
        self.assertEqual(connection.execute('SELECT status FROM meta.build_runs').fetchone()[0],'Interrupted')
        self.assertFalse((self.config.output_root/'published/silver.json').exists())


if __name__ == "__main__":
    unittest.main()
