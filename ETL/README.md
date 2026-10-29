# Marvel CPG AI Platform — Aiven PostgreSQL ETL & Loaders

This directory contains database initialization, transformation, and bulk ETL streaming scripts to load the Marvel CPG dataset into Aiven PostgreSQL.

## Scripts Overview

| Script | Architecture | Input Data | Target Schemas | Purpose |
|---|---|---|---|---|
| **[`v3_etl_pipeline.py`](file:///Users/tsomorlig/Documents/marvel-cpg-ai-platform/ETL/v3_etl_pipeline.py)** | **Governed V3 Medallion (DuckDB $\rightarrow$ Aiven PostgreSQL)** | `data/raw/*.csv` | `silver`, `gold`, `serving`, `public` | Full governed V3 pipeline: dense Cartesian calendar (`store_sku_calendar`), 1-4 day gap estimation, tri-state data status (Observed/Estimated/Unknown), dual KPI views, accurate reporting coverage (~91.86%), MSL compliance bounds, and high-speed streaming to Aiven PostgreSQL. |
| **[`etl_pipeline.py`](file:///Users/tsomorlig/Documents/marvel-cpg-ai-platform/ETL/etl_pipeline.py)** | Approach A: Two-Engine Hybrid (DuckDB $\rightarrow$ PostgreSQL) | `data/raw/*.csv` | `silver`, `gold`, `serving` | Standalone ETL: Extracts raw data and loads observed facts/marts into PostgreSQL. |
| **[`load_to_aiven.py`](file:///Users/tsomorlig/Documents/marvel-cpg-ai-platform/ETL/load_to_aiven.py)** | Single-Engine Raw Ingest | `new_marvel_data/*.csv` | `public` | Simple EL loader that loads raw CSVs directly into the public schema without transformation. |

---

## Governed V3 ETL Pipeline (`ETL/v3_etl_pipeline.py`)

### Features
- **Strict V3 Compliance**: Implements the contracts from `data_platform/` and `docs/solution-specifications/kpi-and-missing-store-day-handling.md`.
- **Complete Cartesian Calendar Grid**: Generates `silver.store_sku_calendar` over `calendar date x store x active SKU`.
- **Short-Gap State Estimation**: Reconstructs 1-4 day gaps via rolling hierarchical sales baselines (weekday, recent, group medians) and physical inventory recurrence with delivery receipt reconciliation.
- **Tri-State Visibility**: Explicit `Observed`, `Estimated`, and `Unknown` tags on every row.
- **Accurate Reporting Coverage**: Correctly accounts for non-reporting store-days (the 18 absent stores daily, ~91.86% coverage, rather than a false 100%).
- **Strict MSL Compliance Bounds**: Evaluates eligible, assessable, compliant, noncompliant, unknown store counts and calculates lower/upper bounds.
- **Dual Metric Views**: Populates `reported_only` and `operational_estimate` views.
- **High-Speed Streaming to PostgreSQL**: Bulk streams transformed DuckDB tables to Aiven PostgreSQL using `COPY ... FROM STDIN`.
- **Automated Verification**: Validates row counts and KPI component parity between DuckDB and PostgreSQL.

### Usage

```bash
# 1. Dry Run (Tests transformations and PostgreSQL connection without streaming):
uv run python ETL/v3_etl_pipeline.py --dry-run

# 2. Run Full Governed V3 ETL (Transforms in DuckDB and loads to Aiven):
uv run python ETL/v3_etl_pipeline.py --drop-schemas

# 3. Views Only (Refreshes serving and public views and runs verification without re-streaming tables):
uv run python ETL/v3_etl_pipeline.py --views-only

# 4. Transform Only (Inspects DuckDB tables without writing to PostgreSQL):
uv run python ETL/v3_etl_pipeline.py --skip-load
```

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

