# Marvel CPG - PostgreSQL ETL Pipeline

This directory contains the automated, repeatable ETL pipeline to load all raw Marvel CPG CSV files from `marvel_data/` into an online PostgreSQL database.

## Features

- **100% Repeatable:** Drops and recreates all tables (`DROP TABLE IF EXISTS ... CASCADE`) with primary keys, foreign key constraints, and analytical B-tree indexes.
- **High Performance:** Utilizes PostgreSQL's native streaming `COPY` protocol, loading all 1.65+ million records in under 30 seconds.
- **Secure Credentials:** Credentials and database URIs are loaded from `.env` or system environment variables, ensuring zero secrets are committed to the repository.

## Setup & Configuration

1. Copy `.env.example` in the project root to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Add your PostgreSQL connection URI to `.env`:
   ```env
   DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<dbname>?sslmode=require
   ```
   *(Alternatively, individual variables `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` can be specified).*

3. Ensure `psycopg2-binary` is installed:
   ```bash
   pip install psycopg2-binary
   ```

## Running the Pipeline

Execute the pipeline script:
```bash
python ETL/load_marvel_data.py
```

## Schema & Tables Loaded

| Order | Table Name | Source CSV | Grain | Estimated Rows |
|---|---|---|---|---|
| 1 | `dim_stores` | `marvel_data/dim_stores.csv` | Store Account | 221 |
| 2 | `dim_products` | `marvel_data/dim_products.csv` | SKU | 23 |
| 3 | `dim_guardrails` | `marvel_data/dim_guardrails.csv` | Channel-Category Policy | 9 |
| 4 | `fact_execution_audits` | `marvel_data/fact_execution_audits.csv` | Store-SKU Audit Date | 207,020 |
| 5 | `fact_sell_in` | `marvel_data/fact_sell_in.csv` | Store-SKU Invoice Week | 277,969 |
| 6 | `fact_sell_out` | `marvel_data/fact_sell_out.csv` | Store-SKU Day | 1,656,756 |
