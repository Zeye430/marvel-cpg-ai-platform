"""Shared KPI components, period flows and visit-scoped execution evidence."""

from .ingestion import literal

METRIC_VERSION = "shared_kpi.v1"


def build_kpi_marts(connection, quality, *, invoice_extract_complete=False):
    """Build internal marts; candidate estimates never become observed facts."""
    connection.execute("""
        CREATE TEMP VIEW _kpi_daily AS
        SELECT d.date,d.store_id,d.sku_id,d.build_id,d.dataset_hash,d.data_version,
               d.channel,d.region,d.segment_tier,d.category,d.is_msl,v.metric_view,
               d.inventory_is_observed,d.sales_is_observed,
               d.days_since_inventory_report,d.estimator_version,
               CASE WHEN v.metric_view='reported_only' THEN d.inventory_observed
                    ELSE d.inventory_model_state END AS inventory,
               CASE WHEN v.metric_view='reported_only' THEN d.units_sold_observed
                    ELSE d.sales_model_state END AS sales,
               v.metric_view='operational_estimate' AND d.inventory_estimated IS NOT NULL AS inventory_is_estimated,
               v.metric_view='operational_estimate' AND d.units_sold_estimated IS NOT NULL AS sales_is_estimated,
               cast(d.units_sold_observed AS DECIMAL(18,0))*d.consumer_price_observed AS consumer_value_observed,
               CASE WHEN v.metric_view='reported_only' THEN 'Reported source facts'
                    ELSE 'Candidate operational estimates; not approved' END AS release_status
        FROM gold.mart_store_sku_daily d
        CROSS JOIN (VALUES ('reported_only'),('operational_estimate')) v(metric_view)
    """)
    build_store_day(connection)
    build_commercial_periods(connection, invoice_extract_complete)
    build_execution_visits(connection)
    build_metric_definitions(connection)
    validate_marts(connection, quality)
    return {
        "metric_version": METRIC_VERSION,
        "invoice_extract_complete_assumed": invoice_extract_complete,
        "rows": {name: connection.execute(f"SELECT count(*) FROM gold.{name}").fetchone()[0]
                 for name in ("mart_store_day", "mart_commercial_period", "mart_execution_visit", "metric_definitions")},
        "status": "Internal KPI marts; operational estimates remain candidate; no application publication",
    }


def build_store_day(con):
    con.execute(f"""
        CREATE TABLE gold.mart_store_day AS
        WITH components AS (
            SELECT date,store_id,metric_view,build_id,dataset_hash,data_version,
                   any_value(channel) AS channel,any_value(region) AS region,
                   any_value(segment_tier) AS segment_tier,
                   count(*) AS expected_sku_count,
                   count(inventory) AS known_inventory_sku_count,
                   count(*) FILTER(WHERE inventory_is_observed) AS observed_inventory_sku_count,
                   count(*) FILTER(WHERE inventory_is_estimated) AS estimated_inventory_sku_count,
                   count(*) FILTER(WHERE inventory IS NULL) AS unknown_inventory_sku_count,
                   count(*) FILTER(WHERE inventory=0) AS oos_sku_count,
                   count(sales) AS known_sales_sku_count,
                   count(*) FILTER(WHERE sales_is_observed) AS observed_sales_sku_count,
                   count(*) FILTER(WHERE sales_is_estimated) AS estimated_sales_sku_count,
                   sum(sales) AS sell_out_units_available,
                   count(*) FILTER(WHERE is_msl) AS required_msl_sku_count,
                   count(*) FILTER(WHERE is_msl AND inventory IS NOT NULL) AS known_msl_sku_count,
                   count(*) FILTER(WHERE is_msl AND inventory=0) AS unavailable_msl_sku_count,
                   count(*) FILTER(WHERE is_msl AND inventory IS NULL) AS unknown_msl_sku_count,
                   max(days_since_inventory_report) AS max_days_since_inventory_report,
                   any_value(release_status) AS release_status,
                   any_value(estimator_version) AS estimator_version
            FROM _kpi_daily GROUP BY date,store_id,metric_view,build_id,dataset_hash,data_version
        ), states AS (
            SELECT *, CASE WHEN required_msl_sku_count=0 THEN 'N/A'
                           WHEN unavailable_msl_sku_count>0 THEN 'Noncompliant'
                           WHEN known_msl_sku_count=required_msl_sku_count THEN 'Compliant'
                           ELSE 'Unknown' END AS msl_state
            FROM components
        ), store_counts AS (
            SELECT *, (msl_state<>'N/A')::INTEGER AS msl_eligible_store_count,
                      (msl_state IN ('Compliant','Noncompliant'))::INTEGER AS msl_assessable_store_count,
                      (msl_state='Compliant')::INTEGER AS msl_compliant_store_count,
                      (msl_state='Noncompliant')::INTEGER AS msl_noncompliant_store_count,
                      (msl_state='Unknown')::INTEGER AS msl_unknown_store_count
            FROM states
        )
        SELECT *, {literal(METRIC_VERSION)} AS metric_version,
               100.0*oos_sku_count/nullif(known_inventory_sku_count,0) AS oos_rate_pct,
               100.0*observed_inventory_sku_count/expected_sku_count AS reporting_coverage_pct,
               100.0*known_inventory_sku_count/expected_sku_count AS known_inventory_coverage_pct,
               100.0*estimated_inventory_sku_count/nullif(known_inventory_sku_count,0) AS estimated_inventory_share_of_known_pct,
               100.0*msl_compliant_store_count/nullif(msl_assessable_store_count,0) AS msl_compliance_pct,
               100.0*msl_assessable_store_count/nullif(msl_eligible_store_count,0) AS msl_assessment_coverage_pct,
               100.0*msl_compliant_store_count/nullif(msl_eligible_store_count,0) AS msl_lower_bound_pct,
               100.0*(msl_compliant_store_count+msl_unknown_store_count)/nullif(msl_eligible_store_count,0) AS msl_upper_bound_pct,
               'Active assortment with global is_msl; applicability requires review' AS msl_applicability_policy
        FROM store_counts
    """)


def build_commercial_periods(con, invoice_extract_complete):
    complete = "true" if invoice_extract_complete else "false"
    con.execute("""
        CREATE TEMP TABLE _commercial_dates AS
        WITH dates AS (SELECT DISTINCT date FROM gold.mart_store_sku_daily),
        base AS (
            SELECT date,'week' AS period_type,cast(date_trunc('week',date) AS DATE) AS period_start,
                   cast(date_trunc('week',date) AS DATE)+6 AS period_end,'event_date' AS alignment_basis FROM dates
            UNION ALL
            SELECT date,'biweek',DATE '2025-01-06'+cast(14*floor(date_diff('day',DATE '2025-01-06',date)/14.0) AS INTEGER),
                   DATE '2025-01-06'+cast(14*floor(date_diff('day',DATE '2025-01-06',date)/14.0) AS INTEGER)+13,'event_date' FROM dates
            UNION ALL
            SELECT date,'month',cast(date_trunc('month',date) AS DATE),last_day(date),'event_date' FROM dates
            UNION ALL
            SELECT date,'week',cast(date_trunc('week',date) AS DATE),cast(date_trunc('week',date) AS DATE)+6,
                   'source_week_diagnostic' FROM dates
        ) SELECT * FROM base
    """)
    # Aggregate each flow independently before joining: no invoice/sales/receipt fan-out.
    sales_query="""
        SELECT p.period_type,p.period_start,p.period_end,p.alignment_basis,
               d.store_id,d.sku_id,d.metric_view,
               min(d.date) AS covered_start,max(d.date) AS covered_end,count(*) AS expected_sku_days,
               count(d.sales) AS known_sell_out_days,
               count(*) FILTER(WHERE d.sales_is_observed) AS reported_sell_out_days,
               count(*) FILTER(WHERE d.sales_is_estimated) AS estimated_sell_out_days,
               sum(d.sales) AS sell_out_units_available,
               sum(d.consumer_value_observed) AS consumer_sales_value_observed
        FROM _kpi_daily d JOIN _commercial_dates p USING(date)
        WHERE p.period_type={period_type} AND p.alignment_basis={alignment_basis}
        GROUP BY p.period_type,p.period_start,p.period_end,p.alignment_basis,d.store_id,d.sku_id,
                 d.metric_view
    """
    # Bound aggregation memory by processing one period family at a time. Add
    # repeated build/dimension context only after aggregation, not in every hash state.
    for index,(period_type,alignment_basis) in enumerate([
        ('week','event_date'),('biweek','event_date'),('month','event_date'),('week','source_week_diagnostic')
    ]):
        prefix='CREATE TEMP TABLE _commercial_sales AS ' if index==0 else 'INSERT INTO _commercial_sales '
        con.execute(prefix+sales_query.format(period_type=literal(period_type),alignment_basis=literal(alignment_basis)))
    con.execute("""
        CREATE TEMP TABLE _commercial_invoices AS
        WITH aligned AS (
            SELECT i.*,p.unit_cost,i.date AS alignment_date,'event_date' AS alignment_basis
            FROM silver.fact_sell_in i JOIN silver.dim_products p USING(sku_id)
            UNION ALL
            SELECT i.*,p.unit_cost,i.date-7 AS alignment_date,'source_week_diagnostic' AS alignment_basis
            FROM silver.fact_sell_in i JOIN silver.dim_products p USING(sku_id)
        )
        SELECT d.period_type,d.period_start,d.alignment_basis,i.store_id,i.sku_id,
               count(*) AS invoice_line_count,sum(i.units_sold) AS invoice_units_observed,
               sum(i.gross_sales) AS invoice_gross_sales,sum(i.net_sales) AS invoice_net_sales,
               sum(i.gross_sales-i.net_sales) AS invoice_discount_value,
               sum(i.units_sold*i.unit_cost) AS invoice_cost_at_static_reference,
               sum(i.units_sold*i.extra_discount_pct) AS extra_discount_unit_weighted_sum,
               count(*) FILTER(WHERE i.guardrail_violation) AS source_violation_line_count,
               max(i.date) AS last_invoice_date
        FROM aligned i JOIN (
            SELECT DISTINCT period_type,period_start,period_end,alignment_basis FROM _commercial_dates
        ) d ON i.alignment_basis=d.alignment_basis AND i.alignment_date BETWEEN d.period_start AND d.period_end
        WHERE i.alignment_basis='source_week_diagnostic' OR i.alignment_date BETWEEN
            (SELECT min(date) FROM gold.mart_store_sku_daily) AND (SELECT max(date) FROM gold.mart_store_sku_daily)
        GROUP BY d.period_type,d.period_start,d.alignment_basis,i.store_id,i.sku_id
    """)
    con.execute("""
        CREATE TEMP TABLE _commercial_receipts AS
        SELECT p.period_type,p.period_start,p.alignment_basis,r.store_id,r.sku_id,
               sum(r.receipt_units) AS delivered_units,count(*) AS delivery_event_count
        FROM silver.replenishment_events r JOIN _commercial_dates p ON r.receipt_date=p.date
        GROUP BY p.period_type,p.period_start,p.alignment_basis,r.store_id,r.sku_id
    """)
    con.execute(f"""
        CREATE TABLE gold.mart_commercial_period AS
        WITH joined AS (
            SELECT s.*,b.build_id,b.dataset_hash,b.data_version,b.estimator_version,
                   stores.channel,stores.region,stores.segment_tier,products.category,
                   CASE WHEN s.metric_view='reported_only' THEN 'Reported source facts'
                        ELSE 'Candidate operational estimates; not approved' END AS release_status,
                   coalesce(i.invoice_line_count,0) AS invoice_line_count,
                   i.invoice_units_observed,i.invoice_gross_sales,i.invoice_net_sales,
                   i.invoice_discount_value,i.invoice_cost_at_static_reference,
                   i.extra_discount_unit_weighted_sum,i.source_violation_line_count,i.last_invoice_date,
                   coalesce(r.delivered_units,0) AS delivered_units,
                   coalesce(r.delivery_event_count,0) AS delivery_event_count,
                   {complete} AS invoice_extract_complete_assumed,
                   CASE WHEN s.alignment_basis='source_week_diagnostic' THEN s.period_end+1 END AS expected_invoice_date,
                   (SELECT max(date) FROM gold.mart_store_sku_daily) AS data_cutoff,
                   s.covered_start=s.period_start AND s.covered_end=s.period_end AS period_is_complete
            FROM _commercial_sales s LEFT JOIN _commercial_invoices i
              USING(period_type,period_start,alignment_basis,store_id,sku_id)
            LEFT JOIN _commercial_receipts r USING(period_type,period_start,alignment_basis,store_id,sku_id)
            JOIN silver.dim_stores stores ON s.store_id=stores.store_id
            JOIN silver.dim_products products ON s.sku_id=products.sku_id
            CROSS JOIN (SELECT DISTINCT build_id,dataset_hash,data_version,estimator_version FROM gold.mart_store_sku_daily) b
        ), flows AS (
            SELECT *, CASE WHEN alignment_basis='source_week_diagnostic' AND expected_invoice_date>data_cutoff THEN NULL
                           WHEN invoice_line_count>0 THEN invoice_units_observed
                           WHEN invoice_extract_complete_assumed THEN 0 ELSE NULL END AS sell_in_units_available
            FROM joined
        )
        SELECT *,{literal(METRIC_VERSION)} AS metric_version,
               expected_sku_days-known_sell_out_days AS unknown_sell_out_days,
               100.0*reported_sell_out_days/expected_sku_days AS reporting_coverage_pct,
               100.0*known_sell_out_days/expected_sku_days AS known_sales_coverage_pct,
               sell_in_units_available-sell_out_units_available AS sell_in_out_gap_units,
               sell_in_units_available IS NOT NULL AND sell_out_units_available IS NOT NULL AS flow_is_comparable,
               CASE WHEN sell_in_units_available IS NOT NULL AND sell_out_units_available IS NOT NULL
                    THEN sell_in_units_available END AS comparable_sell_in_units,
               CASE WHEN sell_in_units_available IS NOT NULL AND sell_out_units_available IS NOT NULL
                    THEN sell_out_units_available END AS comparable_sell_out_units,
               CASE WHEN sell_in_units_available IS NOT NULL AND sell_out_units_available IS NOT NULL
                    THEN sell_in_units_available-sell_out_units_available END AS comparable_gap_units,
               100.0*(sell_in_units_available-sell_out_units_available)/nullif(sell_out_units_available,0) AS sell_in_out_gap_pct,
               delivered_units-sell_out_units_available AS delivered_out_gap_units,
               100.0*invoice_discount_value/nullif(invoice_gross_sales,0) AS trade_discount_weighted_pct,
               invoice_net_sales/nullif(invoice_units_observed,0) AS net_trade_unit_price,
               invoice_net_sales-invoice_cost_at_static_reference AS trade_margin_value_at_static_cost,
               100.0*(invoice_net_sales-invoice_cost_at_static_reference)/nullif(invoice_net_sales,0) AS trade_margin_pct_at_static_cost,
               extra_discount_unit_weighted_sum/nullif(invoice_units_observed,0) AS source_extra_discount_unit_weighted_pct,
               CASE WHEN alignment_basis='source_week_diagnostic' AND expected_invoice_date>data_cutoff THEN 'Awaiting invoice date'
                    WHEN sell_in_units_available IS NULL OR sell_out_units_available IS NULL THEN 'Unknown flow'
                    WHEN known_sell_out_days<expected_sku_days THEN 'Partial Sell-out coverage'
                    WHEN NOT period_is_complete THEN 'Partial calendar period'
                    WHEN NOT invoice_extract_complete_assumed THEN 'Invoice extract completeness unconfirmed'
                    ELSE 'Complete under extract assumption' END AS comparison_status,
               coalesce(last_invoice_date<=period_end,true) AND alignment_basis='event_date' AS is_point_in_time_safe_for_period_end,
               'Static product unit_cost; not historical invoice cost evidence' AS margin_cost_basis,
               'Reported invoice discount; extra discount composition not inferred' AS discount_interpretation,
               channel<>'Traditional Trade' OR period_type IN ('biweek','month') AS recommended_channel_period,
               '2025-01-06 Monday' AS biweek_anchor,
               'Delivery stream complete within supplied snapshot: simulation assumption' AS receipt_coverage_policy
        FROM flows
    """)


def build_execution_visits(con):
    con.execute(f"""
        CREATE TABLE gold.mart_execution_visit AS
        WITH scoped AS (
            SELECT a.*,p.category,s.channel,s.region,s.segment_tier,v.sku_scope_id,v.scope_label
            FROM silver.fact_execution_audits a JOIN silver.dim_products p USING(sku_id)
            JOIN silver.dim_stores s USING(store_id),
            LATERAL (
                SELECT 'all_category' AS sku_scope_id,'All audited category SKUs' AS scope_label
                UNION ALL SELECT 'sku:'||a.sku_id,'Individual audited SKU'
                UNION ALL SELECT 'msl_subset','Audited MSL SKUs' WHERE p.is_msl
            ) v
        )
        SELECT store_id,date AS audit_date,category,sku_scope_id,build_id,dataset_hash,data_version,
               any_value(channel) AS channel,any_value(region) AS region,any_value(segment_tier) AS segment_tier,
               any_value(scope_label) AS scope_label,count(*) AS audited_row_count,
               count(DISTINCT sku_id) AS audited_sku_count,
               count(*) FILTER(WHERE planogram_compliance) AS compliant_audit_row_count,
               count(*) FILTER(WHERE secondary_display) AS secondary_display_row_count,
               sum(shelf_share_pct) AS shelf_allocation_pct,1 AS visit_count,
               100.0*count(*) FILTER(WHERE planogram_compliance)/count(*) AS planogram_compliance_pct,
               {literal(METRIC_VERSION)} AS metric_version,
               'Audited supplied SKU allocation; not competitor/market share' AS shelf_share_scope,
               'Observed audit visit; not interpolated daily state' AS release_status
        FROM scoped GROUP BY store_id,date,category,sku_scope_id,build_id,dataset_hash,data_version
    """)


def build_metric_definitions(con):
    con.execute("""CREATE TABLE gold.metric_definitions (
        metric_id VARCHAR,metric_version VARCHAR,unit VARCHAR,numerator_column VARCHAR,
        denominator_column VARCHAR,formula VARCHAR,source_relation VARCHAR,scope_note VARCHAR,
        zero_denominator_policy VARCHAR,release_note VARCHAR
    )""")
    rows = [
        ("msl_compliance", "msl_compliant_store_count", "msl_assessable_store_count", "mart_store_day", "Assessable store-days; global MSL within active assortment"),
        ("msl_assessment_coverage", "msl_assessable_store_count", "msl_eligible_store_count", "mart_store_day", "Unknown is not measured failure; no applicable MSL is N/A"),
        ("msl_lower_bound", "msl_compliant_store_count", "msl_eligible_store_count", "mart_store_day", "Lower bound across eligible store-days"),
        ("oos_rate", "oos_sku_count", "known_inventory_sku_count", "mart_store_day", "Select reported_only or operational_estimate explicitly"),
        ("inventory_reporting_coverage", "observed_inventory_sku_count", "expected_sku_count", "mart_store_day", "Observed only, independent of operational reconstruction"),
        ("known_inventory_coverage", "known_inventory_sku_count", "expected_sku_count", "mart_store_day", "Unknown states excluded from OOS denominator and counted separately"),
        ("trade_discount", "invoice_discount_value", "invoice_gross_sales", "mart_commercial_period", "Weighted invoice discount; consumer promotions are separate"),
        ("planogram_compliance", "compliant_audit_row_count", "audited_row_count", "mart_execution_visit", "Choose one SKU scope; never combine overlapping scope rows"),
    ]
    con.executemany("INSERT INTO gold.metric_definitions VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (name,METRIC_VERSION,"percent",num,den,f"100 * SUM({num}) / NULLIF(SUM({den}), 0)",
         "gold."+relation,note,"NULL / N/A","Candidate estimates remain distinct from reported facts")
        for name,num,den,relation,note in rows
    ])
    con.executemany("INSERT INTO gold.metric_definitions VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("sell_in_out_gap",METRIC_VERSION,"percent","comparable_gap_units","comparable_sell_out_units",
         "100 * SUM(comparable_gap_units) / NULLIF(SUM(comparable_sell_out_units),0)",
         "gold.mart_commercial_period","Use identical period/view/alignment; require known comparable flows",
         "NULL / N/A","Invoice completeness and Sell-out coverage are explicit"),
        ("audited_sku_shelf_allocation",METRIC_VERSION,"percent","shelf_allocation_pct","visit_count",
         "SUM(shelf_allocation_pct) / SUM(visit_count)","gold.mart_execution_visit",
         "Average visit-level allocations for one consistent SKU scope; not market share","NULL / N/A","Observed audit visits only"),
        ("msl_upper_bound",METRIC_VERSION,"percent","msl_compliant_store_count + msl_unknown_store_count","msl_eligible_store_count",
         "100 * SUM(msl_compliant_store_count + msl_unknown_store_count) / NULLIF(SUM(msl_eligible_store_count),0)",
         "gold.mart_store_day","Upper bound across eligible store-days; Unknown is not measured compliance",
         "NULL / N/A","Bounds must accompany assessment coverage"),
    ])


def validate_marts(con, quality):
    checks = {
        "store_day_grain": "SELECT date,store_id,metric_view FROM gold.mart_store_day GROUP BY 1,2,3 HAVING count(*)<>1",
        "inventory_partition": "SELECT date,store_id FROM gold.mart_store_day WHERE known_inventory_sku_count+unknown_inventory_sku_count<>expected_sku_count OR observed_inventory_sku_count+estimated_inventory_sku_count<>known_inventory_sku_count",
        "msl_partition": "SELECT date,store_id FROM gold.mart_store_day WHERE msl_compliant_store_count+msl_noncompliant_store_count<>msl_assessable_store_count OR msl_assessable_store_count+msl_unknown_store_count<>msl_eligible_store_count",
        "unknown_msl_not_failed": "SELECT date,store_id FROM gold.mart_store_day WHERE msl_state='Unknown' AND (msl_noncompliant_store_count<>0 OR msl_compliance_pct IS NOT NULL)",
        "reported_has_no_estimates": "SELECT date,store_id FROM gold.mart_store_day WHERE metric_view='reported_only' AND (estimated_inventory_sku_count<>0 OR estimated_sales_sku_count<>0)",
        "commercial_grain": "SELECT period_type,period_start,store_id,sku_id,metric_view,alignment_basis FROM gold.mart_commercial_period GROUP BY 1,2,3,4,5,6 HAVING count(*)<>1",
        "unknown_invoice_not_zero": "SELECT period_start,store_id,sku_id FROM gold.mart_commercial_period WHERE invoice_line_count=0 AND NOT invoice_extract_complete_assumed AND sell_in_units_available IS NOT NULL",
        "zero_sell_out_gap_na": "SELECT period_start,store_id,sku_id FROM gold.mart_commercial_period WHERE (sell_out_units_available=0 OR sell_out_units_available IS NULL) AND sell_in_out_gap_pct IS NOT NULL",
        "awaiting_invoice_not_zero": "SELECT period_start,store_id,sku_id FROM gold.mart_commercial_period WHERE comparison_status='Awaiting invoice date' AND sell_in_units_available IS NOT NULL",
        "audit_scope_grain": "SELECT store_id,audit_date,category,sku_scope_id FROM gold.mart_execution_visit GROUP BY 1,2,3,4 HAVING count(*)<>1",
        "shelf_allocation_scope": "SELECT store_id,audit_date,category FROM gold.mart_execution_visit WHERE sku_scope_id='all_category' AND abs(shelf_allocation_pct-100)>.031",
        "audit_denominators": "SELECT store_id,audit_date FROM gold.mart_execution_visit WHERE compliant_audit_row_count>audited_row_count OR audited_row_count=0",
    }
    for name, sql in checks.items():
        table = next(table for table in ("mart_store_day","mart_commercial_period","mart_execution_visit")
                     if "gold."+table in sql)
        population = con.execute(f"SELECT count(*) FROM gold.{table}").fetchone()[0]
        quality.query(name,"shared_kpi_marts",sql,checked_count=population)
    quality.require_pass()
