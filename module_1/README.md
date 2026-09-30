# Marvel CPG — Module 1: Commercial Execution Dashboard

An enterprise-grade commercial analytics dashboard built for **Regional Managers** and **Field Representatives**, powered by real Marvel CPG warehouse datasets (`dim_products.csv`, `dim_stores.csv`, `fact_execution_audits.csv`, `fact_sell_in.csv`, and `fact_sell_out.csv`).

---

## Requirements & Functions Compliance Checklist

Below is the verification checklist mapping all required KPIs and technical requirements from the project specification to their implementation in [`module_1/app.py`](app.py).

### 1. Required KPIs

| Status | KPI | Brief Definition | Implementation Details & Benchmark | Data Sources |
| :---: | :--- | :--- | :--- | :--- |
| **[x]** | **MSL Compliance %** | Percentage of stores with mandatory SKUs available on shelf. | Evaluated across all 15 MSL products. Computes `%` of store-days where every mandatory SKU is in stock. Target: **≥ 90%**. Features prior-period delta (`▲ 0.2 pts`) and initial slot-machine number shuffling. | `dim_products.csv`<br>`fact_sell_out.csv` |
| **[x]** | **OOS Rate %** | Out-of-stock rate by SKU, channel, and region. | Computes store-SKU-days out of stock (`6.0%`). Visualized dynamically across channels, regions, and individual SKUs. Target: **≤ 5%** (highlighted with dotted threshold guideline). | `dim_stores.csv`<br>`fact_sell_out.csv` |
| **[x]** | **Sell-in vs. Sell-out Gap** | Difference between what Marvel invoices to the distributor and what the end consumer buys. | Quantifies replenishment velocity vs. consumer offtake (`-3.1%` gap, `-42,046 units`). Visualized over time with a shaded channel stock buildup / depletion area. Target: **Within ±5%**. | `fact_sell_in.csv`<br>`fact_sell_out.csv` |
| **[x]** | **Shelf Share %** | Average shelf share by category and channel. | Calculates average category shelf share held by Marvel (`35.2%`). Visualized across Surface Cleaners, Dishwashing, and Laundry by retail channel. Target: **≥ 30%**. | `fact_execution_audits.csv`<br>`dim_products.csv` |
| **[x]** | **Planogram Compliance %** | Compliance with the display planogram. | Measures `%` of audited store-SKUs displayed strictly according to planogram (`83.0%`). Displayed as an executive KPI and integrated into the Store Priority Matrix for territory route optimization. Target: **≥ 80%**. | `fact_execution_audits.csv`<br>`dim_stores.csv` |

---

### 2. Technical Requirements

- [x] **Dynamic Filters**:
  - **Date Range**: Snaps to full calendar weeks with interactive slider spanning `2025-01-01` to `2026-06-30` (defaults to latest quarter: Q2 2026).
  - **Channel Filter**: Multi-select filtering across `Modern Trade`, `Traditional Trade`, and `Distributor`.
  - **Region Filter**: Multi-select filtering across `Northeast`, `Southeast`, `Midwest`, and `West`.
  - **Product SKU Filter**: Searchable selectize selector spanning all 23 real Marvel SKUs categorized across Surface Cleaners, Dishwashing, and Laundry.

- [x] **Role-Based Views (Field Rep vs. Regional Manager)**:
  - **Authentication & Role Selection Interface**: Dedicated Apple-style luxury login screen with segmented role controls and 1-click demo access.
  - **Regional Manager (Aggregated View)**: Executive portfolio oversight across all 221 stores, 4 regions, and 3 channels (Executive Profile: *Elena Rostova*).
  - **Field Representative (Assigned Stores Only)**: Territory-scoped route view restricting analytics exclusively to stores assigned to the active rep (Profiles: *Alex Mercer / Rep A*, *Beatrice Vance / Rep B*, *Carlos Delgado / Rep C*, *Danielle Wu / Rep D*, *Marcus Stone / Rep E*, *Sarah Jenkins / Rep F*).
  - **Dynamic Session Switcher**: Sticky executive navbar displaying active user profile, territory scope, and a 1-click **"Sign Out / Switch Role"** button.
  - **Clean Sidebar**: The old account radio section under `<h6> VIEW </h6>` was completely removed, giving full focus to commercial filters.

- [x] **Distinct Visualization Types (6 Implemented — Exceeds Minimum of 4)**:
  1. **Dual Flow Area Chart (Time Series Trend)**: Weekly Sell-in vs. Sell-out units with shaded channel stock accumulation area.
  2. **Multi-Series Line Trend**: Weekly channel-specific OOS rate trend with an annotated 5% target threshold dotted line.
  3. **Interactive Matrix Heatmap**: SKU-level OOS intensity matrix with a dynamic toggle between **By Channel** and **By Region** views (safeguarded with zero-fill against missing data).
  4. **Horizontal Ranking Bar Chart**: Top 10 high-risk SKUs by OOS rate, color-coded by product category.
  5. **Grouped Categorical Bar Chart**: Average Shelf Share % categorized by product line and retail channel.
  6. **Interactive Priority Data Grid**: Store execution priority table sorting highest OOS stores first, detailing store name, channel, region, tier, sell-in gap, planogram compliance, and OOS rate.

- [x] **Luxury Professional Brand Aesthetics**:
  - Light & silvery Apple Pro aesthetic (`#F5F5F7` canvas, pure `#FFFFFF` liquid glass cards, hairline borders).
  - High-contrast sidebar typography (`#1D1D1F`) explicitly styled for `#bslib_sidebar_1e20f2`.
  - Slot-machine number shuffling and cascading deceleration animations on load for all 5 KPI metrics.

---

## Docker Quick Start Guide



Ensure you have **Docker** and **Docker Compose** installed on your system:

```bash
docker --version
docker compose version
```

1. **Navigate to the `module_1` directory:**
   ```bash
   cd module_1
   ```

2. **Build and start the container:**
   ```bash
   docker compose up --build
   ```
   *(To run in the background as a daemon, add the `-d` flag: `docker compose up -d --build`)*

3. **Open the Dashboard in your browser:**
   ```
   http://localhost:8000
   ```

4. **Stop the container:**
   ```bash
   docker compose down
   ```

---
