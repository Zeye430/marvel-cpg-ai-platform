"""Module 1 shared-data semantics on a small hand-built SYNTHETIC serving fixture.

Each case targets one rule from docs/data/v3-module-1-integration.md section 4
and the KPI specification: known-inventory denominators, MSL states, complete
comparable commercial periods of one family, the individual audited-SKU scope,
calendar-split OOS episodes, Unknown never shown as zero, and no inferred
guardrail approval. No client data, cloud access or model is used.
"""

import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

import duckdb

from data_platform.access import ServingReader
from data_platform.scoped_cache import materialize_business_views
from modules.module_1.data import Filters, Module1Pool

D0 = date(2026, 6, 1)
STORES = [("S1", "SYNTHETIC Store 1", "Modern Trade", "Northeast", "NY", "Tier 1", "City A"),
          ("S2", "SYNTHETIC Store 2", "Traditional Trade", "West", "CA", "Tier 2", "City B")]
PRODUCTS = [("P1", "SYNTHETIC Laundry MSL", "Laundry", "Brand A", True, 5.0, 10.0, 13.0),
            ("P2", "SYNTHETIC Laundry", "Laundry", "Brand A", False, 4.0, 8.0, 11.0),
            ("P3", "SYNTHETIC Dish MSL", "Dishwashing", "Brand B", True, 3.0, 6.0, 8.0)]
SCOPE = [("mgr", "S1", "manager"), ("mgr", "S2", "manager"), ("rep", "S1", "sales")]


def inventory(store, sku, day):
    """Hand-made inventory pattern; None = Unknown report; 'skip' = no assortment row that day."""
    n = (day - D0).days
    if (store, sku) == ("S1", "P1"):  # OOS d0-d1, Unknown d2, OOS d3, no row d4, OOS d5-d6, stocked after
        return {0: 0, 1: 0, 2: None, 3: 0, 4: "skip", 5: 0, 6: 0}.get(n, 7)
    if (store, sku) == ("S2", "P3"):
        return None if n % 3 == 0 else (0 if n == 4 else 9)
    return 4 if n % 5 else 0


def build_fixture(path):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving")
        con.execute("""CREATE TABLE gold.dim_stores (store_id VARCHAR, store_name VARCHAR, channel VARCHAR,
            region VARCHAR, state VARCHAR, segment_tier VARCHAR, city VARCHAR)""")
        con.executemany("INSERT INTO gold.dim_stores VALUES (?,?,?,?,?,?,?)", STORES)
        con.execute("""CREATE TABLE gold.dim_products (sku_id VARCHAR, product_name VARCHAR, category VARCHAR,
            brand VARCHAR, is_msl BOOLEAN, unit_cost DECIMAL(18,2), list_price DECIMAL(18,2), consumer_price DECIMAL(18,2))""")
        con.executemany("INSERT INTO gold.dim_products VALUES (?,?,?,?,?,?,?,?)", PRODUCTS)
        con.execute("CREATE TABLE gold.account_scope (principal_id VARCHAR, store_id VARCHAR, role VARCHAR, scope_version VARCHAR)")
        con.executemany("INSERT INTO gold.account_scope VALUES (?,?,?,'v1')", SCOPE)
        con.execute("""CREATE TABLE gold.daily (date DATE, store_id VARCHAR, sku_id VARCHAR, channel VARCHAR,
            region VARCHAR, segment_tier VARCHAR, category VARCHAR, is_msl BOOLEAN, units_sold_observed DOUBLE,
            inventory_observed DOUBLE, oos_observed BOOLEAN, inventory_data_status VARCHAR, sales_data_status VARCHAR,
            last_report_date DATE)""")
        stores = {s[0]: s for s in STORES}
        rows = []
        for n in range(28):
            day = D0 + timedelta(days=n)
            for store in ("S1", "S2"):
                for sku, _, category, _, msl, *_ in PRODUCTS:
                    inv = inventory(store, sku, day)
                    if inv == "skip":
                        continue
                    _, _, channel, region, _, tier, _ = stores[store]
                    rows.append((day, store, sku, channel, region, tier, category, msl,
                                 None if inv is None else float(n % 4), None if inv is None else float(inv),
                                 None if inv is None else inv == 0, "Unknown" if inv is None else "Observed",
                                 "Unknown" if inv is None else "Observed", day))
        con.executemany("INSERT INTO gold.daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        # store_day built exactly like the shared mart's reported components.
        con.execute("""CREATE TABLE gold.store_day AS
            WITH c AS (SELECT date, store_id, 'reported_only' AS metric_view, any_value(channel) AS channel,
                    any_value(region) AS region, count(*) AS expected_sku_count,
                    count(inventory_observed) AS known_inventory_sku_count,
                    count(*) FILTER (WHERE inventory_data_status='Observed') AS observed_inventory_sku_count,
                    count(*) FILTER (WHERE inventory_observed=0) AS oos_sku_count,
                    count(*) FILTER (WHERE is_msl) AS r, count(*) FILTER (WHERE is_msl AND inventory_observed IS NOT NULL) AS k,
                    count(*) FILTER (WHERE is_msl AND inventory_observed=0) AS u
                FROM gold.daily GROUP BY ALL),
            s AS (SELECT *, CASE WHEN r=0 THEN 'N/A' WHEN u>0 THEN 'Noncompliant' WHEN k=r THEN 'Compliant'
                                 ELSE 'Unknown' END AS msl_state FROM c)
            SELECT * EXCLUDE (r, k, u), (msl_state<>'N/A')::INTEGER AS msl_eligible_store_count,
                   (msl_state IN ('Compliant','Noncompliant'))::INTEGER AS msl_assessable_store_count,
                   (msl_state='Compliant')::INTEGER AS msl_compliant_store_count,
                   (msl_state='Unknown')::INTEGER AS msl_unknown_store_count FROM s""")
        con.execute("""CREATE TABLE gold.periods (period_type VARCHAR, period_start DATE, period_end DATE,
            alignment_basis VARCHAR, store_id VARCHAR, sku_id VARCHAR, metric_view VARCHAR,
            flow_is_comparable BOOLEAN, comparable_sell_in_units DOUBLE, comparable_sell_out_units DOUBLE,
            comparable_gap_units DOUBLE, invoice_extract_complete_assumed BOOLEAN,
            period_is_complete BOOLEAN DEFAULT true)""")
        # a partial calendar period inside the range (e.g. the dataset edge): never compared
        con.execute("INSERT INTO gold.periods VALUES ('biweek', DATE '2026-06-15', DATE '2026-06-28', 'event_date', "
                    "'S1', 'P1', 'reported_only', true, 700, 1, 699, false, false)")
        con.executemany("INSERT INTO gold.periods VALUES (?,?,?,?,?,?,'reported_only',?,?,?,?,false,true)", [
            ("biweek", D0, D0 + timedelta(13), "event_date", "S1", "P1", True, 120, 100, 20),
            ("biweek", D0, D0 + timedelta(13), "event_date", "S1", "P2", False, None, None, None),
            ("biweek", D0, D0 + timedelta(13), "event_date", "S2", "P1", True, 30, 50, -20),
            # partially outside the selected range: excluded
            ("biweek", D0 - timedelta(7), D0 + timedelta(6), "event_date", "S1", "P1", True, 999, 1, 998),
            # another family over the same days: never mixed in
            ("week", D0, D0 + timedelta(6), "event_date", "S1", "P1", True, 5000, 1, 4999),
            # a comparable row with zero sell-out only (for the N/A case)
            ("biweek", D0, D0 + timedelta(13), "event_date", "S2", "P3", True, 10, 0, 10)])
        con.execute("""CREATE TABLE gold.visits (store_id VARCHAR, audit_date DATE, category VARCHAR, sku_scope_id VARCHAR,
            scope_label VARCHAR, audited_row_count BIGINT, compliant_audit_row_count BIGINT,
            secondary_display_row_count BIGINT, shelf_allocation_pct DECIMAL(38,4), visit_count INTEGER)""")
        con.executemany("INSERT INTO gold.visits VALUES (?,?,?,?,?,?,?,?,?,1)", [
            ("S1", D0 + timedelta(2), "Laundry", "sku:P1", "Individual audited SKU", 1, 1, 1, 60),
            ("S1", D0 + timedelta(2), "Laundry", "sku:P2", "Individual audited SKU", 1, 0, 0, 40),
            ("S1", D0 + timedelta(2), "Laundry", "all_category", "All audited category SKUs", 2, 1, 1, 100),
            ("S1", D0 + timedelta(2), "Laundry", "msl_subset", "Audited MSL SKUs", 1, 1, 1, 60),
            ("S2", D0 + timedelta(3), "Dishwashing", "sku:P3", "Individual audited SKU", 1, 1, 0, 100)])
        con.execute("""CREATE TABLE gold.sell_in (transaction_id VARCHAR, date DATE, store_id VARCHAR, sku_id VARCHAR,
            units_sold BIGINT, gross_sales DECIMAL(18,2), net_sales DECIMAL(18,2), guardrail_violation BOOLEAN)""")
        con.executemany("INSERT INTO gold.sell_in VALUES (?,?,?,?,?,?,?,?)", [
            ("T1", D0 + timedelta(1), "S1", "P1", 10, 100, 80, False),
            ("T2", D0 + timedelta(1), "S2", "P1", 10, 100, 95, False)])
        con.execute("""CREATE TABLE gold.context (as_of_date DATE, store_id VARCHAR, sku_id VARCHAR, channel VARCHAR,
            category VARCHAR, standard_discount_pct_source DECIMAL(9,4), max_discount_pct_source DECIMAL(9,4),
            min_target_margin_source DECIMAL(9,4), required_exec_tradeoff VARCHAR, rule_version VARCHAR,
            policy_status VARCHAR)""")
        con.executemany("INSERT INTO gold.context VALUES (DATE '2026-06-28',?,?,?,?,?,?,?,?,?, 'Requires review')", [
            ("S1", "P1", "Modern Trade", "Laundry", 4, 15, 18, "Secondary display", "r1"),
            ("S1", "P2", "Modern Trade", "Laundry", 4, 15, 18, "Secondary display", "r1"),
            ("S2", "P1", "Traditional Trade", "Laundry", 3, 10, 18, "Endcap", "r1"),
            ("S2", "P2", "Traditional Trade", "Laundry", 3, 12, 18, "Endcap", "r2")])
        for view, table in (("stores", "dim_stores"), ("products", "dim_products"), ("store_sku_daily", "daily"),
                            ("store_day", "store_day"), ("commercial_period", "periods"),
                            ("execution_visits", "visits"), ("sell_in_events", "sell_in"),
                            ("negotiation_context", "context")):
            con.execute(f"CREATE VIEW serving.vw_{view} AS SELECT * FROM gold.{table}")
        # The serving view carries the store channel and product category (as the shared mart does).
        con.execute("""CREATE OR REPLACE VIEW serving.vw_commercial_period AS SELECT p.*, s.channel, d.category
            FROM gold.periods p JOIN gold.dim_stores s USING (store_id) JOIN gold.dim_products d USING (sku_id)""")


def reader_for(path):
    # Focused fixture reader (production callers use the verified constructor).
    reader = ServingReader.__new__(ServingReader)
    reader.database = path
    reader.record = {"artifact_id": "synthetic-m1", "as_of": "2026-06-28", "dataset_hash": "synthetic",
                     "model_outputs_status": "Unavailable_model_not_released"}
    return reader


class Module1SharedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic.duckdb"
        build_fixture(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.pool = Module1Pool(reader_for(self.path))
        self.data = self.pool.get("mgr")
        self.addCleanup(self.data.close)

    def filters(self, skus=("P1", "P2", "P3"), channels=("Modern Trade", "Traditional Trade"),
                regions=("Northeast", "West"), start=D0, end=D0 + timedelta(13)):
        return Filters(tuple(channels), tuple(regions), tuple(skus), set(skus) == {"P1", "P2", "P3"}, start, end)

    def reference_rows(self, f):
        with duckdb.connect(str(self.path), read_only=True) as con:
            return con.execute("""SELECT d.store_id, d.sku_id, d.date, d.is_msl, d.inventory_observed
                FROM gold.daily d JOIN gold.dim_stores s USING (store_id)
                WHERE d.date BETWEEN ? AND ? AND list_contains(?, d.sku_id) AND list_contains(?, s.channel)
                  AND list_contains(?, s.region)""",
                               [f.start, f.end, list(f.skus), list(f.channels), list(f.regions)]).fetchall()

    def test_oos_uses_known_inventory_denominator_and_reports_coverage(self):
        for skus in (("P1", "P2", "P3"), ("P1",), ("P3",)):
            with self.subTest(skus=skus):
                f = self.filters(skus)
                rows = self.reference_rows(f)
                known = [r for r in rows if r[4] is not None]
                k = self.data.kpis(f)
                self.assertEqual(k["oos_known"], len(known))
                self.assertAlmostEqual(k["oos"], 100 * sum(r[4] == 0 for r in known) / len(known))
                self.assertAlmostEqual(k["coverage"], 100 * len(known) / len(rows))
                self.assertLess(len(known), len(rows))  # Unknown rows exist and are not counted as in stock

    def test_msl_states_unknown_is_not_failure_and_no_msl_sku_is_na(self):
        f = self.filters()
        rows = self.reference_rows(f)
        states = {}
        for store, sku, day, msl, inv in rows:
            if msl:
                states.setdefault((store, day), []).append(inv)
        compliant = sum(all(v is not None and v > 0 for v in vals) for vals in states.values())
        noncompliant = sum(any(v == 0 for v in vals) for vals in states.values())
        unknown = len(states) - compliant - noncompliant
        k = self.data.kpis(f)
        self.assertGreater(unknown, 0)
        self.assertEqual(k["msl_unknown"], unknown)
        self.assertAlmostEqual(k["msl"], 100 * compliant / (compliant + noncompliant))
        self.assertAlmostEqual(k["msl_coverage"], 100 * (compliant + noncompliant) / len(states))
        na = self.data.kpis(self.filters(("P2",)))  # no mandatory SKU selected
        self.assertIsNone(na["msl"])
        self.assertEqual(na["msl_eligible"], 0)

    def test_all_sku_store_day_and_recomputed_components_agree(self):
        all_sku = self.data.kpis(self.filters())
        f = self.filters()
        recomputed = self.data.kpis(Filters(f.channels, f.regions, f.skus, False, f.start, f.end))
        for key in ("oos", "coverage", "msl", "msl_coverage", "msl_unknown", "expected", "oos_known"):
            self.assertEqual(all_sku[key], recomputed[key], key)

    def test_filters_reconcile_without_fan_out(self):
        total = self.data.availability_components(self.filters())
        parts = [self.data.availability_components(self.filters(channels=(c,))) for c in ("Modern Trade", "Traditional Trade")]
        for key in ("expected", "known", "oos", "msl_eligible", "msl_compliant", "store_days"):
            self.assertEqual(total[key], sum(p[key] for p in parts), key)
        by_sku = [self.data.availability_components(self.filters((s,))) for s in ("P1", "P2", "P3")]
        for key in ("expected", "known", "oos"):
            self.assertEqual(total[key], sum(p[key] for p in by_sku), key)
        empty = self.data.kpis(self.filters(channels=("Distributor",)))
        self.assertIsNone(empty["oos"])  # no rows is N/A, not 0%
        self.assertIsNone(empty["msl"])

    def test_gap_uses_one_family_complete_periods_and_comparable_rows(self):
        k = self.data.kpis(self.filters(("P1", "P2")))
        self.assertAlmostEqual(k["gap_pct"], 100 * (20 - 20) / (100 + 50))
        self.assertEqual(k["gap_units"], 0)
        self.assertEqual(k["gap_periods"], 1)
        self.assertEqual((k["gap_first"], k["gap_last"]), (D0, D0 + timedelta(13)))  # effective bounds
        four_weeks = self.data.kpis(self.filters(("P1", "P2"), end=D0 + timedelta(27)))
        self.assertEqual((four_weeks["gap_periods"], four_weeks["gap_pct"]), (1, k["gap_pct"]))  # partial biweek (699) out
        self.assertEqual(four_weeks["gap_last"], D0 + timedelta(13))
        self.assertEqual((k["gap_rows"], k["gap_comparable_rows"]), (3, 2))
        self.assertTrue(k["gap_completeness_unconfirmed"])
        s1 = self.data.kpis(self.filters(("P1", "P2"), channels=("Modern Trade",)))
        self.assertAlmostEqual(s1["gap_pct"], 20.0)  # the week row (4999) and partial biweek (998) are excluded
        zero = self.data.kpis(self.filters(("P3",), channels=("Traditional Trade",)))
        self.assertIsNone(zero["gap_pct"])  # zero comparable sell-out is N/A
        short = self.data.kpis(self.filters(end=D0 + timedelta(6)))
        self.assertEqual(short["gap_periods"], 0)
        self.assertIsNone(short["gap_pct"])

    def test_execution_uses_individual_sku_scope_only(self):
        k = self.data.kpis(self.filters())
        self.assertEqual(k["audited"], 3)  # 3 SKU rows; overlapping category/MSL rows are not added
        self.assertAlmostEqual(k["plano"], 100 * 2 / 3)
        self.assertEqual(k["visits"], 2)
        self.assertAlmostEqual(k["allocation"], (100 + 100) / 2)  # all SKUs of a visit sum to ~100 by construction
        p1 = self.data.kpis(self.filters(("P1",)))
        self.assertAlmostEqual(p1["allocation"], 60.0)
        self.assertEqual(p1["visits"], 1)

    def test_oos_episodes_split_on_unknown_and_missing_calendar_days(self):
        episodes = self.data.oos_episodes(self.filters(("P1",), channels=("Modern Trade",)), "channel")
        self.assertEqual(sorted(episodes["days"].tolist()), [1, 2, 2])

    def test_latest_inventory_keeps_unknown_unknown(self):
        frame = self.data.latest_inventory(self.filters(("P3",), end=D0 + timedelta(12)))
        s2 = frame[frame["store_name"] == "SYNTHETIC Store 2"].iloc[0]
        self.assertTrue(s2["inventory_observed"] != s2["inventory_observed"] or s2["inventory_observed"] is None)
        self.assertEqual(s2["inventory_data_status"], "Unknown")

    def test_discount_reference_is_standard_plus_maximum_additional(self):
        refs = self.data.references().set_index(["channel", "category"])
        self.assertAlmostEqual(refs.loc[("Modern Trade", "Laundry"), "discount_ceiling_pct"], 19.0)
        self.data.cache.con.execute("UPDATE negotiation_context SET standard_discount_pct_source = 5 "
                                    "WHERE store_id = 'S1'")            # 20% = 5 + 15 exactly: within
        status = dict(zip(*[self.data.trade_terms(self.filters())[c] for c in ("store_id", "reference_status")]))
        self.assertEqual(status["S1"], "Within standard + maximum additional")

    def test_release_without_a_standard_places_no_store_against_a_reference(self):
        self.data.cache.con.execute("ALTER TABLE negotiation_context DROP COLUMN standard_discount_pct_source")
        frame = self.data.trade_terms(self.filters())
        self.assertEqual(set(frame["reference_status"]), {"No single source reference"})
        self.assertTrue(self.data.references()["discount_ceiling_pct"].isna().all())

    def test_trade_terms_require_review_and_never_resolve_conflicting_references(self):
        frame = self.data.trade_terms(self.filters())
        status = dict(zip(frame["store_id"], frame["reference_status"]))
        self.assertEqual(status["S1"], "Above standard + maximum additional (manager review range)")  # 20% > 4 + 15
        self.assertEqual(status["S2"], "No single source reference")
        text = " ".join(frame["reference_status"])
        for forbidden in ("Compliant", "Approved", "Safe Zone"):
            self.assertNotIn(forbidden, text)
        row = frame[frame["store_id"] == "S1"].iloc[0]
        self.assertAlmostEqual(row["trade_discount_pct"], 20.0)
        self.assertAlmostEqual(row["margin_pct_static_cost"], 100 * (80 - 50) / 80)
        evidence = self.data.tradeoff_evidence(self.filters())
        self.assertEqual(evidence["required_exec_tradeoff"].tolist(), ["Secondary display"])

    def test_rep_scope_and_scope_change_close_the_cache(self):
        rep = self.pool.get("rep")
        self.addCleanup(rep.close)
        self.assertEqual(rep.choices()["store_count"], 1)
        self.assertEqual(rep.store_count(self.filters()), 1)
        self.assertFalse(rep.manager)
        with self.assertRaises(PermissionError):
            self.pool.get("nobody")
        with duckdb.connect(str(self.path)) as con:
            con.execute("UPDATE gold.account_scope SET scope_version='v2' WHERE principal_id='rep'")
        self.addCleanup(self._restore_scope)
        with self.assertRaises(PermissionError):
            rep.check()
        self.assertTrue(rep.closed)
        with self.assertRaises(PermissionError):
            rep.kpis(self.filters())
        fresh = self.pool.get("rep")
        self.addCleanup(fresh.close)
        self.assertIsNot(fresh, rep)

    def _restore_scope(self):
        with duckdb.connect(str(self.path)) as con:
            con.execute("UPDATE gold.account_scope SET scope_version='v1'")

    def test_concurrent_requests_build_one_cache_per_principal(self):
        calls = []

        def counting(reader, principal_id, names):
            calls.append(principal_id)
            time.sleep(0.2)
            return materialize_business_views(reader, principal_id, names)

        pool = Module1Pool(reader_for(self.path), materialize=counting)
        results = []
        threads = [threading.Thread(target=lambda: results.append(pool.get("rep"))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls, ["rep"])
        self.assertEqual(len({id(r) for r in results}), 1)
        results[0].close()

    def test_eviction_closes_least_recent_and_never_shares_principals(self):
        pool = Module1Pool(reader_for(self.path), size=1)
        first = pool.get("mgr")
        second = pool.get("rep")
        self.addCleanup(second.close)
        self.assertTrue(first.closed)
        self.assertIsNot(pool.get("rep"), first)
        self.assertEqual(second.principal_id, "rep")


class Module1ServerTextTests(unittest.TestCase):
    def test_no_unsupported_claims_or_legacy_sources_in_module1(self):
        root = Path(__file__).resolve().parents[1] / "modules" / "module_1"
        text = (root / "server.py").read_text() + (root / "ui.py").read_text()
        for claim in ("Met (Delivered)", "Safe Zone (Compliant)", "LIVE TELEMETRY", "fact_sell_out",
                      "dim_guardrails", "rep_id", "fillna(0)", "scope_dataset"):
            self.assertNotIn(claim, text, claim)


if __name__ == "__main__":
    unittest.main()
