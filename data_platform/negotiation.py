"""Shared negotiation facts, not a simulator or automatic policy approval."""

from .ingestion import literal


def build_negotiation_context(con, as_of, quality):
    # _products/_guardrails/_invoices/_latest_context are bounded, temporary
    # serving-build inputs, not connections to raw or evaluation artifacts.
    con.execute(f"""CREATE TABLE gold.mart_negotiation_context AS
        WITH sales AS (
            SELECT store_id,sku_id,sum(units_sold_observed) AS sell_out_units_observed_28d,
                   count(units_sold_observed) AS observed_sales_days_28d,count(*) AS expected_days_28d
            FROM gold.mart_store_sku_daily WHERE date>=DATE {literal(as_of)}-27 GROUP BY 1,2
        ), invoices AS (
            SELECT store_id,sku_id,max(date) AS last_invoice_date,
                   count(*) FILTER(WHERE date>=DATE {literal(as_of)}-27) AS invoice_lines_28d,
                   sum(units_sold) FILTER(WHERE date>=DATE {literal(as_of)}-27) AS sell_in_units_reported_28d,
                   sum(gross_sales) FILTER(WHERE date>=DATE {literal(as_of)}-27) AS gross_sales_reported_28d,
                   sum(net_sales) FILTER(WHERE date>=DATE {literal(as_of)}-27) AS net_sales_reported_28d,
                   count(*) FILTER(WHERE date>=DATE {literal(as_of)}-27 AND guardrail_violation) AS source_violation_lines_28d
            FROM _invoices GROUP BY 1,2
        )
        SELECT d.build_id,d.dataset_hash,DATE {literal(as_of)} AS as_of_date,d.store_id,d.sku_id,
               d.channel,d.region,d.segment_tier,d.category,d.is_msl,
               p.product_name,p.brand,p.list_price AS reference_list_price,p.unit_cost AS reference_unit_cost,
               p.consumer_price AS reference_consumer_price,
               'Not supplied' AS currency_status,'Static product dimension, not historical cost' AS cost_basis,
               d.inventory_observed,d.inventory_data_status,s.sell_out_units_observed_28d,
               s.observed_sales_days_28d,s.expected_days_28d,i.last_invoice_date,i.invoice_lines_28d,
               i.sell_in_units_reported_28d,i.gross_sales_reported_28d,i.net_sales_reported_28d,
               i.net_sales_reported_28d/nullif(i.sell_in_units_reported_28d,0) AS reported_net_trade_price_28d,
               100.0*(i.gross_sales_reported_28d-i.net_sales_reported_28d)/nullif(i.gross_sales_reported_28d,0) AS weighted_trade_discount_pct_28d,
               100.0*(p.list_price-p.unit_cost)/nullif(p.list_price,0) AS static_reference_margin_pct,
               100.0*(i.net_sales_reported_28d-i.sell_in_units_reported_28d*p.unit_cost)/nullif(i.net_sales_reported_28d,0) AS historical_static_cost_margin_pct,
               i.source_violation_lines_28d,'Reported invoice rows; extract completeness unconfirmed' AS invoice_coverage_status,
               g.max_discount_pct AS max_discount_pct_source,g.min_target_margin AS min_target_margin_source,
               g.required_exec_tradeoff,g.rule_version,'Requires review' AS policy_status,
               'Total/extra composition and source margin units require approval' AS policy_limitation,
               a.last_audit_id,a.last_audit_date,a.days_since_last_audit,
               a.planogram_compliance_latest,a.secondary_display_latest,a.shelf_share_pct_latest,
               'negotiation_context.v1' AS context_version
        FROM gold.mart_store_sku_daily d JOIN _products p USING(sku_id)
        LEFT JOIN _guardrails g ON g.channel=d.channel AND g.category=d.category
        LEFT JOIN sales s USING(store_id,sku_id) LEFT JOIN invoices i USING(store_id,sku_id)
        LEFT JOIN _latest_context a USING(store_id,sku_id)
        WHERE d.date=DATE {literal(as_of)}""")
    quality.query("negotiation_grain","mart_negotiation_context","SELECT store_id,sku_id FROM gold.mart_negotiation_context GROUP BY 1,2 HAVING count(*)<>1")
    quality.query("unapproved_rules_stay_review","mart_negotiation_context","SELECT store_id,sku_id FROM gold.mart_negotiation_context WHERE policy_status<>'Requires review'")
    quality.query("zero_price_margin_na","mart_negotiation_context","SELECT store_id,sku_id FROM gold.mart_negotiation_context WHERE reference_list_price=0 AND static_reference_margin_pct IS NOT NULL")
    quality.require_pass()
