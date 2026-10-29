---
document_id: MCPG-08
title: Commercial KPI Handbook
version: 1.0.0
effective_date: 2026-10-20
document_type: kpi_definitions
origin: fictional_marvel
authorized_source: Team 1 authored fictional Marvel CPG document (fictional company documents are permitted by the capstone brief and the official client clarifications); definitions from the official client clarifications and the shared V3 data platform `metric_definitions`
permission_scope: sales, manager
status: draft_for_team_review
owner: Team 1 knowledge base (Sanika Najan)
data_basis: Authorized V3 dataset (dataset hash f5403651d8aa...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out
related_documents: MCPG-06, MCPG-09
---

# Commercial KPI Handbook

> Marvel CPG is a fictional company created for the University at Buffalo CDA 650 capstone. This document was written by Team 1 for the project's RAG knowledge base. Figures marked *data* come from the project dataset; everything else is fictional business content.

## 1 Purpose

This handbook defines the five commercial execution KPIs used across the platform, explains how they are calculated from the shared data layer, and gives the current baseline for each. Every module (dashboard, OOS risk engine, negotiation simulator and copilot) must use these definitions. The copilot must not invent a different numerator or denominator.

## 2 Rules that apply to every KPI

- **"Today" is 30 June 2026.** All current views and forward-looking figures are relative to that date.
- **Missing reports are Unknown, not zero.** About 8% of store-days are not reported. A missing day is not a day with zero sales or zero stock. KPIs count only known values, and the share of known values (coverage) is shown next to the KPI.
- **Three data states**: **Observed** (reported by the store), **Estimated** (reconstructed by an approved method, shown only in separate opt-in views and labelled Candidate) and **Unknown** (not reported, not estimated). The default view is reported data only. The last reporting date is shown instead of a confidence label.
- **Distributors are wholesale accounts.** Show their KPIs separately from retail (Modern Trade and Traditional Trade) KPIs.
- **Add up components, not percentages.** A KPI for a region or month is the sum of its numerators divided by the sum of its denominators. Never average daily percentages.
- **Baselines** below cover reported data from 1 January 2025 to 31 May 2026. June 2026 is reserved for testing the OOS risk model.

## 3 The five KPIs

### 3.1 MSL Compliance %

- **Definition:** stores where every MSL SKU has inventory above zero, divided by all stores in the filter, x 100. A store fails if even one MSL SKU is at zero. It is a store-level availability measure (MCPG-06, section 3).
- **Platform metric:** `msl_compliance` = compliant store-days / assessable store-days. A store-day is assessable only when the stock of every MSL SKU is known. Assessment coverage, a lower bound and an upper bound are published alongside it (`msl_assessment_coverage`, `msl_lower_bound`, `msl_upper_bound`), because Unknown is not the same as failure.
- **Baseline** *(data)*: 39.7% of reported store-days; 67.9% in Tier 1, 37.9% in Tier 2, 11.0% in Tier 3.

### 3.2 OOS Rate %

- **Definition:** store-SKU-days that ended out of stock, divided by all store-SKU-days in the filter, x 100. A SKU is out of stock when it ends the day with zero inventory. It can sell units during the day and still end the day out of stock.
- **Platform metric:** `oos_rate` = out-of-stock SKU-days / SKU-days with known inventory. Unknown days are excluded from the denominator and reported as coverage (`known_inventory_coverage`).
- **Baseline** *(data)*: 7.78% overall; by tier 2.85% (Tier 1), 7.23% (Tier 2), 15.08% (Tier 3); by channel 6.68% (Modern Trade), 8.90% (Traditional Trade), 8.28% (Distributors).

### 3.3 Sell-in vs Sell-out Gap %

- **Definition:** (sell-in units - sell-out units) / sell-out units x 100, with **both sides over the same time window**. A positive gap means Marvel shipped more than shoppers bought (risk of stock building up); a negative gap means less (risk of stock-outs).
- **Time alignment:** each sell-in invoice is dated on a Monday and covers the sell-out week that ended the day before. Traditional Trade accounts are invoiced about every two weeks, so compare over matching two-week or monthly windows. A store-SKU-week without an invoice counts as zero sell-in once the invoice data for that period is complete.
- **Platform metric:** `sell_in_out_gap` = comparable gap units / comparable sell-out units, using an identical period, view and alignment.
- **Baseline** *(data, Team 1 V3 EDA section 7)*: Modern Trade +11.4%, Distributors +11.3%, Traditional Trade -44.9%, all accounts -2.1%. The all-account figure hides two opposite patterns, so always show the gap by channel.

### 3.4 Shelf Share %

- **Definition:** the average `shelf_share_pct` from execution audits, grouped by category, channel or region.
- **Platform metric:** `audited_sku_shelf_allocation`, the average visit-level allocation for one consistent SKU scope. In the current audits the shares of the SKUs in one category visit add up to 100%, so this is the split of space **among Marvel's audited SKUs**, not Marvel's share against competitors (MCPG-06, section 5).
- **Baseline** *(data)*: about 14.6% per audited SKU on average.

### 3.5 Planogram Compliance %

- **Definition:** audit rows where the SKU follows the planogram, divided by all audit rows in the filter, x 100. It measures shelf placement, not availability, and must never be confused with MSL compliance.
- **Platform metric:** `planogram_compliance` = compliant audit rows / audited rows, for one chosen SKU scope (do not combine overlapping scopes).
- **Baseline** *(data)*: 83.0% overall; by tier and channel in MCPG-06, section 4.2.

## 4 Other measures used in the platform

- **Trade discount %** (`trade_discount`): invoice discount value / invoice gross sales. It is a weighted average of trade discounts and does not include consumer promotions. Weighted invoice discount, invoice lines to 31 May 2026 *(data)*: Modern Trade 12.6%, Traditional Trade 5.7%, Distributors 15.6%.
- **Lost sales estimate:** on out-of-stock days, the store's normal in-stock daily sales for that SKU, valued at list price. *(data)* About 4.8% of potential sales overall (Tier 1 2.5%, Tier 2 6.5%, Tier 3 13.8%).
- **Delivery performance:** on time or early vs late against the scheduled delivery date, and fill rate (units delivered / units ordered). *(data)* Late deliveries: Modern Trade 11.8%, Traditional Trade 24.2%, Distributors 6.5%; fill rate about 99% in every channel.

## 5 OOS risk prediction and visit ranking

- **What is predicted:** whether a store-SKU will be out of stock on **any** of the next 7 days after the scoring date.
- **Minimum quality, fixed:** precision of at least 0.75, recall of at least 0.70 and ROC-AUC of at least 0.80. These thresholds cannot be lowered.
- **Visit ranking:** stores are ranked by the potential lost net sales at risk (units at risk x list price), giving priority to the highest OOS risk and the largest MSL gaps.
- **Until a model is released**, predictions and rankings are **unavailable**. The copilot must say "not available", never "0% risk".

## 6 Targets

Marvel has not confirmed targets for the five KPIs. The only fixed thresholds in the project are the OOS model's minimum precision, recall and AUC (section 5). Any target line shown in a dashboard is a team reference value until Marvel confirms it.

## Document control

| Item | Value |
|---|---|
| Document | MCPG-08, version 1.0.0 |
| Effective date | 2026-10-20 |
| Owner | Team 1 knowledge base (Sanika Najan) |
| Status | Draft for team review |
| Source | KPI definitions: capstone brief and official client clarifications |
| Source | Platform implementation: shared V3 data platform, `gold.metric_definitions` |
| Source | Baselines: project dataset, Authorized V3 dataset (dataset hash f5403651d8aa...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out; Team 1 V3 EDA sections 7 and 8 |

**Open points and review notes**

- The Traditional Trade sell-in gap stays at about -45% even with matched two-week and monthly windows; the reason is being confirmed with the client.
- The capstone brief states that 62% of stores comply with the planogram and the MSL. With the definitions above, the data shows 83.0% planogram compliance and 39.7% MSL compliance, so the 62% figure cannot be reproduced.
