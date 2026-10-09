#!/usr/bin/env python3
"""Marvel CPG - Repeatable PostgreSQL ETL Data Pipeline.

Loads all *.csv datasets from marvel_data/ directory into an online PostgreSQL database.
Ensures 100% repeatability by dropping and recreating tables from scratch with
governed data types, primary keys, foreign keys, and analytical indexes.

Credentials:
- Reads securely from project root .env or environment variables (DATABASE_URL / PG*).
- Never hardcodes or commits credentials to git.
"""

import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Set up clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("marvel_etl")

# Resolve repository and data directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "marvel_data"
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(filepath: Path) -> None:
    """Load key-value pairs from .env into os.environ without third-party dependencies."""
    if not filepath.exists():
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val


# Load .env if present
load_env_file(ENV_FILE)


def get_db_connection_params() -> Tuple[Optional[str], Dict[str, str]]:
    """Retrieve connection string or parameters from environment variables."""
    db_url = os.environ.get("DATABASE_URL")
    params = {
        "host": os.environ.get("PGHOST", ""),
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ.get("PGDATABASE", ""),
        "user": os.environ.get("PGUSER", ""),
        "password": os.environ.get("PGPASSWORD", ""),
        "sslmode": os.environ.get("PGSSLMODE", "require"),
    }
    return db_url, params


# Database Schema DDL: Recreates all tables cleanly
DDL_SCHEMA = """
-- Drop existing tables in reverse dependency order
DROP TABLE IF EXISTS fact_execution_audits CASCADE;
DROP TABLE IF EXISTS fact_sell_in CASCADE;
DROP TABLE IF EXISTS fact_sell_out CASCADE;
DROP TABLE IF EXISTS dim_guardrails CASCADE;
DROP TABLE IF EXISTS dim_products CASCADE;
DROP TABLE IF EXISTS dim_stores CASCADE;

-- 1. Dimension Tables
CREATE TABLE dim_stores (
    store_id VARCHAR(32) PRIMARY KEY,
    store_name VARCHAR(255) NOT NULL,
    channel VARCHAR(64) NOT NULL,
    region VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL,
    segment_tier VARCHAR(32) NOT NULL,
    city VARCHAR(128) NOT NULL
);

CREATE TABLE dim_products (
    sku_id VARCHAR(32) PRIMARY KEY,
    product_name VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    brand VARCHAR(64) NOT NULL,
    is_msl BOOLEAN NOT NULL,
    unit_cost NUMERIC(10, 2) NOT NULL,
    list_price NUMERIC(10, 2) NOT NULL
);

CREATE TABLE dim_guardrails (
    channel VARCHAR(64) NOT NULL,
    category VARCHAR(64) NOT NULL,
    max_discount_pct NUMERIC(6, 2) NOT NULL,
    min_target_margin NUMERIC(6, 2) NOT NULL,
    required_exec_tradeoff TEXT NOT NULL,
    PRIMARY KEY (channel, category)
);

-- 2. Fact Tables
CREATE TABLE fact_execution_audits (
    audit_id VARCHAR(32) PRIMARY KEY,
    date DATE NOT NULL,
    store_id VARCHAR(32) NOT NULL REFERENCES dim_stores(store_id),
    sku_id VARCHAR(32) NOT NULL REFERENCES dim_products(sku_id),
    planogram_compliance BOOLEAN NOT NULL,
    shelf_share_pct NUMERIC(6, 2) NOT NULL,
    secondary_display BOOLEAN NOT NULL
);

CREATE TABLE fact_sell_in (
    transaction_id VARCHAR(32) PRIMARY KEY,
    date DATE NOT NULL,
    store_id VARCHAR(32) NOT NULL REFERENCES dim_stores(store_id),
    sku_id VARCHAR(32) NOT NULL REFERENCES dim_products(sku_id),
    units_sold INTEGER NOT NULL,
    net_sales NUMERIC(12, 2) NOT NULL
);

CREATE TABLE fact_sell_out (
    date DATE NOT NULL,
    store_id VARCHAR(32) NOT NULL REFERENCES dim_stores(store_id),
    sku_id VARCHAR(32) NOT NULL REFERENCES dim_products(sku_id),
    units_sold_daily INTEGER NOT NULL,
    inventory_level INTEGER NOT NULL,
    is_out_of_stock BOOLEAN NOT NULL,
    PRIMARY KEY (date, store_id, sku_id)
);

-- 3. Analytical Performance Indexes
CREATE INDEX idx_sell_out_store_sku ON fact_sell_out(store_id, sku_id);
CREATE INDEX idx_sell_out_date ON fact_sell_out(date);
CREATE INDEX idx_sell_in_store_sku ON fact_sell_in(store_id, sku_id);
CREATE INDEX idx_sell_in_date ON fact_sell_in(date);
CREATE INDEX idx_audits_store_sku ON fact_execution_audits(store_id, sku_id);
CREATE INDEX idx_audits_date ON fact_execution_audits(date);
"""

# Ordered list of tables and their corresponding CSV file
DATASETS = [
    ("dim_stores", "dim_stores.csv"),
    ("dim_products", "dim_products.csv"),
    ("dim_guardrails", "dim_guardrails.csv"),
    ("fact_execution_audits", "fact_execution_audits.csv"),
    ("fact_sell_in", "fact_sell_in.csv"),
    ("fact_sell_out", "fact_sell_out.csv"),
]


def count_csv_rows(filepath: Path) -> int:
    """Count number of data lines in CSV file (excluding header)."""
    with open(filepath, "r", encoding="utf-8") as f:
        count = sum(1 for _ in f) - 1
    return max(0, count)


def connect_postgres():
    """Establish a PostgreSQL connection using psycopg2 or psycopg3."""
    db_url, params = get_db_connection_params()

    if not db_url and not (params["host"] and params["dbname"] and params["user"]):
        raise ValueError(
            "Missing database credentials! Please configure DATABASE_URL or PG* variables "
            "in your project root .env file.\n"
            "Example: DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require"
        )

    # Try psycopg2 first
    try:
        import psycopg2
        if db_url:
            return psycopg2.connect(db_url)
        return psycopg2.connect(**{k: v for k, v in params.items() if v})
    except ImportError:
        pass

    # Try psycopg 3 next
    try:
        import psycopg
        if db_url:
            return psycopg.connect(db_url)
        return psycopg.connect(**{k: v for k, v in params.items() if v})
    except ImportError:
        pass

    raise ImportError(
        "Neither 'psycopg2' nor 'psycopg' was found in your Python environment.\n"
        "Please install psycopg2-binary: pip install psycopg2-binary"
    )


def load_table_copy(conn, table_name: str, csv_path: Path) -> int:
    """Stream bulk-load a CSV into PostgreSQL using COPY command."""
    cursor = conn.cursor()
    t0 = time.time()
    logger.info(f"Loading {table_name} from {csv_path.name}...")

    with open(csv_path, "r", encoding="utf-8") as f:
        # Check driver type
        if hasattr(cursor, "copy_expert"):
            # psycopg2 style
            sql = f"COPY {table_name} FROM STDIN WITH (FORMAT csv, HEADER true)"
            cursor.copy_expert(sql, f)
        elif hasattr(cursor, "copy"):
            # psycopg 3 style
            with cursor.copy(f"COPY {table_name} FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                while data := f.read(65536):
                    copy.write(data)
        else:
            raise RuntimeError("Database driver does not support COPY command.")

    conn.commit()

    # Query row count
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    duration = time.time() - t0
    logger.info(f"✓ Loaded {count:,} rows into {table_name} in {duration:.2f}s ({count / max(0.01, duration):,.0f} rows/s)")
    return count


def run_pipeline():
    """Main ETL orchestration function."""
    logger.info("==================================================")
    logger.info("Starting Marvel CPG PostgreSQL ETL Pipeline")
    logger.info("==================================================")

    if not DATA_DIR.exists():
        logger.error(f"Source data directory not found: {DATA_DIR}")
        sys.exit(1)

    t_start = time.time()

    # 1. Connect to PostgreSQL
    logger.info("Connecting to PostgreSQL database online...")
    try:
        conn = connect_postgres()
        logger.info("✓ Connected successfully!")
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        sys.exit(1)

    # 2. Recreate schema from scratch
    logger.info("Recreating database tables from scratch (repeatable pipeline)...")
    try:
        with conn.cursor() as cur:
            cur.execute(DDL_SCHEMA)
        conn.commit()
        logger.info("✓ Dropped and recreated tables: dim_stores, dim_products, dim_guardrails, fact_execution_audits, fact_sell_in, fact_sell_out.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create schema: {e}")
        sys.exit(1)

    # 3. Load datasets in order
    summary = []
    has_error = False

    for table_name, filename in DATASETS:
        csv_path = DATA_DIR / filename
        if not csv_path.exists():
            logger.warning(f"File not found: {csv_path}, skipping {table_name}")
            continue

        expected_rows = count_csv_rows(csv_path)
        try:
            actual_rows = load_table_copy(conn, table_name, csv_path)
            assert actual_rows == expected_rows, f"Row count mismatch! Expected {expected_rows:,}, got {actual_rows:,}"
            summary.append((table_name, actual_rows, expected_rows, "SUCCESS"))
        except Exception as e:
            conn.rollback()
            logger.error(f"Error loading {table_name}: {e}")
            summary.append((table_name, 0, expected_rows, f"FAILED: {e}"))
            has_error = True
            break

    conn.close()
    total_time = time.time() - t_start

    # 4. Final summary report
    logger.info("==================================================")
    logger.info("ETL Pipeline Execution Summary")
    logger.info("==================================================")
    for tbl, actual, expected, status in summary:
        logger.info(f"{tbl:<25}: {actual:>10,} / {expected:>10,} rows [{status}]")
    logger.info(f"Total pipeline execution time: {total_time:.2f} seconds")

    if has_error:
        logger.error("Pipeline finished with errors.")
        sys.exit(1)
    else:
        logger.info("All tables successfully created and loaded into PostgreSQL!")


if __name__ == "__main__":
    run_pipeline()
