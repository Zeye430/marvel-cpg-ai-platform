# KPI and Missing Store Day Handling Specification

## Document Purpose

This document defines the governed implementation policy for Marvel CPG commercial KPIs, missing Sell-out store-days, replenishment reconciliation, and Module 2 labels using the client-supplied V3 dataset received on October 16, 2026. It is the contract for the analytical data layer, dashboards, feature engineering, model evaluation, and client communication.

The central policy is that an absent Sell-out store-day means not reported. It must not be silently converted to zero sales, zero inventory, or a non-OOS observation. Observed facts and estimated values must remain distinguishable throughout the data pipeline and application.

The reproducible V3 evidence is contained in [`../../notebooks/v3/02_v3_missing_day_analysis.ipynb`](../../notebooks/v3/02_v3_missing_day_analysis.ipynb) and [`../../notebooks/v3/04_v3_oos_model_development.ipynb`](../../notebooks/v3/04_v3_oos_model_development.ipynb). Generated evidence remains local under `outputs/v3/`.

**Status (2026-10-18):** V3 implementation contract. The published notebook outputs are experimental baselines; the implementation checks under "Notebook-to-Data-Layer Handoff" must pass before production readiness or final test approval is claimed.

## Decision Summary

- Build a complete active store-SKU calendar.
- Preserve explicit observation and imputation flags.
- Do not modify raw source values.
- Estimate missing sales and inventory separately because they represent different processes.
- Treat a positive `restock_units` row and its `restock_delivery_id` as the confirmed V3 store-SKU inventory receipt. The foreign key, date, store, SKU, quantity, and inventory equation must pass the reconciliation tests below.
- On a missing Sell-out day, use the matching Delivery event as a known inventory inflow while keeping daily sales estimated and separately flagged.
- Keep Marvel trade discounts separate from consumer promotion discounts.
- Use backward as-of joins for audits and display the last audit date and days since the audit.
- Do not create supervised OOS labels from imputed future inventory.
- Split model data chronologically and prevent seven-day target-window overlap.
- Use the Estimated Operational View as the default product view and provide a Reported Data Only view for verification.
- Use only three user-facing data states: Observed, Estimated, and Unknown.
- Show the last reporting date rather than a subjective estimation-confidence label.
- Adopt delivery-reconciled reconstruction as the V3 candidate for one- to four-day gaps, subject to corrected masking and time-availability checks. Mark longer gaps `Unknown`.

## Problem Classification

### Missing Report

A store-day is absent because the POS system or data feed did not report. The correct interpretation is unknown activity.

```text
Missing report
!= zero sales
!= zero inventory
!= not OOS
```

This is the principal interpretation of the approximately 8.1% missing Marvel account-days in V3.

### Censored Demand

Observed sales may understate customer demand when inventory becomes unavailable. If a store sells five units and ends the day at zero inventory, additional customer demand may have occurred after the stockout.

```text
observed sales <= true demand during a stockout
```

Censored demand is not the same as a missing data feed and should not be treated with ordinary interpolation alone.

### Inventory Record Uncertainty

Even a reported inventory value may differ from physical shelf inventory because of receiving delays, shrinkage, damage, returns, scanning errors, misplaced products, or backroom inventory. The supplied data cannot support a full inventory-reconciliation system. The prototype will therefore use a simplified treatment: identify whether inventory is Observed, Estimated, or Unknown and show when the last inventory report was received.

## Dataset Evidence

Unless stated otherwise, descriptive figures below use January 1, 2025 through April 30, 2026. May is the validation checkpoint; June remains locked for analytical evaluation.

- Observed account-days: 98,455 of 107,185 possible calendar account-days.
- Calendar coverage: 91.8552%; missing rate: 8.1448%.
- Reporting accounts per day: exactly 203 of 221, leaving 18 absent account-days per date.
- A reported account-day contains 15-23 SKU rows.
- Missingness appears primarily as absent store-days rather than null fields.
- When a store reports, its active assortment is generally reported as a group of SKU rows.
- An absent store-SKU invoice week may be reported as zero invoiced flow only after the ingestion run confirms that the invoice extract for that period is complete.
- Every reported account-day contains the account's complete, stable V3 assortment of 15-23 SKUs.
- The observed OOS rate across 1,948,765 reported store-SKU-days is 7.8863% for this development EDA period.
- There are 4,375 active store-SKU pairs in the pre-June data, with 2,073,376 observed Sell-out rows through May 31. The complete calendar through May contains 2,257,500 rows.
- V3 adds explicit `restock_units` and `restock_delivery_id` fields to Sell-out.

The fixed daily reporting count suggests a synthetic or scheduled reporting mechanism. The completed V3 analysis preserves the intentional missingness design and validates the repaired replenishment relationship.

## V3 Missingness and Reconciliation Result

The V3 missingness analysis through April 2026 found:

- Coverage ranges from 91.6609% to 91.9563% across channels and from 91.6667% to 92.1554% across tiers.
- There are 8,051 missing runs; 92.37% last one day, 99.27% last no more than two days, and the maximum observed run lasts five days.
- Aggregate missingness is identical by weekday because exactly 18 accounts are absent every date.

Prior V2 estimates of missingness dependencies are historical references. Do not reuse them as measured V3 values. The fixed-count mechanism does not prove MCAR; rerun prior-state and audit-timing diagnostics for the production ingestion contract.

The replenishment checks found:

- All 123,623 positive pre-June observed restocks resolve one-to-one to Delivery.
- Date, store, SKU, and quantity match rates are all 100%.
- All 1,899,743 consecutive observed transitions satisfy the inventory equation exactly.
- All 11,119 pre-June deliveries without an observed Sell-out restock occur on a missing Sell-out day.

The evidence supports one common missing-day framework for the MVP. Error must still be reported by channel and tier because sales, inventory level, and OOS prevalence differ materially across segments. Production monitoring must rerun these diagnostics whenever a new data version arrives.

## Complete Calendar Design

Create one row for every active combination of:

```text
calendar date x store x active SKU
```

For the static V3 analysis, define each account's active assortment as its distinct observed V3 SKU set. Every reported account-day contains that exact set, so this rule does not invent unobserved SKU membership. In a live pipeline, maintain an effective-dated assortment mapping and do not use a future first appearance to construct historical model features.

Minimum fields:

```text
date
store_id
sku_id
data_version
ingestion_run_id
source_file_hash
as_of_timestamp
is_store_day_reported
sales_is_observed
inventory_is_observed
units_sold_observed
inventory_observed
oos_observed
restock_units_observed
restock_delivery_id
units_sold_estimated
inventory_estimated
inventory_model_state
receipt_units_from_deliveries
receipt_delivery_count
sales_data_status
inventory_data_status
sales_was_imputed
inventory_was_imputed
days_since_inventory_report
estimation_method
```

Observed and estimated values must not overwrite one another. The key of a versioned daily state is `(data_version, date, store_id, sku_id)`; published model features additionally identify the feature-set version and scoring timestamp. Receipts from Delivery and Sell-out restocks represent the same event and must never be added twice.

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

V3 candidate baseline, to be implemented with strict historical inputs:

```text
store x SKU x weekday median over the prior 8 weekday occurrences
    -> store x SKU median over the prior 28 calendar days
    -> channel x category median fitted from available history
    -> Unknown if no eligible history exists
```

The notebook uses minimum counts of two and five non-null observations for the first two levels. Calendar-window length is not the same as a count of eligible observations. Broader tier/SKU fallbacks and in-stock-only demand estimators remain challenger methods requiring validation.

The current V3 masking output reports sales MAE of 2.28 units and WAPE of 38.63%. These are provisional baselines pending the masking correction described below. Every fallback and promotion factor must be fitted from information available before the row being reconstructed; do not compute a full-panel statistic and broadcast it back over earlier dates. Zero fill has 100% WAPE in the experiment and is rejected.

Promotion factors must retain their fitting cutoff and version. Apply a documented promotion baseline consistently; a rolling baseline already containing promotion days may overstate demand if multiplied again. Consumer price reductions and Sell-in trade discounts remain separate fields.

### Censored Demand Adjustment

Do not treat sales on an OOS day as normal unrestricted demand. For demand-velocity features:

- Prefer fully in-stock observed days.
- Mark OOS days as censored.
- Where necessary, estimate lost demand from comparable in-stock periods.
- Keep recovered demand separate from observed sales.

This adjustment is intended for demand and risk features, not for rewriting official observed-sales history.

## Inventory Estimation Policy

Inventory is a state variable. V3 provides a validated receipt event, so a missing-day estimate begins with the last observed inventory, subtracts estimated daily sales, and adds actual receipts that occurred on or before that date.

Conceptual equation:

```text
inventory_t = inventory_t-1 - units_sold_t + restock_units_t
```

For every positive observed V3 restock, `restock_delivery_id` resolves to one Delivery and the date, store, SKU, and units match exactly. The equation above holds for every consecutive observed transition tested.

Default short-gap estimate:

```text
estimated_inventory_t
    = last_observed_inventory
    - cumulative_estimated_sales_since_last_report
    + cumulative_actual_receipts_since_last_report
```

Across 30,000 artificially masked V3 rows, the current output reports inventory MAE of 2.97 units, WAPE of 2.22%, OOS-state precision of 97.52%, and recall of 89.94%. When a Delivery occurs inside the gap, its inventory MAE is 3.49 units; methods that omit the receipt have inventory MAE of approximately 64 units. These imputation metrics require strict block-masking confirmation before adoption as acceptance thresholds.

Enforce a lower bound of zero and retain any unconstrained residual for diagnosis. Store the estimate and method rather than replacing the observed field. The intended MVP limit is `days_since_inventory_report <= 4`, conditional on passing corrected masking. For a five-day run, days one through four may be estimated; day five is Unknown. A later report resets the state to the observed inventory. Missing leading history is Unknown. Do not calculate lot age or shelf life; the dataset lacks batch-level receipt information.

Use a documented recurrence consistently in masking and implementation. If clipping is applied only after subtracting cumulative gap sales, record that assumption: applying a zero floor every day can produce different results when a receipt follows an earlier depletion. Intraday ordering is not supplied and must not be inferred from the daily identity.

### Delivery and Sell-in Replenishment Signals

Sell-in is a weekly commercial invoice and must remain separate from physical replenishment. Delivery is the logistics fact; `restock_delivery_id` is the V3 link from Sell-out inventory to that fact. Create features such as:

```text
recent_sell_in_units_7d
recent_sell_in_units_14d
days_since_last_invoice
invoice_frequency
sell_in_units_at_lag_1_to_14
recent_delivered_units_7d
recent_delivered_units_14d
days_since_last_actual_delivery
delivery_fill_rate_pct
short_ship_flag
```

Use actual delivery events only on or after the event date. Add their units to a missing-day state only when the V3 restock contract passes. Scheduled future deliveries may enter Module 2 only for orders placed on or before prediction date `t`; future `actual_delivery`, future `restock_units`, and any event status not yet known at `t` are prohibited. Never add Sell-in invoice units directly to inventory.

### Mandatory Delivery-to-Inventory Reconciliation Gate

The reconciliation gate is mandatory for every data version. V3 passes the gate and classifies Delivery as a **Validated replenishment signal** for the supplied simulation.

The intended production hypothesis must be stated explicitly:

```text
latest observed or estimated store inventory
    + inbound quantity expected to become available within seven days
    - forecast demand over the same horizon
    + promotion and execution effects
    -> probability of any OOS during t+1 through t+7
```

Required checks for each new dataset are:

1. every positive restock has exactly one non-null Delivery foreign key;
2. the Delivery foreign key resolves uniquely;
3. delivery date, store, SKU, and delivered units equal the Sell-out restock fields;
4. consecutive observed days satisfy the inventory equation exactly;
5. every Delivery without an observed restock is explained by a missing Sell-out row;
6. masking metrics are reported separately for gaps with and without a Delivery;
7. scheduled and actual timestamps are classified by whether they were known at scoring time.

If any check fails materially, downgrade the source to `Timing-only` or `Unreconciled logistics-only`, prevent its units from entering inventory reconstruction, and reopen the client escalation.

For natural gaps, the interval equation may recover aggregate missing-period sales:

```text
implied_missing_sales
    = previous_observed_inventory
    + receipts_between_reports
    - current_observed_sales
    - current_observed_inventory
```

This does not identify the exact daily sales path. Daily estimates must still be selected with artificial masking.

The right-hand observation is available only after the gap closes. This interval bridge is a retrospective diagnostic; it must never revise features that claim to have been known at an earlier prediction time. V3 gaps without receipts show zero median implied missing-day sales, suggesting a simulation mechanism that differs from ordinary daily depletion. Artificial masking alone does not prove the same error rates on natural gaps.

Inventory and sales estimates produced by the missing-day analysis may be used only as features available on or before prediction date `t`. They must not be used to manufacture future OOS ground truth. Formal Module 2 evaluation must still use the complete-window label policy below and must test whether the final model simultaneously reaches Precision 0.75, Recall 0.70, and ROC-AUC 0.80.

## Audit Handling Policy

Audit variables should not be linearly interpolated. For each store-SKU-date, select the latest audit with `audit_date <= analysis_date`.

In production, also require `audit_known_at <= scoring_timestamp`. The static experiment assumes same-day availability because the source has no reporting timestamp. Audit age is measured in calendar days; a carried observation remains historical audit evidence rather than a newly observed daily audit.

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
target_oos_7d = 1 if all seven future store-SKU-days are observed and at least one is OOS
target_oos_7d = 0 if all seven future store-SKU-days are observed and none is OOS
target_oos_7d = NULL otherwise
```

Do not use imputed future inventory as official ground truth.

### Training and Benchmark Samples

The accepted baseline uses complete windows for training and evaluation. Preserve other outcome metadata for a separately validated training challenger:

- **Complete-window positive:** all seven future days are observed and at least one is OOS. Use in training, validation, and test.
- **Complete-window negative:** all seven future days are observed and none is OOS. Use in training, validation, and test.
- **Partial-window confirmed positive:** the future window is incomplete, but at least one observed day is OOS. Keep `confirmed_observed_oos = TRUE` and a separate `label_source`; it is excluded from the accepted complete-window baseline. Adding it to training is a challenger requiring a new validation run.
- **Partial-window unknown:** the future window is incomplete and no observed day is OOS. Exclude from supervised training and formal evaluation because the missing days could contain an unobserved OOS event.

Recommended label metadata:

```text
label_source = complete_window
or partial_window_confirmed_positive
```

Formal validation and test metrics must use complete windows only. This keeps precision, recall, and AUC based on fully observed outcomes. Report label eligibility coverage alongside metrics, including the share of currently missing predictor rows. The measured performance is conditional on complete future reporting; it is not a measurement of unknown outcomes.

Complete-window eligibility is not a substitute for a time-based split. After constructing labels:

1. Sort samples by prediction date.
2. Split chronologically into training, validation, and test periods.
3. Apply a seven-day purge or embargo when target windows could overlap a later split.
4. Perform class weighting or resampling only inside the training set.
5. Preserve the natural positive rate in validation and test sets.
6. Report complete positives, complete negatives, partial confirmed positives, and unknowns by split, channel, tier, and category.

The purge gap prevents a training label window from overlapping the feature dates of the next evaluation period. For example, if the final training prediction date is May 15, its label uses May 16 through May 22, so validation prediction dates should begin after that window rather than on May 16.

The governed V3 split is:

- March and April 2026: development folds for feature, architecture, and threshold-policy selection.
- May 1-24, 2026: iterative validation checkpoint with complete seven-day outcomes.
- June 2026: locked out-of-time test, evaluated only after the data layer and model policy reproduce the May result.

| Fold | Last training prediction date | Validation prediction dates | Last required outcome date |
| --- | --- | --- | --- |
| March development | 2026-02-21 | 2026-03-01 to 2026-03-24 | 2026-03-31 |
| April development | 2026-03-23 | 2026-04-01 to 2026-04-23 | 2026-04-30 |
| May validation | 2026-04-23 | 2026-05-01 to 2026-05-24 | 2026-05-31 |

The V3 notebook trains on a deterministic sample of at most 450,000 eligible rows per fold (seed 650), with balanced class weights and the natural validation prevalence. Keep this configuration versioned when comparing data-layer parity.

The development operating threshold uses a Recall floor of 0.75 to provide margin above the formal 0.70 requirement. This rule is frozen before the June test.

### Model Metrics

Fixed minimums:

- Precision: 0.75.
- Recall: 0.70.
- AUC-ROC: 0.80.

Additional diagnostics should include PR-AUC, confusion matrix, probability calibration, performance by channel and tier, eligible-label coverage, and performance by `days_since_inventory_report` group.

The selected experimental V3 configuration is one shared LightGBM model with replenishment-enriched features and one global threshold of 0.9117622890. The current May output is Precision 0.9302, Recall 0.7208, ROC-AUC 0.9914, and PR-AUC 0.9384 (4,277 TP, 321 FP, 1,657 FN). Separate channel-by-tier models and segment thresholds did not improve the development operating point. Overall attainment does not imply that every segment meets the Recall requirement.

May was evaluated twice: the initial threshold selected at development Recall 0.70 gave May Recall 0.6739; the subsequent development Recall buffer of 0.75 gave the result above. May is therefore an iterative validation set. June remains the independent confirmation, subject to the implementation audit below.

## KPI Specifications

### MSL Compliance

**Definition:** percentage of assessable stores where every applicable required `is_msl = TRUE` SKU has inventory greater than zero, accompanied by eligible-store counts and assessment coverage.

```text
MSL Compliance % = compliant stores / assessable stores in filter * 100
Assessment Coverage % = assessable stores / eligible stores in filter * 100
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

Compute a store-day state before aggregating: `Noncompliant` if any applicable required SKU is known to be unavailable; `Compliant` if every required SKU has a known positive state; `Unknown` otherwise. For each view, publish compliant, noncompliant, and unknown counts. Report compliance among assessable stores together with assessable/eligible coverage. If Unknown stores exist, also report the lower/upper bounds `compliant / eligible` and `(compliant + unknown) / eligible`; do not classify Unknown as a measured failure. Stores with no applicable required SKU are N/A.

**Limitations:** the official definition uses the global `is_msl` flag, but the tier descriptions suggest selective or partial assortments for Tier 2 and Tier 3. Confirm whether MSL should vary by channel or tier.

### OOS Rate

**Definition:** percentage of observed store-SKU-day rows with `is_out_of_stock = TRUE`.

```text
OOS Rate % = observed OOS store-SKU-days / observed store-SKU-days * 100
```

The official reported rate uses observed rows only and must show coverage. An estimated current-state OOS rate may be displayed separately.

Operational OOS uses known observed-or-estimated states as its denominator and publishes estimated-state share and known-state coverage. Unknown states are excluded from that rate and counted separately. Aggregate numerator and denominator before division; do not average subgroup percentages.

**Business purpose:** measures product-availability failure and helps identify SKUs, stores, channels, or regions that are losing sales because products are unavailable.

**Interpretation:**

- Lower is better.
- A high rate indicates frequent end-of-day stockouts and possible lost sales.
- A low rate indicates better product availability, but it should still be interpreted with reporting coverage.

**Recommended action:** investigate the highest-rate SKUs and stores, review demand velocity, recent deliveries, and recent Sell-in, and prioritize field visits or replenishment actions.

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

The primary commercial view uses event dates: invoice date for Sell-in and sale date for Sell-out. A separate simulation diagnostic may map a Monday invoice to the preceding Monday-Sunday Sell-out week; label it `source-week comparison`. Do not shift the official invoice date or make a subsequent Monday invoice available to an earlier prediction.

Missing-data policy:

- Missing weekly Sell-in row means zero invoiced flow only when extract completeness is confirmed; otherwise the period is Unknown.
- Missing daily Sell-out row means not reported.
- Publish reported and estimated Sell-out gap versions with coverage.
- Positive gap suggests over-shipping or inventory accumulation risk.
- Negative gap suggests under-shipping or replenishment/OOS risk.

**Limitations:** the official KPI compares invoiced Sell-in with consumer Sell-out and is not a direct inventory equation. V3 supplies validated daily restocks, but batch opening balances, returns, transfers, shrinkage, and other adjustments remain unavailable. Publish delivered-versus-sold units as a separate diagnostic rather than silently substituting delivered units into the approved invoice-based KPI. If Sell-out is zero, the percentage is undefined and the application must display `N/A` rather than divide by zero.

### Shelf Share

**Definition:** `shelf_share_pct` is the V3 SKU allocation share within a store × audit date × category visit. Values across the supplied category SKUs sum to 100% within a 0.031 percentage-point rounding tolerance. Aggregate a selected SKU set by summing its shares within each visit, then average the visit-level totals across eligible visits. A SKU-level view may show that SKU's own share. Publish visit count and scope.

**Business purpose:** describes the allocation of audited shelf space among supplied SKUs and supports discussions about display and assortment mix.

**Interpretation:**

- A higher SKU share means that SKU has a larger share of the normalized category visit.
- Summing all supplied category SKUs produces approximately 100% by construction; it does not establish Marvel's share versus competitors.
- A change in percentage points should be distinguished from a relative percentage change.

**Recommended action:** review the SKU allocation within each category visit and use the latest audit evidence during execution planning or negotiation. Compare the same SKU scope across stores.

**Missing-data policy:** use actual audit observations for official period reporting. For a current store view, show the latest prior audit and `days_since_last_audit`. Do not fabricate unaudited shelf share through linear interpolation.

**Limitations:** the normalization universe does not identify competitor shelf space. Until the client confirms that universe, label the measure `Audited SKU shelf allocation` and withhold a brand-versus-market shelf-share KPI. Audits are intermittent; do not claim daily or causal sales impact from the normalized field alone.

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
Estimated MSL Compliance: {current governed value}%
View: Operational Estimate
Store-day reporting coverage: {current governed coverage}%
```

Example Reported Data Only display:

```text
Reported MSL Compliance: {current governed value}%
View: Reported Data Only
Store-day reporting coverage: {current governed coverage}%
```

`days_since_inventory_report` means the number of calendar days between the selected date and the latest observed inventory record for that store-SKU. It does not mean product age, inventory turnover, shelf life, or time since physical receipt.

V1 and V2 latest-status values are retired and must not be shown as V3 results. Recompute product KPIs from the governed V3 Gold tables after the active-assortment and estimation rules are implemented. Summary age statistics belong in a manager or data-quality view, not the main field-representative workflow.

## Imputation Validation

The V3 masking experiment ranks `delivery_reconciled` first among the evaluated inventory methods; the implementation contract requires the corrected procedure below before promotion to the operational view.

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

The required V3 comparison includes LOCF, sales depletion, promotion-adjusted depletion, and delivery-reconciled depletion.

Evaluate sales MAE/WAPE, inventory MAE, derived OOS precision/recall/F1, bias by channel/tier, and error by gap length. Use the simplest method that performs adequately and remains explainable.

The existing experiment covers one-, two-, three-, and four-day blocks. Recompute all rolling inputs after hiding the entire block: later masked days must not consume hidden sales from earlier masked days. Mask complete store-days across their active SKU set to reproduce the reporting mechanism, and retain Delivery events as an independent feed when they would be known by that day. Report uncertainty using store- or block-grouped resampling rather than treating overlapping samples as independent. Estimates beyond four days since the last report remain Unknown.

## Notebook-to-Data-Layer Handoff

Stage 3 implementation evidence is recorded in [V3 candidate validation](../data/v3-medallion-stage-3-validation.md). Corrected block masking supports the receipt-aware estimator, but natural-gap closure exhibits a distinct mechanical balance pattern, including gaps with receipts. This remains an operational-adoption limitation; the older notebook figures below are not a certified replacement for that review.

The numerical outputs above remain the historical V3 experiment. The following requirements make the next implementation reproducible and prevent those outputs from being treated as an audited production benchmark:

1. **Historical fitting:** replace the notebooks' full-panel channel/category fallback medians with historical-only estimates. Promotion factors used for a training row must also be available by that row's timestamp, or be fitted on a separate earlier warm-up period. Store fitting cutoffs with estimator artifacts.
2. **Block masking:** rebuild rolling features after removing masked observations. The existing precomputed rolling features may consume hidden observations on the second and later masked days. Repeat sales/inventory error and current-state OOS metrics after correction.
3. **Order availability:** build pending-order features from orders known by `t`, schedules known by `t`, and receipts posted by `t`. The source contains no order/schedule change history, so static order-date availability is an explicit simulation assumption. Future actual receipt time must not determine a feature other than via the absence of a posted receipt at `t`.
4. **Boundary orders:** the notebooks retain only deliveries realized before June. A live backlog instead retains all orders known by the scoring date, including those eventually delivered in June. Before the final evaluation, implement a restricted known-order projection with realized June fields hidden; otherwise disclose the boundary omission.
5. **Gap policy:** apply the four-day freshness limit and missing-leading-history rule. The model notebook currently reconstructs every gap, so parity with the revised policy may change its sample population and metrics.
6. **Categorical fitting:** fit category encoders on training/reference dimensions and map genuinely unseen levels to an explicit unknown category. Do not discover learned transformations from validation outcomes.
7. **Replay and comparison:** first reproduce the stored notebook baseline with its original configuration, then run the corrected implementation as a separate version. Compare values, eligible populations, precision, recall, ROC-AUC, PR-AUC, and segment results. A corrected result is not required to equal 0.9302/0.7208/0.9914; it must be reported honestly and meet the business target before final test release.
8. **Test release:** freeze data hashes, temporal rules, estimator artifacts, feature version, model parameters, and threshold before June is evaluated. May may be reused for development with a documented run history; June must not drive iteration before that freeze.

These checks are part of the next data-layer work, not evidence that the underlying V3 receipt linkage failed. The linkage and consecutive-day inventory identity are direct source-data checks independent of the imputation estimator.

## Data-Layer Contract

The [V3 shared Medallion architecture](../data/v3-medallion-architecture.md) provides the detailed relation catalog, keys, four-module consumer mapping, serving boundaries and build sequence. The [source contract](../../data_platform/contracts/v3/sources.json) defines the eight source file headers and logical types.

The DuckDB implementation will use three schemas with versioned ingestion metadata:

| Layer | Responsibility | Required outputs |
| --- | --- | --- |
| Bronze | Preserve source records and ingestion lineage | Eight source tables, version, file hash, load time, ingestion-run status |
| Silver | Type and validate facts; resolve keys and timestamps | Active assortment, Sell-out, Sell-in, Delivery, restock events, audits, promotion calendar, reconciliation results |
| Gold | Publish governed business and modeling views | Store-SKU daily state, reported/operational KPI marts, as-of features, separately restricted labels and evaluation results |

`mart_store_sku_daily` uses the calendar and field contract above. Official reported fields remain null on missing Sell-out dates. Model feature queries must not access future-label columns; labels and retrospective gap diagnostics are separate relations. Re-running the same input version and configuration must be idempotent. Failed hard checks prevent promotion of a new version without deleting the prior published version.

Core KPI aggregation rules are shared across modules: sum numerators and denominators before calculating percentages; keep Unknown counts and reporting coverage; compute currency totals before rounding; return NULL/N/A for zero denominators; identify data version, period, view, and scope on exported results. Execution sales uplifts in the simulation are associations to validate, not additional units to hard-code into observed sales.

## Product Integration

Store 360 should display data quality directly:

```text
Inventory status: Estimated
Last reported: 3 days ago
Last actual delivery: 5 days ago
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
- Invoice units are never added to inventory. Validated receipt units enter missing-day estimates once, without duplicating Sell-out restock fields.
- Trade invoice discount and consumer promotion discount remain separate measures.
- Complete-calendar construction is reproducible.
- Every estimate has a method, last reporting date, and observed/imputed flag.
- No feature uses information after its prediction date.
- Official model labels are not created from imputed future inventory.
- Model datasets use chronological splits and seven-day purge gaps where target windows could overlap.
- The baseline uses complete-window labels for training, validation, and test; partial-window training is a separately versioned challenger.
- KPI outputs show the relevant reporting coverage, last reporting date, or last audit date.
- The product defaults to the Estimated Operational View and provides a Reported Data Only view.
- User-facing data status is limited to Observed, Estimated, or Unknown.
- The MVP does not display subjective estimation-confidence labels.
- Each KPI documents its business purpose, interpretation, recommended action, and limitations.
- Imputation methods are validated through artificial masking.
- The pipeline enforces V3 restock foreign-key, date, store, SKU, quantity, missing-day coverage, and inventory-equation checks.
- Delivery features are assigned one documented decision: validated replenishment signal, timing-only signal, or unreconciled logistics-only signal.
- A material reconciliation failure is recorded as a client escalation and prevents delivered units from entering estimated inventory.
- The archived V3 notebook baseline can be reproduced, and corrected implementation results are versioned separately.
- Corrected one- to four-day account-block masking and historical-fitting checks pass before operational estimates are published.
- MSL Unknown states, shelf-allocation scope, rounding, zero denominators, and coverage rules are implemented explicitly.
- Published KPI/feature marts expose version and lineage; label access and the June test boundary are enforced.
- The README and application explain the selected method and its limitations.

## Open Decisions

- Maximum audit carry-forward period.
- Whether scheduled future deliveries are reliably available at prediction time.
- Availability and revision timestamps for orders, schedules, promotions, receipts, and audits in production.
- Whether real store `inventory_level` means shelf stock or total on-hand; V3's exact receipt linkage resolves the simulation, not the production physical scope.
- The normalization universe for `shelf_share_pct` and any separate competitor-share data needed for Module 1.
- Client confirmation of the additional `dim_guardrails.csv` changes supplied in the ZIP but omitted from the four-file replacement instruction. Keep this dimension unapproved for Module 3 rules until resolved.
- Aggregation grain for promotion `min_volume_units`.
- Whether later block-masking or challenger-model results justify segment-specific imputation beyond the common MVP framework.
- Final weighting, if any, for partial-window confirmed positives in training.
- Whether current MSL is based on one global list or channel/tier-specific requirements.
