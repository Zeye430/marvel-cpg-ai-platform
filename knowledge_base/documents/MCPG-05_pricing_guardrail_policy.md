---
document_id: MCPG-05
title: Pricing, Discount and Commercial Guardrail Policy
version: 1.0.0
effective_date: 2026-10-20
document_type: policy
origin: fictional_marvel
authorized_source: Team 1 authored fictional Marvel CPG document (fictional company documents are permitted by the capstone brief and the official client clarifications); guardrail values from the retained V3 `dim_guardrails` table; formulas from the official client clarifications
permission_scope: sales, manager
status: draft_for_team_review
owner: Team 1 knowledge base (Sanika Najan)
data_basis: Authorized V3 dataset (dataset hash f5403651d8aa...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out
related_documents: MCPG-01, MCPG-04, MCPG-07, MCPG-09
---

# Pricing, Discount and Commercial Guardrail Policy

> Marvel CPG is a fictional company created for the University at Buffalo CDA 650 capstone. This document was written by Team 1 for the project's RAG knowledge base. Figures marked *data* come from the project dataset; everything else is fictional business content.

## 1 Purpose and scope

This policy explains how Marvel prices to its accounts, how a trade discount affects margin, and which discount limits (commercial guardrails) apply to each channel and category. It applies to every trade discount a field representative proposes to a Modern Trade, Traditional Trade or Distributor account. It is the reference for the negotiation simulator (Module 3) and for the copilot.

## 2 Price definitions

- **Trade list price**: the price Marvel charges the account before any trade discount. It is the base for every discount and margin calculation. Prices for all 23 SKUs are in MCPG-01, section 5.1.
- **Trade discount**: the percentage taken off the list price on the invoice. In this policy, "discount" means the **total trade discount off list price** on the invoice line.
- **Net price**: what the account actually pays per unit after the trade discount.
- **Unit cost**: Marvel's cost per unit, used only to calculate margin.
- **Reference consumer price**: the planning reference for the shelf price shoppers pay (1.30 to 1.40 times list). Retailers set their own shelf prices. It is not used in margin calculations.
- **Consumer promotion discount**: a price reduction for shoppers during a planned promotion (MCPG-07). It is separate from the trade discount and must never be added to it.

## 3 How to calculate net price and margin

```text
net price      = list price x (1 - discount % / 100)
gross margin % = (net price - unit cost) / net price x 100
```

The minimum margin in the guardrails is a **gross margin on net price**, not a markup on cost.

**Worked example.** Marvel Power Clean Lemon 1L: list price $4.50, unit cost $2.10.

| Proposed discount | Net price | Gross margin |
|---|---|---|
| 0% | $4.50 | 53.3% |
| 10% | $4.05 | 48.1% |
| 12.80% (Modern Trade cap) | $3.92 | 46.5% |
| 15% | $3.82 | 45.1% |

## 4 Guardrails by channel and category

The commercial guardrails set, for each channel and category, the maximum discount a representative can offer without extra approval, the minimum gross margin, and the execution commitment that can unlock a larger discount.

| Channel | Category | Maximum discount without extra approval | Minimum gross margin on net price | Execution commitment that can unlock a larger discount |
|---|---|---|---|---|
| Modern Trade | Dishwashing | 11.34% | 22.26% | Commit to featured endcap placement for 4 weeks |
| Modern Trade | Laundry | 13.30% | 25.50% | Secure +15% shelf share or place 1 secondary display |
| Modern Trade | Surface Cleaners | 12.80% | 26.62% | Secure +10% shelf share or place 1 secondary display |
| Traditional Trade | Dishwashing | 7.99% | 27.52% | Achieve 100% MSL compliance + planogram adherence |
| Traditional Trade | Laundry | 7.16% | 27.08% | Commit to featured endcap placement for 4 weeks |
| Traditional Trade | Surface Cleaners | 6.62% | 31.47% | Secure +10% shelf share or place 1 secondary display |
| Distributor | Dishwashing | 15.07% | 21.97% | Secure +15% shelf share or place 1 secondary display |
| Distributor | Laundry | 16.34% | 17.41% | Commit to featured endcap placement for 4 weeks |
| Distributor | Surface Cleaners | 17.80% | 23.45% | Achieve 100% MSL compliance + planogram adherence |

### 4.1 Modern Trade: Dishwashing

- **Maximum discount without extra approval:** 11.34% off list price.
- **Minimum gross margin:** 22.26% of net price.
- **Execution commitment that can unlock a larger discount:** Commit to featured endcap placement for 4 weeks.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Dishwashing SKUs is still 46.9% to 48.4%. The minimum margin would only be reached at a total discount of about 40% to 41%.

### 4.2 Modern Trade: Laundry

- **Maximum discount without extra approval:** 13.30% off list price.
- **Minimum gross margin:** 25.50% of net price.
- **Execution commitment that can unlock a larger discount:** Secure +15% shelf share or place 1 secondary display.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Laundry SKUs is still 43.6% to 45.6%. The minimum margin would only be reached at a total discount of about 34% to 37%.

### 4.3 Modern Trade: Surface Cleaners

- **Maximum discount without extra approval:** 12.80% off list price.
- **Minimum gross margin:** 26.62% of net price.
- **Execution commitment that can unlock a larger discount:** Secure +10% shelf share or place 1 secondary display.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Surface Cleaners SKUs is still 44.6% to 47.2%. The minimum margin would only be reached at a total discount of about 34% to 37%.

### 4.4 Traditional Trade: Dishwashing

- **Maximum discount without extra approval:** 7.99% off list price.
- **Minimum gross margin:** 27.52% of net price.
- **Execution commitment that can unlock a larger discount:** Achieve 100% MSL compliance + planogram adherence.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Dishwashing SKUs is still 48.9% to 50.3%. The minimum margin would only be reached at a total discount of about 35% to 37%.

### 4.5 Traditional Trade: Laundry

- **Maximum discount without extra approval:** 7.16% off list price.
- **Minimum gross margin:** 27.08% of net price.
- **Execution commitment that can unlock a larger discount:** Commit to featured endcap placement for 4 weeks.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Laundry SKUs is still 47.3% to 49.2%. The minimum margin would only be reached at a total discount of about 33% to 35%.

### 4.6 Traditional Trade: Surface Cleaners

- **Maximum discount without extra approval:** 6.62% off list price.
- **Minimum gross margin:** 31.47% of net price.
- **Execution commitment that can unlock a larger discount:** Secure +10% shelf share or place 1 secondary display.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Surface Cleaners SKUs is still 48.3% to 50.7%. The minimum margin would only be reached at a total discount of about 30% to 33%.

### 4.7 Distributor: Dishwashing

- **Maximum discount without extra approval:** 15.07% off list price.
- **Minimum gross margin:** 21.97% of net price.
- **Execution commitment that can unlock a larger discount:** Secure +15% shelf share or place 1 secondary display.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Dishwashing SKUs is still 44.6% to 46.2%. The minimum margin would only be reached at a total discount of about 40% to 41%.

### 4.8 Distributor: Laundry

- **Maximum discount without extra approval:** 16.34% off list price.
- **Minimum gross margin:** 17.41% of net price.
- **Execution commitment that can unlock a larger discount:** Commit to featured endcap placement for 4 weeks.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Laundry SKUs is still 41.5% to 43.6%. The minimum margin would only be reached at a total discount of about 41% to 43%.

### 4.9 Distributor: Surface Cleaners

- **Maximum discount without extra approval:** 17.80% off list price.
- **Minimum gross margin:** 23.45% of net price.
- **Execution commitment that can unlock a larger discount:** Achieve 100% MSL compliance + planogram adherence.
- **Headroom** *(calculated from list prices and unit costs)*: at the maximum discount, Marvel's gross margin on Surface Cleaners SKUs is still 41.3% to 44.0%. The minimum margin would only be reached at a total discount of about 37% to 40%.

**What the headroom means.** In every channel and category, the discount cap is reached long before the margin floor. In practice the **cap is the rule that limits a proposal**. The margin floor protects against very deep discounts and against errors.

## 5 How a proposal is assessed

Every proposal gets one of three results. These are the same rules the negotiation simulator applies.

| Result | When it applies | What happens next |
|---|---|---|
| **APPROVED** | Discount is at or below the cap **and** gross margin is at or above the minimum | The representative can agree it with the account |
| **MANAGER OVERRIDE REQUIRED** | Discount is above the cap, gross margin is still at or above the minimum, **and** the account commits to the required execution trade-off | Prepare the proposal with the evidence for regional manager review (MCPG-09). Do not confirm it to the account yet |
| **POLICY BREACH / REJECTED** | Gross margin falls below the minimum, **or** the discount is above the cap without the required commitment | Do not offer it. Explain the limit and propose an alternative within policy |

**Examples** (Marvel Power Clean Lemon 1L, Modern Trade, Surface Cleaners: cap 12.80%, minimum margin 26.62%):

- 10% discount: margin 48.1%, within the cap → **APPROVED**.
- 15% discount, account commits to +10 percentage points of shelf share or one secondary display: above the cap, margin 45.1% is above the floor → **MANAGER OVERRIDE REQUIRED**.
- 15% discount, no commitment → **POLICY BREACH / REJECTED**.

## 6 Execution commitments (trade-offs)

The guardrails name the commitment an account must make for a discount above the cap:

- **"+10% / +15% shelf share"** means **percentage points**, not a relative increase: if the current share is 30%, +15% means 45%, not 34.5%.
- **"Secondary display" and "featured endcap"** are the same thing. Any off-shelf placement counts: an endcap, a free-standing island or a checkout display.
- **"Featured endcap placement for 4 weeks"** means the display stays in place for four consecutive weeks.
- **"100% MSL compliance + planogram adherence"** means every Must-Stock SKU is on the shelf and placed according to the planogram (MCPG-06).

A commitment must be verifiable from a later field audit (planogram compliance, shelf share and secondary display are all recorded in execution audits). Record the date of the latest audit with the proposal.

## 7 Distributors

Distributors are wholesale accounts that resell to smaller stores. They negotiate **volume** discounts, not shelf execution:

- Distributor caps are higher than retail caps: Dishwashing 15.07%, Laundry 16.34%, Surface Cleaners 17.80%.
- Planned Distributor Volume Deals give 14% off with a minimum order of 200 units in set windows (MCPG-07).
- Shelf-share and planogram commitments have little meaning for a depot. Treat distributor proposals above the cap as manager review cases.

## 8 What a representative may and may not do

- **May** offer any discount up to the account's cap that keeps the margin at or above the minimum.
- **May** prepare a proposal above the cap when the account offers the required commitment, for manager review.
- **May not** confirm a discount above the cap to the account before the manager review.
- **May not** combine a trade discount with a consumer promotion discount into one larger discount.
- **May not** treat a past invoice that exceeded a limit as a precedent. Past invoices are history, not approval.

## Document control

| Item | Value |
|---|---|
| Document | MCPG-05, version 1.0.0 |
| Effective date | 2026-10-20 |
| Owner | Team 1 knowledge base (Sanika Najan) |
| Status | Draft for team review |
| Source | Guardrail table `dim_guardrails` as retained in the shared V3 data platform (9 channel x category rules) |
| Source | Margin formula and trade-off definitions: capstone brief and official client clarifications |
| Source | Decision rules: negotiation simulator (Module 3) status logic |

**Open points and review notes**

- The platform marks guardrails 'Requires review': approval logic above the cap, whether the minimum margin is an absolute floor, and whether the cap applies to the total or to an additional discount still need client confirmation. This policy uses total trade discount, as the simulator does.
- A revised guardrail table with a separate standard discount was supplied on 16 October 2026 but has not been approved, so it is not used here.
- In the V3 audits, shelf share is the allocation among Marvel's audited SKUs in a category visit (it sums to 100%). How a '+10/+15 points of shelf share' commitment is verified needs confirmation (MCPG-06, section 5).
