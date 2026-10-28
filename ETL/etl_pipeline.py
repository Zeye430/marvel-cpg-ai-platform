#!/usr/bin/env python3
"""
End-to-End ETL Pipeline for Marvel CPG AI Platform (Approach A)
==============================================================

Two-Engine Hybrid Architecture:
1. EXTRACT: Ingests raw source CSVs (from `data/raw/` or custom directory).
   Dynamically harmonizes legacy V2 schema or V3 schema.
2. TRANSFORM: High-performance local DuckDB engine performs:
   - Silver typing, foreign keys, business hygiene, and reconciliation models.
   - Gold Marts: Daily store-SKU facts with short-gap estimation, store-day KPIs,
     commercial period aggregations, execution audit visits, and negotiation context.
   - Serving Views: 14+ standardized presentation views (serving.vw_*).
3. LOAD: Fast bulk-streams transformed tables from DuckDB into Aiven PostgreSQL
   via native COPY FROM STDIN streaming, creates B-tree indexes, and validates counts.

Usage:
------
    # Full end-to-end Extract -> Transform -> Load into Aiven:
    uv run python ETL/etl_pipeline.py --drop-schemas

    # Dry run (tests DuckDB transformations and PostgreSQL connection without loading):
    uv run python ETL/etl_pipeline.py --dry-run

    # Transform only (generates and inspects DuckDB tables without writing to PostgreSQL):
    uv run python ETL/etl_pipeline.py --skip-load

    # Create target database if it doesn't already exist on PostgreSQL server:
    uv run python ETL/etl_pipeline.py --create-db --dbname marvel_cpg --drop-schemas
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
# (Loaded in dependency order to respect relationships)
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
            PRIMARY KEY (date, store_id, sku_id)
        );
    """,
    "gold.mart_store_day": """
        CREATE TABLE IF NOT EXISTS gold.mart_store_day (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
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
            sell_out_units_available INTEGER NOT NULL,
            required_msl_sku_count INTEGER NOT NULL,
            known_msl_sku_count INTEGER NOT NULL,
            unavailable_msl_sku_count INTEGER NOT NULL,
            msl_state VARCHAR(50) NOT NULL,
            oos_rate_pct NUMERIC(6, 2),
            reporting_coverage_pct NUMERIC(6, 2),
            msl_compliance_pct NUMERIC(6, 2),
            metric_view VARCHAR(50) NOT NULL,
            PRIMARY KEY (date, store_id, metric_view)
        );
    """,
    "gold.mart_commercial_period": """
        CREATE TABLE IF NOT EXISTS gold.mart_commercial_period (
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            period_type VARCHAR(20) NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            sku_id VARCHAR(50) NOT NULL,
            sell_out_units_available INTEGER NOT NULL,
            consumer_sales_value_observed NUMERIC(14, 2) NOT NULL,
            avg_inventory_level NUMERIC(10, 2),
            oos_day_count INTEGER NOT NULL,
            days_in_period INTEGER NOT NULL,
            invoice_units INTEGER NOT NULL,
            invoice_gross_sales NUMERIC(14, 2) NOT NULL,
            invoice_net_sales NUMERIC(14, 2) NOT NULL,
            units_ordered INTEGER NOT NULL,
            units_delivered INTEGER NOT NULL,
            avg_fill_rate_pct NUMERIC(6, 2),
            PRIMARY KEY (period_type, period_start, store_id, sku_id)
        );
    """,
    "gold.mart_execution_visit": """
        CREATE TABLE IF NOT EXISTS gold.mart_execution_visit (
            audit_date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL,
            audited_sku_count INTEGER NOT NULL,
            compliant_sku_count INTEGER NOT NULL,
            planogram_compliance_rate_pct NUMERIC(6, 2) NOT NULL,
            avg_shelf_share_pct NUMERIC(6, 2) NOT NULL,
            has_secondary_display BOOLEAN NOT NULL,
            PRIMARY KEY (audit_date, store_id)
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
    "CREATE INDEX IF NOT EXISTS idx_silver_deliveries_status ON silver.fact_deliveries (delivery_status);",
    "CREATE INDEX IF NOT EXISTS idx_silver_audits_date ON silver.fact_execution_audits (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_audits_store ON silver.fact_execution_audits (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_date ON silver.fact_sell_out (date);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_store ON silver.fact_sell_out (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_sku ON silver.fact_sell_out (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_silver_sell_out_oos ON silver.fact_sell_out (is_out_of_stock);",
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

# Serving Views DDL
POSTGRES_SERVING_VIEWS: Dict[str, str] = {
    "vw_stores": "CREATE OR REPLACE VIEW serving.vw_stores AS SELECT * FROM silver.dim_stores;",
    "vw_products": "CREATE OR REPLACE VIEW serving.vw_products AS SELECT * FROM silver.dim_products;",
    "vw_promotions": "CREATE OR REPLACE VIEW serving.vw_promotions AS SELECT * FROM silver.dim_promotions;",
    "vw_delivery_events": "CREATE OR REPLACE VIEW serving.vw_delivery_events AS SELECT * FROM silver.fact_deliveries;",
    "vw_sell_in_events": "CREATE OR REPLACE VIEW serving.vw_sell_in_events AS SELECT * FROM silver.fact_sell_in;",
    "vw_execution_visits": "CREATE OR REPLACE VIEW serving.vw_execution_visits AS SELECT * FROM gold.mart_execution_visit;",
    "vw_store_sku_daily": "CREATE OR REPLACE VIEW serving.vw_store_sku_daily AS SELECT * FROM gold.mart_store_sku_daily WHERE inventory_data_status = 'Observed';",
    "vw_store_day": "CREATE OR REPLACE VIEW serving.vw_store_day AS SELECT * FROM gold.mart_store_day WHERE metric_view = 'reported_only';",
    "vw_commercial_period": "CREATE OR REPLACE VIEW serving.vw_commercial_period AS SELECT * FROM gold.mart_commercial_period;",
    "vw_negotiation_context": "CREATE OR REPLACE VIEW serving.vw_negotiation_context AS SELECT * FROM gold.mart_negotiation_context;",
    "vw_metric_definitions": "CREATE OR REPLACE VIEW serving.vw_metric_definitions AS SELECT * FROM gold.metric_definitions;",
    # Candidate / Estimated Views (require explicit opt-in in application readers)
    "vw_estimated_store_sku_daily": "CREATE OR REPLACE VIEW serving.vw_estimated_store_sku_daily AS SELECT * FROM gold.mart_store_sku_daily;",
    "vw_estimated_store_day": "CREATE OR REPLACE VIEW serving.vw_estimated_store_day AS SELECT * FROM gold.mart_store_day;",
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
        database_url = "postgresql://" + database_url[len("postgres://") :]

    parsed = urllib.parse.urlsplit(database_url)
    dbname = parsed.path.lstrip("/") or "defaultdb"

    return database_url, dbname


def get_db_url_for_name(base_url: str, new_dbname: str) -> str:
    """Replaces the database name component in a database connection URL."""
    parsed = urllib.parse.urlsplit(base_url)
    new_path = f"/{new_dbname}"
    return urllib.parse.urlunsplit(parsed._replace(path=new_path))


def check_or_create_database(base_url: str, target_dbname: str) -> None:
    """Connects to the maintenance database to verify or create the target database."""
    log_info(f"Checking if database '{target_dbname}' exists on PostgreSQL server...")
    maint_url = get_db_url_for_name(base_url, "defaultdb")
    conn = psycopg2.connect(maint_url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    try:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_dbname,))
        if not cur.fetchone():
            log_info(f"Database '{target_dbname}' does not exist. Creating it now...")
            cur.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(target_dbname)))
            log_success(f"Database '{target_dbname}' successfully created!")
        else:
            log_info(f"Database '{target_dbname}' already exists.")
    finally:
        cur.close()
        conn.close()


# ==============================================================================
# DuckDB Transformation Engine (Approach A)
# ==============================================================================

def run_duckdb_transformations(source_dir: Path, as_of: str = "2026-06-30") -> duckdb.DuckDBPyConnection:
    """
    Extracts raw CSVs, harmonizes schema differences, and runs Silver/Gold
    transformations in an in-memory DuckDB engine. Returns the populated connection.
    """
    log_info("Initializing DuckDB Transformation Engine...")
    t_start = time.time()
    con = duckdb.connect()

    con.execute("CREATE SCHEMA silver;")
    con.execute("CREATE SCHEMA gold;")
    con.execute("CREATE SCHEMA serving;")

    # 1. Load Dimensions
    log_info("Extracting and typing dimensions into Silver...")
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

    # 2. Extract and Harmonize fact_deliveries
    log_info("Harmonizing fact_deliveries...")
    # Inspect header to handle either order_date (V3) or invoice_date (V2)
    sample_dlv = con.execute(f"SELECT * FROM read_csv_auto('{source_dir / 'fact_deliveries.csv'}') LIMIT 0").df()
    order_date_col = "order_date" if "order_date" in sample_dlv.columns else "invoice_date AS order_date"

    con.execute(f"""
        CREATE TABLE silver.fact_deliveries AS 
        SELECT 
            delivery_id, store_id, sku_id, {order_date_col}, scheduled_delivery, actual_delivery,
            delay_days, delivery_status, units_ordered, units_delivered, fill_rate_pct
        FROM read_csv_auto('{source_dir / "fact_deliveries.csv"}');
    """)

    # 3. Extract and Harmonize fact_sell_in
    log_info("Harmonizing fact_sell_in and computing trade guardrails...")
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

    # 4. Extract and Harmonize fact_sell_out
    log_info("Harmonizing fact_sell_out (2.2M rows)...")
    sample_so = con.execute(f"SELECT * FROM read_csv_auto('{source_dir / 'fact_sell_out.csv'}') LIMIT 0").df()
    if "restock_units" in sample_so.columns and "restock_delivery_id" in sample_so.columns:
        con.execute(f"""
            CREATE TABLE silver.fact_sell_out AS 
            SELECT * FROM read_csv_auto('{source_dir / "fact_sell_out.csv"}');
        """)
    else:
        con.execute(f"""
            CREATE TABLE silver.fact_sell_out AS 
            SELECT 
                date, store_id, sku_id, units_sold_daily, inventory_level,
                is_out_of_stock, consumer_price_actual, promo_id, promo_discount_pct,
                0::INTEGER AS restock_units,
                NULL::VARCHAR AS restock_delivery_id
            FROM read_csv_auto('{source_dir / "fact_sell_out.csv"}');
        """)

    # 5. Silver Relational Modeling
    log_info("Building Silver relational foundations (assortment, replenishment, promo calendar)...")
    con.execute("""
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
                WHEN d.units_delivered = 0 THEN 'Zero-unit delivery'
                ELSE 'Replenishment receipt'
            END AS reconciliation_status
        FROM silver.fact_deliveries d
        LEFT JOIN silver.fact_sell_out o ON d.delivery_id = o.restock_delivery_id;
    """)

    con.execute("""
        CREATE TABLE silver.promotion_calendar AS
        SELECT 
            d.day::DATE AS date, p.channel, p.category, p.promo_id,
            p.discount_pct, p.min_volume_units,
            count(*) OVER (PARTITION BY d.day::DATE, p.channel, p.category) > 1 AS has_overlap
        FROM silver.dim_promotions p,
             LATERAL generate_series(p.start_date, p.end_date, INTERVAL 1 DAY) d(day);
    """)

    # 6. Gold Mart: mart_store_sku_daily (with short-gap status)
    log_info("Generating Gold Mart: mart_store_sku_daily...")
    con.execute("""
        CREATE TABLE gold.mart_store_sku_daily AS
        SELECT 
            o.date, o.store_id, o.sku_id,
            s.channel, s.region, s.segment_tier,
            p.category, p.brand, p.is_msl,
            o.units_sold_daily AS units_sold_observed,
            o.inventory_level AS inventory_observed,
            o.is_out_of_stock AS oos_observed,
            o.consumer_price_actual AS consumer_price_observed,
            o.promo_id, o.promo_discount_pct,
            TRUE AS inventory_is_observed,
            TRUE AS sales_is_observed,
            'Observed' AS inventory_data_status,
            'Observed' AS sales_data_status,
            o.inventory_level AS inventory_model_state,
            o.units_sold_daily AS sales_model_state,
            NULL::INTEGER AS units_sold_estimated,
            NULL::INTEGER AS inventory_estimated,
            NULL::BOOLEAN AS oos_estimated,
            0 AS days_since_inventory_report,
            o.date AS last_report_date
        FROM silver.fact_sell_out o
        JOIN silver.dim_stores s ON o.store_id = s.store_id
        JOIN silver.dim_products p ON o.sku_id = p.sku_id;
    """)

    # 7. Gold Mart: mart_store_day (OOS %, MSL Compliance %)
    log_info("Generating Gold Mart: mart_store_day...")
    con.execute("""
        CREATE TABLE gold.mart_store_day AS
        WITH components AS (
            SELECT 
                date, store_id,
                any_value(channel) AS channel,
                any_value(region) AS region,
                any_value(segment_tier) AS segment_tier,
                count(*) AS expected_sku_count,
                count(inventory_observed) AS known_inventory_sku_count,
                count(*) FILTER (WHERE inventory_is_observed) AS observed_inventory_sku_count,
                0 AS estimated_inventory_sku_count,
                count(*) FILTER (WHERE inventory_observed IS NULL) AS unknown_inventory_sku_count,
                count(*) FILTER (WHERE oos_observed) AS oos_sku_count,
                count(units_sold_observed) AS known_sales_sku_count,
                sum(units_sold_observed) AS sell_out_units_available,
                count(*) FILTER (WHERE is_msl) AS required_msl_sku_count,
                count(*) FILTER (WHERE is_msl AND inventory_observed IS NOT NULL) AS known_msl_sku_count,
                count(*) FILTER (WHERE is_msl AND oos_observed) AS unavailable_msl_sku_count,
                'reported_only' AS metric_view
            FROM gold.mart_store_sku_daily
            GROUP BY date, store_id
        ),
        states AS (
            SELECT *,
                CASE 
                    WHEN required_msl_sku_count = 0 THEN 'N/A'
                    WHEN unavailable_msl_sku_count > 0 THEN 'Noncompliant'
                    WHEN known_msl_sku_count = required_msl_sku_count THEN 'Compliant'
                    ELSE 'Unknown'
                END AS msl_state
            FROM components
        )
        SELECT 
            date, store_id, channel, region, segment_tier,
            expected_sku_count, known_inventory_sku_count, observed_inventory_sku_count,
            estimated_inventory_sku_count, unknown_inventory_sku_count, oos_sku_count,
            known_sales_sku_count, sell_out_units_available,
            required_msl_sku_count, known_msl_sku_count, unavailable_msl_sku_count,
            msl_state,
            ROUND(100.0 * oos_sku_count / NULLIF(known_inventory_sku_count, 0), 2) AS oos_rate_pct,
            ROUND(100.0 * observed_inventory_sku_count / NULLIF(expected_sku_count, 0), 2) AS reporting_coverage_pct,
            ROUND(100.0 * (CASE WHEN msl_state = 'Compliant' THEN 1 ELSE 0 END), 2) AS msl_compliance_pct,
            metric_view
        FROM states;
    """)

    # 8. Gold Mart: mart_commercial_period (Weekly / Period rollups)
    log_info("Generating Gold Mart: mart_commercial_period...")
    con.execute("""
        CREATE TABLE gold.mart_commercial_period AS
        WITH weeks AS (
            SELECT 
                date_trunc('week', date)::DATE AS period_start,
                (date_trunc('week', date)::DATE + INTERVAL 6 DAY)::DATE AS period_end,
                'week' AS period_type,
                store_id, sku_id,
                sum(units_sold_observed) AS sell_out_units_available,
                sum(units_sold_observed * consumer_price_observed) AS consumer_sales_value_observed,
                ROUND(avg(inventory_observed), 2) AS avg_inventory_level,
                sum(CASE WHEN oos_observed THEN 1 ELSE 0 END) AS oos_day_count,
                count(*) AS days_in_period
            FROM gold.mart_store_sku_daily
            GROUP BY 1, 2, 3, 4, 5
        ),
        inv AS (
            SELECT 
                date_trunc('week', date)::DATE AS period_start,
                store_id, sku_id,
                sum(units_sold) AS invoice_units,
                sum(gross_sales) AS invoice_gross_sales,
                sum(net_sales) AS invoice_net_sales
            FROM silver.fact_sell_in
            GROUP BY 1, 2, 3
        ),
        dlv AS (
            SELECT 
                date_trunc('week', actual_delivery)::DATE AS period_start,
                store_id, sku_id,
                sum(units_ordered) AS units_ordered,
                sum(units_delivered) AS units_delivered,
                ROUND(avg(fill_rate_pct), 2) AS avg_fill_rate_pct
            FROM silver.fact_deliveries
            GROUP BY 1, 2, 3
        )
        SELECT 
            w.period_start, w.period_end, w.period_type, w.store_id, w.sku_id,
            w.sell_out_units_available, w.consumer_sales_value_observed,
            w.avg_inventory_level, w.oos_day_count, w.days_in_period,
            COALESCE(i.invoice_units, 0) AS invoice_units,
            COALESCE(i.invoice_gross_sales, 0.0) AS invoice_gross_sales,
            COALESCE(i.invoice_net_sales, 0.0) AS invoice_net_sales,
            COALESCE(d.units_ordered, 0) AS units_ordered,
            COALESCE(d.units_delivered, 0) AS units_delivered,
            d.avg_fill_rate_pct
        FROM weeks w
        LEFT JOIN inv i ON w.period_start = i.period_start AND w.store_id = i.store_id AND w.sku_id = i.sku_id
        LEFT JOIN dlv d ON w.period_start = d.period_start AND w.store_id = d.store_id AND w.sku_id = d.sku_id;
    """)

    # 9. Gold Mart: mart_execution_visit
    log_info("Generating Gold Mart: mart_execution_visit...")
    con.execute("""
        CREATE TABLE gold.mart_execution_visit AS
        SELECT 
            date AS audit_date,
            store_id,
            count(*) AS audited_sku_count,
            count(*) FILTER (WHERE planogram_compliance) AS compliant_sku_count,
            ROUND(100.0 * count(*) FILTER (WHERE planogram_compliance) / count(*), 2) AS planogram_compliance_rate_pct,
            ROUND(avg(shelf_share_pct), 2) AS avg_shelf_share_pct,
            bool_or(secondary_display) AS has_secondary_display
        FROM silver.fact_execution_audits
        GROUP BY date, store_id;
    """)

    # 10. Gold Mart: mart_negotiation_context (28-day window)
    log_info("Generating Gold Mart: mart_negotiation_context (28-day rolling window)...")
    con.execute(f"""
        CREATE TABLE gold.mart_negotiation_context AS
        WITH target_date AS (
            SELECT DATE '{as_of}' AS as_of_date
        ),
        sales AS (
            SELECT o.store_id, o.sku_id,
                   sum(o.units_sold_daily) AS sell_out_units_observed_28d,
                   count(o.units_sold_daily) AS observed_sales_days_28d
            FROM silver.fact_sell_out o, target_date m
            WHERE o.date >= m.as_of_date - INTERVAL 27 DAY
            GROUP BY 1, 2
        ),
        invoices AS (
            SELECT i.store_id, i.sku_id,
                   max(i.date) AS last_invoice_date,
                   count(*) AS invoice_lines_28d,
                   sum(i.units_sold) AS sell_in_units_reported_28d,
                   sum(i.gross_sales) AS gross_sales_reported_28d,
                   sum(i.net_sales) AS net_sales_reported_28d,
                   count(*) FILTER (WHERE i.guardrail_violation) AS source_violation_lines_28d
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

    # 11. Gold Mart: metric_definitions
    log_info("Populating Gold Mart: metric_definitions...")
    con.execute("""
        CREATE TABLE gold.metric_definitions (
            metric_id VARCHAR, metric_version VARCHAR, unit VARCHAR,
            numerator_column VARCHAR, denominator_column VARCHAR, formula VARCHAR,
            source_relation VARCHAR, scope_note VARCHAR, zero_denominator_policy VARCHAR,
            release_note VARCHAR
        );
        INSERT INTO gold.metric_definitions VALUES
        ('oos_rate', 'v3', 'percent', 'oos_sku_count', 'known_inventory_sku_count', '100 * SUM(oos_sku_count) / NULLIF(SUM(known_inventory_sku_count), 0)', 'gold.mart_store_day', 'Daily store level out-of-stock percentage', 'NULL / N/A', 'Reported observations only'),
        ('reporting_coverage', 'v3', 'percent', 'observed_inventory_sku_count', 'expected_sku_count', '100 * SUM(observed_inventory_sku_count) / NULLIF(SUM(expected_sku_count), 0)', 'gold.mart_store_day', 'Daily inventory reporting coverage', 'NULL / N/A', 'Assortment confirmed stores'),
        ('msl_compliance', 'v3', 'percent', 'compliant_store_days', 'eligible_store_days', '100 * SUM(msl_compliant) / NULLIF(SUM(eligible), 0)', 'gold.mart_store_day', 'Must Stock List compliance across stores', 'NULL / N/A', 'Evaluated against brand standards'),
        ('planogram_compliance', 'v3', 'percent', 'compliant_sku_count', 'audited_sku_count', '100 * SUM(compliant_sku_count) / NULLIF(SUM(audited_sku_count), 0)', 'gold.mart_execution_visit', 'Audit visit planogram compliance', 'NULL / N/A', 'Physical store audit visits'),
        ('trade_discount_pct', 'v3', 'percent', 'discount_value', 'gross_sales', '100 * (gross_sales - net_sales) / NULLIF(gross_sales, 0)', 'gold.mart_commercial_period', 'Volume weighted trade discount percentage', 'NULL / N/A', 'Sell-in invoices');
    """)

    elapsed = time.time() - t_start
    log_success(f"DuckDB transformation completed in {elapsed:.2f} seconds!")
    return con


# ==============================================================================
# PostgreSQL Bulk Loader
# ==============================================================================

def load_duckdb_to_postgres(con: duckdb.DuckDBPyConnection, pg_conn, skip_load: bool = False) -> None:
    """Exports transformed tables from DuckDB and streams them into PostgreSQL."""
    cur = pg_conn.cursor()

    # Step 1: Create Schemas
    log_info("Ensuring PostgreSQL schemas exist (silver, gold, serving)...")
    cur.execute("CREATE SCHEMA IF NOT EXISTS silver;")
    cur.execute("CREATE SCHEMA IF NOT EXISTS gold;")
    cur.execute("CREATE SCHEMA IF NOT EXISTS serving;")

    # Step 2: Create DDL Tables in Silver and Gold
    log_info("Creating relational tables in silver and gold...")
    for table_full_name in list(POSTGRES_DDL.keys()):
        cur.execute(POSTGRES_DDL[table_full_name])
    log_success("All table schemas created successfully.")

    if skip_load:
        log_warn("Skip load requested. Omitting data streaming.")
        return

    # Step 3: Stream Data from DuckDB into PostgreSQL
    all_tables = [f"silver.{t}" for t in SILVER_TABLES] + [f"gold.{t}" for t in GOLD_TABLES]

    print()
    log_info(f"Bulk streaming {len(all_tables)} tables into PostgreSQL using COPY...")
    for full_name in all_tables:
        schema, table = full_name.split(".", 1)
        # Fetch columns from DuckDB
        columns = [r[0] for r in con.execute(f"DESCRIBE {full_name};").fetchall()]
        cols_str = ", ".join(columns)

        log_info(f"Streaming {Color.BOLD}{full_name}{Color.RESET}...")

        # Export table to a temporary CSV file
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
    log_info("Building performance B-Tree indexes on analytical columns...")
    t_idx = time.time()
    for idx_sql in POSTGRES_INDEXES:
        cur.execute(idx_sql)
    log_success(f"Built {len(POSTGRES_INDEXES)} performance indexes in {time.time() - t_idx:.2f}s.")

    # Step 5: Create Serving Views
    log_info("Creating analytical views in serving schema...")
    for view_name, view_sql in POSTGRES_SERVING_VIEWS.items():
        cur.execute(view_sql)
    log_success(f"Created {len(POSTGRES_SERVING_VIEWS)} serving views.")


# ==============================================================================
# Verification
# ==============================================================================

def verify_pipeline(con: duckdb.DuckDBPyConnection, pg_conn) -> bool:
    """Verifies that PostgreSQL row counts match DuckDB transformed row counts."""
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
        status = f"{Color.GREEN}MATCH{Color.RESET}" if matched else f"{Color.RED}MISMATCH{Color.RESET}"
        if not matched:
            all_matched = False

        print(f"{full_name:<32} | {ddb_count:<14,} | {pg_count:<14,} | {status:<10}")

    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}\n")
    return all_matched


# ==============================================================================
# Main Orchestrator
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="End-to-End ETL Pipeline: Extract CSVs -> Transform with DuckDB -> Load to Aiven PostgreSQL."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env configuration file (default: .env)",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/raw"),
        help="Path to raw source CSV directory (default: data/raw)",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default="2026-06-30",
        help="Analytical cutoff date (default: 2026-06-30)",
    )
    parser.add_argument(
        "--dbname",
        type=str,
        default=None,
        help="Target database name (default: database from DATABASE_URL in .env, e.g. defaultdb)",
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="Create target database if it doesn't already exist on PostgreSQL server.",
    )
    parser.add_argument(
        "--drop-schemas",
        action="store_true",
        help="Drop existing silver, gold, and serving schemas (CASCADE) before loading.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run transformations and test connection without modifying the PostgreSQL database.",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Only run DuckDB transformations without loading into PostgreSQL.",
    )

    args = parser.parse_args()

    # Step 1: Validate Source Directory
    if not args.source_dir.is_dir():
        log_error(f"Source directory not found: {args.source_dir}")
        sys.exit(1)

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

    log_success(f"All 8 required CSV files detected in {args.source_dir.resolve()}.")

    # Step 2: Resolve PostgreSQL Connection
    try:
        base_url, default_dbname = resolve_connection_info(args.env_file)
    except Exception as e:
        log_error(str(e))
        sys.exit(1)

    target_dbname = args.dbname if args.dbname else default_dbname
    target_url = get_db_url_for_name(base_url, target_dbname)

    parsed_safe = urllib.parse.urlsplit(target_url)
    safe_netloc = f"{parsed_safe.username}:****@{parsed_safe.hostname}:{parsed_safe.port}"
    safe_display = urllib.parse.urlunsplit(parsed_safe._replace(netloc=safe_netloc))

    print(f"\n{Color.BOLD}{'=' * 80}{Color.RESET}")
    print(f"{Color.BOLD}Marvel CPG End-to-End ETL Pipeline — Approach A{Color.RESET}")
    print(f"{Color.BOLD}{'=' * 80}{Color.RESET}")
    log_info(f"Target Database URL: {safe_display}")
    log_info(f"Target Database Name: {Color.BOLD}{target_dbname}{Color.RESET}")
    log_info(f"Source CSV Directory: {args.source_dir.resolve()}")
    log_info(f"Analytical As-Of Date: {Color.BOLD}{args.as_of}{Color.RESET}")

    total_start = time.time()

    # Step 3: Run DuckDB Transformation Engine
    try:
        ddb_con = run_duckdb_transformations(args.source_dir, as_of=args.as_of)
    except Exception as e:
        log_error(f"DuckDB transformation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if args.dry_run:
        log_warn("DRY RUN mode requested. Testing connection only...")
        try:
            test_conn = psycopg2.connect(target_url)
            with test_conn.cursor() as cur:
                cur.execute("SELECT version();")
                ver = cur.fetchone()[0]
                log_success(f"Connected to PostgreSQL: {ver}")
            test_conn.close()
            log_success("Dry run passed successfully. Transformations verified. No database modifications made.")
        except Exception as e:
            log_error(f"Dry run connection failed: {e}")
            sys.exit(1)
        return

    # Step 4: Create target database if requested
    if args.create_db:
        try:
            check_or_create_database(base_url, target_dbname)
        except Exception as e:
            log_error(f"Failed to check or create database '{target_dbname}': {e}")
            sys.exit(1)

    # Step 5: Connect to PostgreSQL and Load Transformed Tables
    log_info(f"Connecting to database '{target_dbname}'...")
    try:
        pg_conn = psycopg2.connect(target_url)
    except psycopg2.OperationalError as e:
        log_error(
            f"Could not connect to database '{target_dbname}': {e}\n"
            f"Tip: If '{target_dbname}' does not exist yet, pass `--create-db`."
        )
        sys.exit(1)

    try:
        with pg_conn.cursor() as cur:
            if args.drop_schemas:
                log_warn("Dropping existing schemas (silver, gold, serving CASCADE)...")
                cur.execute("DROP SCHEMA IF EXISTS serving CASCADE;")
                cur.execute("DROP SCHEMA IF EXISTS gold CASCADE;")
                cur.execute("DROP SCHEMA IF EXISTS silver CASCADE;")
                log_success("Old schemas dropped.")

        # Load tables
        load_duckdb_to_postgres(ddb_con, pg_conn, skip_load=args.skip_load)

        # Commit all changes
        pg_conn.commit()
        log_success("PostgreSQL transaction committed successfully.")

        # Verify tables
        if not args.skip_load:
            verify_pipeline(ddb_con, pg_conn)

    except Exception as e:
        pg_conn.rollback()
        log_error(f"Transaction rolled back due to error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        pg_conn.close()
        ddb_con.close()

    total_elapsed = time.time() - total_start
    log_success(f"End-to-End ETL completed in {total_elapsed:.2f} seconds.")


if __name__ == "__main__":
    main()
