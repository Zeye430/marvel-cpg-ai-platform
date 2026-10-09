# Sales & Inventory ETL Pipeline Architecture

## System Overview
This ETL pipeline converts raw, fragmented Point-of-Sale (POS) logs, inventory balances, and execution audits into clean, enriched data marts for downstream dashboards and predictive modeling. The architecture handles missing store-day reports (Missing Completely at Random / MCAR) using machine learning imputation and inventory propagation while maintaining audit traceability.

---

## Architecture Diagram

```
[ Raw CSV Data Sources ]
  │  ├── fact_sell_out.csv
  │  ├── fact_sell_in.csv
  │  ├── fact_execution_audits.csv
  │  ├── dim_stores.csv
  │  └── dim_products.csv
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. INGESTION & GRID GENERATION                              │
│    • Schema validation & primary key integrity              │
│    • Deduplication (Sum daily POS receipts)                 │
│    • Full Cartesian Grid (Store × SKU × Calendar Day)       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. DATA PREPROCESSING & CLEANING                            │
│    • Explicitize hidden gaps (NaN generation)               │
│    • Outlier filtering (Negative stock & sales spikes)      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. IMPUTATION & INVENTORY PROPAGATION                       │
│    • Inventory propagation: Forward-fill (ffill)            │
│    • Sales estimation: Causal LightGBM v2 model             │
│    • Lineage tracking: Audit flags (is_imputed_sales, etc.) │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. FEATURE ENGINEERING & METRIC CALCULATION                 │
│    • Calendar features (Day of week, Month, Holidays)       │
│    • Rolling statistics (7d, 14d, 28d moving averages)       │
│    • KPI calculation (MSL %, OOS Rate %, Gap %, Share %)    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. TARGET DATA MARTS / PARQUET STORAGE                      │
│    • sell_out_filled.parquet (For Analytics & Module 1)      │
│    • Model-ready feature stores                             │
└─────────────────────────────────────────────────────────────┘
```

---

## ETL Pipeline Execution Steps

| Step # | Pipeline Phase | Key Operations & Logic | Notebook Reference |
| :--- | :--- | :--- | :--- |
| **1** | **Ingestion & Full Grid Expansion** | Load raw datasets, validate primary keys, aggregate duplicate intraday receipts, and construct a complete Cartesian grid ($221 \text{ stores} \times \text{SKUs} \times 546 \text{ calendar days}$). Left join raw logs to expose missing store-days explicitly. | Cells 3–10 |
| **2** | **Exploratory Validation & Mechanism Check** | Run statistical tests ($\chi^2$ test, lag dependencies) to confirm missingness occurs uniformly at the whole store-day level (Missing Completely at Random / MCAR). | Cells 11–22 |
| **3** | **Inventory Dynamics & Stock Math** | Perform stock accounting ($I_t = I_{t-1} + \text{Deliveries} - \text{Sales}$) across missing gaps to validate physical stock movement and justify inventory forward-filling. | Cells 23–26 |
| **4** | **Imputation Modeling & Benchmarking** | Evaluate ML and baseline algorithms using a 10% masked cross-validation benchmark. Select a leakage-free, causal LightGBM model for sales estimation ($\text{MAE} = 1.61$). | Cells 27–38 |
| **5** | **Production Fill & Lineage Flagging** | Apply the LightGBM model for missing sales units and forward-fill (`ffill`) for stock levels. Append audit metadata flags (`is_imputed_sales`, `is_imputed_inventory`, `units_fill_method`). | Cells 39–42 |
| **6** | **OOS Label Engineering & Export** | Re-anchor downstream 7-day lookahead Out-of-Stock (`is_out_of_stock`) indicators using continuous stock balances and export final parquet data files. | Cells 43–45 |

---

## Core KPI Definitions & Technical Functions

All formulas adhere to standard definitions to maintain cross-team consistency.

### 1. Mandatory Stock List (MSL) Compliance %
* **Definition:** Percentage of stores where every mandatory ($is\_msl = \text{TRUE}$) SKU has $inventory\_level > 0$. A store fails if even one mandatory SKU is unavailable.
* **Source Tables:** `fact_inventory` / `fact_sell_out`, `dim_products`
* **Required Columns:** `store_id`, `sku_id`, `is_msl`, `inventory_level`
* **SQL Formulation:**
  ```sql
  WITH StoreCompliance AS (
      SELECT 
          store_id,
          SUM(CASE WHEN is_msl = TRUE AND (inventory_level <= 0 OR inventory_level IS NULL) THEN 1 ELSE 0 END) AS non_compliant_skus
      FROM fact_sell_out
      GROUP BY store_id
  )
  SELECT 
      COUNT(DISTINCT CASE WHEN non_compliant_skus = 0 THEN store_id END) * 100.0 / COUNT(DISTINCT store_id) AS msl_compliance_pct
  FROM StoreCompliance;
  ```

### 2. Out-Of-Stock (OOS) Rate %
* **Definition:** Percentage of SKU-store-day records where $is\_out\_of\_stock = \text{TRUE}$.
* **Source Tables:** `fact_inventory` / `fact_sell_out`
* **Required Columns:** `sku_id`, `store_id`, `date`, `is_out_of_stock`
* **SQL Formulation:**
  ```sql
  SELECT 
      SUM(CASE WHEN is_out_of_stock = TRUE THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS oos_rate_pct
  FROM fact_sell_out;
  ```

### 3. Sell-in vs. Sell-out Gap %
* **Definition:** Percentage difference between total units invoiced by the distributor (sell-in) and total units purchased by consumers (sell-out).
* **Source Tables:** `fact_sell_in`, `fact_sell_out`
* **Required Columns:** `fact_sell_in.units_sell_in`, `fact_sell_out.units_sold_daily`
* **SQL Formulation:**
  ```sql
  SELECT 
      (SUM(si.units_sell_in) - SUM(so.units_sold_daily)) * 100.0 / SUM(so.units_sold_daily) AS sell_in_sell_out_gap_pct
  FROM fact_sell_in si
  FULL OUTER JOIN fact_sell_out so ON si.store_id = so.store_id AND si.sku_id = so.sku_id AND si.date = so.date;
  ```

### 4. Shelf Share %
* **Definition:** Average shelf share percentage aggregated across category, channel, and region.
* **Source Tables:** `fact_execution_audits`
* **Required Columns:** `shelf_share_pct`, `category`, `channel`, `region`
* **SQL Formulation:**
  ```sql
  SELECT 
      category,
      channel,
      region,
      AVG(shelf_share_pct) AS avg_shelf_share_pct
  FROM fact_execution_audits
  GROUP BY category, channel, region;
  ```

### 5. Planogram Compliance %
* **Definition:** Percentage of audit records compliant with the visual merchandise planogram.
* **Source Tables:** `fact_execution_audits`
* **Required Columns:** `planogram_compliance`
* **SQL Formulation:**
  ```sql
  SELECT 
      SUM(CASE WHEN planogram_compliance = TRUE THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS planogram_compliance_pct
  FROM fact_execution_audits;
  ```