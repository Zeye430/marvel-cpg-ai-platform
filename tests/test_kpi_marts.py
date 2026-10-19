"""Business fixtures for denominators, unknown states, dates and flow fan-out."""

import unittest
from datetime import date,timedelta

import duckdb

from data_platform.marts import build_kpi_marts
from data_platform.pipeline import PACKAGE
from data_platform.quality import Quality


class KpiMartTests(unittest.TestCase):
    def fixture(self, complete=False, trim_start=None):
        c=duckdb.connect()
        self.addCleanup(c.close)
        c.execute((PACKAGE/'sql/meta.sql').read_text())
        c.execute("""
            CREATE TABLE gold.mart_store_sku_daily (
                date DATE,store_id VARCHAR,sku_id VARCHAR,build_id VARCHAR,dataset_hash VARCHAR,data_version VARCHAR,
                channel VARCHAR,region VARCHAR,segment_tier VARCHAR,category VARCHAR,is_msl BOOLEAN,
                inventory_observed DOUBLE,inventory_model_state DOUBLE,units_sold_observed DOUBLE,sales_model_state DOUBLE,
                inventory_estimated DOUBLE,units_sold_estimated DOUBLE,inventory_is_observed BOOLEAN,sales_is_observed BOOLEAN,
                consumer_price_observed DECIMAL(18,2),days_since_inventory_report BIGINT,estimator_version VARCHAR
            );
            CREATE TABLE silver.dim_products AS SELECT * FROM (VALUES
                ('P1','Dishwashing',true,5.0),('P2','Dishwashing',true,5.0),('P3','Dishwashing',false,5.0)
            ) t(sku_id,category,is_msl,unit_cost);
            CREATE TABLE silver.dim_stores AS SELECT * FROM (VALUES
                ('A','Modern Trade','East','Tier 1'),('B','Traditional Trade','East','Tier 3'),('C','Distributor','West','Tier 2')
            ) t(store_id,channel,region,segment_tier);
            CREATE TABLE silver.fact_sell_in (
                transaction_id VARCHAR,date DATE,store_id VARCHAR,sku_id VARCHAR,units_sold BIGINT,
                gross_sales DECIMAL(18,2),net_sales DECIMAL(18,2),extra_discount_pct DOUBLE,guardrail_violation BOOLEAN
            );
            INSERT INTO silver.fact_sell_in VALUES
                ('T1','2025-01-06','A','P1',2,20,18,1,false),
                ('T2','2025-01-06','A','P1',3,30,27,2,true),
                ('T3','2025-01-13','A','P1',4,40,36,0,false);
            CREATE TABLE silver.replenishment_events AS SELECT * FROM (VALUES
                (DATE '2025-01-07','A','P1',10),(DATE '2025-01-07','A','P1',20)
            ) t(receipt_date,store_id,sku_id,receipt_units);
            CREATE TABLE silver.fact_execution_audits (
                audit_id VARCHAR,date DATE,store_id VARCHAR,sku_id VARCHAR,planogram_compliance BOOLEAN,
                shelf_share_pct DOUBLE,secondary_display BOOLEAN,build_id VARCHAR,dataset_hash VARCHAR,data_version VARCHAR
            );
            INSERT INTO silver.fact_execution_audits VALUES
                ('A1','2025-01-06','A','P1',true,20,true,'b','h','v3'),
                ('A2','2025-01-06','A','P2',false,30,false,'b','h','v3'),
                ('A3','2025-01-06','A','P3',false,50,false,'b','h','v3'),
                ('A4','2025-01-06','B','P3',true,100,false,'b','h','v3');
        """)
        rows=[]
        for offset in range(8):
            day=date(2025,1,6)+timedelta(days=offset)
            if trim_start is not None and day<trim_start:
                continue
            for store,sku in [('A','P1'),('A','P2'),('A','P3'),('B','P3'),('C','P1')]:
                inv,sales,inv_est,sales_est,observed=10.,1.,None,None,True
                if day==date(2025,1,6) and store=='A' and sku=='P1':inv=0.
                if day in (date(2025,1,6),date(2025,1,7)) and store=='A' and sku=='P2':
                    inv,sales,observed=None,None,False
                    if day==date(2025,1,7):inv_est,sales_est=5.,2.
                if day==date(2025,1,13):sales=0.
                channel={'A':'Modern Trade','B':'Traditional Trade','C':'Distributor'}[store]
                rows.append((day,store,sku,'b','h','v3',channel,'East','Tier 1','Dishwashing',sku!='P3',
                             inv,inv if observed else inv_est,sales,sales if observed else sales_est,
                             inv_est,sales_est,observed,observed,15. if observed else None,
                             0 if observed else 1,'candidate'))
        c.executemany('INSERT INTO gold.mart_store_sku_daily VALUES ('+','.join(['?']*22)+')',rows)
        build_kpi_marts(c,Quality(c,'b'),invoice_extract_complete=complete)
        return c

    def test_msl_unknown_failure_and_no_applicable_sku(self):
        c=self.fixture()
        self.assertEqual(c.execute("SELECT msl_state,msl_assessable_store_count FROM gold.mart_store_day WHERE store_id='A' AND date='2025-01-06' AND metric_view='reported_only'").fetchone(),('Noncompliant',1))
        self.assertEqual(c.execute("SELECT msl_state,msl_compliance_pct,msl_lower_bound_pct,msl_upper_bound_pct FROM gold.mart_store_day WHERE store_id='A' AND date='2025-01-07' AND metric_view='reported_only'").fetchone(),('Unknown',None,0.,100.))
        self.assertEqual(c.execute("SELECT msl_state FROM gold.mart_store_day WHERE store_id='A' AND date='2025-01-07' AND metric_view='operational_estimate'").fetchone()[0],'Compliant')
        self.assertEqual(c.execute("SELECT DISTINCT msl_state,msl_eligible_store_count,msl_compliance_pct FROM gold.mart_store_day WHERE store_id='B'").fetchall(),[('N/A',0,None)])

    def test_oos_aggregation_uses_components_not_average_rates(self):
        c=self.fixture()
        weighted,naive=c.execute("""SELECT 100.0*sum(oos_sku_count)/sum(known_inventory_sku_count),avg(oos_rate_pct)
            FROM gold.mart_store_day WHERE date='2025-01-06' AND metric_view='reported_only'""").fetchone()
        self.assertEqual(weighted,25.)
        self.assertNotAlmostEqual(weighted,naive)

    def test_period_flows_do_not_fan_out_or_shift_official_invoice_dates(self):
        c=self.fixture()
        row=c.execute("""SELECT sell_in_units_available,invoice_net_sales,delivered_units,sell_out_units_available
            FROM gold.mart_commercial_period WHERE store_id='A' AND sku_id='P1' AND period_type='week'
            AND period_start='2025-01-06' AND metric_view='reported_only' AND alignment_basis='event_date'""").fetchone()
        self.assertEqual(row,(5,45,30,7.))
        diag=c.execute("""SELECT sell_in_units_available,is_point_in_time_safe_for_period_end,last_invoice_date
            FROM gold.mart_commercial_period WHERE store_id='A' AND sku_id='P1' AND period_type='week'
            AND period_start='2025-01-06' AND metric_view='reported_only' AND alignment_basis='source_week_diagnostic'""").fetchone()
        self.assertEqual(diag,(4,False,date(2025,1,13)))

    def test_missing_invoice_needs_explicit_completeness_and_zero_sales_is_na(self):
        c=self.fixture()
        self.assertIsNone(c.execute("SELECT sell_in_units_available FROM gold.mart_commercial_period WHERE store_id='B' AND period_type='month' AND metric_view='reported_only' AND alignment_basis='event_date'").fetchone()[0])
        self.assertIsNone(c.execute("SELECT sell_in_out_gap_pct FROM gold.mart_commercial_period WHERE store_id='A' AND sku_id='P1' AND period_type='week' AND period_start='2025-01-13' AND metric_view='reported_only' AND alignment_basis='event_date'").fetchone()[0])
        explicit=self.fixture(complete=True)
        self.assertEqual(explicit.execute("SELECT sell_in_units_available FROM gold.mart_commercial_period WHERE store_id='B' AND period_type='month' AND metric_view='reported_only' AND alignment_basis='event_date'").fetchone()[0],0)
        self.assertIsNone(explicit.execute("SELECT sell_in_units_available FROM gold.mart_commercial_period WHERE store_id='B' AND period_start='2025-01-13' AND alignment_basis='source_week_diagnostic' AND metric_view='reported_only'").fetchone()[0])

    def test_audit_scope_and_planogram_weighting(self):
        c=self.fixture()
        row=c.execute("SELECT 100.0*sum(compliant_audit_row_count)/sum(audited_row_count),avg(planogram_compliance_pct) FROM gold.mart_execution_visit WHERE sku_scope_id='all_category'").fetchone()
        self.assertEqual(row[0],50.)
        self.assertNotAlmostEqual(row[0],row[1])
        self.assertEqual(c.execute("SELECT shelf_allocation_pct FROM gold.mart_execution_visit WHERE store_id='A' AND sku_scope_id='msl_subset'").fetchone()[0],50.)
        self.assertEqual(c.execute("SELECT count(*) FROM gold.mart_execution_visit WHERE audit_date>'2025-01-06'").fetchone()[0],0)

    def test_partial_start_week_keeps_diagnostic_invoice_but_not_earlier_event(self):
        c=self.fixture(trim_start=date(2025,1,8))
        rows=dict(c.execute("""SELECT alignment_basis,sell_in_units_available
            FROM gold.mart_commercial_period WHERE store_id='A' AND sku_id='P1'
            AND period_type='week' AND period_start='2025-01-06' AND metric_view='reported_only'""").fetchall())
        self.assertEqual(rows,{'event_date':None,'source_week_diagnostic':4})

    def test_gap_rollup_excludes_unknown_flow_from_both_sides(self):
        c=self.fixture()
        value=c.execute("""SELECT 100.0*sum(comparable_gap_units)/sum(comparable_sell_out_units)
            FROM gold.mart_commercial_period WHERE period_type='week' AND period_start='2025-01-06'
            AND metric_view='reported_only' AND alignment_basis='event_date'""").fetchone()[0]
        self.assertAlmostEqual(value,-200/7)


if __name__=='__main__':
    unittest.main()
