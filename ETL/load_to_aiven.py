#!/usr/bin/env python3
"""
ETL / Database Setup Script for Marvel CPG AI Platform (Aiven PostgreSQL)
========================================================================

This script:
1. Reads database credentials from .env (DATABASE_URL or PG* variables).
2. Optionally creates a new database on the Aiven PostgreSQL cluster.
3. Creates the 8 relational tables matching the schema in `new_marvel_data/erd_code.mmd`
   with all primary keys, foreign keys, and column constraints.
4. Fast-streams data from all CSV files into Aiven PostgreSQL using `COPY ... FROM STDIN`
   in topological dependency order.
5. Builds optimized B-tree indexes for analytical and dashboard queries.
6. Performs automated validation, comparing CSV line counts against database row counts.

Usage:
------
    # Load directly into defaultdb (or database in DATABASE_URL):
    python ETL/load_to_aiven.py --drop-tables

    # Create a new dedicated database (e.g. marvel_cpg) and load tables:
    python ETL/load_to_aiven.py --create-db --dbname marvel_cpg

    # Check connection and CSV files without running changes:
    python ETL/load_to_aiven.py --dry-run
"""

import argparse
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Try loading python-dotenv
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Try loading psycopg2
try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    print(
        "ERROR: 'psycopg2' is required to run this script.\n"
        "Please install it using: pip install psycopg2-binary python-dotenv",
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


# Topological order for table creation and data loading:
# Dimensions must be created and loaded before Fact tables due to Foreign Key constraints.
TABLE_ORDER = [
    "dim_stores",
    "dim_products",
    "dim_guardrails",
    "dim_promotions",
    "fact_sell_in",
    "fact_deliveries",
    "fact_execution_audits",
    "fact_sell_out",
]

# SQL DDL Statements
DDL_STATEMENTS: Dict[str, str] = {
    "dim_stores": """
        CREATE TABLE IF NOT EXISTS dim_stores (
            store_id VARCHAR(50) PRIMARY KEY,
            store_name VARCHAR(255) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            region VARCHAR(100) NOT NULL,
            state VARCHAR(50) NOT NULL,
            segment_tier VARCHAR(50) NOT NULL,
            city VARCHAR(100) NOT NULL
        );
    """,
    "dim_products": """
        CREATE TABLE IF NOT EXISTS dim_products (
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
    "dim_guardrails": """
        CREATE TABLE IF NOT EXISTS dim_guardrails (
            channel VARCHAR(100) NOT NULL,
            category VARCHAR(100) NOT NULL,
            max_discount_pct NUMERIC(6, 2) NOT NULL,
            min_target_margin NUMERIC(6, 2) NOT NULL,
            required_exec_tradeoff TEXT NOT NULL,
            PRIMARY KEY (channel, category)
        );
    """,
    "dim_promotions": """
        CREATE TABLE IF NOT EXISTS dim_promotions (
            promo_id VARCHAR(50) PRIMARY KEY,
            promo_name VARCHAR(255) NOT NULL,
            promo_type VARCHAR(100) NOT NULL,
            category VARCHAR(100) NOT NULL,
            channel VARCHAR(100) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            discount_pct NUMERIC(6, 2) NOT NULL,
            min_volume_units INTEGER NOT NULL,
            FOREIGN KEY (channel, category) REFERENCES dim_guardrails (channel, category)
        );
    """,
    "fact_sell_in": """
        CREATE TABLE IF NOT EXISTS fact_sell_in (
            transaction_id VARCHAR(50) PRIMARY KEY,
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES dim_products (sku_id),
            units_sold INTEGER NOT NULL,
            gross_sales NUMERIC(12, 2) NOT NULL,
            discount_pct NUMERIC(6, 2) NOT NULL,
            net_sales NUMERIC(12, 2) NOT NULL,
            promo_id VARCHAR(50) REFERENCES dim_promotions (promo_id)
        );
    """,
    "fact_deliveries": """
        CREATE TABLE IF NOT EXISTS fact_deliveries (
            delivery_id VARCHAR(50) PRIMARY KEY,
            transaction_id VARCHAR(50) NOT NULL UNIQUE REFERENCES fact_sell_in (transaction_id),
            store_id VARCHAR(50) NOT NULL REFERENCES dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES dim_products (sku_id),
            invoice_date DATE NOT NULL,
            scheduled_delivery DATE NOT NULL,
            actual_delivery DATE NOT NULL,
            delay_days INTEGER NOT NULL,
            delivery_status VARCHAR(50) NOT NULL,
            units_ordered INTEGER NOT NULL,
            units_delivered INTEGER NOT NULL,
            fill_rate_pct NUMERIC(6, 2) NOT NULL
        );
    """,
    "fact_execution_audits": """
        CREATE TABLE IF NOT EXISTS fact_execution_audits (
            audit_id VARCHAR(50) PRIMARY KEY,
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES dim_products (sku_id),
            planogram_compliance BOOLEAN NOT NULL,
            shelf_share_pct NUMERIC(6, 2) NOT NULL,
            secondary_display BOOLEAN NOT NULL
        );
    """,
    "fact_sell_out": """
        CREATE TABLE IF NOT EXISTS fact_sell_out (
            date DATE NOT NULL,
            store_id VARCHAR(50) NOT NULL REFERENCES dim_stores (store_id),
            sku_id VARCHAR(50) NOT NULL REFERENCES dim_products (sku_id),
            units_sold_daily INTEGER NOT NULL,
            inventory_level INTEGER NOT NULL,
            is_out_of_stock BOOLEAN NOT NULL,
            consumer_price_actual NUMERIC(10, 2) NOT NULL,
            promo_id VARCHAR(50) REFERENCES dim_promotions (promo_id),
            promo_discount_pct NUMERIC(6, 2) NOT NULL,
            PRIMARY KEY (date, store_id, sku_id)
        );
    """,
}

# Performance Indexes for fast analytical dashboard queries
INDEX_STATEMENTS: List[str] = [
    # dim_promotions
    "CREATE INDEX IF NOT EXISTS idx_promotions_dates ON dim_promotions (start_date, end_date);",
    "CREATE INDEX IF NOT EXISTS idx_promotions_channel_cat ON dim_promotions (channel, category);",
    # fact_sell_in
    "CREATE INDEX IF NOT EXISTS idx_sell_in_date ON fact_sell_in (date);",
    "CREATE INDEX IF NOT EXISTS idx_sell_in_store ON fact_sell_in (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_sell_in_sku ON fact_sell_in (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_sell_in_promo ON fact_sell_in (promo_id);",
    # fact_deliveries
    "CREATE INDEX IF NOT EXISTS idx_deliveries_dates ON fact_deliveries (invoice_date, actual_delivery);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_store ON fact_deliveries (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_sku ON fact_deliveries (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_deliveries_status ON fact_deliveries (delivery_status);",
    # fact_execution_audits
    "CREATE INDEX IF NOT EXISTS idx_audits_date ON fact_execution_audits (date);",
    "CREATE INDEX IF NOT EXISTS idx_audits_store ON fact_execution_audits (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_audits_sku ON fact_execution_audits (sku_id);",
    # fact_sell_out (2.2M rows - critical indexes)
    "CREATE INDEX IF NOT EXISTS idx_sell_out_date ON fact_sell_out (date);",
    "CREATE INDEX IF NOT EXISTS idx_sell_out_store ON fact_sell_out (store_id);",
    "CREATE INDEX IF NOT EXISTS idx_sell_out_sku ON fact_sell_out (sku_id);",
    "CREATE INDEX IF NOT EXISTS idx_sell_out_promo ON fact_sell_out (promo_id);",
    "CREATE INDEX IF NOT EXISTS idx_sell_out_oos ON fact_sell_out (is_out_of_stock);",
]


def resolve_connection_info(env_path: Path) -> Tuple[str, str]:
    """
    Resolves connection URI and default database name from environment or .env file.
    Returns: (database_url, dbname)
    """
    if env_path.is_file():
        if load_dotenv:
            load_dotenv(dotenv_path=env_path)
            log_info(f"Loaded environment variables from: {env_path.resolve()}")
        else:
            log_warn(f"python-dotenv not installed. Parsing {env_path} manually.")
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

    database_url = os.getenv("DATABASE_URL")

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

    # Connect to maintenance db (defaultdb) with autocommit enabled
    maint_url = get_db_url_for_name(base_url, "defaultdb")
    conn = psycopg2.connect(maint_url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s;",
            (target_dbname,),
        )
        exists = cur.fetchone() is not None

        if not exists:
            log_info(f"Database '{target_dbname}' does not exist. Creating it now...")
            cur.execute(
                sql.SQL("CREATE DATABASE {};").format(sql.Identifier(target_dbname))
            )
            log_success(f"Database '{target_dbname}' successfully created!")
        else:
            log_info(f"Database '{target_dbname}' already exists.")
    finally:
        cur.close()
        conn.close()


def drop_existing_tables(cur) -> None:
    """Drops existing tables in reverse topological order."""
    log_warn("Dropping existing tables if they exist (CASCADE)...")
    for table_name in reversed(TABLE_ORDER):
        cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
        log_info(f"Dropped table: {table_name}")


def create_tables(cur) -> None:
    """Creates all 8 tables defined in DDL_STATEMENTS in topological order."""
    log_info("Creating relational tables...")
    for table_name in TABLE_ORDER:
        ddl = DDL_STATEMENTS[table_name]
        cur.execute(ddl)
        log_info(f"Table ready: {table_name}")
    log_success("All table definitions created successfully.")


def count_csv_rows(csv_path: Path) -> int:
    """Quickly counts rows in a CSV file (total lines minus header)."""
    lines = 0
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            lines += chunk.count(b"\n")
    return max(0, lines - 1)


def load_csv_into_table(cur, table_name: str, csv_path: Path) -> Tuple[int, float]:
    """
    Streams CSV directly into PostgreSQL table using COPY ... FROM STDIN with header.
    Treats empty strings as NULL to safely handle optional foreign keys (promo_id).
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing CSV file: {csv_path}")

    # Read header to determine columns
    with open(csv_path, "r", encoding="utf-8") as f:
        header_line = f.readline().strip()
    columns = [col.strip() for col in header_line.split(",") if col.strip()]

    log_info(
        f"Loading {Color.BOLD}{table_name}{Color.RESET} from {csv_path.name} "
        f"({len(columns)} columns)..."
    )

    copy_sql = f"""
        COPY {table_name} ({', '.join(columns)})
        FROM STDIN WITH (
            FORMAT csv,
            HEADER true,
            NULL ''
        );
    """

    start_time = time.time()
    with open(csv_path, "r", encoding="utf-8") as f:
        cur.copy_expert(copy_sql, f)
    elapsed = time.time() - start_time

    # Get loaded count
    cur.execute(f"SELECT count(*) FROM {table_name};")
    count = cur.fetchone()[0]

    log_success(
        f"Loaded {Color.BOLD}{count:,}{Color.RESET} rows into {table_name} "
        f"in {elapsed:.2f}s ({count / max(elapsed, 0.001):,.0f} rows/s)"
    )
    return count, elapsed


def build_indexes(cur) -> None:
    """Builds analytical indexes after data ingestion."""
    log_info("Creating analytical indexes for fast dashboard queries...")
    start_time = time.time()
    for idx_sql in INDEX_STATEMENTS:
        cur.execute(idx_sql)
    elapsed = time.time() - start_time
    log_success(f"Built {len(INDEX_STATEMENTS)} indexes in {elapsed:.2f}s.")


def verify_tables(cur, data_dir: Path) -> bool:
    """Verifies that database row counts match CSV line counts."""
    print()
    print(f"{Color.BOLD}{'=' * 75}{Color.RESET}")
    print(f"{Color.BOLD}{'VERIFICATION REPORT':^75}{Color.RESET}")
    print(f"{Color.BOLD}{'=' * 75}{Color.RESET}")
    print(
        f"{'Table Name':<25} | {'CSV Rows':<12} | {'DB Rows':<12} | {'Status':<10}"
    )
    print(f"{'-' * 25}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 10}")

    all_matched = True
    for table_name in TABLE_ORDER:
        csv_file = data_dir / f"{table_name}.csv"
        csv_count = count_csv_rows(csv_file) if csv_file.is_file() else -1

        cur.execute(f"SELECT count(*) FROM {table_name};")
        db_count = cur.fetchone()[0]

        matched = csv_count == db_count
        status = (
            f"{Color.GREEN}MATCH{Color.RESET}"
            if matched
            else f"{Color.RED}MISMATCH{Color.RESET}"
        )
        if not matched:
            all_matched = False

        print(
            f"{table_name:<25} | {csv_count:<12,} | {db_count:<12,} | {status:<10}"
        )

    print(f"{Color.BOLD}{'=' * 75}{Color.RESET}\n")
    return all_matched


def main():
    parser = argparse.ArgumentParser(
        description="Create PostgreSQL database, tables, and bulk load new_marvel_data CSVs to Aiven."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env configuration file (default: .env)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("new_marvel_data"),
        help="Path to directory containing the 8 CSV files (default: new_marvel_data)",
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
        help="Create the target database if it doesn't already exist on the PostgreSQL server.",
    )
    parser.add_argument(
        "--drop-tables",
        action="store_true",
        help="Drop existing tables (CASCADE) before creating fresh tables.",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Only create the schema and tables without loading CSV data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connection and verify CSV paths without making any database changes.",
    )

    args = parser.parse_args()

    # Step 1: Resolve credentials
    try:
        base_url, default_dbname = resolve_connection_info(args.env_file)
    except Exception as e:
        log_error(str(e))
        sys.exit(1)

    target_dbname = args.dbname if args.dbname else default_dbname
    target_url = get_db_url_for_name(base_url, target_dbname)

    # Hide password when displaying URL
    parsed_safe = urllib.parse.urlsplit(target_url)
    safe_netloc = (
        f"{parsed_safe.username}:****@{parsed_safe.hostname}:{parsed_safe.port}"
    )
    safe_display = urllib.parse.urlunsplit(parsed_safe._replace(netloc=safe_netloc))

    print(f"\n{Color.BOLD}{'=' * 75}{Color.RESET}")
    print(f"{Color.BOLD}Marvel CPG Data Loader — Aiven PostgreSQL{Color.RESET}")
    print(f"{Color.BOLD}{'=' * 75}{Color.RESET}")
    log_info(f"Target Database URL: {safe_display}")
    log_info(f"Target Database Name: {Color.BOLD}{target_dbname}{Color.RESET}")
    log_info(f"CSV Source Directory: {args.data_dir.resolve()}")

    # Check that CSV directory exists and contains all required files
    if not args.data_dir.is_dir():
        log_error(f"CSV directory not found: {args.data_dir}")
        sys.exit(1)

    missing_files = [
        f"{t}.csv"
        for t in TABLE_ORDER
        if not (args.data_dir / f"{t}.csv").is_file()
    ]
    if missing_files:
        log_error(f"Missing required CSV files in {args.data_dir}: {missing_files}")
        sys.exit(1)

    log_success("All 8 required CSV files detected.")

    if args.dry_run:
        log_warn("DRY RUN mode requested. Testing connection only...")
        try:
            test_conn = psycopg2.connect(target_url)
            with test_conn.cursor() as cur:
                cur.execute("SELECT version();")
                ver = cur.fetchone()[0]
                log_success(f"Connected to PostgreSQL: {ver}")
            test_conn.close()
            log_success("Dry run passed successfully. No database modifications were made.")
        except Exception as e:
            log_error(f"Dry run connection failed: {e}")
            sys.exit(1)
        return

    # Step 2: Create target database if requested
    if args.create_db:
        try:
            check_or_create_database(base_url, target_dbname)
        except Exception as e:
            log_error(f"Failed to check or create database '{target_dbname}': {e}")
            sys.exit(1)

    # Step 3: Connect to target database
    log_info(f"Connecting to database '{target_dbname}'...")
    try:
        conn = psycopg2.connect(target_url)
    except psycopg2.OperationalError as e:
        log_error(
            f"Could not connect to database '{target_dbname}': {e}\n"
            f"Tip: If '{target_dbname}' does not exist yet, pass `--create-db`."
        )
        sys.exit(1)

    total_start = time.time()
    try:
        with conn.cursor() as cur:
            # Check for existing tables if --drop-tables was not passed
            cur.execute(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = ANY(%s);
                """,
                (TABLE_ORDER,),
            )
            existing_tables = [r[0] for r in cur.fetchall()]

            if existing_tables and not args.drop_tables:
                log_warn(
                    f"Found {len(existing_tables)} existing tables in '{target_dbname}': {existing_tables}"
                )
                log_warn(
                    "To drop and recreate tables with the clean schema, please rerun with:\n"
                    f"    python {sys.argv[0]} --drop-tables"
                )
                sys.exit(1)

            if args.drop_tables:
                drop_existing_tables(cur)

            # Step 4: Create tables
            create_tables(cur)

            # Step 5: Bulk Load CSVs
            if not args.skip_load:
                log_info(f"Streaming data into {len(TABLE_ORDER)} tables using PostgreSQL COPY...")
                for table_name in TABLE_ORDER:
                    csv_path = args.data_dir / f"{table_name}.csv"
                    load_csv_into_table(cur, table_name, csv_path)

                # Step 6: Create Indexes
                build_indexes(cur)

            # Commit all changes
            conn.commit()
            log_success("Transaction committed successfully.")

            # Step 7: Run verification
            if not args.skip_load:
                verify_tables(cur, args.data_dir)

    except Exception as e:
        conn.rollback()
        log_error(f"Transaction rolled back due to error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

    total_elapsed = time.time() - total_start
    log_success(f"ETL completed in {total_elapsed:.2f} seconds.")


if __name__ == "__main__":
    main()
