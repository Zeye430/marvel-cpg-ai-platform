# Historical Data Dictionary and Analytical Data Model (V1)

> Superseded for active development as of 2026-10-18. This document describes the earlier six-table dataset. Use the [V3 shared Medallion architecture and table catalog](v3-medallion-architecture.md), the [V3 source contract](../../data_platform/contracts/v3/sources.json), and the [V3 KPI specification](../solution-specifications/kpi-and-missing-store-day-handling.md) for current implementation. The counts, fields, invoice timing, receipt inference, and label policy below are historical and must not drive V3 code.

## Document Purpose

This document defines the initial data contract for the Marvel CPG platform. It records the grain, keys, business meaning, time semantics, relationships, and proposed analytical outputs for the six supplied datasets. It also identifies derived tables required by the four product modules.

The main design rule is that each business concept should be calculated once in a governed analytical layer and reused by the dashboard, model, simulator, and conversational agent.

The detailed KPI formulas, missing-store-day rules, estimation policies, and OOS label policy are defined in [`../solution-specifications/kpi-and-missing-store-day-handling.md`](../solution-specifications/kpi-and-missing-store-day-handling.md). This data model must remain consistent with that specification.

## System Time Context

- Source-data period: January 2025 through June 30, 2026, with the first Sell-in invoice week beginning December 30, 2024.
- Application simulation date: June 30, 2026.
- Sell-out and inventory frequency: daily when reported.
- Sell-in frequency: weekly invoices issued on Monday.
- Execution audit frequency: intermittent.

## Source Table Overview

| Table | Rows | Proposed grain | Candidate key | Primary modules |
| --- | ---: | --- | --- | --- |
| `dim_stores` | 221 | One row per account/store | `store_id` | 1, 2, 3, 4 |
| `dim_products` | 23 | One row per SKU | `sku_id` | 1, 2, 3, 4 |
| `dim_guardrails` | 9 | One rule per channel-category | `channel`, `category` | 3, 4 |
| `fact_sell_in` | 277,969 | One invoiced store-SKU-week transaction | `transaction_id` | 1, 2, 3, 4 |
| `fact_sell_out` | 1,656,756 | One observed store-SKU-day | `date`, `store_id`, `sku_id` | 1, 2, 4 |
| `fact_execution_audits` | 207,020 | One audited store-SKU-date record | `audit_id` | 1, 2, 3, 4 |

The supplied tables contain no null cells and no duplicate candidate keys. Missingness primarily appears as absent store-days rather than null values inside existing rows.

## Source Data Dictionary

### `dim_stores`

**Grain:** one Marvel customer account. Modern Trade and Traditional Trade rows represent retail stores. Distributor rows represent wholesale accounts, not physical consumer retail stores.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `store_id` | string | Stable account identifier | Required join key; use this instead of store name. |
| `store_name` | string | Display name of the account | Not unique; 221 IDs and 210 unique names. |
| `channel` | category | Modern Trade, Traditional Trade, or Distributor | Controls KPI interpretation and Module 3 guardrails. |
| `region` | category | Commercial region | Used for manager aggregation. |
| `state` | category | State code | Can support synthetic territory construction. |
| `segment_tier` | ordered category | Fixed commercial-potential tier | Tier 1 is highest priority, followed by Tier 2 and Tier 3. |
| `city` | string | City of the account | Not sufficient for precise mapping or routing. |

Tier interpretation:

- Tier 1: high-volume, large-footprint accounts; full MSL expectation and weekly visits.
- Tier 2: medium-volume accounts; selective MSL and usually biweekly visits.
- Tier 3: small accounts; partial assortment and monthly or reactive visits.

**Open issue:** the official KPI definition uses every global `is_msl = TRUE` SKU, while the tier description implies different MSL assortments. No tier-specific MSL mapping is supplied.

### `dim_products`

**Grain:** one sellable SKU.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `sku_id` | string | Stable SKU identifier | Required join key. |
| `product_name` | string | Product and pack description | Pack size and unit may be parsed into derived fields. |
| `category` | category | Surface Cleaners, Dishwashing, or Laundry | Used in KPIs and guardrail joins. |
| `brand` | category | Marvel brand or sub-brand | Useful for product analysis and RAG. |
| `is_msl` | boolean | Global Must-Stock List flag | The client-defined KPI currently applies this flag to stores in filter. |
| `unit_cost` | decimal | Manufacturing/distribution cost per selling unit | Used in gross-margin calculation. |
| `list_price` | decimal | Marvel trade list price before discount | Not the consumer shelf price. |

Proposed derived fields:

- `pack_size_value`
- `pack_size_unit`
- `product_form`
- `base_product_family`

Derived pack fields must retain the original product name and document parsing exceptions.

### `dim_guardrails`

**Grain:** one commercial rule per channel-category combination.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `channel` | category | Applicable customer channel | Join with store channel. |
| `category` | category | Applicable product category | Join with SKU category. |
| `max_discount_pct` | decimal | Maximum discount allowed without additional approval | Approval behavior above this value remains open. |
| `min_target_margin` | decimal | Minimum gross margin percentage on net trade price | Whether it can be overridden remains open. |
| `required_exec_tradeoff` | text | Condition associated with additional discount | Must be parsed into structured application rules. |

**Known conflict:** Distributor rules currently include retail execution commitments such as planogram, MSL, shelf share, secondary display, and endcap. The client states that Distributor negotiations should use volume commitments instead. Do not alter the raw table; implement any approved transformed rule in a separate governed table.

### `fact_sell_in`

**Grain:** one SKU, one account, and one weekly invoice transaction. Orders are batch-closed Sunday night and invoices are issued Monday morning. The Monday date is both invoice date and official reporting-week start.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `transaction_id` | string | Unique invoice transaction identifier | Candidate primary key. |
| `date` | date | Monday invoice date and week start | Does not confirm physical delivery date. |
| `store_id` | string | Invoiced customer account | Foreign key to `dim_stores`. |
| `sku_id` | string | Invoiced SKU | Foreign key to `dim_products`. |
| `units_sold` | integer | Units invoiced | An absent store-SKU-week row means zero Sell-in, not missing data. |
| `net_sales` | decimal | Net invoiced trade sales | Effective unit price can be derived as `net_sales / units_sold`. |

Proposed derived fields:

```text
week_start = date
invoice_unit_price = net_sales / units_sold
effective_discount_pct = 100 * (1 - invoice_unit_price / list_price)
```

The effective discount calculation should be validated for range and documented as an invoice-price-derived measure. Sell-in units must not be treated as same-day physical receipts without an approved delivery-lag rule.

### `fact_sell_out`

**Grain:** one observed store-SKU-day. When a store reports, its active assortment appears as a set of SKU rows. Absent store-days mean not reported, not zero sales.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `date` | date | Consumer sales and end-of-day inventory date | Daily calendar date. |
| `store_id` | string | Reporting account/store | Foreign key to `dim_stores`. |
| `sku_id` | string | Reported SKU | Foreign key to `dim_products`. |
| `units_sold_daily` | integer | Observed consumer units sold that day | May understate demand when the SKU stocks out. |
| `inventory_level` | integer | End-of-day physical inventory after daily sales | Zero is consistent with OOS. |
| `is_out_of_stock` | boolean | End-of-day OOS status | Positive daily sales and OOS can coexist if the last unit was sold during the day. |

**Coverage:** approximately 84,084 of 120,666 possible store-days are observed, or 69.7%. Each date contains approximately 154 reporting accounts out of 221.

### `fact_execution_audits`

**Grain:** one audited store-SKU-date record. Audits are intermittent observations, not a daily fact table.

| Field | Type | Business meaning | Notes |
| --- | --- | --- | --- |
| `audit_id` | string | Unique audit record identifier | Candidate primary key. |
| `date` | date | Physical audit date | Use only on or before an analysis date. |
| `store_id` | string | Audited account/store | Foreign key to `dim_stores`. |
| `sku_id` | string | Audited SKU | Foreign key to `dim_products`. |
| `planogram_compliance` | boolean | Whether the audited SKU follows the planogram | Distinct from MSL availability. |
| `shelf_share_pct` | decimal | Marvel share of category shelf space at audit | Treat SKU-level interpretation cautiously if values represent category-level shelf share. |
| `secondary_display` | boolean | Whether an off-shelf display is present | Includes endcaps, islands, and checkout displays. |

**As-of rule:** for a date without a new audit, select the latest audit on or before that date and include `days_since_last_audit`. Never use a future audit. The maximum valid carry-forward period remains a client or team decision.

## Entity Relationships

```text
dim_stores 1 ---- * fact_sell_in * ---- 1 dim_products
dim_stores 1 ---- * fact_sell_out * --- 1 dim_products
dim_stores 1 ---- * fact_execution_audits * ---- 1 dim_products

dim_stores.channel + dim_products.category
                    |
                    v
             dim_guardrails
```

## Join Contract

### Safe Dimension Enrichment

The following joins are many-to-one and should preserve fact-table row counts:

- Fact table to `dim_stores` on `store_id`.
- Fact table to `dim_products` on `sku_id`.
- Store and product attributes to `dim_guardrails` on `channel` and `category`.

Every pipeline should assert that the row count is unchanged after these joins.

### Time-Grain Alignment

Do not directly join daily Sell-out, weekly Sell-in, and intermittent audits at their original grains. First align them to a declared analytical grain.

- Sell-in versus Sell-out: aggregate both to channel-appropriate biweekly or monthly periods.
- OOS features: maintain a daily store-SKU grain and derive lagged weekly-invoice features.
- Audit features: perform a backward as-of join from daily store-SKU rows to the latest eligible audit.
- Negotiation simulator: join the selected store channel and SKU category to exactly one guardrail row.

## Proposed Derived Dimensions

### `dim_reps`

| Field | Meaning |
| --- | --- |
| `rep_id` | Synthetic representative identifier |
| `rep_name` | Fictional display name |
| `territory_id` | Assigned territory |
| `manager_id` | Optional regional-manager relationship |

### `dim_territories`

| Field | Meaning |
| --- | --- |
| `territory_id` | Stable synthetic territory identifier |
| `territory_name` | Display name |
| `region` | Commercial region |
| `state_or_city_cluster` | Geographic construction rule |

### `bridge_rep_store`

| Field | Meaning |
| --- | --- |
| `rep_id` | Assigned representative |
| `store_id` | Assigned retail account |
| `effective_start_date` | Assignment start date |
| `effective_end_date` | Assignment end date if applicable |

The initial prototype may use fixed assignments with approximately 8-15 retail stores per representative. Distributor accounts should be assigned or displayed separately.

## Proposed Analytical Tables

### `mart_store_sku_daily`

**Grain:** one active store-SKU-calendar-day.

Purpose:

- Preserve complete calendar coverage.
- Separate observed values from estimates.
- Support daily features and current-state views.

Core fields:

```text
date, store_id, sku_id
sales_observed, sales_estimated, sales_is_observed
last_sales_report_date, sales_estimation_method, sales_data_status
inventory_observed, inventory_estimated, inventory_is_observed
last_inventory_report_date, days_since_inventory_report
inventory_estimation_method, inventory_data_status
oos_observed, oos_estimated, oos_data_status
recent_sell_in_units, days_since_invoice
latest_planogram_compliance, latest_shelf_share_pct
latest_secondary_display, days_since_last_audit
channel, region, tier, category, is_msl
```

User-facing data status values are limited to:

```text
Observed
Estimated
Unknown
```

`days_since_inventory_report` measures the age of the source observation, not the physical age, shelf life, or turnover age of the inventory. Do not add a subjective High/Medium/Low estimation-confidence field to the MVP.

### `mart_store_latest_status`

**Grain:** one store-SKU as of the selected application date.

Purpose:

- Store 360.
- Current MSL and OOS views.
- Visit-ranking inputs.

The default product view uses observed values where available and labeled estimates where the selected date is missing. The same table must support a Reported Data Only view that excludes estimated values.

### `mart_commercial_kpis`

**Grain:** metric, date period, and supported filter dimensions.

Purpose:

- Module 1 dashboard.
- Governed NLQ answers.
- Consistency across user roles.

Every record should identify whether the metric is reported-only or estimated-adjusted and include the relevant coverage rate. The default application view is `operational_estimate`; `reported_only` is the verification view.

Core fields:

```text
metric_name
metric_value
metric_view
reporting_coverage_pct
observation_count
estimated_count
period_start
period_end
filter_dimensions
```

Allowed `metric_view` values:

```text
operational_estimate
reported_only
```

### `mart_sell_in_sell_out_period`

**Grain:** period, store or requested aggregation, and SKU.

Purpose:

- Align weekly invoices and daily consumer sales.
- Calculate unit-based Sell-in versus Sell-out Gap.

Core fields:

```text
period_start, period_end
store_id, sku_id
sell_in_units
sell_out_units_reported
sell_out_units_estimated
sell_out_coverage_pct
gap_reported_pct
gap_estimated_pct
```

### `features_oos_daily`

**Grain:** prediction date, store, and SKU.

Purpose:

- Reproducible Module 2 training and scoring.

Requirements:

- Every feature must use information available on or before the prediction date.
- Include missingness indicators, data-status fields, and `days_since_inventory_report`.
- Store feature version and transformation metadata.

### `labels_oos_next7`

**Grain:** prediction date, store, and SKU.

```text
target = 1 if any OOS is observed from t+1 through t+7
target = 0 if all seven future days are observed and none is OOS
target = unknown if the future window is incomplete and no observed day is OOS
```

Core label metadata:

```text
label_source = complete_window
or partial_window_confirmed_positive
```

Label usage policy:

- Complete-window positives and negatives may be used in training, validation, and test.
- A partial future window with at least one observed OOS is a confirmed positive and may be added to training with `label_source = partial_window_confirmed_positive`.
- A partial future window with no observed OOS is unknown and must be excluded from supervised training and formal evaluation.
- Formal validation and test metrics use complete windows only.
- Compare training results with and without partial-window confirmed positives and document any sample weighting.

After label construction, split samples chronologically by prediction date. Apply a seven-day purge or embargo where a training target window would otherwise overlap the feature dates of the next evaluation period. Perform class balancing only within the training set.

### `predictions_oos`

**Grain:** model version, scoring date, store, and SKU.

Core fields:

```text
oos_probability_next7
predicted_class
units_at_risk
expected_trade_value_at_risk
top_risk_drivers
input_data_status
days_since_inventory_report
model_version
```

`input_data_status` uses only `Observed`, `Estimated`, or `Unknown`. It must not introduce unsupported confidence or inventory-aging categories.

### `mart_negotiation_scenarios`

**Grain:** one simulated scenario.

Core calculations:

```text
net_price = list_price * (1 - discount_pct / 100)
gross_margin_pct = 100 * (net_price - unit_cost) / net_price
```

The final approval status must remain configurable until the approval decision tree is confirmed.

### `vw_nlq_*`

Create a small set of documented, read-only analytical views for Text-to-SQL. Do not expose unrestricted raw tables to the LLM.

Suggested views:

- `vw_nlq_commercial_kpis`
- `vw_nlq_store_latest_status`
- `vw_nlq_oos_predictions`
- `vw_nlq_sell_in_sell_out`
- `vw_nlq_execution_audits`

## Data Flow

```text
Authorized local CSV files
    -> schema and key validation
    -> cleaned dimensions and facts
    -> complete store-SKU calendar
    -> as-of joins and documented estimates
    -> governed KPI marts and ML feature tables
    -> model predictions and scenario calculations
    -> application pages and controlled NLQ views
```

## Data Quality Tests

### Source Tests

- Candidate keys are unique.
- All fact-table store and SKU IDs exist in their dimensions.
- Units, inventory, cost, price, and sales are nonnegative.
- `is_out_of_stock` agrees with zero end-of-day inventory.
- Channel, category, region, tier, and boolean values are valid.
- Dates are within the documented project range.

### Transformation Tests

- Dimension joins preserve fact row counts.
- Complete-calendar row counts match active-assortment rules.
- Missing Sell-out rows remain distinguishable from zero sales.
- Missing weekly Sell-in rows are converted to zero only at the declared weekly grain.
- As-of audit joins never select a future audit.
- Rolling features never use future observations.
- Time splits are chronological and seven-day purge gaps prevent target-window overlap where required.
- Partial-window confirmed positives are identified separately from complete-window labels.
- Validation and test labels come from complete future windows only.
- Reported and estimated values are not stored in the same field.
- `metric_view` is either `operational_estimate` or `reported_only`.
- Data-status fields contain only `Observed`, `Estimated`, or `Unknown`.

### Business Tests

- MSL sample stores can be reproduced manually.
- KPI numerators never exceed their denominators.
- Sell-in versus Sell-out periods use aligned boundaries.
- Margin calculations match manual examples.
- Every guardrail join returns exactly one rule.
- Distributor records are excluded or separately labeled in retail-facing metrics.

## Data Governance and Repository Handling

- Keep raw source files unchanged and access-controlled.
- Do not commit supplied raw data unless the instructor or client explicitly authorizes it.
- Commit schemas, validation code, data dictionaries, and small nonconfidential test fixtures.
- Do not commit API keys, credentials, local environment files, or private knowledge documents.
- Record each derived field's source and transformation.
- Version KPI definitions, feature logic, model artifacts, and RAG documents.

## Open Data Decisions

- MSL requirements by channel or tier.
- Invoice-to-delivery lag and whether a receipt proxy may be derived.
- Maximum audit carry-forward period.
- Whether Distributor Sell-out should support a separate wholesale-flow view.
- Structured Distributor volume commitments.
- Whether value at risk should use trade list price, estimated net price, or gross margin.
- Whether client-provided coordinates or addresses will be available for mapping.
