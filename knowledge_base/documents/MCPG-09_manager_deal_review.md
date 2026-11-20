---
document_id: MCPG-09
title: Regional Manager Guide: Deal Review, Escalation and Exception Management
version: 1.1.0
effective_date: 2026-11-20
document_type: manager_guide
origin: fictional_marvel
authorized_source: Team 1 authored fictional Marvel CPG document (fictional company documents are permitted by the capstone brief and the official client clarifications); review rules aligned with the negotiation simulator and the shared data platform; findings from the Team 1 V3 EDA
permission_scope: manager
status: draft_for_team_review
owner: Team 1 knowledge base (Sanika Najan)
data_basis: Authorized V3 dataset, contract v3.sources.2 (dataset hash 6550fe85d890...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out
related_documents: MCPG-05, MCPG-08
---

# Regional Manager Guide: Deal Review, Escalation and Exception Management

> Marvel CPG is a fictional company created for the University at Buffalo CDA 650 capstone. This document was written by Team 1 for the project's RAG knowledge base. Figures marked *data* come from the project dataset; everything else is fictional business content.

> **Access:** regional managers only. The copilot must not return this document to field representative accounts.

## 1 Purpose

This guide helps regional managers review discount proposals that need their decision, decide what to escalate, and find the stores and SKUs that need attention. It complements the pricing policy (MCPG-05), which representatives also use.

## 2 Reviewing a proposal sent for MANAGER REVIEW

A proposal reaches you when the additional discount is above the maximum for the account's channel and category and the gross margin is still at or above the minimum, or when a distributor asks for any additional discount. The commitment does not change this: above the maximum, a proposal comes to you with or without one. Check, in this order:

1. **Margin.** Recalculate the gross margin on net price with the reference list price and unit cost (MCPG-05, section 3). If it is below the minimum, it is not a review case: the simulator rejects it.
2. **The commitment matches the guardrail.** Above the maximum, expect at least the commitment named for the channel and category (for example "+10% shelf share or 1 secondary display" for Modern Trade Laundry). "+10%" means 10 percentage points; "or" means any one option, "+" means every part is required.
3. **The commitment can be verified.** Note the date of the latest audit for the store and SKU and what it showed (planogram, shelf share, secondary display). Agree when the next audit will confirm the commitment. Old audit evidence is not proof of current execution.
4. **Account context.** Look at the account's recent invoiced discount (28-day weighted; history only, never added to the standard discount), its stock position and its sell-in vs sell-out gap. Do not approve deeper discounts for an account that is already over-shipped.
5. **Record the decision** with the scenario's data version, rules version (m3-rules.v1-provisional), date, inputs and outcome. A saved scenario is a record of the decision, not an approval in the shared data.

**Past invoices are not precedent.** Some historical invoices carry a source flag for exceeding a limit, and some exceed the limits without a flag. Treat these flags as history about that invoice, not as approval or rejection of a new proposal.

## 3 What to escalate above regional level

- **Any proposal below the minimum gross margin.** The simulator rejects it. A guardrail cannot be breached without senior commercial approval, which is outside the regional manager's authority.
- **Distributor proposals well above the standard discount.** Distributors negotiate volume, not shelf execution, and their commitments are not confirmed, so the simulator sends any additional distributor discount to you. Escalate unless the proposal is a planned volume deal (MCPG-07).
- **Repeated proposals above the maximum for the same account** in a short period.

## 4 Finding exceptions

Use the dashboard with the KPI definitions in MCPG-08:

- **Tier 3 availability.** *(data)* Tier 3 accounts are out of stock on 15.1% of store-days and MSL compliant on only 11.0% of days. Their stock-outs are mostly a **reorder-timing** problem: orders are placed at a median of about 4 days of stock, 31% on an empty shelf, and 48% of restocks arrive after the shelf is already empty. Coach representatives to agree reorder points with these stores.
- **Traditional Trade deliveries.** *(data)* 24% of Traditional Trade deliveries arrive late (Modern Trade 12%, Distributors 6.5%). Late deliveries explain only about 10% of stock-out days, so fixing ordering matters more than chasing logistics.
- **Planogram in small stores.** *(data)* Traditional Trade Tier 3 planogram compliance is about 53%, against 95% in Modern Trade Tier 1.
- **Data freshness.** Check coverage and the last reporting date before acting on a KPI. A store with several Unknown days needs a visit to confirm its real stock, not a conclusion from missing data.

## 5 Coaching points for representatives

- Sell availability first, then execution; quote only traceable figures (MCPG-03, section 1).
- Do not promise sales uplifts from displays or planogram compliance. The data does not show one larger than about 1%.
- Use the substitution guide (MCPG-02) as a stop-gap, and fix the reorder point as the real solution.
- Never present a proposal that needs manager review as agreed before your review.

## 6 Known data limitations to keep in mind

- **Execution uplift.** Marvel's data was described as including a sales uplift from secondary displays (+8%), planogram compliance (+5%) and shelf share (+3% per 10 points). Measured on the data, these effects are at most about 1%. Treat any execution uplift in the simulator as a labelled assumption.
- **Guardrail version.** The platform and this guide use the guardrail table with a separate standard discount (delivered 16 October 2026), adopted provisionally (contract v3.sources.2). The client still has to confirm it and what representatives may agree on their own; until then every simulator result is provisional.
- **Shelf share** is an allocation among Marvel's audited SKUs, not a share against competitors (MCPG-06, section 5).
- **Deliveries on unreported days.** About 8% of deliveries land on days the store did not report. They never appear in the reported stock, which simply carries on after the gap. Reported stock is reliable on reported days.
- **Traditional Trade sell-in gap** of about -45% persists even with matched windows (MCPG-08, section 3.3).

## Document control

| Item | Value |
|---|---|
| Document | MCPG-09, version 1.1.0 |
| Effective date | 2026-11-20 |
| Owner | Team 1 knowledge base (Sanika Najan) |
| Status | Draft for team review |
| Source | Pricing policy MCPG-05 (version 2.0.0) and the negotiation simulator status rules (m3-rules.v1-provisional) |
| Source | Shared V3 data platform (Module 3 guidance, contract v3.sources.2: guardrails 'Requires review'; source flags are historical evidence) |
| Source | Team 1 V3 EDA (notebook 01_v3_eda): sections 7, 8.2, 8.4, 8.6, 8.7, 8.8 |

**Open points and review notes**

- Approval levels above the regional manager are a team proposal based on the brief's definition of a commercial guardrail ('cannot be violated without senior approval'). The full approval decision tree is still an open decision.
- Permission scope 'manager': the retrieval layer must filter this document out for field representative sessions.
