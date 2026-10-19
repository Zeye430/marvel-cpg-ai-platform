# Marvel CPG Project Bottlenecks and Decision Register

## Purpose

This document is the current register of requirement, data, business-rule, and product-design issues discovered during project review. It records what the client has confirmed, what the team has decided for implementation, and what still requires validation or approval.

Use this document to:

- Prioritize client and team decisions.
- Prevent inconsistent definitions across modules.
- Record approved assumptions and unresolved limitations.
- Update requirements, tests, and acceptance criteria when a decision changes.

Historical discovery notes that conflict with a later client response are not active requirements. The following documents contain the detailed implementation specifications:

- [`solution-specifications/kpi-and-missing-store-day-handling.md`](solution-specifications/kpi-and-missing-store-day-handling.md)
- [Current V3 Gold dictionary](data/v3-gold-data-dictionary.md)
- [`product/product-requirements-and-user-workflow.md`](product/product-requirements-and-user-workflow.md)

## Status Definitions

| Status | Meaning |
| --- | --- |
| Resolved | The client or brief supplied a usable definition. |
| Team policy | The team has a documented implementation method that remains subject to empirical validation. |
| Partial | Part of the issue is settled, but an important rule or limitation remains. |
| Open | A client or team decision is still required before the feature can be finalized. |
| Limitation | The supplied data cannot support the requested interpretation without additional data. |

## Current Decision Register

| ID | Priority | Area | Status | Current decision or remaining issue |
| --- | --- | --- | --- | --- |
| B-01 | Critical | Module 2 | Resolved | Predict any OOS event from `t+1` through `t+7`, not only OOS on day seven. Formal validation and test metrics use complete observed future windows. |
| B-02 | Critical | Modules 1 and 2 | Team policy | Missing Sell-out store-days mean not reported. Use separate observed and estimated fields, validate estimation through masking, and never create formal labels from imputed future inventory. |
| B-03 | Critical | Modules 1 and 2 | Resolved | `inventory_level` is end-of-day inventory after that day's sales; `is_out_of_stock` means end-of-day inventory equals zero. |
| B-04 | Critical | Module 3 | Resolved | `list_price` is Marvel's trade list price before discount, not consumer shelf price. |
| B-05 | Critical | Module 3 | Open | The complete approval logic for discount limits, margin floors, execution commitments, and manager overrides is not confirmed. |
| B-06 | Critical | Module 1 | Partial | The KPI is the percentage of eligible stores where every required MSL SKU has inventory greater than zero. The global `is_msl` flag conflicts with tier descriptions that imply selective assortments. |
| B-07 | Critical | Module 1 | Resolved | Use the unit-based gap `(Sell-in units - Sell-out units) / Sell-out units * 100` over aligned periods, with reported and estimated Sell-out versions. |
| B-08 | Critical | Module 3 | Partial | Data-derived elasticity and execution-uplift estimates are allowed, but they are observational assumptions rather than causal estimates. |
| B-09 | High | Modules 1, 2, and 3 | Open | Distributor accounts are wholesale accounts and must be separated from retail KPIs. Their supplied guardrails still contain inappropriate retail shelf commitments. |
| B-10 | High | Modules 1 and 2 | Open | Use the latest prior audit and show `days_since_last_audit`; the maximum valid carry-forward period is not confirmed. |
| B-11 | High | Module 1 | Resolved | The team may create documented synthetic territories and assign approximately 8-15 retail stores per representative. |
| B-12 | High | Modules 1 and 2 | Partial | Monday is the invoice date and official week start. An absent weekly row means zero Sell-in. Invoice-to-physical-delivery timing remains unknown. |
| B-13 | High | Module 1 | Resolved for implementation | Use the client-approved unit-based gap. Any value-based gap must be an additional, clearly labeled diagnostic. |
| B-14 | High | Modules 1 and 2 | Resolved | Tier 1 is highest priority with full MSL and weekly visits; Tier 2 is selective and generally biweekly; Tier 3 is partial and monthly or reactive. |
| B-15 | High | Module 2 | Open | The proposed units-at-risk and store-ranking formulas require client or team approval before they become final acceptance criteria. |
| B-16 | High | Module 3 | Partial | `+15% shelf share` means 15 percentage points and featured endcap maps to `secondary_display`; duration and combined free-text conditions are not fully verifiable. |
| B-17 | High | Product | Resolved | Treat June 30, 2026 as the application date. Preserve chronological model splits and reserve June for validation/testing where feasible. |
| B-18 | High | Module 4 | Resolved | Use 6-7 fictional Marvel documents and 3-4 appropriate public documents; do not use proprietary third-party content. |
| B-19 | Medium | Product | Resolved | Build one integrated application containing four connected modules. |
| B-20 | Medium | Data | Team policy | Parse pack size and unit from product names, retain the original name, and document parsing exceptions. |
| B-21 | Medium | Data and UI | Limitation | Store IDs are the keys. Precise store mapping and routing are unsupported without coordinates or full addresses. |
| B-22 | High | Module 1 | Partial | Planogram Compliance uses eligible audit rows. Shelf Share may be grouped at category/channel/region level, but SKU-level attribution remains unsupported without an explicit assumption. |

## Confirmed Cross-Module Decisions

### OOS Target and Evaluation

For a prediction made at the end of date `t`:

```text
target = 1 if any observed OOS occurs from t+1 through t+7
target = 0 if all seven future store-days are observed and none is OOS
target = unknown if the future window is incomplete and no observed OOS occurs
```

A partial future window containing an observed OOS is a logically confirmed positive and may be added to training with `label_source = partial_window_confirmed_positive`. Formal validation and test metrics use complete windows only. All splits must be chronological, with a seven-day purge or embargo where target windows would otherwise overlap a later split.

Fixed minimum model metrics are:

- Precision: 0.75.
- Recall: 0.70.
- AUC-ROC: 0.80.

### Missing Store-Day Policy

Approximately 30% of possible Sell-out store-days are absent. An absent store-day means not reported, not zero sales, zero inventory, confirmed closure, or non-OOS.

Implementation requirements:

1. Build a complete active store-SKU calendar.
2. Preserve observed and estimated values in separate fields.
3. Estimate sales with past-only demand history and documented hierarchical fallbacks.
4. Estimate inventory as a state variable; do not use simple linear interpolation across replenishment jumps.
5. Use Sell-in only as a possible lagged replenishment signal, not a confirmed receipt.
6. Validate estimation by masking known observations and reporting error by channel, tier, and gap length.
7. Use only `Observed`, `Estimated`, and `Unknown` as user-facing data states.
8. Show the last reporting date and `days_since_inventory_report`; do not display a subjective confidence label.
9. Default the application to the Operational Estimate view and provide a Reported Data Only view.

The preliminary June 30 latest-known analysis found approximately 4,355 active store-SKU pairs, a median `days_since_inventory_report` of 0 days, a 95th percentile of 2 days, and a maximum of 6 days. The preliminary latest-known MSL compliance estimate was 44.34%. These are dataset results, not client benchmarks.

### Price and Margin Definitions

The client confirmed that `dim_products.list_price` is Marvel's trade list price. Use:

```text
net_price = list_price * (1 - discount_pct / 100)
gross_margin_pct = 100 * (net_price - unit_cost) / net_price
```

The historical invoice unit price may be calculated as `net_sales / units_sold`, and the invoice-price-derived effective discount may be calculated as:

```text
effective_discount_pct = 100 * (1 - invoice_unit_price / list_price)
```

This does not resolve whether a simulator input represents total discount or an additional discount. That issue remains under B-05.

### Sell-in Versus Sell-out Gap

Use:

```text
gap_pct = 100 * (sell_in_units - sell_out_units) / sell_out_units
```

Interpretation:

- Near zero: invoiced and downstream flows are broadly aligned over the selected period.
- Positive: Sell-in exceeds Sell-out and may indicate replenishment, distribution expansion, or inventory buildup.
- Negative: Sell-out exceeds Sell-in and may indicate inventory drawdown or future replenishment risk.
- `sell_out_units = 0`: display `N/A` rather than divide by zero.

Traditional Trade should use biweekly or monthly aligned periods. Missing weekly Sell-in means zero invoices; missing daily Sell-out means not reported. The KPI is a flow-imbalance signal, not a direct inventory equation.

### Audit KPI Definitions

- Planogram Compliance is the percentage of eligible audit rows with `planogram_compliance = TRUE`.
- Shelf Share is the average eligible `shelf_share_pct` grouped at supported category, channel, region, and time levels.
- Official period reporting uses actual audits only.
- Current-state views may use the latest audit on or before the analysis date and must show the audit date and `days_since_last_audit`.
- A missing audit is not noncompliance.

The client's general planogram-compliance example is industry context, not a benchmark calculated from the supplied period.

## Open Decisions Requiring Follow-Up

### B-05 Discount and Approval Decision Tree

The dataset supplies `max_discount_pct`, `min_target_margin`, and a free-text `required_exec_tradeoff`, but the complete approval tree remains undefined.

Questions for the client:

1. Is `max_discount_pct` a representative authority limit or an absolute cap?
2. Above that limit, is the required execution commitment sufficient, or is manager approval also required?
3. Is `min_target_margin` an absolute floor, or can a senior approver override it?
4. Is the simulator discount the total discount from trade list price or an additional discount?

Provisional configurable rule:

```text
margin below minimum
-> rejected

margin meets minimum and discount is within representative authority
-> approved

margin meets minimum, discount exceeds authority, and required commitment is accepted
-> approval required / approved with conditions

discount exceeds authority and no qualifying commitment is accepted
-> rejected
```

This rule must remain configurable and must not be presented as client policy until confirmed.

### B-06 MSL Scope by Tier or Channel

The approved formula requires every required MSL SKU to have inventory greater than zero. The unresolved issue is which SKUs are required for each store. The global `is_msl` field identifies 15 products, while the client describes Tier 2 as selective and Tier 3 as partial assortment.

Question for the client:

> Should all retail stores use the same global MSL, or should the team construct channel- or tier-specific MSL mappings?

Until confirmed, use the global `is_msl` field, label the assumption, and provide store-SKU drill-down.

### B-09 and B-16 Distributor Guardrails

Distributor accounts represent wholesale accounts. Their Sell-out records may describe downstream wholesale movement or synthetic account flow, not end-consumer demand.

Current handling:

- Exclude Distributor accounts from retail MSL, planogram, shelf-share, store-visit, and consumer OOS KPIs.
- Keep Distributor flows available only in a separately labeled wholesale view if needed.
- Use a separate Module 3 mode based on order quantity, price, margin, and approval authority.
- Preserve the raw guardrail table unchanged.

The conflict is that Distributor guardrails still require MSL, planogram, shelf-share, secondary-display, or endcap commitments. These are retail shelf conditions rather than wholesale volume commitments.

Questions for the client:

1. Should Distributor execution conditions be replaced by order-volume commitments?
2. What quantity tiers unlock each discount level?
3. Which approvals are required for Distributor discounts?
4. Should Distributor Sell-out support a separate wholesale-flow dashboard?

### B-10 Audit Carry-Forward Period

Audits are intermittent and must be joined backward in time. Never use a future audit. The MVP will display the latest audit date and `days_since_last_audit`, but the maximum valid period is unresolved.

Question for the client:

> Should audit validity use a fixed limit such as 30 days, the expected tier visit cadence, or remain visible until replaced with no validity claim?

### B-12 Invoice-to-Delivery Timing

The Monday Sell-in date is both invoice date and official week start. Orders are batch-closed Sunday night. It does not establish when product physically arrives or becomes available for sale.

Current handling:

- Keep Monday as `week_start`; do not shift it to the prior week.
- Do not add invoiced units directly to daily inventory.
- Create past-available features such as recent invoiced units and days since invoice.
- Test whether inventory increases follow invoices after a stable lag.

Question for the client:

> Is a standard or channel-specific invoice-to-delivery lag available?

### B-15 Units at Risk and Store Ranking

Proposed store-SKU calculations on scoring date `t`:

```text
forecast_demand_7d = estimated in-stock demand for t+1 through t+7
units_at_risk = max(0, forecast_demand_7d - current_inventory_estimate)
expected_trade_value_at_risk = P(any OOS in next 7 days) * units_at_risk * list_price
```

`expected_trade_value_at_risk` is a trade-list-price exposure measure, not confirmed Marvel net sales or gross-profit loss.

Proposed transparent store ranking:

1. Current MSL OOS stores first.
2. Then descending total expected trade value at risk across SKUs.
3. Use OOS probability as a tie-breaker.
4. Include data freshness as context, not as hidden confidence scoring.

Questions for the client:

1. Should value at risk use trade list price, expected invoice price, or gross margin contribution?
2. Should tier, channel, or strategic-account status modify the ordering?
3. Is the lexicographic ranking above acceptable, or is a weighted score required?

### B-08 Elasticity and Execution Uplift

Sell-out has no historical consumer price, promotion, or retail revenue. Static SKU list-price differences do not identify own-price elasticity. Sell-in invoice-price variation reflects trade pricing and should not be mislabeled as consumer price elasticity.

Allowed prototype approach:

- Treat elasticity as a transparent scenario assumption or observational estimate.
- Estimate execution uplift from associations between sales velocity and `secondary_display`, planogram status, or shelf-share groups.
- Control for available product, store, channel, tier, and time factors where feasible.
- Display the selected assumptions in the simulator and README.
- Do not claim causal impact.

### B-21 Mapping Limitation

There are 221 store IDs but only 210 unique store names. Store names must not be used as keys. City and state support regional views and synthetic territory grouping, but not exact store pins or routing.

Use region, state, or city visualizations unless the client supplies coordinates or fuller addresses.

### B-22 Shelf-Share Grain

The audit table contains store-SKU-date rows, but `shelf_share_pct` may describe Marvel's category-level share. Values can differ across SKU rows within the same store-date-category group, so SKU-level interpretation is unsafe.

Question for the client:

> Does `shelf_share_pct` describe each SKU's shelf share or Marvel's total category shelf share, and should the official KPI be audit-row weighted, store-visit weighted, or latest-store weighted?

Until clarified, use the approved audit-row calculation for the formal KPI, aggregate only at supported category or higher levels, and state the limitation.

## Team-Owned Implementation Items

These items do not require a client response before work begins, but their methods and validation results must be documented:

- Select and validate sales and inventory estimation methods.
- Compare OOS training with and without partial-window confirmed positives.
- Construct reproducible synthetic territories and representative assignments.
- Parse pack size and unit from product names and test exceptions.
- Build reported-only and operational-estimate KPI views.
- Structure free-text execution commitments into configurable application rules.
- Create read-only analytical views for Text-to-SQL.
- Curate the approved fictional and public RAG documents.

## Client Meeting Priority

Recommended order for the next requirements discussion:

1. B-05 discount authority, conditions, margin floor, and approval tree.
2. B-09/B-16 Distributor volume commitments and guardrails.
3. B-06 global versus tier/channel-specific MSL requirements.
4. B-15 value-at-risk and store-ranking method.
5. B-10 audit carry-forward period.
6. B-12 invoice-to-delivery timing.
7. B-22 shelf-share grain and weighting.

## Decision Log

| Date | ID | Decision | Owner | Implementation impact | Status |
| --- | --- | --- | --- | --- | --- |
| 2026-09-30 | B-01 | Target any OOS event during the following seven calendar days. | Client | Use any-event label and complete-window formal evaluation. | Resolved |
| 2026-09-30 | B-03 | Inventory is an end-of-day level after daily sales. | Client | Same-day sales and ending inventory can coexist. | Resolved |
| 2026-09-30 | B-04 | `list_price` is Marvel trade list price. | Client | Use it as the discount baseline. | Resolved |
| 2026-09-30 | B-07 | Use the unit-based Sell-in versus Sell-out Gap. | Client | Align periods and display coverage. | Resolved |
| 2026-09-30 | B-11 | Synthetic territories and assignments are permitted. | Client | Create documented representative-store mappings. | Resolved |
| 2026-09-30 | B-17 | Treat June 30, 2026 as the application date. | Client | Use past-only features and chronological evaluation. | Resolved |
| 2026-09-30 | B-18 | Public and fictional documents are allowed for RAG. | Client | Curate 10 or more nonproprietary documents. | Resolved |
| 2026-09-30 | B-19 | Deliver one integrated application. | Client | Use connected modules and a shared data layer. | Resolved |

## Maintenance Rule

When a new decision is received:

1. Update the status and wording in the Current Decision Register.
2. Add a dated row to the Decision Log.
3. Update the linked implementation specification and acceptance tests.
4. Remove or rewrite any provisional rule that conflicts with the new decision.
5. Preserve raw source data and client wording outside the implementation layer.
