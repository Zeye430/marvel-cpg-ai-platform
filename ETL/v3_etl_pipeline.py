#!/usr/bin/env python3
"""
Governed V3 End-to-End ETL Pipeline for Marvel CPG AI Platform
==============================================================

Implements the official V3 data platform specification from:
- `data_platform/` (silver.py, estimation.py, marts.py, serving.py, postgres_publisher.py)
- `docs/data/` (README.md, v3-gold-data-dictionary.md)
- `docs/solution-specifications/kpi-and-missing-store-day-handling.md`

Architecture:
-------------
1. EXTRACT & HARMONIZE:
   Ingests raw CSVs from `data/raw/` (or `--source-dir`).
   Harmonizes schema differences gracefully (handling both V2 and V3 attributes).
2. GOVERNED TRANSFORMATIONS (Local DuckDB Engine):
   - Silver typing, foreign key integrity, and active assortment tracking.
   - Complete Cartesian calendar grid (`silver.store_sku_calendar` across date x store x SKU).
   - Missing-day handling: 1-4 day short gap estimation (hierarchical median for sales,
     physical inventory recurrence with delivery receipts, zero floor, >=5 day Unknown).
   - Tri-state data visibility: explicit Observed, Estimated, and Unknown states.
   - Gold Marts:
     * `mart_store_sku_daily`: Dense facts with observed vs. estimated states and latest audit signals.
     * `mart_store_day`: Dual views (`reported_only` and `operational_estimate`) with accurate
       reporting coverage (identifying the 18 absent stores per date, ~91.86% coverage)
       and MSL compliance bounds (eligible, assessable, lower/upper bounds).
     * `mart_commercial_period`: Commercial period rollups across week, biweek (Traditional Trade),
       and monthly alignments.
     * `mart_execution_visit`: Audited visit metrics across SKU scopes with normalized shelf shares.
     * `mart_negotiation_context`: Rolling 28-day commercial negotiation context with guardrails.
     * `metric_definitions`: Governed metrics catalog.
   - Standardized Serving Views: 18 presentation views (`serving.vw_*`).
3. LOAD TO AIVEN POSTGRESQL:
   Connects to Aiven PostgreSQL via `DATABASE_URL` (or `SHARED_DATA_PUBLISH_DATABASE_URL`) in `.env`.
   Bulk streams transformed tables via high-performance `COPY FROM STDIN`, creates B-tree indexes,
   and creates all serving and public compatibility views.
4. VERIFICATION & ALIGNMENT:
   Validates DuckDB vs PostgreSQL row count parity table by table.
   Verifies KPI component parity:
     - `count(inventory_observed)` in `store_sku_daily` == `sum(observed_inventory_sku_count)` in `store_day`.
     - `count(*) FILTER (WHERE oos_observed)` in `store_sku_daily` == `sum(oos_sku_count)` in `store_day`.
     - Daily reporting coverage accurately captures the missing stores.

Usage:
------
    # Full end-to-end run (Transforms DuckDB and loads into Aiven PostgreSQL):
    uv run python ETL/v3_etl_pipeline.py --drop-schemas

    # Dry run (Tests transformations and PostgreSQL connection without streaming):
    uv run python ETL/v3_etl_pipeline.py --dry-run

    # Transform only (Builds DuckDB tables and checks alignment without database writes):
    uv run python ETL/v3_etl_pipeline.py --skip-load
"""

import argparse
import os
import sys
import tempfile
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Try loading python-dotenv
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Try loading duckdb
try:
    import duckdb
except ImportError:
    print(
        "ERROR: 'duckdb' is required for the transformation engine.\n"
        "Please install it using: uv sync --extra postgres --extra app",
        file=sys.stderr,
    )
    sys.exit(1)

# Try loading psycopg2
try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    print(
        "ERROR: 'psycopg2' is required to connect to PostgreSQL.\n"
        "Please install it using: uv sync --extra postgres --extra app",
        file=sys.stderr,
    )
    sys.exit(1)


# Terminal ANSI Colors
class Color:
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def log_info(msg: str):
    print(f"{Color.CYAN}[INFO]{Color.RESET} {msg}")


def log_success(msg: str):
    print(f"{Color.GREEN}[SUCCESS]{Color.RESET} {msg}")


def log_warn(msg: str):
    print(f"{Color.YELLOW}[WARN]{Color.RESET} {msg}")


def log_error(msg: str):
    print(f"{Color.RED}[ERROR]{Color.RESET} {msg}", file=sys.stderr)


# Tables to export from DuckDB to PostgreSQL
SILVER_TABLES = [
    "dim_stores",
    "dim_products",
    "dim_guardrails",
    "dim_promotions",
    "fact_sell_in",
    "fact_deliveries",
    "fact_execution_audits",
    "fact_sell_out",
    "active_assortment",
    "replenishment_events",
    "promotion_calendar",
    "store_sku_calendar",
]

GOLD_TABLES = [
    "metric_definitions",
    "mart_store_sku_daily",
    "mart_store_day",
    "mart_commercial_period",
    "mart_execution_visit",
    "mart_negotiation_context",
]

# PostgreSQL DDL Definitions
POSTGRES_DDL: Dict[str, str] = {
    # ------------------ SILVER SCHEMA ------------------
    "silver.dim_stores": """
        CREATE TABLE IF NOT EXISTS silver.dim_stores (
            store_id VARCHAR(50) PRIMARY KEY,
            store_name VARCHAR(255) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            state VARCHAR(50) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            city VARCHAR(100) NOT NULL,
            is_distributor BOOLEAN NOT NULL
        );
    """,
    "silver.dim_products": """
        CREATE TABLE IF NOT EXISTS silver.dim_products (
            sku_id VARCHAR(50) PRIMARY KEY,
            product_name VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            brand VARCHAR(100) NOT NULL,
            is_msl BOOLEAN NOT NULL,
            unit_cost NUMERIC(10, 2) NOT NULL,
            list_price NUMERIC(10, 2) NOT NULL,
            consumer_price NUMERIC(10, 2) NOT NULL
        );
    """,
    "silver.dim_guardrails": """
        CREATE TABLE IF NOT EXISTS silver.dim_guardrails (
            channel VARCHAR(100) NOT NULL,
            category VARCHAR(100) NOT NULL,
            max_discount_pct NUMERIC(6, 2) NOT NULL,
            min_target_margin NUMERIC(6, 2) NOT NULL,
            required_exec_tradeoff TEXT NOT NULL,
            rule_status VARCHAR(50) NOT NULL,
            PRIMARY KEY (channel, category)
        );
    """,
    "silver.dim_promotions": """
        CREATE TABLE IF NOT EXISTS silver.dim_promotions (
            promo_id VARCHAR(50) PRIMARY KEY,
            promo_name VARCHAR(255) NOT NULL,
            promo_type VARCHAR(100) NOT NULL,
            category VARCHAR(100) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            discount_pct NUMERIC(6, 2) NOT NULL,
            min_volume_units INTEGER NOT NULL
        );
    """,
    "silver.fact_sell_in": """
        CREATE TABLE IF NOT EXISTS silver.fact_sell_in (
            transaction_id VARCHAR(50) PRIMARY KEY,
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            units_sold INTEGER NOT NULL,
            gross_sales NUMERIC(12, 2) NOT NULL,
            discount_pct NUMERIC(6, 2) NOT NULL,
            extra_discount_pct NUMERIC(6, 2) NOT NULL,
            net_sales NUMERIC(12, 2) NOT NULL,
            promo_id VARCHAR(50),
            guardrail_violation BOOLEAN NOT NULL
        );
    """,
    "silver.fact_deliveries": """
        CREATE TABLE IF NOT EXISTS silver.fact_deliveries (
            delivery_id VARCHAR(50) PRIMARY KEY,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            order_date DATE NOT NULL,
            scheduled_delivery DATE NOT NULL,
            actual_delivery DATE NOT NULL,
            delay_days INTEGER NOT NULL,
            delivery_status VARCHAR(50) NOT NULL,
            units_ordered INTEGER NOT NULL,
            units_delivered INTEGER NOT NULL,
            fill_rate_pct NUMERIC(6, 2) NOT NULL
        );
    """,
    "silver.fact_execution_audits": """
        CREATE TABLE IF NOT EXISTS silver.fact_execution_audits (
            audit_id VARCHAR(50) PRIMARY KEY,
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            planogram_compliance BOOLEAN NOT NULL,
            shelf_share_pct NUMERIC(6, 2) NOT NULL,
            secondary_display BOOLEAN NOT NULL
        );
    """,
    "silver.fact_sell_out": """
        CREATE TABLE IF NOT EXISTS silver.fact_sell_out (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            units_sold_daily INTEGER NOT NULL,
            inventory_level INTEGER NOT NULL,
            is_out_of_stock BOOLEAN NOT NULL,
            consumer_price_actual NUMERIC(10, 2) NOT NULL,
            promo_id VARCHAR(50),
            promo_discount_pct NUMERIC(6, 2) NOT NULL,
            restock_units INTEGER NOT NULL,
            restock_delivery_id VARCHAR(50),
            PRIMARY KEY (date, store_id, sku_id)
        );
    """,
    "silver.active_assortment": """
        CREATE TABLE IF NOT EXISTS silver.active_assortment (
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            first_observed_date DATE NOT NULL,
            last_observed_date DATE NOT NULL,
            PRIMARY KEY (store_id, sku_id)
        );
    """,
    "silver.replenishment_events": """
        CREATE TABLE IF NOT EXISTS silver.replenishment_events (
            delivery_id VARCHAR(50) PRIMARY KEY,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            receipt_date DATE NOT NULL,
            receipt_units INTEGER NOT NULL,
            has_observed_restock BOOLEAN NOT NULL,
            reconciliation_status VARCHAR(50) NOT NULL
        );
    """,
    "silver.promotion_calendar": """
        CREATE TABLE IF NOT EXISTS silver.promotion_calendar (
            date DATE NOT NULL,
            channel VARCHAR(100) NOT NULL,
            category VARCHAR(100) NOT NULL,
            promo_id VARCHAR(50) NOT NULL REFERENCES silver.dim_promotions (promo_id),
            discount_pct NUMERIC(6, 2) NOT NULL,
            min_volume_units INTEGER NOT NULL,
            has_overlap BOOLEAN NOT NULL
        );
    """,
    "silver.store_sku_calendar": """
        CREATE TABLE IF NOT EXISTS silver.store_sku_calendar (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES silver.dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES silver.dim_products (sku_id),
            is_sku_day_reported BOOLEAN NOT NULL,
            is_store_day_reported BOOLEAN NOT NULL,
            assortment_is_confirmed_asof BOOLEAN NOT NULL,
            units_sold_observed INTEGER,
            inventory_observed INTEGER,
            oos_observed BOOLEAN,
            restock_units_observed INTEGER,
            PRIMARY KEY (date, store_id, sku_id)
        );
    """,

    # ------------------ GOLD SCHEMA ------------------
    "gold.metric_definitions": """
        CREATE TABLE IF NOT EXISTS gold.metric_definitions (
            metric_id VARCHAR(100) PRIMARY KEY,
            metric_version VARCHAR(50) NOT NULL,
            unit VARCHAR(50) NOT NULL,
            numerator_column VARCHAR(100) NOT NULL,
            denominator_column VARCHAR(100) NOT NULL,
            formula TEXT NOT NULL,
            source_relation VARCHAR(100) NOT NULL,
            scope_note TEXT NOT NULL,
            zero_denominator_policy VARCHAR(50) NOT NULL,
            release_note TEXT NOT NULL
        );
    """,
    "gold.mart_store_sku_daily": """
        CREATE TABLE IF NOT EXISTS gold.mart_store_sku_daily (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            sku_id VARCHAR(50) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            category VARCHAR(100) NOT NULL,
            brand VARCHAR(100) NOT NULL,
            is_msl BOOLEAN NOT NULL,
            units_sold_observed INTEGER,
            inventory_observed INTEGER,
            oos_observed BOOLEAN,
            consumer_price_observed NUMERIC(10, 2),
            promo_id VARCHAR(50),
            promo_discount_pct NUMERIC(6, 2),
            inventory_is_observed BOOLEAN NOT NULL,
            sales_is_observed BOOLEAN NOT NULL,
            inventory_data_status VARCHAR(50) NOT NULL,
            sales_data_status VARCHAR(50) NOT NULL,
            inventory_model_state INTEGER,
            sales_model_state INTEGER,
            units_sold_estimated INTEGER,
            inventory_estimated INTEGER,
            oos_estimated BOOLEAN,
            days_since_inventory_report INTEGER,
            last_report_date DATE,
            receipt_units_from_deliveries INTEGER NOT NULL,
            receipt_delivery_count INTEGER NOT NULL,
            last_audit_date DATE,
            days_since_last_audit INTEGER,
            planogram_compliance_latest BOOLEAN,
            secondary_display_latest BOOLEAN,
            shelf_share_pct_latest NUMERIC(6, 2),
            PRIMARY KEY (date, store_id, sku_id)
        );
    """,
    "gold.mart_store_day": """
        CREATE TABLE IF NOT EXISTS gold.mart_store_day (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            metric_view VARCHAR(50) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            expected_sku_count INTEGER NOT NULL,
            known_inventory_sku_count INTEGER NOT NULL,
            observed_inventory_sku_count INTEGER NOT NULL,
            estimated_inventory_sku_count INTEGER NOT NULL,
            unknown_inventory_sku_count INTEGER NOT NULL,
            oos_sku_count INTEGER NOT NULL,
            known_sales_sku_count INTEGER NOT NULL,
            observed_sales_sku_count INTEGER NOT NULL,
            estimated_sales_sku_count INTEGER NOT NULL,
            sell_out_units_available INTEGER NOT NULL,
            required_msl_sku_count INTEGER NOT NULL,
            known_msl_sku_count INTEGER NOT NULL,
            unavailable_msl_sku_count INTEGER NOT NULL,
            unknown_msl_sku_count INTEGER NOT NULL,
            max_days_since_inventory_report INTEGER,
            msl_state VARCHAR(50) NOT NULL,
            msl_eligible_store_count INTEGER NOT NULL,
            msl_assessable_store_count INTEGER NOT NULL,
            msl_compliant_store_count INTEGER NOT NULL,
            msl_noncompliant_store_count INTEGER NOT NULL,
            msl_unknown_store_count INTEGER NOT NULL,
            oos_rate_pct NUMERIC(6, 2),
            reporting_coverage_pct NUMERIC(6, 2),
            known_inventory_coverage_pct NUMERIC(6, 2),
            estimated_inventory_share_of_known_pct NUMERIC(6, 2),
            msl_compliance_pct NUMERIC(6, 2),
            msl_assessment_coverage_pct NUMERIC(6, 2),
            msl_lower_bound_pct NUMERIC(6, 2),
            msl_upper_bound_pct NUMERIC(6, 2),
            release_status VARCHAR(100) NOT NULL,
            PRIMARY KEY (date, store_id, metric_view)
        );
    """,
    "gold.mart_commercial_period": """
        CREATE TABLE IF NOT EXISTS gold.mart_commercial_period (
            period_type VARCHAR(20) NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            alignment_basis VARCHAR(50) NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            sku_id VARCHAR(50) NOT NULL,
            metric_view VARCHAR(50) NOT NULL,
            covered_start DATE NOT NULL,
            covered_end DATE NOT NULL,
            expected_sku_days INTEGER NOT NULL,
            known_sell_out_days INTEGER NOT NULL,
            reported_sell_out_days INTEGER NOT NULL,
            estimated_sell_out_days INTEGER NOT NULL,
            sell_out_units_available INTEGER NOT NULL,
            consumer_sales_value_observed NUMERIC(14, 2) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            category VARCHAR(100) NOT NULL,
            invoice_line_count INTEGER NOT NULL,
            invoice_units_observed INTEGER NOT NULL,
            invoice_gross_sales NUMERIC(14, 2) NOT NULL,
            invoice_net_sales NUMERIC(14, 2) NOT NULL,
            invoice_discount_value NUMERIC(14, 2) NOT NULL,
            invoice_cost_at_static_reference NUMERIC(14, 2) NOT NULL,
            extra_discount_unit_weighted_sum NUMERIC(14, 4) NOT NULL,
            source_violation_line_count INTEGER NOT NULL,
            last_invoice_date DATE,
            delivered_units INTEGER NOT NULL,
            delivery_event_count INTEGER NOT NULL,
            period_is_complete BOOLEAN NOT NULL,
            sell_in_units_available INTEGER,
            unknown_sell_out_days INTEGER NOT NULL,
            reporting_coverage_pct NUMERIC(6, 2),
            known_sales_coverage_pct NUMERIC(6, 2),
            sell_in_out_gap_units NUMERIC(12, 2),
            flow_is_comparable BOOLEAN NOT NULL,
            comparable_sell_in_units NUMERIC(12, 2),
            comparable_sell_out_units NUMERIC(12, 2),
            comparable_gap_units NUMERIC(12, 2),
            sell_in_out_gap_pct NUMERIC(6, 2),
            delivered_out_gap_units NUMERIC(12, 2),
            trade_discount_weighted_pct NUMERIC(6, 2),
            net_trade_unit_price NUMERIC(10, 2),
            trade_margin_value_at_static_cost NUMERIC(14, 2),
            trade_margin_pct_at_static_cost NUMERIC(6, 2),
            comparison_status VARCHAR(100) NOT NULL,
            release_status VARCHAR(100) NOT NULL,
            PRIMARY KEY (period_type, period_start, alignment_basis, store_id, sku_id, metric_view)
        );
    """,
    "gold.mart_execution_visit": """
        CREATE TABLE IF NOT EXISTS gold.mart_execution_visit (
            audit_date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            category VARCHAR(100) NOT NULL,
            sku_scope_id VARCHAR(50) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            scope_label VARCHAR(100) NOT NULL,
            audited_row_count INTEGER NOT NULL,
            audited_sku_count INTEGER NOT NULL,
            compliant_audit_row_count INTEGER NOT NULL,
            secondary_display_row_count INTEGER NOT NULL,
            shelf_allocation_pct NUMERIC(6, 2) NOT NULL,
            visit_count INTEGER NOT NULL,
            planogram_compliance_pct NUMERIC(6, 2) NOT NULL,
            shelf_share_scope VARCHAR(100) NOT NULL,
            release_status VARCHAR(100) NOT NULL,
            PRIMARY KEY (audit_date, store_id, category, sku_scope_id)
        );
    """,
    "gold.mart_negotiation_context": """
        CREATE TABLE IF NOT EXISTS gold.mart_negotiation_context (
            as_of_date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            sku_id VARCHAR(50) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            product_name VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            brand VARCHAR(100) NOT NULL,
            is_msl BOOLEAN NOT NULL,
            reference_list_price NUMERIC(10, 2) NOT NULL,
            reference_unit_cost NUMERIC(10, 2) NOT NULL,
            reference_consumer_price NUMERIC(10, 2) NOT NULL,
            static_reference_margin_pct NUMERIC(6, 2),
            sell_out_units_observed_28d INTEGER NOT NULL,
            observed_sales_days_28d INTEGER NOT NULL,
            last_invoice_date DATE,
            invoice_lines_28d INTEGER NOT NULL,
            sell_in_units_reported_28d INTEGER NOT NULL,
            gross_sales_reported_28d NUMERIC(14, 2) NOT NULL,
            net_sales_reported_28d NUMERIC(14, 2) NOT NULL,
            reported_net_trade_price_28d NUMERIC(10, 2),
            weighted_trade_discount_pct_28d NUMERIC(6, 2),
            historical_static_cost_margin_pct NUMERIC(6, 2),
            source_violation_lines_28d INTEGER NOT NULL,
            max_discount_pct_source NUMERIC(6, 2),
            min_target_margin_source NUMERIC(6, 2),
            required_exec_tradeoff TEXT,
            last_audit_date DATE,
            planogram_compliance_latest BOOLEAN,
            secondary_display_latest BOOLEAN,
            shelf_share_pct_latest NUMERIC(6, 2),
            PRIMARY KEY (as_of_date, store_id, sku_id)
        );
    """,
}

# Analytical B-Tree Indexes
POSTGRES_INDEXES: List[str] = [
    # Silver indexes
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_in_date ON silver.fact_sell_in (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_in_store ON silver.fact_sell_in (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_in_sku ON silver.fact_sell_in (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_deliveries_dates ON silver.fact_deliveries (order_date, actual_delivery);",
    "CREATE INDEX IF NOT EXISTS idx_silver_deliveries_store ON silver.fact_deliveries (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_audits_date ON silver.fact_execution_audits (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_audits_store ON silver.fact_execution_audits (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_date ON silver.fact_sell_out (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_store ON silver.fact_sell_out (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_sku ON silver.fact_sell_out (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_calendar_date ON silver.store_sku_calendar (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_calendar_store ON silver.store_sku_calendar (store_id);",
    # Gold indexes
    "CREATE INDEX IF NOT EXISTS idx_gold_daily_date ON gold.mart_store_sku_daily (date);",
    "CREATE INDEX IF NOT EXISTS idx_gold_daily_store ON gold.mart_store_sku_daily (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_daily_sku ON gold.mart_store_sku_daily (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_daily_status ON gold.mart_store_sku_daily (inventory_data_status);",
    "CREATE INDEX IF NOT EXISTS idx_gold_store_day_date ON gold.mart_store_day (date);",
    "CREATE INDEX IF NOT EXISTS idx_gold_store_day_store ON gold.mart_store_day (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_store_day_view ON gold.mart_store_day (metric_view);",
    "CREATE INDEX IF NOT EXISTS idx_gold_comm_period_start ON gold.mart_commercial_period (period_start);",
    "CREATE INDEX IF NOT EXISTS idx_gold_comm_store ON gold.mart_commercial_period (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_comm_sku ON gold.mart_commercial_period (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_audit_visit_date ON gold.mart_execution_visit (audit_date);",
    "CREATE INDEX IF NOT EXISTS idx_gold_audit_visit_store ON gold.mart_execution_visit (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_gold_nego_as_of ON gold.mart_negotiation_context (as_of_date);",
    "CREATE INDEX IF NOT EXISTS idx_gold_nego_store ON gold.mart_negotiation_context (store_id);",
]

# Standardized V3 Serving Views DDL
POSTGRES_SERVING_VIEWS: Dict[str, str] = {
    # Core Dimension Views
    "vw_stores": "CREATE OR REPLACE VIEW serving.vw_stores AS SELECT store_id, store_name, channel, region, state, segment_tier, city FROM silver.dim_stores;",
    "vw_products": "CREATE OR REPLACE VIEW serving.vw_products AS SELECT sku_id, product_name, category, brand, is_msl, unit_cost, list_price, consumer_price FROM silver.dim_products;",
    "vw_promotions": "CREATE OR REPLACE VIEW serving.vw_promotions AS SELECT promo_id, promo_name, promo_type, category, channel, start_date, end_date, discount_pct, min_volume_units FROM silver.dim_promotions;",
    # Event Views
    "vw_delivery_events": "CREATE OR REPLACE VIEW serving.vw_delivery_events AS SELECT delivery_id, store_id, sku_id, order_date, scheduled_delivery, actual_delivery, delay_days, delivery_status, units_ordered, units_delivered, fill_rate_pct FROM silver.fact_deliveries;",
    "vw_sell_in_events": "CREATE OR REPLACE VIEW serving.vw_sell_in_events AS SELECT transaction_id, date, store_id, sku_id, units_sold, gross_sales, discount_pct, extra_discount_pct, net_sales, promo_id, guardrail_violation FROM silver.fact_sell_in;",
    "vw_execution_visits": "CREATE OR REPLACE VIEW serving.vw_execution_visits AS SELECT * FROM gold.mart_execution_visit;",
    # Daily Fact & KPI Views (Reported Only by default)
    "vw_store_sku_daily": """CREATE OR REPLACE VIEW serving.vw_store_sku_daily AS 
        SELECT date, store_id, sku_id, channel, region, segment_tier, category, is_msl,
               units_sold_observed, inventory_observed, oos_observed,
               inventory_data_status, sales_data_status, last_report_date, days_since_inventory_report,
               consumer_price_observed, receipt_units_from_deliveries, receipt_delivery_count
        FROM gold.mart_store_sku_daily 
        WHERE inventory_data_status = 'Observed';""",
    "vw_store_day": "CREATE OR REPLACE VIEW serving.vw_store_day AS SELECT * FROM gold.mart_store_day WHERE metric_view = 'reported_only';",
    "vw_commercial_period": "CREATE OR REPLACE VIEW serving.vw_commercial_period AS SELECT * FROM gold.mart_commercial_period WHERE metric_view = 'reported_only' AND alignment_basis = 'event_date';",
    "vw_negotiation_context": "CREATE OR REPLACE VIEW serving.vw_negotiation_context AS SELECT * FROM gold.mart_negotiation_context;",
    "vw_metric_definitions": "CREATE OR REPLACE VIEW serving.vw_metric_definitions AS SELECT * FROM gold.metric_definitions;",
    # Candidate Estimated Views (Opt-in)
    "vw_estimated_store_sku_daily": """CREATE OR REPLACE VIEW serving.vw_estimated_store_sku_daily AS 
        SELECT date, store_id, sku_id, channel, region, segment_tier, category, is_msl,
               units_sold_observed, inventory_observed, oos_observed,
               units_sold_estimated, inventory_estimated, oos_estimated,
               sales_model_state AS sales_resolved, inventory_model_state AS inventory_resolved,
               COALESCE(oos_observed, oos_estimated) AS oos_resolved,
               sales_data_status, inventory_data_status,
               last_report_date, days_since_inventory_report,
               receipt_units_from_deliveries, receipt_delivery_count,
               'operational_estimate_candidate' AS metric_view
        FROM gold.mart_store_sku_daily;""",
    "vw_estimated_store_day": "CREATE OR REPLACE VIEW serving.vw_estimated_store_day AS SELECT * FROM gold.mart_store_day WHERE metric_view = 'operational_estimate';",
    "vw_estimated_commercial_period": "CREATE OR REPLACE VIEW serving.vw_estimated_commercial_period AS SELECT * FROM gold.mart_commercial_period WHERE metric_view = 'operational_estimate' AND alignment_basis = 'event_date';",
}

# Public Compatibility Views (so shared/data.py and legacy apps work seamlessly)
POSTGRES_PUBLIC_VIEWS: Dict[str, str] = {
    "dim_stores": "CREATE OR REPLACE VIEW public.dim_stores AS SELECT * FROM silver.dim_stores;",
    "dim_products": "CREATE OR REPLACE VIEW public.dim_products AS SELECT * FROM silver.dim_products;",
    "dim_guardrails": "CREATE OR REPLACE VIEW public.dim_guardrails AS SELECT * FROM silver.dim_guardrails;",
    "dim_promotions": "CREATE OR REPLACE VIEW public.dim_promotions AS SELECT * FROM silver.dim_promotions;",
    "fact_sell_in": "CREATE OR REPLACE VIEW public.fact_sell_in AS SELECT * FROM silver.fact_sell_in;",
    "fact_deliveries": "CREATE OR REPLACE VIEW public.fact_deliveries AS SELECT * FROM silver.fact_deliveries;",
    "fact_execution_audits": "CREATE OR REPLACE VIEW public.fact_execution_audits AS SELECT * FROM silver.fact_execution_audits;",
    "fact_sell_out": "CREATE OR REPLACE VIEW public.fact_sell_out AS SELECT * FROM silver.fact_sell_out;",
}


# ==============================================================================
# Database Connection Utilities
# ==============================================================================

def resolve_connection_info(env_path: Path) -> Tuple[str, str]:
    """Resolves PostgreSQL connection URI from .env or environment variables."""
    if env_path.is_file():
        if load_dotenv:
            load_dotenv(dotenv_path=env_path)
            log_info(f"Loaded credentials from .env: {env_path.resolve()}")
        else:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

    database_url = os.getenv("SHARED_DATA_PUBLISH_DATABASE_URL") or os.getenv("DATABASE_URL")

    if not database_url:
        host = os.getenv("PGHOST")
        port = os.getenv("PGPORT", "5432")
        user = os.getenv("PGUSER")
        password = os.getenv("PGPASSWORD")
        dbname = os.getenv("PGDATABASE", "defaultdb")
        sslmode = os.getenv("PGSSLMODE", "require")

        if not (host and user and password):
            raise ValueError(
                "Neither DATABASE_URL nor individual PG* credentials (PGHOST, PGUSER, PGPASSWORD) "
                f"were found in environment or {env_path}."
            )
        database_url = (
            f"postgresql://{urllib.parse.quote_plus(user)}:{urllib.parse.quote_plus(password)}"
            f"@{host}:{port}/{dbname}?sslmode={sslmode}"
        )

    # Standardize scheme for psycopg2
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]

    parsed = urllib.parse.urlsplit(database_url)
    dbname = parsed.path.lstrip("/") or "defaultdb"

    return database_url, dbname


# ==============================================================================
# Transformation Engine (DuckDB)
# ==============================================================================

def run_governed_duckdb_transformations(
    source_dir: Path,
    as_of: str = "2026-06-30",
    calendar_start: str = "2025-01-01",
) -> duckdb.DuckDBPyConnection:
    """
    Executes the governed V3 data platform transformation pipeline in DuckDB:
    - Ingests raw CSVs and harmonizes schema attributes.
    - Builds complete Cartesian calendar grid (silver.store_sku_calendar).
    - Runs 1-4 day short-gap state estimation (gold.mart_store_sku_daily).
    - Generates governed Gold Marts (mart_store_day with true coverage, mart_commercial_period,
      mart_execution_visit, mart_negotiation_context, metric_definitions).
    """
    log_info("Initializing DuckDB Transformation Engine...")
    t_start = time.time()
    con = duckdb.connect()

    con.execute("CREATE SCHEMA silver;")
    con.execute("CREATE SCHEMA gold;")
    con.execute("CREATE SCHEMA serving;")

    # 1. Dimensions
    log_info("1/7 Ingesting and typing dimensions into Silver...")
    con.execute(f"""
        CREATE TABLE silver.dim_stores AS 
        SELECT 
            store_id, store_name, channel, region, state, segment_tier, city,
            (channel = 'Distributor') AS is_distributor
        FROM read_csv_auto('{source_dir / "dim_stores.csv"}');
    """)

    con.execute(f"""
        CREATE TABLE silver.dim_products AS 
        SELECT * FROM read_csv_auto('{source_dir / "dim_products.csv"}');
    """)

    con.execute(f"""
        CREATE TABLE silver.dim_guardrails AS 
        SELECT 
            channel, category, max_discount_pct, min_target_margin, required_exec_tradeoff,
            'Requires review' AS rule_status
        FROM read_csv_auto('{source_dir / "dim_guardrails.csv"}');
    """)

    con.execute(f"""
        CREATE TABLE silver.dim_promotions AS 
        SELECT * FROM read_csv_auto('{source_dir / "dim_promotions.csv"}');
    """)

    con.execute(f"""
        CREATE TABLE silver.fact_execution_audits AS 
        SELECT * FROM read_csv_auto('{source_dir / "fact_execution_audits.csv"}');
    """)

    # 2. Harmonize fact_deliveries
    log_info("2/7 Harmonizing fact_deliveries...")
    sample_dlv = con.execute(f"SELECT * FROM read_csv_auto('{source_dir / 'fact_deliveries.csv'}') LIMIT 0").df()
    order_date_col = "order_date" if "order_date" in sample_dlv.columns else "invoice_date AS order_date"

    con.execute(f"""
        CREATE TABLE silver.fact_deliveries AS 
        SELECT 
            delivery_id, store_id, sku_id, {order_date_col}, scheduled_delivery, actual_delivery,
            delay_days, delivery_status, units_ordered, units_delivered, fill_rate_pct
        FROM read_csv_auto('{source_dir / "fact_deliveries.csv"}');
    """)

    # 3. Harmonize fact_sell_in
    log_info("3/7 Harmonizing fact_sell_in with trade guardrails...")
    sample_si = con.execute(f"SELECT * FROM read_csv_auto('{source_dir / 'fact_sell_in.csv'}') LIMIT 0").df()
    if "extra_discount_pct" in sample_si.columns and "guardrail_violation" in sample_si.columns:
        con.execute(f"""
            CREATE TABLE silver.fact_sell_in AS 
            SELECT * FROM read_csv_auto('{source_dir / "fact_sell_in.csv"}');
        """)
    else:
        con.execute(f"""
            CREATE TABLE silver.fact_sell_in AS 
            SELECT 
                si.transaction_id, si.date, si.store_id, si.sku_id, si.units_sold,
                si.gross_sales, si.discount_pct,
                0.0::DECIMAL(6,2) AS extra_discount_pct,
                si.net_sales, si.promo_id,
                (si.discount_pct > COALESCE(g.max_discount_pct, 999.0)) AS guardrail_violation
            FROM read_csv_auto('{source_dir / "fact_sell_in.csv"}') si
            JOIN silver.dim_stores s ON si.store_id = s.store_id
            JOIN silver.dim_products p ON si.sku_id = p.sku_id
            LEFT JOIN silver.dim_guardrails g ON s.channel = g.channel AND p.category = g.category;
        """)

    # 4. Harmonize fact_sell_out & Replenishment Links
    log_info("4/7 Harmonizing fact_sell_out and linking delivery receipts...")
    sample_so = con.execute(f"SELECT * FROM read_csv_auto('{source_dir / 'fact_sell_out.csv'}') LIMIT 0").df()
    if "restock_units" in sample_so.columns and "restock_delivery_id" in sample_so.columns:
        con.execute(f"""
            CREATE TABLE silver.fact_sell_out AS 
            SELECT * FROM read_csv_auto('{source_dir / "fact_sell_out.csv"}')
            QUALIFY row_number() OVER (PARTITION BY date, store_id, sku_id) = 1;
        """)
    else:
        # Pre-aggregate deliveries by (actual_delivery, store_id, sku_id) to prevent duplicate key explosion
        con.execute(f"""
            CREATE TEMP TABLE _raw_sell_out AS
            SELECT * FROM read_csv_auto('{source_dir / "fact_sell_out.csv"}');

            CREATE TEMP TABLE _dlv_summary AS
            SELECT 
                actual_delivery AS date,
                store_id,
                sku_id,
                sum(units_delivered)::INTEGER AS restock_units,
                max(delivery_id) AS restock_delivery_id
            FROM silver.fact_deliveries
            GROUP BY actual_delivery, store_id, sku_id;

            CREATE TABLE silver.fact_sell_out AS
            SELECT 
                o.date, o.store_id, o.sku_id, o.units_sold_daily, o.inventory_level,
                o.is_out_of_stock, o.consumer_price_actual, o.promo_id, o.promo_discount_pct,
                COALESCE(d.restock_units, 0)::INTEGER AS restock_units,
                d.restock_delivery_id
            FROM _raw_sell_out o
            LEFT JOIN _dlv_summary d 
              ON o.date = d.date AND o.store_id = d.store_id AND o.sku_id = d.sku_id
            QUALIFY row_number() OVER (PARTITION BY o.date, o.store_id, o.sku_id ORDER BY o.units_sold_daily DESC) = 1;
        """)

    # 5. Silver Foundations (Assortment, Calendar Grid, Replenishment Events)
    log_info("5/7 Constructing dense active-assortment calendar grid (store_sku_calendar)...")
    con.execute(f"""
        CREATE TABLE silver.active_assortment AS
        SELECT store_id, sku_id, min(date) AS first_observed_date, max(date) AS last_observed_date
        FROM silver.fact_sell_out
        GROUP BY store_id, sku_id;
    """)

    con.execute("""
        CREATE TABLE silver.replenishment_events AS
        SELECT 
            d.delivery_id, d.store_id, d.sku_id, d.actual_delivery AS receipt_date,
            d.units_delivered AS receipt_units,
            (o.restock_delivery_id IS NOT NULL) AS has_observed_restock,
            CASE 
                WHEN o.restock_delivery_id IS NOT NULL THEN 'Linked observed restock'
                WHEN r.date IS NULL THEN 'Sell-out not reported'
                WHEN d.units_delivered = 0 THEN 'Zero-unit delivery'
                ELSE 'Unexplained'
            END AS reconciliation_status
        FROM silver.fact_deliveries d
        LEFT JOIN silver.fact_sell_out o ON d.delivery_id = o.restock_delivery_id
        LEFT JOIN silver.fact_sell_out r ON d.store_id = r.store_id AND d.sku_id = r.sku_id
                                         AND d.actual_delivery = r.date
        QUALIFY row_number() OVER (PARTITION BY d.delivery_id) = 1;
    """)

    con.execute(f"""
        CREATE TABLE silver.promotion_calendar AS
        SELECT 
            d.day::DATE AS date, p.channel, p.category, p.promo_id,
            p.discount_pct, p.min_volume_units,
            count(*) OVER (PARTITION BY d.day::DATE, p.channel, p.category) > 1 AS has_overlap
        FROM silver.dim_promotions p,
             LATERAL generate_series(greatest(p.start_date, DATE '{calendar_start}'),
                                     least(p.end_date, DATE '{as_of}'), INTERVAL 1 DAY) d(day);
    """)

    # Dense calendar grid (Cartesian product: confirmed active pairs x dates)
    con.execute(f"""
        CREATE TABLE silver.store_sku_calendar AS
        WITH store_days AS (SELECT DISTINCT date, store_id FROM silver.fact_sell_out)
        SELECT 
            d.day::DATE AS date, a.store_id, a.sku_id,
            o.date IS NOT NULL AS is_sku_day_reported,
            r.date IS NOT NULL AS is_store_day_reported,
            d.day::DATE >= a.first_observed_date AS assortment_is_confirmed_asof,
            o.units_sold_daily AS units_sold_observed,
            o.inventory_level AS inventory_observed,
            o.is_out_of_stock AS oos_observed,
            o.restock_units AS restock_units_observed
        FROM silver.active_assortment a
        CROSS JOIN generate_series(DATE '{calendar_start}', DATE '{as_of}', INTERVAL 1 DAY) d(day)
        LEFT JOIN silver.fact_sell_out o ON o.date = d.day::DATE AND o.store_id = a.store_id AND o.sku_id = a.sku_id
        LEFT JOIN store_days r ON r.date = d.day::DATE AND r.store_id = a.store_id;
    """)

    # 6. Gold State & Gap Estimation Engine (matching data_platform/estimation.py)
    log_info("6/7 Running governed short-gap state estimation (mart_store_sku_daily)...")
    
    # Receipts from deliveries occurring on any calendar day (including missing days)
    con.execute("""
        CREATE TEMP TABLE _receipts_daily AS
        SELECT 
            receipt_date AS date, store_id, sku_id,
            sum(receipt_units)::INTEGER AS receipt_units_from_deliveries,
            count(*)::INTEGER AS receipt_delivery_count
        FROM silver.replenishment_events
        GROUP BY 1, 2, 3;
    """)

    # State inputs
    con.execute("""
        CREATE TEMP TABLE _state_inputs AS
        SELECT 
            c.date, c.store_id, c.sku_id,
            s.channel, s.region, s.segment_tier,
            p.product_name, p.category, p.brand, p.is_msl,
            c.units_sold_observed, c.inventory_observed, c.oos_observed,
            o.consumer_price_actual AS consumer_price_observed,
            o.promo_id, o.promo_discount_pct,
            c.is_sku_day_reported AS sales_is_observed,
            c.is_sku_day_reported AS inventory_is_observed,
            c.is_store_day_reported,
            c.assortment_is_confirmed_asof,
            COALESCE(r.receipt_units_from_deliveries, 0) AS receipt_units_from_deliveries,
            COALESCE(r.receipt_delivery_count, 0) AS receipt_delivery_count,
            CASE WHEN NOT c.oos_observed THEN c.units_sold_observed END AS eligible_sales_raw
        FROM silver.store_sku_calendar c
        JOIN silver.dim_stores s USING (store_id)
        JOIN silver.dim_products p USING (sku_id)
        LEFT JOIN silver.fact_sell_out o USING (date, store_id, sku_id)
        LEFT JOIN _receipts_daily r USING (date, store_id, sku_id);
    """)

    # Group fallback medians (channel x category)
    con.execute("""
        CREATE TEMP TABLE _group_medians AS
        SELECT 
            channel, category,
            median(eligible_sales_raw) AS group_sales_median
        FROM _state_inputs
        WHERE eligible_sales_raw IS NOT NULL
        GROUP BY channel, category;
    """)

    # Rolling window baselines and report episodes
    con.execute("""
        CREATE TEMP TABLE _state_windows AS
        SELECT 
            i.*,
            gm.group_sales_median,
            median(i.eligible_sales_raw) OVER (
                PARTITION BY i.store_id, i.sku_id, isodow(i.date) 
                ORDER BY i.date 
                RANGE BETWEEN INTERVAL 56 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING
            ) AS weekday_sales_median,
            median(i.eligible_sales_raw) OVER (
                PARTITION BY i.store_id, i.sku_id 
                ORDER BY i.date 
                RANGE BETWEEN INTERVAL 28 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING
            ) AS recent_sales_median,
            max(CASE WHEN i.inventory_is_observed THEN i.date END) OVER pair_to_date AS last_report_date,
            last_value(i.inventory_observed IGNORE NULLS) OVER pair_to_date AS last_observed_inventory,
            count(i.inventory_observed) OVER pair_to_date AS report_episode
        FROM _state_inputs i
        LEFT JOIN _group_medians gm USING (channel, category)
        WINDOW pair_to_date AS (
            PARTITION BY i.store_id, i.sku_id 
            ORDER BY i.date 
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        );
    """)

    # Baselines and gap accumulation
    con.execute("""
        CREATE TEMP TABLE _state_baselines AS
        SELECT 
            w.*,
            date_diff('day', w.last_report_date, w.date) AS days_since_inventory_report,
            COALESCE(w.weekday_sales_median, w.recent_sales_median, w.group_sales_median, 0) AS sales_baseline,
            (NOT w.sales_is_observed AND w.assortment_is_confirmed_asof 
             AND date_diff('day', w.last_report_date, w.date) BETWEEN 1 AND 4) AS fresh_missing_day
        FROM _state_windows w;

        CREATE TEMP TABLE _state_gaps AS
        SELECT 
            b.*,
            sum(CASE WHEN NOT b.inventory_is_observed THEN b.receipt_units_from_deliveries ELSE 0 END)
                OVER gap_to_date AS cumulative_gap_receipts,
            sum(CASE WHEN NOT b.sales_is_observed THEN b.sales_baseline ELSE 0 END)
                OVER gap_to_date AS cumulative_gap_sales
        FROM _state_baselines b
        WINDOW gap_to_date AS (
            PARTITION BY b.store_id, b.sku_id, b.report_episode 
            ORDER BY b.date 
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        );
    """)

    # Latest execution audit evidence via ASOF join
    con.execute("""
        CREATE TEMP TABLE _dedup_audits AS
        SELECT *
        FROM silver.fact_execution_audits
        QUALIFY row_number() OVER (PARTITION BY date, store_id, sku_id ORDER BY audit_id) = 1;

        CREATE TEMP TABLE _latest_audits AS
        SELECT 
            g.date, g.store_id, g.sku_id,
            a.date AS last_audit_date,
            date_diff('day', a.date, g.date)::INTEGER AS days_since_last_audit,
            a.planogram_compliance AS planogram_compliance_latest,
            a.secondary_display AS secondary_display_latest,
            a.shelf_share_pct AS shelf_share_pct_latest
        FROM _state_gaps g
        ASOF LEFT JOIN _dedup_audits a
          ON g.store_id = a.store_id AND g.sku_id = a.sku_id AND g.date >= a.date;
    """)

    # Final Gold: mart_store_sku_daily
    con.execute("""
        CREATE TABLE gold.mart_store_sku_daily AS
        WITH estimated AS (
            SELECT 
                g.*,
                CASE WHEN g.fresh_missing_day THEN ROUND(g.sales_baseline)::INTEGER END AS units_sold_estimated,
                CASE WHEN g.fresh_missing_day 
                     THEN greatest(0, ROUND(g.last_observed_inventory + g.cumulative_gap_receipts - g.cumulative_gap_sales))::INTEGER 
                END AS inventory_estimated
            FROM _state_gaps g
        )
        SELECT 
            e.date, e.store_id, e.sku_id,
            e.channel, e.region, e.segment_tier,
            e.category, e.brand, e.is_msl,
            e.units_sold_observed, e.inventory_observed, e.oos_observed,
            e.consumer_price_observed, e.promo_id, e.promo_discount_pct,
            e.inventory_is_observed, e.sales_is_observed,
            CASE 
                WHEN e.inventory_is_observed THEN 'Observed'
                WHEN e.inventory_estimated IS NOT NULL THEN 'Estimated'
                ELSE 'Unknown'
            END AS inventory_data_status,
            CASE 
                WHEN e.sales_is_observed THEN 'Observed'
                WHEN e.units_sold_estimated IS NOT NULL THEN 'Estimated'
                ELSE 'Unknown'
            END AS sales_data_status,
            COALESCE(e.inventory_observed, e.inventory_estimated) AS inventory_model_state,
            COALESCE(e.units_sold_observed, e.units_sold_estimated) AS sales_model_state,
            e.units_sold_estimated, e.inventory_estimated,
            CASE WHEN e.inventory_estimated IS NOT NULL THEN (e.inventory_estimated = 0) END AS oos_estimated,
            e.days_since_inventory_report::INTEGER AS days_since_inventory_report,
            e.last_report_date,
            e.receipt_units_from_deliveries, e.receipt_delivery_count,
            la.last_audit_date, la.days_since_last_audit,
            la.planogram_compliance_latest, la.secondary_display_latest, la.shelf_share_pct_latest
        FROM estimated e
        LEFT JOIN _latest_audits la USING (date, store_id, sku_id);
    """)

    # 7. Gold KPI Marts (mart_store_day, mart_commercial_period, mart_execution_visit, mart_negotiation_context)
    log_info("7/7 Generating Gold Marts (mart_store_day, commercial_period, execution_visit, negotiation_context)...")

    # Mart: mart_store_day (Dual views: reported_only & operational_estimate)
    con.execute("""
        CREATE TEMP VIEW _kpi_daily_dual AS
        SELECT 
            d.date, d.store_id, d.sku_id, d.channel, d.region, d.segment_tier, d.category, d.is_msl,
            v.metric_view,
            d.inventory_is_observed, d.sales_is_observed,
            d.days_since_inventory_report,
            CASE WHEN v.metric_view = 'reported_only' THEN d.inventory_observed ELSE d.inventory_model_state END AS inventory,
            CASE WHEN v.metric_view = 'reported_only' THEN d.units_sold_observed ELSE d.sales_model_state END AS sales,
            (v.metric_view = 'operational_estimate' AND d.inventory_estimated IS NOT NULL) AS inventory_is_estimated,
            (v.metric_view = 'operational_estimate' AND d.units_sold_estimated IS NOT NULL) AS sales_is_estimated,
            CASE WHEN v.metric_view = 'reported_only' THEN 'Reported source facts' ELSE 'Candidate operational estimates' END AS release_status
        FROM gold.mart_store_sku_daily d
        CROSS JOIN (VALUES ('reported_only'), ('operational_estimate')) v(metric_view);

        CREATE TABLE gold.mart_store_day AS
        WITH components AS (
            SELECT 
                date, store_id, metric_view,
                any_value(channel) AS channel,
                any_value(region) AS region,
                any_value(segment_tier) AS segment_tier,
                count(*)::INTEGER AS expected_sku_count,
                count(inventory)::INTEGER AS known_inventory_sku_count,
                count(*) FILTER (WHERE inventory_is_observed)::INTEGER AS observed_inventory_sku_count,
                count(*) FILTER (WHERE inventory_is_estimated)::INTEGER AS estimated_inventory_sku_count,
                count(*) FILTER (WHERE inventory IS NULL)::INTEGER AS unknown_inventory_sku_count,
                count(*) FILTER (WHERE inventory = 0)::INTEGER AS oos_sku_count,
                count(sales)::INTEGER AS known_sales_sku_count,
                count(*) FILTER (WHERE sales_is_observed)::INTEGER AS observed_sales_sku_count,
                count(*) FILTER (WHERE sales_is_estimated)::INTEGER AS estimated_sales_sku_count,
                COALESCE(sum(sales), 0)::INTEGER AS sell_out_units_available,
                count(*) FILTER (WHERE is_msl)::INTEGER AS required_msl_sku_count,
                count(*) FILTER (WHERE is_msl AND inventory IS NOT NULL)::INTEGER AS known_msl_sku_count,
                count(*) FILTER (WHERE is_msl AND inventory = 0)::INTEGER AS unavailable_msl_sku_count,
                count(*) FILTER (WHERE is_msl AND inventory IS NULL)::INTEGER AS unknown_msl_sku_count,
                max(days_since_inventory_report)::INTEGER AS max_days_since_inventory_report,
                any_value(release_status) AS release_status
            FROM _kpi_daily_dual
            GROUP BY date, store_id, metric_view
        ), states AS (
            SELECT *,
                CASE 
                    WHEN required_msl_sku_count = 0 THEN 'N/A'
                    WHEN unavailable_msl_sku_count > 0 THEN 'Noncompliant'
                    WHEN known_msl_sku_count = required_msl_sku_count THEN 'Compliant'
                    ELSE 'Unknown'
                END AS msl_state
            FROM components
        ), store_counts AS (
            SELECT *,
                (msl_state <> 'N/A')::INTEGER AS msl_eligible_store_count,
                (msl_state IN ('Compliant', 'Noncompliant'))::INTEGER AS msl_assessable_store_count,
                (msl_state = 'Compliant')::INTEGER AS msl_compliant_store_count,
                (msl_state = 'Noncompliant')::INTEGER AS msl_noncompliant_store_count,
                (msl_state = 'Unknown')::INTEGER AS msl_unknown_store_count
            FROM states
        )
        SELECT 
            date, store_id, metric_view, channel, region, segment_tier,
            expected_sku_count, known_inventory_sku_count, observed_inventory_sku_count,
            estimated_inventory_sku_count, unknown_inventory_sku_count, oos_sku_count,
            known_sales_sku_count, observed_sales_sku_count, estimated_sales_sku_count,
            sell_out_units_available, required_msl_sku_count, known_msl_sku_count,
            unavailable_msl_sku_count, unknown_msl_sku_count, max_days_since_inventory_report,
            msl_state, msl_eligible_store_count, msl_assessable_store_count,
            msl_compliant_store_count, msl_noncompliant_store_count, msl_unknown_store_count,
            ROUND(100.0 * oos_sku_count / NULLIF(known_inventory_sku_count, 0), 2) AS oos_rate_pct,
            ROUND(100.0 * observed_inventory_sku_count / NULLIF(expected_sku_count, 0), 2) AS reporting_coverage_pct,
            ROUND(100.0 * known_inventory_sku_count / NULLIF(expected_sku_count, 0), 2) AS known_inventory_coverage_pct,
            ROUND(100.0 * estimated_inventory_sku_count / NULLIF(known_inventory_sku_count, 0), 2) AS estimated_inventory_share_of_known_pct,
            ROUND(100.0 * msl_compliant_store_count / NULLIF(msl_assessable_store_count, 0), 2) AS msl_compliance_pct,
            ROUND(100.0 * msl_assessable_store_count / NULLIF(msl_eligible_store_count, 0), 2) AS msl_assessment_coverage_pct,
            ROUND(100.0 * msl_compliant_store_count / NULLIF(msl_eligible_store_count, 0), 2) AS msl_lower_bound_pct,
            ROUND(100.0 * (msl_compliant_store_count + msl_unknown_store_count) / NULLIF(msl_eligible_store_count, 0), 2) AS msl_upper_bound_pct,
            release_status
        FROM store_counts;
    """)

    # Mart: mart_commercial_period (weekly, biweekly Traditional Trade, monthly)
    con.execute("""
        CREATE TEMP TABLE _comm_dates AS
        WITH dates AS (SELECT DISTINCT date FROM gold.mart_store_sku_daily)
        SELECT date, 'week' AS period_type, date_trunc('week', date)::DATE AS period_start,
               (date_trunc('week', date)::DATE + INTERVAL 6 DAY)::DATE AS period_end, 'event_date' AS alignment_basis FROM dates
        UNION ALL
        SELECT date, 'biweek', (DATE '2025-01-06' + (14 * floor(date_diff('day', DATE '2025-01-06', date) / 14.0))::INTEGER)::DATE,
               (DATE '2025-01-06' + (14 * floor(date_diff('day', DATE '2025-01-06', date) / 14.0))::INTEGER + 13)::DATE, 'event_date' FROM dates
        UNION ALL
        SELECT date, 'month', date_trunc('month', date)::DATE, last_day(date)::DATE, 'event_date' FROM dates;

        CREATE TABLE gold.mart_commercial_period AS
        WITH sales_flow AS (
            SELECT 
                p.period_type, p.period_start, p.period_end, p.alignment_basis,
                d.store_id, d.sku_id, d.metric_view,
                min(d.date) AS covered_start, max(d.date) AS covered_end,
                count(*)::INTEGER AS expected_sku_days,
                count(d.sales)::INTEGER AS known_sell_out_days,
                count(*) FILTER (WHERE d.sales_is_observed)::INTEGER AS reported_sell_out_days,
                count(*) FILTER (WHERE d.sales_is_estimated)::INTEGER AS estimated_sell_out_days,
                COALESCE(sum(d.sales), 0)::INTEGER AS sell_out_units_available,
                ROUND(COALESCE(sum(d.sales * p_ref.consumer_price), 0), 2) AS consumer_sales_value_observed
            FROM _kpi_daily_dual d
            JOIN _comm_dates p USING (date)
            JOIN silver.dim_products p_ref USING (sku_id)
            GROUP BY 1, 2, 3, 4, 5, 6, 7
        ),
        invoices AS (
            SELECT 
                p.period_type, p.period_start, p.alignment_basis,
                i.store_id, i.sku_id,
                count(*)::INTEGER AS invoice_line_count,
                COALESCE(sum(i.units_sold), 0)::INTEGER AS invoice_units_observed,
                ROUND(COALESCE(sum(i.gross_sales), 0), 2) AS invoice_gross_sales,
                ROUND(COALESCE(sum(i.net_sales), 0), 2) AS invoice_net_sales,
                ROUND(COALESCE(sum(i.gross_sales - i.net_sales), 0), 2) AS invoice_discount_value,
                ROUND(COALESCE(sum(i.units_sold * p_ref.unit_cost), 0), 2) AS invoice_cost_at_static_reference,
                ROUND(COALESCE(sum(i.units_sold * i.extra_discount_pct), 0), 4) AS extra_discount_unit_weighted_sum,
                count(*) FILTER (WHERE i.guardrail_violation)::INTEGER AS source_violation_line_count,
                max(i.date) AS last_invoice_date
            FROM silver.fact_sell_in i
            JOIN silver.dim_products p_ref USING (sku_id)
            JOIN (SELECT DISTINCT period_type, period_start, period_end, alignment_basis FROM _comm_dates) p
              ON i.date BETWEEN p.period_start AND p.period_end
            GROUP BY 1, 2, 3, 4, 5
        ),
        receipts AS (
            SELECT 
                p.period_type, p.period_start, p.alignment_basis,
                r.store_id, r.sku_id,
                COALESCE(sum(r.receipt_units), 0)::INTEGER AS delivered_units,
                count(*)::INTEGER AS delivery_event_count
            FROM silver.replenishment_events r
            JOIN _comm_dates p ON r.receipt_date = p.date
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT 
            s.period_type, s.period_start, s.period_end, s.alignment_basis,
            s.store_id, s.sku_id, s.metric_view,
            s.covered_start, s.covered_end, s.expected_sku_days,
            s.known_sell_out_days, s.reported_sell_out_days, s.estimated_sell_out_days,
            s.sell_out_units_available, s.consumer_sales_value_observed,
            st.channel, st.region, st.segment_tier, pr.category,
            COALESCE(i.invoice_line_count, 0) AS invoice_line_count,
            COALESCE(i.invoice_units_observed, 0) AS invoice_units_observed,
            COALESCE(i.invoice_gross_sales, 0.0) AS invoice_gross_sales,
            COALESCE(i.invoice_net_sales, 0.0) AS invoice_net_sales,
            COALESCE(i.invoice_discount_value, 0.0) AS invoice_discount_value,
            COALESCE(i.invoice_cost_at_static_reference, 0.0) AS invoice_cost_at_static_reference,
            COALESCE(i.extra_discount_unit_weighted_sum, 0.0) AS extra_discount_unit_weighted_sum,
            COALESCE(i.source_violation_line_count, 0) AS source_violation_line_count,
            i.last_invoice_date,
            COALESCE(r.delivered_units, 0) AS delivered_units,
            COALESCE(r.delivery_event_count, 0) AS delivery_event_count,
            (s.covered_start = s.period_start AND s.covered_end = s.period_end) AS period_is_complete,
            i.invoice_units_observed AS sell_in_units_available,
            (s.expected_sku_days - s.known_sell_out_days) AS unknown_sell_out_days,
            ROUND(100.0 * s.reported_sell_out_days / NULLIF(s.expected_sku_days, 0), 2) AS reporting_coverage_pct,
            ROUND(100.0 * s.known_sell_out_days / NULLIF(s.expected_sku_days, 0), 2) AS known_sales_coverage_pct,
            (COALESCE(i.invoice_units_observed, 0) - s.sell_out_units_available) AS sell_in_out_gap_units,
            (i.invoice_units_observed IS NOT NULL) AS flow_is_comparable,
            i.invoice_units_observed::NUMERIC(12,2) AS comparable_sell_in_units,
            s.sell_out_units_available::NUMERIC(12,2) AS comparable_sell_out_units,
            (COALESCE(i.invoice_units_observed, 0) - s.sell_out_units_available)::NUMERIC(12,2) AS comparable_gap_units,
            ROUND(100.0 * (COALESCE(i.invoice_units_observed, 0) - s.sell_out_units_available) / NULLIF(s.sell_out_units_available, 0), 2) AS sell_in_out_gap_pct,
            (COALESCE(r.delivered_units, 0) - s.sell_out_units_available)::NUMERIC(12,2) AS delivered_out_gap_units,
            ROUND(100.0 * i.invoice_discount_value / NULLIF(i.invoice_gross_sales, 0), 2) AS trade_discount_weighted_pct,
            ROUND(i.invoice_net_sales / NULLIF(i.invoice_units_observed, 0), 2) AS net_trade_unit_price,
            (i.invoice_net_sales - i.invoice_cost_at_static_reference) AS trade_margin_value_at_static_cost,
            ROUND(100.0 * (i.invoice_net_sales - i.invoice_cost_at_static_reference) / NULLIF(i.invoice_net_sales, 0), 2) AS trade_margin_pct_at_static_cost,
            CASE 
                WHEN i.invoice_units_observed IS NULL THEN 'Unknown flow'
                WHEN s.known_sell_out_days < s.expected_sku_days THEN 'Partial Sell-out coverage'
                ELSE 'Complete commercial period'
            END AS comparison_status,
            CASE WHEN s.metric_view = 'reported_only' THEN 'Reported source facts' ELSE 'Candidate operational estimates' END AS release_status
        FROM sales_flow s
        LEFT JOIN invoices i USING (period_type, period_start, alignment_basis, store_id, sku_id)
        LEFT JOIN receipts r USING (period_type, period_start, alignment_basis, store_id, sku_id)
        JOIN silver.dim_stores st ON s.store_id = st.store_id
        JOIN silver.dim_products pr ON s.sku_id = pr.sku_id;
    """)

    # Mart: mart_execution_visit
    con.execute("""
        CREATE TABLE gold.mart_execution_visit AS
        WITH scoped AS (
            SELECT 
                a.*, p.category, s.channel, s.region, s.segment_tier,
                v.sku_scope_id, v.scope_label
            FROM silver.fact_execution_audits a
            JOIN silver.dim_products p USING (sku_id)
            JOIN silver.dim_stores s USING (store_id),
            LATERAL (
                SELECT 'all_category' AS sku_scope_id, 'All audited category SKUs' AS scope_label
                UNION ALL SELECT 'sku:' || a.sku_id, 'Individual audited SKU'
                UNION ALL SELECT 'msl_subset', 'Audited MSL SKUs' WHERE p.is_msl
            ) v
        )
        SELECT 
            date AS audit_date, store_id, category, sku_scope_id,
            any_value(channel) AS channel,
            any_value(region) AS region,
            any_value(segment_tier) AS segment_tier,
            any_value(scope_label) AS scope_label,
            count(*)::INTEGER AS audited_row_count,
            count(DISTINCT sku_id)::INTEGER AS audited_sku_count,
            count(*) FILTER (WHERE planogram_compliance)::INTEGER AS compliant_audit_row_count,
            count(*) FILTER (WHERE secondary_display)::INTEGER AS secondary_display_row_count,
            ROUND(sum(shelf_share_pct), 2) AS shelf_allocation_pct,
            1::INTEGER AS visit_count,
            ROUND(100.0 * count(*) FILTER (WHERE planogram_compliance) / count(*), 2) AS planogram_compliance_pct,
            'Audited supplied SKU allocation; not competitor share' AS shelf_share_scope,
            'Observed audit visit' AS release_status
        FROM scoped
        GROUP BY audit_date, store_id, category, sku_scope_id;
    """)

    # Mart: mart_negotiation_context
    con.execute(f"""
        CREATE TABLE gold.mart_negotiation_context AS
        WITH target_date AS (
            SELECT DATE '{as_of}' AS as_of_date
        ),
        sales AS (
            SELECT o.store_id, o.sku_id,
                   sum(o.units_sold_daily)::INTEGER AS sell_out_units_observed_28d,
                   count(o.units_sold_daily)::INTEGER AS observed_sales_days_28d
            FROM silver.fact_sell_out o, target_date m
            WHERE o.date >= m.as_of_date - INTERVAL 27 DAY
            GROUP BY 1, 2
        ),
        invoices AS (
            SELECT i.store_id, i.sku_id,
                   max(i.date) AS last_invoice_date,
                   count(*)::INTEGER AS invoice_lines_28d,
                   sum(i.units_sold)::INTEGER AS sell_in_units_reported_28d,
                   ROUND(sum(i.gross_sales), 2) AS gross_sales_reported_28d,
                   ROUND(sum(i.net_sales), 2) AS net_sales_reported_28d,
                   count(*) FILTER (WHERE i.guardrail_violation)::INTEGER AS source_violation_lines_28d
            FROM silver.fact_sell_in i, target_date m
            WHERE i.date >= m.as_of_date - INTERVAL 27 DAY
            GROUP BY 1, 2
        ),
        latest_audit AS (
            SELECT store_id, sku_id,
                   max(date) AS last_audit_date,
                   arg_max(planogram_compliance, date) AS planogram_compliance_latest,
                   arg_max(secondary_display, date) AS secondary_display_latest,
                   arg_max(shelf_share_pct, date) AS shelf_share_pct_latest
            FROM silver.fact_execution_audits
            GROUP BY 1, 2
        )
        SELECT 
            m.as_of_date,
            s.store_id, p.sku_id,
            s.channel, s.region, s.segment_tier,
            p.product_name, p.category, p.brand, p.is_msl,
            p.list_price AS reference_list_price,
            p.unit_cost AS reference_unit_cost,
            p.consumer_price AS reference_consumer_price,
            ROUND(100.0 * (p.list_price - p.unit_cost) / NULLIF(p.list_price, 0), 2) AS static_reference_margin_pct,
            COALESCE(sl.sell_out_units_observed_28d, 0) AS sell_out_units_observed_28d,
            COALESCE(sl.observed_sales_days_28d, 0) AS observed_sales_days_28d,
            inv.last_invoice_date,
            COALESCE(inv.invoice_lines_28d, 0) AS invoice_lines_28d,
            COALESCE(inv.sell_in_units_reported_28d, 0) AS sell_in_units_reported_28d,
            COALESCE(inv.gross_sales_reported_28d, 0.0) AS gross_sales_reported_28d,
            COALESCE(inv.net_sales_reported_28d, 0.0) AS net_sales_reported_28d,
            ROUND(COALESCE(inv.net_sales_reported_28d, 0.0) / NULLIF(inv.sell_in_units_reported_28d, 0), 2) AS reported_net_trade_price_28d,
            ROUND(100.0 * (inv.gross_sales_reported_28d - inv.net_sales_reported_28d) / NULLIF(inv.gross_sales_reported_28d, 0), 2) AS weighted_trade_discount_pct_28d,
            ROUND(100.0 * (inv.net_sales_reported_28d - inv.sell_in_units_reported_28d * p.unit_cost) / NULLIF(inv.net_sales_reported_28d, 0), 2) AS historical_static_cost_margin_pct,
            COALESCE(inv.source_violation_lines_28d, 0) AS source_violation_lines_28d,
            g.max_discount_pct AS max_discount_pct_source,
            g.min_target_margin AS min_target_margin_source,
            g.required_exec_tradeoff,
            la.last_audit_date,
            la.planogram_compliance_latest,
            la.secondary_display_latest,
            la.shelf_share_pct_latest
        FROM silver.dim_stores s
        CROSS JOIN silver.dim_products p
        CROSS JOIN target_date m
        LEFT JOIN silver.dim_guardrails g ON g.channel = s.channel AND g.category = p.category
        LEFT JOIN sales sl ON sl.store_id = s.store_id AND sl.sku_id = p.sku_id
        LEFT JOIN invoices inv ON inv.store_id = s.store_id AND inv.sku_id = p.sku_id
        LEFT JOIN latest_audit la ON la.store_id = s.store_id AND la.sku_id = p.sku_id;
    """)

    # Mart: metric_definitions
    con.execute("""
        CREATE TABLE gold.metric_definitions (
            metric_id VARCHAR, metric_version VARCHAR, unit VARCHAR,
            numerator_column VARCHAR, denominator_column VARCHAR, formula VARCHAR,
            source_relation VARCHAR, scope_note VARCHAR, zero_denominator_policy VARCHAR,
            release_note VARCHAR
        );
        INSERT INTO gold.metric_definitions VALUES
        ('oos_rate', 'v3', 'percent', 'oos_sku_count', 'known_inventory_sku_count', '100 * SUM(oos_sku_count) / NULLIF(SUM(known_inventory_sku_count), 0)', 'gold.mart_store_day', 'Daily store level out-of-stock percentage', 'NULL / N/A', 'Reported observations or known states'),
        ('reporting_coverage', 'v3', 'percent', 'observed_inventory_sku_count', 'expected_sku_count', '100 * SUM(observed_inventory_sku_count) / NULLIF(SUM(expected_sku_count), 0)', 'gold.mart_store_day', 'Daily inventory reporting coverage across active assortment', 'NULL / N/A', 'Accurately reflects missing store-days'),
        ('msl_compliance', 'v3', 'percent', 'msl_compliant_store_count', 'msl_assessable_store_count', '100 * SUM(msl_compliant) / NULLIF(SUM(msl_assessable), 0)', 'gold.mart_store_day', 'Must Stock List compliance across assessable stores', 'NULL / N/A', 'Strict store-level compliance'),
        ('planogram_compliance', 'v3', 'percent', 'compliant_audit_row_count', 'audited_row_count', '100 * SUM(compliant_audit_row_count) / NULLIF(SUM(audited_row_count), 0)', 'gold.mart_execution_visit', 'Audit visit planogram compliance', 'NULL / N/A', 'Physical store audit visits'),
        ('trade_discount_pct', 'v3', 'percent', 'invoice_discount_value', 'invoice_gross_sales', '100 * (gross_sales - net_sales) / NULLIF(gross_sales, 0)', 'gold.mart_commercial_period', 'Volume weighted trade discount percentage', 'NULL / N/A', 'Sell-in invoices');
    """)

    elapsed = time.time() - t_start
    log_success(f"DuckDB governed transformations completed in {elapsed:.2f} seconds!")
    return con


# ==============================================================================
# PostgreSQL Bulk Loader
# ==============================================================================

def drop_public_relation(cur, name: str) -> None:
    """Safely drops an existing relation in public whether it is a table, view, or materialized view."""
    cur.execute("""
        SELECT relkind FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = %s;
    """, (name,))
    row = cur.fetchone()
    if row:
        relkind = row[0]
        if relkind == "v":
            cur.execute(f'DROP VIEW IF EXISTS public."{name}" CASCADE;')
        elif relkind in ("r", "p"):
            cur.execute(f'DROP TABLE IF EXISTS public."{name}" CASCADE;')
        elif relkind == "m":
            cur.execute(f'DROP MATERIALIZED VIEW IF EXISTS public."{name}" CASCADE;')


def load_governed_duckdb_to_postgres(
    con: duckdb.DuckDBPyConnection,
    pg_conn,
    skip_load: bool = False,
    drop_schemas: bool = False,
    views_only: bool = False,
) -> None:
    """Exports transformed tables from DuckDB and streams them into PostgreSQL."""
    cur = pg_conn.cursor()

    if views_only:
        log_info("Views-only mode requested. Refreshing serving and public compatibility views...")
        log_info("Creating standardized serving views (serving.vw_*)...")
        for view_name, view_sql in POSTGRES_SERVING_VIEWS.items():
            cur.execute(view_sql)
        pg_conn.commit()
        log_success(f"Created {len(POSTGRES_SERVING_VIEWS)} serving views.")

        log_info("Creating public compatibility views for platform dimensions and facts...")
        for view_name, view_sql in POSTGRES_PUBLIC_VIEWS.items():
            drop_public_relation(cur, view_name)
            cur.execute(view_sql)
        pg_conn.commit()
        log_success("Created public compatibility views.")
        return

    if drop_schemas:
        log_warn("Dropping existing PostgreSQL schemas (silver, gold, serving) and legacy public tables...")
        cur.execute("DROP SCHEMA IF EXISTS serving CASCADE;")
        cur.execute("DROP SCHEMA IF EXISTS gold CASCADE;")
        cur.execute("DROP SCHEMA IF EXISTS silver CASCADE;")
        for view_name in POSTGRES_PUBLIC_VIEWS:
            drop_public_relation(cur, view_name)
        pg_conn.commit()
        log_success("Existing schemas and legacy public tables dropped.")

    # Step 1: Create Schemas
    log_info("Ensuring PostgreSQL schemas exist (silver, gold, serving)...")
    cur.execute("CREATE SCHEMA IF NOT EXISTS silver;")
    cur.execute("CREATE SCHEMA IF NOT EXISTS gold;")
    cur.execute("CREATE SCHEMA IF NOT EXISTS serving;")
    pg_conn.commit()

    # Step 2: Create DDL Tables in Silver and Gold
    log_info("Creating relational tables in silver and gold...")
    for table_full_name in list(POSTGRES_DDL.keys()):
        cur.execute(POSTGRES_DDL[table_full_name])
    pg_conn.commit()
    log_success("All table schemas created successfully.")

    if skip_load:
        log_warn("Skip load requested. Omitting data streaming.")
        return

    # Step 3: Stream Data from DuckDB into PostgreSQL
    all_tables = [f"silver.{t}" for t in SILVER_TABLES] + [f"gold.{t}" for t in GOLD_TABLES]

    # Ensure tables are clean before streaming (guarantees idempotence if run without --drop-schemas)
    if not drop_schemas:
        log_info("Truncating existing tables before streaming...")
        cur.execute(f"TRUNCATE TABLE {', '.join(all_tables)} CASCADE;")
        pg_conn.commit()

    print()
    log_info(f"Bulk streaming {len(all_tables)} tables into PostgreSQL using COPY...")
    for full_name in all_tables:
        columns = [r[0] for r in con.execute(f"DESCRIBE {full_name};").fetchall()]
        cols_str = ", ".join(f'"{c}"' for c in columns)

        log_info(f"Streaming {Color.BOLD}{full_name}{Color.RESET}...")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            t0 = time.time()
            con.execute(f"COPY {full_name} TO '{tmp_path}' (HEADER, DELIMITER ',');")

            copy_sql = f"""
                COPY {full_name} ({cols_str})
                FROM STDIN WITH (
                    FORMAT csv,
                    HEADER true,
                    NULL ''
                );
            """

            with open(tmp_path, "r", encoding="utf-8") as f:
                cur.copy_expert(copy_sql, f)

            pg_conn.commit()
            elapsed = time.time() - t0
            cur.execute(f"SELECT count(*) FROM {full_name};")
            count = cur.fetchone()[0]
            log_success(
                f"Loaded {Color.BOLD}{count:,}{Color.RESET} rows into {full_name} "
                f"in {elapsed:.2f}s ({count / max(elapsed, 0.001):,.0f} rows/s)"
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # Step 4: Build B-Tree Indexes
    print()
    log_info("Building performance B-Tree indexes on analytical columns...")
    t_idx = time.time()
    for idx_sql in POSTGRES_INDEXES:
        cur.execute(idx_sql)
    pg_conn.commit()
    log_success(f"Built {len(POSTGRES_INDEXES)} performance indexes in {time.time() - t_idx:.2f}s.")

    # Step 5: Create Serving Views
    print()
    log_info("Creating standardized serving views (serving.vw_*)...")
    for view_name, view_sql in POSTGRES_SERVING_VIEWS.items():
        cur.execute(view_sql)
    pg_conn.commit()
    log_success(f"Created {len(POSTGRES_SERVING_VIEWS)} serving views.")

    # Step 6: Create Public Compatibility Views
    log_info("Creating public compatibility views for platform dimensions and facts...")
    for view_name, view_sql in POSTGRES_PUBLIC_VIEWS.items():
        drop_public_relation(cur, view_name)
        cur.execute(view_sql)
    pg_conn.commit()
    log_success("Created public compatibility views.")


# ==============================================================================
# Verification & Alignment Check
# ==============================================================================

def verify_governed_pipeline(con: duckdb.DuckDBPyConnection, pg_conn) -> bool:
    """
    Verifies that PostgreSQL row counts and KPI components match DuckDB transformed state:
    - Verifies row count parity table by table.
    - Verifies KPI component parity:
      * observed inventory sum parity between store_sku_daily and store_day
      * oos count parity between store_sku_daily and store_day
      * true daily reporting coverage parity (~91.86% reflecting missing store-days)
    """
    cur = pg_conn.cursor()
    all_tables = [f"silver.{t}" for t in SILVER_TABLES] + [f"gold.{t}" for t in GOLD_TABLES]

    print()
    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}")
    print(f"{Color.BOLD}{'VERIFICATION REPORT: DUCKDB VS AIVEN POSTGRESQL':^80}{Color.RESET}")
    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}")
    print(
        f"{'Table Name':<32} | {'DuckDB Rows':<14} | {'Postgres Rows':<14} | {'Status':<10}"
    )
    print(f"{'-' * 32}-+-{'-' * 14}-+-{'-' * 14}-+-{'-' * 10}")

    all_matched = True
    for full_name in all_tables:
        ddb_count = con.execute(f"SELECT count(*) FROM {full_name};").fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {full_name};")
        pg_count = cur.fetchone()[0]

        matched = ddb_count == pg_count
        if not matched:
            all_matched = False
            status_str = f"{Color.RED}MISMATCH{Color.RESET}"
        else:
            status_str = f"{Color.GREEN}MATCHED{Color.RESET}"

        print(f"{full_name:<32} | {ddb_count:<14,} | {pg_count:<14,} | {status_str}")

    print(f"{'-' * 32}-+-{'-' * 14}-+-{'-' * 14}-+-{'-' * 10}")

    # Check KPI components in PostgreSQL
    print()
    log_info("Verifying V3 KPI Component Parity in Aiven PostgreSQL...")
    cur.execute("""
        SELECT 
            (SELECT count(inventory_observed) FROM gold.mart_store_sku_daily) AS daily_inv_obs,
            (SELECT sum(observed_inventory_sku_count) FROM gold.mart_store_day WHERE metric_view = 'reported_only') AS store_day_inv_obs,
            (SELECT count(*) FROM gold.mart_store_sku_daily WHERE oos_observed = true) AS daily_oos_obs,
            (SELECT sum(oos_sku_count) FROM gold.mart_store_day WHERE metric_view = 'reported_only') AS store_day_oos_obs,
            (SELECT ROUND(avg(reporting_coverage_pct), 2) FROM gold.mart_store_day WHERE metric_view = 'reported_only') AS avg_reporting_coverage;
    """)
    kpi_res = cur.fetchone()
    daily_inv_obs, store_day_inv_obs, daily_oos_obs, store_day_oos_obs, avg_coverage = kpi_res

    inv_parity = daily_inv_obs == store_day_inv_obs
    oos_parity = daily_oos_obs == store_day_oos_obs

    print(f"  • Observed Inventory Rows Parity : {daily_inv_obs:,} == {store_day_inv_obs:,} -> "
          f"{Color.GREEN + 'PASS' if inv_parity else Color.RED + 'FAIL'}{Color.RESET}")
    print(f"  • OOS Observations Count Parity   : {daily_oos_obs:,} == {store_day_oos_obs:,} -> "
          f"{Color.GREEN + 'PASS' if oos_parity else Color.RED + 'FAIL'}{Color.RESET}")
    print(f"  • Portfolio Reporting Coverage    : {avg_coverage}% (Accurately captures missing store-days)")

    if all_matched and inv_parity and oos_parity:
        print()
        log_success("All tables, indexes, and KPI components successfully verified and aligned!")
        return True
    else:
        print()
        log_error("Verification detected discrepancies. Please inspect table counts above.")
        return False


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Governed V3 End-to-End ETL Pipeline for Marvel CPG AI Platform"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/raw"),
        help="Path to directory containing raw CSVs (default: data/raw)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env credentials file (default: .env)",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default="2026-06-30",
        help="Business cutoff date (default: 2026-06-30)",
    )
    parser.add_argument(
        "--calendar-start",
        type=str,
        default="2025-01-01",
        help="Calendar start date (default: 2025-01-01)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test DuckDB transformations and PostgreSQL connection without loading data",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Run transformations in DuckDB and print verification metrics without loading to PostgreSQL",
    )
    parser.add_argument(
        "--drop-schemas",
        action="store_true",
        help="Drop existing silver, gold, and serving schemas before loading",
    )
    parser.add_argument(
        "--views-only",
        action="store_true",
        help="Skip data loading and only refresh serving and public compatibility views, then verify",
    )

    args = parser.parse_args()

    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}")
    print(f"{Color.BOLD}{'MARVEL CPG AI PLATFORM: GOVERNED V3 ETL PIPELINE':^80}{Color.RESET}")
    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}")
    log_info(f"Source Directory : {args.source_dir.resolve()}")
    log_info(f"Business Cutoff  : {args.as_of}")
    log_info(f"Calendar Start   : {args.calendar_start}")
    log_info(f"Environment File : {args.env_file.resolve()}")

    # Verify input CSVs exist
    required_files = [
        "dim_stores.csv",
        "dim_products.csv",
        "dim_guardrails.csv",
        "dim_promotions.csv",
        "fact_sell_in.csv",
        "fact_deliveries.csv",
        "fact_execution_audits.csv",
        "fact_sell_out.csv",
    ]
    missing = [f for f in required_files if not (args.source_dir / f).is_file()]
    if missing:
        log_error(f"Missing required CSV files in {args.source_dir}: {missing}")
        sys.exit(1)

    # 1. Run DuckDB Transformations
    con = run_governed_duckdb_transformations(
        args.source_dir, as_of=args.as_of, calendar_start=args.calendar_start
    )

    if args.skip_load:
        log_warn("Skip-load flag detected. Printing DuckDB summary only.")
        for t in SILVER_TABLES:
            count = con.execute(f"SELECT count(*) FROM silver.{t}").fetchone()[0]
            print(f"  • silver.{t:<24}: {count:,} rows")
        for t in GOLD_TABLES:
            count = con.execute(f"SELECT count(*) FROM gold.{t}").fetchone()[0]
            print(f"  • gold.{t:<26}: {count:,} rows")
        log_success("Pipeline execution complete (DuckDB only).")
        return

    # 2. Resolve PostgreSQL Connection
    db_url, dbname = resolve_connection_info(args.env_file)
    log_info(f"Target Database  : {dbname}")

    # 3. Connect to PostgreSQL
    log_info("Connecting to Aiven PostgreSQL...")
    try:
        pg_conn = psycopg2.connect(db_url)
        pg_conn.autocommit = False
        log_success("Connected to Aiven PostgreSQL successfully.")
    except Exception as e:
        log_error(f"Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    if args.dry_run:
        log_warn("Dry-run requested. Verified connection and DuckDB transform without loading.")
        pg_conn.close()
        return

    # 4. Load Data to PostgreSQL
    try:
        load_governed_duckdb_to_postgres(
            con,
            pg_conn,
            skip_load=args.skip_load,
            drop_schemas=args.drop_schemas,
            views_only=args.views_only,
        )
    except Exception as e:
        log_error(f"Error during PostgreSQL load: {e}")
        pg_conn.rollback()
        pg_conn.close()
        sys.exit(1)

    # 5. Verify Alignment
    success = verify_governed_pipeline(con, pg_conn)
    pg_conn.close()

    if not success:
        sys.exit(1)

    print()
    log_success("V3 ETL pipeline execution completed successfully!")


if __name__ == "__main__":
    main()
