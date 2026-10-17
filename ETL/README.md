# Marvel CPG AI Platform — Aiven PostgreSQL ETL & Loader

This directory contains the database initialization and bulk ETL streaming script to load the transformed CPG dataset (`new_marvel_data`) into Aiven PostgreSQL.

## Features

- **Automated Environment Resolution**: Loads connection credentials directly from `.env` (`DATABASE_URL` or individual `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `PGSSLMODE`).
- **Database Management**: Supports automated creation of dedicated databases (`--create-db --dbname <name>`) or loading directly into existing databases (`defaultdb`).
- **Schema & Constraints**: Complete DDL aligning with [`new_marvel_data/erd_code.mmd`](../new_marvel_data/erd_code.mmd) including Primary Keys, composite keys, and Foreign Key relationships.
- **High-Speed Streaming**: Ingests millions of rows using PostgreSQL's native streaming `COPY ... FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')`.
- **Performance Indexes**: Automatically creates analytical B-Tree indexes on dates, store IDs, SKU IDs, promotion IDs, and status flags to ensure sub-second dashboard performance.
- **Automated Verification**: Automatically validates that table row counts in PostgreSQL match CSV file line counts.

---

## Prerequisites

Ensure `psycopg2` and `python-dotenv` are installed:

```bash
pip install psycopg2-binary python-dotenv
```

Ensure your `.env` contains the Aiven connection string:
```bash
DATABASE_URL=postgres://<user>:<password>@<host>:<port>/<dbname>?sslmode=require
```

---

## Usage

### 1. Dry Run (Test Connection & Validate Files)
```bash
python ETL/load_to_aiven.py --dry-run
```

### 2. Create a Dedicated Database & Ingest Data
Creates the database `marvel_cpg` if it does not exist, builds all tables, and loads all 8 CSVs:
```bash
python ETL/load_to_aiven.py --create-db --dbname marvel_cpg
```

### 3. Load into Default Database (Recreate Tables)
To overwrite or update existing tables in `defaultdb`:
```bash
python ETL/load_to_aiven.py --drop-tables
```

### 4. Create Tables Only (Without Loading CSVs)
```bash
python ETL/load_to_aiven.py --skip-load
```

---

## Schema Overview

| Table | Type | Primary Key | Foreign Keys / References |
|---|---|---|---|
| `dim_stores` | Dimension | `store_id` | - |
| `dim_products` | Dimension | `sku_id` | - |
| `dim_guardrails` | Dimension | `(channel, category)` | - |
| `dim_promotions` | Dimension | `promo_id` | `(channel, category) -> dim_guardrails` |
| `fact_sell_in` | Fact | `transaction_id` | `store_id`, `sku_id`, `promo_id` |
| `fact_deliveries` | Fact | `delivery_id` | `transaction_id (1:1)`, `store_id`, `sku_id` |
| `fact_execution_audits` | Fact | `audit_id` | `store_id`, `sku_id` |
| `fact_sell_out` | Fact | `(date, store_id, sku_id)` | `store_id`, `sku_id`, `promo_id` |
