"""Complete-view extraction on isolated synthetic data; no cloud/client data."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import duckdb

from data_platform.access import DataUnavailable, ServingReader
from data_platform.scoped_cache import materialize_business_views


class ScopedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "synthetic.duckdb"
        with duckdb.connect(str(path)) as con:
            con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving")
            con.execute("CREATE TABLE gold.dim_stores AS SELECT * FROM (VALUES ('S1'),('S2')) t(store_id)")
            con.execute("CREATE TABLE gold.account_scope AS SELECT * FROM (VALUES "
                        "('sales','S1','sales','v1'),('other','S2','sales','v1')) t(principal_id,store_id,role,scope_version)")
            con.execute("CREATE VIEW serving.vw_stores AS SELECT * FROM gold.dim_stores")
            con.execute("CREATE TABLE gold.daily AS SELECT 'S1' AS store_id, i::VARCHAR AS sku_id, "
                        "DATE '2026-06-30' AS date, CASE WHEN i=0 THEN NULL ELSE i::DOUBLE END AS units_sold_observed, "
                        "12.34::DECIMAL(18,2) AS consumer_price_observed, "
                        "CASE WHEN i=0 THEN NULL ELSE false END AS oos_observed FROM range(10005) t(i)")
            con.execute("INSERT INTO gold.daily VALUES ('S2','private',DATE '2026-06-30',99,99.99,true)")
            con.execute("UPDATE gold.daily SET units_sold_observed='NaN'::DOUBLE WHERE sku_id='1'")
            con.execute("ALTER TABLE gold.daily ADD COLUMN large_counter HUGEINT")
            con.execute("UPDATE gold.daily SET large_counter=1267650600228229401496703205383")
            con.execute("CREATE VIEW serving.vw_store_sku_daily AS SELECT * FROM gold.daily")
        # Synthetic focused reader fixture: production callers use the verified
        # constructor. This fixture avoids a full pipeline for every unit case.
        self.reader = ServingReader.__new__(ServingReader)
        self.reader.database = path
        self.reader.record = {"artifact_id": "synthetic", "as_of": "2026-06-30",
                              "dataset_hash": "synthetic", "model_outputs_status": "Unavailable_model_not_released"}

    def cache(self, **kwargs):
        cache = materialize_business_views(self.reader, "sales", ["stores", "store_sku_daily"], **kwargs)
        self.addCleanup(cache.close)
        return cache

    def test_complete_population_over_ten_thousand_and_typed_nulls(self):
        cache = self.cache(batch_rows=1000)
        self.assertEqual(cache.row_counts, {"stores": 1, "store_sku_daily": 10005})
        self.assertEqual(cache.con.execute("SELECT count(*) FROM store_sku_daily WHERE store_id='S2'").fetchone()[0], 0)
        row = cache.con.execute("SELECT date,units_sold_observed,consumer_price_observed,oos_observed "
                                "FROM store_sku_daily WHERE sku_id='0'").fetchone()
        self.assertEqual(row, (date(2026, 6, 30), None, Decimal("12.34"), None))
        self.assertEqual(cache.record["artifact_id"], "synthetic")

    def test_nan_and_huge_integers_do_not_become_null_or_rounded(self):
        cache = self.cache()
        self.assertEqual(cache.con.execute("SELECT isnan(units_sold_observed),large_counter "
                                            "FROM store_sku_daily WHERE sku_id='1'").fetchone(),
                         (True, 2 ** 100 + 7))

    def test_budget_failure_never_returns_partial_data(self):
        with self.assertRaisesRegex(DataUnavailable, "no partial data"):
            self.cache(max_rows=10000)

    def test_unknown_principal_and_unapproved_views_denied(self):
        with self.assertRaises(PermissionError):
            materialize_business_views(self.reader, "unknown", ["stores"])
        for names in (["gold.daily"], ["stores", "stores"], []):
            with self.subTest(names=names), self.assertRaises(ValueError):
                materialize_business_views(self.reader, "sales", names)
        with self.assertRaises(DataUnavailable):
            materialize_business_views(self.reader, "sales", ["oos_predictions"])

    def test_scope_change_discards_cache(self):
        cache = self.cache()
        with duckdb.connect(str(self.reader.database)) as con:
            con.execute("UPDATE gold.account_scope SET scope_version='v2' WHERE principal_id='sales'")
        with self.assertRaises(PermissionError):
            cache.check_access()
        self.assertTrue(cache.closed)

    def test_scope_change_during_build_does_not_return_cache(self):
        original = self.reader.principal_scope
        calls = 0

        def changed(pid):
            nonlocal calls
            calls += 1
            result = original(pid)
            if calls > 1:
                result["scope_fingerprint"] = "changed"
            return result

        with patch.object(self.reader, "principal_scope", side_effect=changed), self.assertRaises(PermissionError):
            self.cache()

    def test_invalid_budget_and_untrusted_reader_rejected(self):
        for kwargs in ({"batch_rows": 10001}, {"batch_rows": True}, {"max_rows": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.cache(**kwargs)
        with self.assertRaises(TypeError):
            materialize_business_views(object(), "sales", ["stores"])


if __name__ == "__main__":
    unittest.main()
