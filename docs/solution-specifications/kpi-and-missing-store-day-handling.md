# KPI and Missing Store Day Handling Specification

## Document Purpose

This document defines the initial implementation policy for Marvel CPG commercial KPIs and missing Sell-out store-days. It is a reusable solution reference for dashboard development, feature engineering, model evaluation, and client communication.

The central policy is that an absent Sell-out store-day means not reported. It must not be silently converted to zero sales, zero inventory, or a non-OOS observation. Observed facts and estimated values must remain distinguishable throughout the data pipeline and application.

## Decision Summary

- Build a complete active store-SKU calendar.
- Preserve explicit observation and imputation flags.
- Do not modify raw source values.
- Estimate missing sales and inventory separately because they represent different processes.
- Use Sell-in only as a possible lagged replenishment signal, not as a confirmed receipt.
- Use backward as-of joins for audits and display the last audit date and days since the audit.
- Do not create supervised OOS labels from imputed future inventory.
- Split model data chronologically and prevent seven-day target-window overlap.
- Use the Estimated Operational View as the default product view and provide a Reported Data Only view for verification.
- Use only three user-facing data states: Observed, Estimated, and Unknown.
- Show the last reporting date rather than a subjective estimation-confidence label.

## Problem Classification

### Missing Report

A store-day is absent because the POS system or data feed did not report. The correct interpretation is unknown activity.

```text
Missing report
!= zero sales
!= zero inventory
!= not OOS
```

This is the principal interpretation of the approximately 30% missing Marvel store-days.

### Censored Demand

Observed sales may understate customer demand when inventory becomes unavailable. If a store sells five units and ends the day at zero inventory, additional customer demand may have occurred after the stockout.

```text
observed sales <= true demand during a stockout
```

Censored demand is not the same as a missing data feed and should not be treated with ordinary interpolation alone.

### Inventory Record Uncertainty

Even a reported inventory value may differ from physical shelf inventory because of receiving delays, shrinkage, damage, returns, scanning errors, misplaced products, or backroom inventory. The supplied data cannot support a full inventory-reconciliation system. The prototype will therefore use a simplified treatment: identify whether inventory is Observed, Estimated, or Unknown and show when the last inventory report was received.

## Dataset Evidence

- Observed store-days: approximately 84,084.
- Possible calendar store-days: approximately 120,666.
- Calendar coverage: approximately 69.7%.
- Reporting accounts per day: approximately 154 of 221.
- Missingness appears primarily as absent store-days rather than null fields.
- When a store reports, its active assortment is generally reported as a group of SKU rows.
- Sell-in absence has a different meaning: an absent store-SKU invoice week means zero Sell-in for that week.

The stable daily reporting count suggests a synthetic reporting mechanism. The team should still analyze whether missingness varies by channel, tier, region, store, previous OOS status, or audit timing.

## Required Missingness EDA

Before selecting a final estimation method, calculate:

- Coverage by channel, tier, region, state, store, weekday, and month.
- Consecutive missing-day run lengths.
- Probability of missingness after an OOS day.
- Probability of missingness after high or low sales.
- Relationship between missingness and audit dates.
- Whether each store follows a repeatable reporting rotation.

The output should determine whether one common policy is sufficient or whether channel- or tier-specific models are required.

## Complete Calendar Design

Create one row for every active combination of:

```text
calendar date x store x active SKU
```

Do not create rows for an SKU before it enters the store's active assortment unless the team explicitly defines a full-assortment assumption.

Minimum fields:

```text
date
store_id
sku_id
is_store_day_reported
sales_is_observed
inventory_is_observed
units_sold_observed
inventory_observed
oos_observed
units_sold_estimated
inventory_estimated
sales_was_imputed
inventory_was_imputed
days_since_inventory_report
estimation_method
```

Observed and estimated values must not overwrite one another.

## Sales Estimation Policy

Sales are a flow variable and should not be forward-filled from the previous day.

### Baseline Method

Estimate a missing store-SKU-day using only information available before that date:

1. Calculate in-stock sales history for the store-SKU.
2. Calculate recent 7-, 14-, and 28-day velocity using observed eligible days.
3. Account for weekday effects.
4. Use a hierarchical fallback when store-SKU history is insufficient.

Recommended hierarchy:

```text
store x SKU x weekday
    -> store x SKU
    -> tier x channel x SKU x weekday
    -> channel x SKU x weekday
    -> category x tier x weekday
```

A rolling median is the preferred explainable baseline. Poisson, Negative Binomial, or tree-based regression can be evaluated as challengers.

### Censored Demand Adjustment

Do not treat sales on an OOS day as normal unrestricted demand. For demand-velocity features:

- Prefer fully in-stock observed days.
- Mark OOS days as censored.
- Where necessary, estimate lost demand from comparable in-stock periods.
- Keep recovered demand separate from observed sales.

This adjustment is intended for demand and risk features, not for rewriting official observed-sales history.

## Inventory Estimation Policy

Inventory is a state variable. A short-gap estimate may begin with the last observed inventory, but simple forward-fill is insufficient because sales reduce inventory and replenishment can create discontinuous increases.

Conceptual equation:

```text
inventory_t = inventory_t-1 + physical_receipts_t - sales_t + inventory_adjustments_t
```

The supplied data does not provide confirmed physical receipts or inventory adjustments.

Proposed short-gap estimate:

```text
estimated_inventory_t
= last_observed_inventory
  - estimated_cumulative_sales
  + estimated_replenishment_if_supported
```

Enforce a lower bound of zero. Store the estimate and its method rather than replacing the observed field. Do not calculate physical inventory age, stock-turnover age, or lot age; the dataset does not contain the required receipt or batch information.

### Sell-in as a Replenishment Signal

Sell-in is a weekly invoice, not a confirmed store receipt. Create optional features such as:

```text
recent_sell_in_units_7d
recent_sell_in_units_14d
days_since_last_invoice
invoice_frequency
sell_in_units_at_lag_1_to_14
```

Test whether inventory increases consistently follow invoices after a measurable lag. If a stable relationship is found, use the lagged signal in an inventory-estimation model. If the relationship is unstable and delivery timing remains unknown, retain Sell-in as a weak feature and do not add invoiced units directly to inventory. Never use invoices occurring after the estimation date.

## Audit Handling Policy

Audit variables should not be linearly interpolated. For each store-SKU-date, select the latest audit with `audit_date <= analysis_date`.

Add:

```text
days_since_last_audit
```

Expected visit cadence may be shown as context:

- Tier 1 expected cadence: 7 days.
- Tier 2 expected cadence: 14 days.
- Tier 3 expected cadence: 30 days.

The MVP should display the latest audit date and its age rather than assign a complex audit-confidence score. A manager warning for an overdue audit may be added later, but it is not required for the core OOS workflow.

## OOS Target and Model Evaluation

### Target Definition

For a prediction made at the end of date `t`:

```text
target = 1 if any observed OOS occurs from t+1 through t+7
target = 0 if all seven future store-days are observed and none is OOS
target = unknown if no OOS is observed and the future window is incomplete
```

Do not use imputed future inventory as official ground truth.

### Training and Benchmark Samples

Use a hybrid label policy to balance label reliability and sample size:

- **Complete-window positive:** all seven future days are observed and at least one is OOS. Use in training, validation, and test.
- **Complete-window negative:** all seven future days are observed and none is OOS. Use in training, validation, and test.
- **Partial-window confirmed positive:** the future window is incomplete, but at least one observed day is OOS. This is still a logically confirmed positive and may be added to the training set with a separate `label_source` value.
- **Partial-window unknown:** the future window is incomplete and no observed day is OOS. Exclude from supervised training and formal evaluation because the missing days could contain an unobserved OOS event.

Recommended label metadata:

```text
label_source = complete_window
or partial_window_confirmed_positive
```

Formal validation and test metrics must use complete windows only. This keeps precision, recall, and AUC based on fully observed outcomes. Training may add partial-window confirmed positives, but the team must compare model results with and without them and document any sample weighting.

Complete-window eligibility is not a substitute for a time-based split. After constructing labels:

1. Sort samples by prediction date.
2. Split chronologically into training, validation, and test periods.
3. Apply a seven-day purge or embargo when target windows could overlap a later split.
4. Perform class weighting or resampling only inside the training set.
5. Preserve the natural positive rate in validation and test sets.
6. Report complete positives, complete negatives, partial confirmed positives, and unknowns by split, channel, tier, and category.

The purge gap prevents a training label window from overlapping the feature dates of the next evaluation period. For example, if the final training prediction date is May 15, its label uses May 16 through May 22, so validation prediction dates should begin after that window rather than on May 16.

The client instructed the team to train approximately through May 2026 and validate/test in June 2026. Exact boundaries must preserve enough observations and avoid leakage.

### Model Metrics

Fixed minimums:

- Precision: 0.75.
- Recall: 0.70.
- AUC-ROC: 0.80.

Additional diagnostics should include PR-AUC, confusion matrix, probability calibration, performance by channel and tier, eligible-label coverage, and performance by `days_since_inventory_report` group.

## KPI Specifications

### MSL Compliance

**Definition:** percentage of stores where every required `is_msl = TRUE` SKU has inventory greater than zero.

```text
MSL Compliance % = compliant stores / total eligible stores in filter * 100
```

**Business purpose:** measures whether a store has the complete mandatory assortment available. It helps a field representative identify which essential SKUs require replenishment and helps a manager compare basic assortment execution across stores.

**Interpretation:**

- Higher is better and means more stores have every required MSL SKU available.
- Lower means more stores are missing at least one mandatory SKU.
- The measure is strict: one missing required SKU causes the entire store to fail.

**Recommended action:** open the failed stores, identify the missing MSL SKUs, and prioritize replenishment or assortment correction.

**Missing-data policy:**

- Use the estimated current state in the default Operational Estimate view.
- Provide a Reported Data Only view for stores with a complete observed MSL state.
- Display reporting coverage, `days_since_inventory_report`, and Observed/Estimated status.
- Do not silently remove missing stores from the denominator.

**Limitations:** the official definition uses the global `is_msl` flag, but the tier descriptions suggest selective or partial assortments for Tier 2 and Tier 3. Confirm whether MSL should vary by channel or tier.

### OOS Rate

**Definition:** percentage of observed store-SKU-day rows with `is_out_of_stock = TRUE`.

```text
OOS Rate % = observed OOS store-SKU-days / observed store-SKU-days * 100
```

The official reported rate uses observed rows only and must show coverage. An estimated current-state OOS rate may be displayed separately.

**Business purpose:** measures product-availability failure and helps identify SKUs, stores, channels, or regions that are losing sales because products are unavailable.

**Interpretation:**

- Lower is better.
- A high rate indicates frequent end-of-day stockouts and possible lost sales.
- A low rate indicates better product availability, but it should still be interpreted with reporting coverage.

**Recommended action:** investigate the highest-rate SKUs and stores, review demand velocity and recent Sell-in, and prioritize field visits or replenishment actions.

**Product default:** display the Estimated OOS Rate in the Operational Estimate view with a clear Estimated label and reporting coverage. Provide the official observed-row calculation in the Reported Data Only view.

**Limitations:** an end-of-day OOS flag does not reveal the exact time of the stockout or the amount of unmet demand. Missing store-days cannot be treated as non-OOS.

### Sell-in Versus Sell-out Gap

**Definition:** unit difference between invoiced flow and consumer sales flow.

```text
Gap % = (Sell-in units - Sell-out units) / Sell-out units * 100
```

**Business purpose:** indicates whether invoiced product flow into the channel is broadly aligned with downstream consumer sales. It is an imbalance signal for possible inventory buildup or future replenishment risk.

**Interpretation:**

- Near zero: Sell-in and Sell-out are approximately balanced over the selected aligned period.
- Positive: Sell-in exceeds Sell-out. This may reflect a recent replenishment, new distribution, or inventory buildup. A persistently large positive value may indicate over-shipping.
- Negative: Sell-out exceeds Sell-in. The customer may be drawing down existing inventory, and a persistently negative value may indicate under-shipping and future OOS risk.
- Larger absolute values indicate a greater flow imbalance relative to Sell-out.

Example:

```text
Sell-out = 100 units
Sell-in = 120 units -> Gap = +20%
Sell-in = 80 units  -> Gap = -20%
```

**Recommended action:** investigate persistent positive gaps for inventory accumulation and persistent negative gaps for replenishment or OOS risk. Use drill-down by store, SKU, and period rather than acting on one short-term spike.

Time alignment:

- Traditional Trade: biweekly or monthly.
- Other channels: weekly, biweekly, or monthly, provided both facts use the same boundaries.

Missing-data policy:

- Missing weekly Sell-in row means zero Sell-in.
- Missing daily Sell-out row means not reported.
- Publish reported and estimated Sell-out gap versions with coverage.
- Positive gap suggests over-shipping or inventory accumulation risk.
- Negative gap suggests under-shipping or replenishment/OOS risk.

**Limitations:** this KPI is not a direct inventory calculation because opening inventory, physical delivery timing, returns, and adjustments are unavailable. If Sell-out is zero, the percentage is undefined and the application must display `N/A` rather than divide by zero.

### Shelf Share

**Definition:** average `shelf_share_pct` from eligible audit rows grouped by category, channel, region, and requested filters.

**Business purpose:** measures Marvel's physical visibility within the category and supports discussions about shelf-space commitments and secondary displays.

**Interpretation:**

- Higher generally means Marvel occupies more of the category shelf.
- Lower may indicate weaker visibility or greater competitor presence.
- A change in percentage points should be distinguished from a relative percentage change.

**Recommended action:** identify low-share stores or channels and use the latest audit evidence during execution planning or negotiation.

**Missing-data policy:** use actual audit observations for official period reporting. For a current store view, show the latest prior audit and `days_since_last_audit`. Do not fabricate unaudited shelf share through linear interpolation.

**Limitations:** audits are intermittent, and the field may represent category-level shelf share even though rows contain an SKU. Do not claim daily or causal sales impact without additional assumptions.

### Planogram Compliance

**Definition:** percentage of eligible audit rows with `planogram_compliance = TRUE`.

```text
Planogram Compliance % = compliant audit rows / total audit rows * 100
```

**Business purpose:** measures whether products are placed according to the agreed display plan. It captures shelf-execution quality, not product availability.

**Interpretation:**

- Higher is better and means more audited placements follow the planogram.
- Lower suggests incorrect placement, reduced visibility, or weak store execution.
- A store may have full MSL compliance but poor planogram compliance, or the reverse.

**Recommended action:** identify stores with repeated noncompliance and define the visit objective as placement correction, retailer discussion, or follow-up audit.

**Missing-data policy:** use actual audit rows for official period reporting. A current-state view may show the latest prior audit and its date. Do not treat a missing audit as noncompliance.

**Limitations:** the KPI describes audited rows and may not represent all stores on the selected date. Always show the number of audits or relevant audit coverage.

## Reported and Estimated Views

The application should default to the **Operational Estimate** view. This view uses observed values when available and documented estimates for missing store-days. Every estimated value must remain visibly labeled.

Provide a **Reported Data Only** view or toggle for users who need to inspect the original observed records and official reported-row KPI calculations.

Recommended user-facing statuses:

| Status | Meaning |
| --- | --- |
| Observed | Value comes directly from an eligible source row. |
| Estimated | Value was reconstructed with a documented method. |
| Unknown | Evidence is insufficient for a defensible estimate. |

Do not display a subjective High/Medium/Low estimation-confidence label in the MVP. Show the last reporting date so the user can understand the basis of an estimate.

Example Operational Estimate display:

```text
Estimated MSL Compliance: 44.3%
View: Operational Estimate
Store-day reporting coverage: 69.7%
```

Example Reported Data Only display:

```text
Reported MSL Compliance: 42.8%
View: Reported Data Only
Store-day reporting coverage: 69.7%
```

`days_since_inventory_report` means the number of calendar days between the selected date and the latest observed inventory record for that store-SKU. It does not mean product age, inventory turnover, shelf life, or time since physical receipt.

At the June 30, 2026 as-of date, the preliminary latest-known analysis found a median `days_since_inventory_report` of 0 days, a 95th percentile of 2 days, and a maximum of 6 days across active store-SKU pairs. The preliminary latest-known MSL compliance estimate was 44.34%. These are analytical results for the supplied period, not client-provided benchmarks. Summary age statistics belong in a manager or data-quality view, not the main field-representative workflow.

## Imputation Validation

Every proposed estimation method must be evaluated through artificial masking:

1. Select observed periods with known sales and inventory.
2. Mask individual days and realistic consecutive-day blocks.
3. Apply the candidate method using past-available information only.
4. Compare estimates with the hidden known values.
5. Report results by channel, tier, SKU, and gap length.

Candidate baselines:

- Zero fill.
- Simple forward fill for inventory only.
- Rolling median.
- Hierarchical weekday median.
- State-based inventory estimate.
- Tree-based regression.

Evaluate sales MAE/WAPE, inventory MAE, derived OOS precision/recall/F1, bias by channel/tier, and error by gap length. Use the simplest method that performs adequately and remains explainable.

## Product Integration

Store 360 should display data quality directly:

```text
Inventory status: Estimated
Last reported: 3 days ago
Recent invoice: 5 days ago
```

Store 360 should open in the Operational Estimate view and offer a Reported Data Only toggle. Observed values should be used whenever present; missing current values may be estimated and labeled. If the available history is insufficient, show Unknown rather than a low-confidence number.

The visit-ranking page may include data-quality intervention reasons such as high OOS probability, a current MSL gap, a long period without Sell-out reporting, or a long period without an execution audit. These are secondary reasons; the core product remains focused on OOS risk and commercial execution rather than inventory aging or turnover.

## Real-World Reference Cases

### Amazon Forecast Missing-Value Guidance

Amazon distinguishes zero sales for an available item from missing sales while an item is unavailable and warns that the wrong filling choice can bias forecasts. It supports separate filling strategies such as NaN, rolling mean, and rolling median.

Source: [Amazon Forecast Handling Missing Values](https://docs.aws.amazon.com/forecast/latest/dg/howitworks-missing-values.html)

### Microsoft CPG Inventory Allocation Case

Microsoft documented work with a large healthcare CPG company that combined diagnostic analysis, repeated ML feasibility studies, store-product forecasting, and prescriptive inventory recommendations. The case identifies POS sales and store inventory as critical inputs and notes limited visibility into retailer logistics.

Source: [Microsoft ISE Solving Store Inventory Allocation for CPG](https://devblogs.microsoft.com/ise/store-inventory-allocation-cpg/)

### Dingdong FreshRetailNet

Dingdong-Inc released a large stockout-annotated retail dataset with hourly sales and stock-status information. Its published baseline separates latent-demand recovery during stockouts from subsequent demand forecasting.

Sources:

- [FreshRetailNet-50K Research Paper](https://arxiv.org/abs/2505.16319)
- [Dingdong-Inc Dataset Documentation](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K/blob/main/README.md)

### Retail Inventory Record Inaccuracy

An empirical study of nearly 370,000 inventory records across 37 stores found substantial inventory-record inaccuracy and identified auditing practices as one mitigating factor.

Source: [Inventory Record Inaccuracy An Empirical Analysis](https://pubsonline.informs.org/doi/10.1287/mnsc.1070.0789)

### Censored Demand at a European Retail Chain

Research motivated by data from a large European retail chain estimated unobservable lost sales using normal in-stock sales patterns instead of interpreting censored sales as true demand.

Source: [The Data-Driven Newsvendor with Censored Demand Observations](https://www.sciencedirect.com/science/article/pii/S092552731300203X)

## Acceptance Criteria

- Missing Sell-out rows are never silently converted to zero.
- Raw values are unchanged and estimates use separate fields.
- Complete-calendar construction is reproducible.
- Every estimate has a method, last reporting date, and observed/imputed flag.
- No feature uses information after its prediction date.
- Official model labels are not created from imputed future inventory.
- Model datasets use chronological splits and seven-day purge gaps where target windows could overlap.
- Training may use partial-window confirmed positives with a separate label source; validation and test use complete windows only.
- KPI outputs show the relevant reporting coverage, last reporting date, or last audit date.
- The product defaults to the Estimated Operational View and provides a Reported Data Only view.
- User-facing data status is limited to Observed, Estimated, or Unknown.
- The MVP does not display subjective estimation-confidence labels.
- Each KPI documents its business purpose, interpretation, recommended action, and limitations.
- Imputation methods are validated through artificial masking.
- The README and application explain the selected method and its limitations.

## Open Decisions

- Maximum audit carry-forward period.
- Approved invoice-to-delivery lag or replenishment proxy.
- Channel- or tier-specific imputation methods.
- Final weighting, if any, for partial-window confirmed positives in training.
- Whether current MSL is based on one global list or channel/tier-specific requirements.
