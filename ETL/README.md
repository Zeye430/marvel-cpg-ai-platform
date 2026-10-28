# Marvel CPG AI Platform — Aiven PostgreSQL ETL & Loaders

This directory contains database initialization, transformation, and bulk ETL streaming scripts to load the Marvel CPG dataset into Aiven PostgreSQL.

## Scripts Overview

| Script | Architecture | Input Data | Target Schemas | Purpose |
|---|---|---|---|---|
| **[`etl_pipeline.py`](file:///Users/tsomorlig/Documents/marvel-cpg-ai-platform/ETL/etl_pipeline.py)** | **Approach A: Two-Engine Hybrid (DuckDB $\rightarrow$ PostgreSQL)** | `data/raw/*.csv` | `silver`, `gold`, `serving` | Full end-to-end ETL: Extracts raw data, runs Silver typing/reconciliations, generates Gold Marts (KPIs, estimation, commercial periods, visits, negotiation context), and creates Serving views in PostgreSQL. |
| **[`load_to_aiven.py`](file:///Users/tsomorlig/Documents/marvel-cpg-ai-platform/ETL/load_to_aiven.py)** | Single-Engine Raw Ingest | `new_marvel_data/*.csv` | `public` | Simple EL loader that loads raw CSVs directly into the public schema without transformation. |

---

## Approach A: End-to-End ETL Pipeline (`ETL/etl_pipeline.py`)

### Features
- **Smart Extraction & Harmonization**: Ingests the 8 raw CSVs from `data/raw/` and harmonizes schema attributes.
- **DuckDB Transformation Engine**: Computes typed Silver foundations, Gold daily state aggregations, Must-Stock List (MSL) compliance %, Out-of-Stock (OOS) rates, commercial period rollups, and rolling 28-day negotiation context.
- **High-Speed Streaming to PostgreSQL**: Pipes transformed tables from DuckDB directly into Aiven PostgreSQL using `COPY ... FROM STDIN`.
- **Governed Serving Views**: Creates 14+ standardized views (`serving.vw_*`) in PostgreSQL for dashboard and application consumption.
- **Automated Verification**: Automatically compares DuckDB and PostgreSQL row counts to ensure data integrity.

### Usage

```bash
# 1. Dry Run (Test transformations and database connection without loading):
uv run python ETL/etl_pipeline.py --dry-run

# 2. Run Full End-to-End ETL (Transforms in DuckDB and loads to Aiven):
uv run python ETL/etl_pipeline.py --drop-schemas

# 3. Create a Dedicated Database & Ingest:
uv run python ETL/etl_pipeline.py --create-db --dbname marvel_cpg --drop-schemas

# 4. Transform Only (Creates DuckDB tables and checks schema without loading):
uv run python ETL/etl_pipeline.py --skip-load
```

---

## Target Database Schema Architecture

```
Aiven PostgreSQL
├── silver/
│   ├── dim_stores, dim_products, dim_guardrails, dim_promotions
│   ├── fact_sell_in, fact_deliveries, fact_execution_audits, fact_sell_out
│   ├── active_assortment, replenishment_events, promotion_calendar
├── gold/
│   ├── mart_store_sku_daily (dense facts with observed vs estimated states)
│   ├── mart_store_day (daily store KPIs: OOS rate, MSL compliance)
│   ├── mart_commercial_period (weekly/biweekly/monthly flow rollups)
│   ├── mart_execution_visit (store audit visits and compliance rates)
│   ├── mart_negotiation_context (28-day rolling window commercial terms)
│   └── metric_definitions (governed metrics catalog)
└── serving/ (Views)
    ├── vw_stores, vw_products, vw_promotions
    ├── vw_store_sku_daily, vw_store_day, vw_commercial_period
    ├── vw_delivery_events, vw_sell_in_events, vw_execution_visits
    ├── vw_negotiation_context, vw_metric_definitions
    └── vw_estimated_store_sku_daily, vw_estimated_store_day
```

---

## Raw Database Loader (`ETL/load_to_aiven.py`)

To load the untransformed CSV files directly into the `public` schema:

```bash
# Dry run:
uv run python ETL/load_to_aiven.py --dry-run

# Recreate tables in public schema:
uv run python ETL/load_to_aiven.py --drop-tables
```

