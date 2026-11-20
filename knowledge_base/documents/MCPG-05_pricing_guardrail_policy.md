---
document_id: MCPG-05
title: Pricing, Discount and Commercial Guardrail Policy
version: 2.0.0
effective_date: 2026-11-20
document_type: policy
origin: fictional_marvel
authorized_source: Team 1 authored fictional Marvel CPG document (fictional company documents are permitted by the capstone brief and the official client clarifications); guardrail values from the V3 `dim_guardrails` table with a standard discount, adopted provisionally by the shared data platform (contract v3.sources.2); formulas from the official client clarifications
permission_scope: sales, manager
status: draft_for_team_review
owner: Team 1 knowledge base (Sanika Najan)
data_basis: Authorized V3 dataset, contract v3.sources.2 (dataset hash 6550fe85d890...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out
related_documents: MCPG-01, MCPG-04, MCPG-06, MCPG-07, MCPG-09
---

# Pricing, Discount and Commercial Guardrail Policy

> Marvel CPG is a fictional company created for the University at Buffalo CDA 650 capstone. This document was written by Team 1 for the project's RAG knowledge base. Figures marked *data* come from the project dataset; everything else is fictional business content.

## 1 Purpose and scope

This policy explains how Marvel prices to its accounts, how a trade discount affects margin, and which discount limits (commercial guardrails) apply to each channel and category. It applies to every trade discount a field representative proposes to a Modern Trade, Traditional Trade or Distributor account. It is the reference for the negotiation simulator (Module 3) and for the copilot, and it uses the same rules as the simulator.

**The rules are provisional.** The guardrail table and the way this policy reads it are the team's proposal and still need client confirmation (see Document control). Until then, every result in section 5 is a check against the guardrails, not permission to offer or agree a deal.

## 2 Price definitions

- **Trade list price**: the price Marvel charges the account before any trade discount. It is the base for every discount and margin calculation. Prices for all 23 SKUs are in MCPG-01, section 5.1.
- **Standard discount**: the discount every account in a channel gets on a category, with no commitment. It is the starting point of every proposal.
- **Additional discount**: what a representative proposes on top of the standard discount, in percentage points.
- **Total trade discount**: the standard discount plus the additional discount, taken off the list price on the invoice line. In this policy, "discount" on its own means this total.
- **Net price**: what the account actually pays per unit after the trade discount.
- **Unit cost**: Marvel's cost per unit, used only to calculate margin.
- **Reference consumer price**: the planning reference for the shelf price shoppers pay (1.30 to 1.40 times list). Retailers set their own shelf prices. It is not used in margin calculations.
- **Consumer promotion discount**: a price reduction for shoppers during a planned promotion (MCPG-07). It is separate from the trade discount and must never be added to it.
- **Recent invoiced discount**: the account's average trade discount on its recent invoices. It is history: it is not the standard discount and is never added to a proposal.

## 3 How to calculate net price and margin

```text
total discount % = standard discount % + additional discount %
net price        = list price x (1 - total discount % / 100)
gross margin %   = (net price - unit cost) / net price x 100
```

The minimum margin in the guardrails is a **gross margin on net price**, not a markup on cost.

**Worked example.** Marvel Power Clean Lemon 1L: list price $4.50, unit cost $2.10, in a Modern Trade store (Surface Cleaners: standard discount 9.64%, up to 5.03 points additional).

| Total discount | Net price | Gross margin |
|---|---|---|
| 0% (list price) | $4.50 | 53.3% |
| 9.64% (standard only) | $4.07 | 48.4% |
| 12.64% (standard + 3 points) | $3.93 | 46.6% |
| 14.67% (standard + the 5.03-point maximum) | $3.84 | 45.3% |
| 20% | $3.60 | 41.7% |

## 4 Guardrails by channel and category

For each channel and category the guardrails set four things: the **standard discount**; the **maximum additional discount** (also called the cap) that a representative can propose on top of it when the account makes the required execution commitment; the **minimum gross margin**; and that **commitment**. The standard discount plus the maximum additional discount is the highest total discount that does not need manager review.

| Channel | Category | Standard discount | Maximum additional | Highest total without manager review | Minimum margin | Commitment for an additional discount |
|---|---|---|---|---|---|---|
| Modern Trade | Dishwashing | 8.71% | 5.85 points | 14.56% | 22.15% | Achieve 100% MSL compliance + planogram adherence |
| Modern Trade | Laundry | 8.28% | 4.05 points | 12.33% | 25.70% | Secure +10% shelf share or place 1 secondary display |
| Modern Trade | Surface Cleaners | 9.64% | 5.03 points | 14.67% | 22.01% | Achieve 100% MSL compliance + planogram adherence |
| Traditional Trade | Dishwashing | 2.84% | 4.36 points | 7.20% | 27.60% | Secure +15% shelf share or place 1 secondary display |
| Traditional Trade | Laundry | 3.24% | 5.23 points | 8.47% | 26.61% | Secure +15% shelf share or place 1 secondary display |
| Traditional Trade | Surface Cleaners | 2.30% | 3.15 points | 5.45% | 28.61% | Guarantee 100% MSL compliance + 1 secondary display |
| Distributor | Dishwashing | 10.81% | 5.51 points | 10.81% (standard only) | 22.63% | Secure +15% shelf share or place 1 secondary display |
| Distributor | Laundry | 11.31% | 6.99 points | 11.31% (standard only) | 19.06% | Secure +10% shelf share or place 1 secondary display |
| Distributor | Surface Cleaners | 11.44% | 5.65 points | 11.44% (standard only) | 22.06% | Achieve 100% MSL compliance + planogram adherence |

### 4.1 Modern Trade: Dishwashing

- **Standard discount:** 8.71% off list price, with no commitment needed.
- **Additional discount:** up to 5.85 points more if the account makes the commitment below, so the highest total discount without manager review is 14.56%.
- **Execution commitment required for an additional discount:** Achieve 100% MSL compliance + planogram adherence.
- **Minimum gross margin:** 22.15% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Dishwashing SKUs is 48.5% to 49.9% at the standard discount and 44.9% to 46.5% at 14.56%. The minimum margin would only be reached at a total discount of about 40% to 41%.

### 4.2 Modern Trade: Laundry

- **Standard discount:** 8.28% off list price, with no commitment needed.
- **Additional discount:** up to 4.05 points more if the account makes the commitment below, so the highest total discount without manager review is 12.33%.
- **Execution commitment required for an additional discount:** Secure +10% shelf share or place 1 secondary display.
- **Minimum gross margin:** 25.70% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Laundry SKUs is 46.7% to 48.6% at the standard discount and 44.2% to 46.2% at 12.33%. The minimum margin would only be reached at a total discount of about 34% to 37%.

### 4.3 Modern Trade: Surface Cleaners

- **Standard discount:** 9.64% off list price, with no commitment needed.
- **Additional discount:** up to 5.03 points more if the account makes the commitment below, so the highest total discount without manager review is 14.67%.
- **Execution commitment required for an additional discount:** Achieve 100% MSL compliance + planogram adherence.
- **Minimum gross margin:** 22.01% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Surface Cleaners SKUs is 46.6% to 49.0% at the standard discount and 43.4% to 46.0% at 14.67%. The minimum margin would only be reached at a total discount of about 38% to 41%.

### 4.4 Traditional Trade: Dishwashing

- **Standard discount:** 2.84% off list price, with no commitment needed.
- **Additional discount:** up to 4.36 points more if the account makes the commitment below, so the highest total discount without manager review is 7.20%.
- **Execution commitment required for an additional discount:** Secure +15% shelf share or place 1 secondary display.
- **Minimum gross margin:** 27.60% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Dishwashing SKUs is 51.6% to 52.9% at the standard discount and 49.3% to 50.7% at 7.20%. The minimum margin would only be reached at a total discount of about 35% to 37%.

### 4.5 Traditional Trade: Laundry

- **Standard discount:** 3.24% off list price, with no commitment needed.
- **Additional discount:** up to 5.23 points more if the account makes the commitment below, so the highest total discount without manager review is 8.47%.
- **Execution commitment required for an additional discount:** Secure +15% shelf share or place 1 secondary display.
- **Minimum gross margin:** 26.61% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Laundry SKUs is 49.4% to 51.3% at the standard discount and 46.6% to 48.5% at 8.47%. The minimum margin would only be reached at a total discount of about 33% to 36%.

### 4.6 Traditional Trade: Surface Cleaners

- **Standard discount:** 2.30% off list price, with no commitment needed.
- **Additional discount:** up to 3.15 points more if the account makes the commitment below, so the highest total discount without manager review is 5.45%.
- **Execution commitment required for an additional discount:** Guarantee 100% MSL compliance + 1 secondary display.
- **Minimum gross margin:** 28.61% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Surface Cleaners SKUs is 50.6% to 52.9% at the standard discount and 48.9% to 51.3% at 5.45%. The minimum margin would only be reached at a total discount of about 32% to 36%.

### 4.7 Distributor: Dishwashing

- **Standard discount:** 10.81% off list price, with no commitment needed.
- **Additional discount:** the guardrail allows up to 5.51 points more with the commitment below, but any additional discount for a distributor goes to manager review (section 7).
- **Execution commitment required for an additional discount:** Secure +15% shelf share or place 1 secondary display.
- **Minimum gross margin:** 22.63% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Dishwashing SKUs is 47.2% to 48.7% at the standard discount. The minimum margin would only be reached at a total discount of about 39% to 41%.

### 4.8 Distributor: Laundry

- **Standard discount:** 11.31% off list price, with no commitment needed.
- **Additional discount:** the guardrail allows up to 6.99 points more with the commitment below, but any additional discount for a distributor goes to manager review (section 7).
- **Execution commitment required for an additional discount:** Secure +10% shelf share or place 1 secondary display.
- **Minimum gross margin:** 19.06% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Laundry SKUs is 44.8% to 46.8% at the standard discount. The minimum margin would only be reached at a total discount of about 40% to 42%.

### 4.9 Distributor: Surface Cleaners

- **Standard discount:** 11.44% off list price, with no commitment needed.
- **Additional discount:** the guardrail allows up to 5.65 points more with the commitment below, but any additional discount for a distributor goes to manager review (section 7).
- **Execution commitment required for an additional discount:** Achieve 100% MSL compliance + planogram adherence.
- **Minimum gross margin:** 22.06% of net price.
- **Headroom** *(calculated from list prices and unit costs)*: Marvel's gross margin on Surface Cleaners SKUs is 45.5% to 48.0% at the standard discount. The minimum margin would only be reached at a total discount of about 38% to 41%.

**What the headroom means.** In every channel and category, the highest total discount is reached long before the margin floor. In practice the **maximum additional discount is the rule that limits a proposal**. The margin floor protects against very deep discounts and against errors.

## 5 How a proposal is assessed

Every proposal gets one of five results. These are the same rules, checked in the same order, as the negotiation simulator (rules version m3-rules.v1-provisional).

| Result | When it applies | What happens next |
|---|---|---|
| **APPROVED** | No additional discount (standard discount only) and the gross margin is at or above the minimum | Within the guardrail, with no commitment needed |
| **APPROVED WITH CONDITIONS** | An additional discount up to and including the maximum, the account offers the required commitment, and the gross margin is at or above the minimum | Within the guardrail on condition the account delivers the commitment. Record it so a later audit can verify it |
| **MANAGER REVIEW** | An additional discount above the maximum with the gross margin still at or above the minimum, with or without a commitment; or any additional discount for a distributor | Prepare the proposal with its evidence for the regional manager (MCPG-09). Do not confirm it to the account |
| **REJECTED** | The gross margin is below the minimum; or the total discount is 100% or more; or an additional discount within the maximum comes without the required commitment | Do not offer it. Explain the limit and propose an alternative within policy |
| **CANNOT ASSESS** | A price, cost, standard discount, maximum, minimum margin or commitment is missing, or the additional discount is empty or negative | Fix the input or check the data. A missing value is never treated as zero |

**Examples** (Marvel Power Clean Lemon 1L, Modern Trade, Surface Cleaners: standard discount 9.64%, maximum additional discount 5.03 points, minimum margin 22.01%, commitment 100% MSL compliance + planogram adherence):

- Standard discount only (9.64% total): margin 48.4% → **APPROVED**.
- 3 points additional (12.64% total) and the account commits to 100% MSL compliance and planogram adherence: within the maximum, margin 46.6% → **APPROVED WITH CONDITIONS**.
- 3 points additional with no commitment → **REJECTED**.
- 6 points additional (15.64% total): above the 5.03-point maximum, margin 44.7% is still above the minimum → **MANAGER REVIEW**, with or without a commitment.
- 31 points additional (40.64% total): margin 21.4% is below the 22.01% minimum → **REJECTED**.

## 6 Execution commitments (trade-offs)

The guardrails name the commitment an account must make for an additional discount:

- **"or"** means any one option is enough: "Secure +15% shelf share or place 1 secondary display" is met by either.
- **"+"** joins parts that are all required: "Guarantee 100% MSL compliance + 1 secondary display" needs full MSL compliance **and** a secondary display; "Achieve 100% MSL compliance + planogram adherence" needs both as well.
- **"+10% / +15% shelf share"** means **percentage points**, not a relative increase: if the current share is 30%, +15% means 45%, not 34.5%.
- **"Secondary display"** is any off-shelf placement: an endcap, a free-standing island or a checkout display.
- **"100% MSL compliance + planogram adherence"** means every Must-Stock SKU is on the shelf and placed according to the planogram (MCPG-06).

A commitment must be verifiable from a later field audit (planogram compliance, shelf share and secondary display are all recorded in execution audits). Record the date of the latest audit with the proposal.

## 7 Distributors

Distributors are wholesale accounts that resell to smaller stores. They negotiate **volume** discounts, not shelf execution:

- Their standard discounts are higher than the retail ones: Dishwashing 10.81%, Laundry 11.31%, Surface Cleaners 11.44%.
- The guardrail table also names a maximum additional discount and a shelf commitment for distributors. Shelf commitments have little meaning for a depot and are not confirmed for distributors, so **any additional discount for a distributor goes to manager review**, whatever commitment is offered.
- Planned Distributor Volume Deals give 14% off with a minimum order of 200 units in set windows (MCPG-07).

## 8 What a representative may and may not do

- **May** propose the standard discount when the margin stays at or above the minimum.
- **May** propose an additional discount up to the maximum when the account offers the required commitment, and must record that commitment so an audit can check it.
- **May** prepare a proposal above the maximum, or any additional discount for a distributor, for manager review.
- **May not** confirm a proposal that needs manager review to the account before the review.
- **May not** add the account's recent invoiced discount to the standard discount, or combine a trade discount with a consumer promotion discount into one larger discount.
- **May not** treat a past invoice that exceeded a limit as a precedent. Past invoices are history, not approval.

## Document control

| Item | Value |
|---|---|
| Document | MCPG-05, version 2.0.0 |
| Effective date | 2026-11-20 |
| Owner | Team 1 knowledge base (Sanika Najan) |
| Status | Draft for team review |
| Source | Guardrail table `dim_guardrails` with a standard discount (delivered 16 October 2026), adopted provisionally by the shared V3 data platform (contract v3.sources.2; 9 channel x category rules) |
| Source | Margin formula and trade-off definitions: capstone brief and official client clarifications |
| Source | Decision rules: negotiation simulator (Module 3), rules version m3-rules.v1-provisional |
| Change | Version 2.0.0 replaces the earlier table (one maximum total discount per channel and category) with a standard discount plus an additional discount, the new minimum margins and commitments, and the simulator's five results |

**Open points and review notes**

- The platform marks the guardrails 'Requires review'. The client still has to confirm that the total discount is the standard plus the additional discount, that the maximum applies to the additional discount, that the minimum margin is a gross margin on net price, what representatives may agree on their own (Sales authority), and how distributor commitments work.
- The earlier guardrail table (one maximum total discount, contract v3.sources.1) is kept by the data platform as history only. It is not used here.
- In the V3 audits, shelf share is the allocation among Marvel's audited SKUs in a category visit (it sums to 100%). How a '+10/+15 points of shelf share' commitment is verified needs confirmation (MCPG-06, section 5).
