---
document_id: MCPG-06
title: MSL, Planogram and In-Store Execution Standards
version: 1.0.0
effective_date: 2026-10-20
document_type: policy
origin: fictional_marvel
authorized_source: Team 1 authored fictional Marvel CPG document (fictional company documents are permitted by the capstone brief and the official client clarifications); definitions from the capstone brief and official client clarifications; performance from the project dataset
permission_scope: sales, manager
status: draft_for_team_review
owner: Team 1 knowledge base (Sanika Najan)
data_basis: Authorized V3 dataset (dataset hash f5403651d8aa...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out
related_documents: MCPG-02, MCPG-05, MCPG-08, PUB-02
---

# MSL, Planogram and In-Store Execution Standards

> Marvel CPG is a fictional company created for the University at Buffalo CDA 650 capstone. This document was written by Team 1 for the project's RAG knowledge base. Figures marked *data* come from the project dataset; everything else is fictional business content.

## 1 Purpose

This document sets the in-store standards Marvel expects in its accounts: which SKUs must always be on the shelf, how products must be placed, how shelf share and secondary displays are recorded, and what a field representative does on a store visit. It also gives the current performance against each standard, so representatives know where the gaps are.

## 2 Account tiers and service model

Every account has a fixed tier that reflects its commercial potential (revenue, volume and brand visibility). Tier 1 is the highest priority.

| Tier | Profile | MSL expectation | Planned visit frequency |
|---|---|---|---|
| Tier 1 | High-volume accounts, large footprint | Full MSL enforcement | Weekly |
| Tier 2 | Mid-volume accounts | Selective MSL | Every two weeks |
| Tier 3 | Small accounts, often family-run stores | Partial assortment | Monthly or when needed |

Accounts by channel and tier *(data, dim_stores)*:

| Channel | Tier 1 | Tier 2 | Tier 3 | Total |
|---|---|---|---|---|
| Modern Trade | 41 | 35 | 24 | 100 |
| Traditional Trade | 16 | 39 | 30 | 85 |
| Distributor | 15 | 10 | 11 | 36 |

Tier assignment does not change over time. Distributors are wholesale accounts, not shops (section 8).

## 3 Must-Stock List (MSL)

### 3.1 The MSL SKUs

15 of the 23 SKUs are on the Must-Stock List. Every account is expected to keep them available on the shelf:

- **Surface Cleaners** (5 MSL): Marvel Power Clean Lemon 1L (SKU-1001); Marvel Power Clean Original 1L (SKU-1002); Marvel Heavy-Duty Degreaser 1L (SKU-1004); Marvel Glass & Surface Spray 32oz (SKU-1006); Marvel Multi-Surface Wipes 75ct (SKU-1008). Optional: Marvel Power Clean Lavender 750ml, Marvel Heavy-Duty Degreaser 500ml, Marvel Bathroom Foam Cleaner 20oz.
- **Dishwashing** (4 MSL): Marvel Dish Soap Citrus 28oz (SKU-1009); Marvel Dish Soap Original 28oz (SKU-1010); Marvel Dishwasher Pods 45ct (SKU-1012); Marvel Dish Soap Antibacterial 28oz (SKU-1015). Optional: Marvel Dish Soap Aloe 22oz, Marvel Dishwasher Pods 20ct, Marvel Dish Rinse Aid 8.45oz.
- **Laundry** (6 MSL): Marvel Laundry Detergent HE 100oz (SKU-1016); Marvel Laundry Detergent HE 50oz (SKU-1017); Marvel Laundry Pods 42ct (SKU-1018); Marvel Fabric Softener Lavender 64oz (SKU-1020); Marvel Stain Remover Spray 22oz (SKU-1022); Marvel Bleach Clean White 96oz (SKU-1023). Optional: Marvel Laundry Pods 20ct, Marvel Fabric Softener Fresh 64oz.

### 3.2 MSL compliance

A store is **MSL compliant** on a day when **every** MSL SKU has stock above zero at the end of that day. If even one MSL SKU is at zero, the store fails for that day. MSL compliance is about **availability**; it says nothing about where the product sits on the shelf.

*(data)* Over January 2025 to May 2026, stores were MSL compliant on 39.7% of reported store-days: 67.9% in Tier 1, 37.9% in Tier 2 and only 11.0% in Tier 3. With 15 MSL SKUs, one empty facing is enough to fail the day, which is why small accounts score so low.

### 3.3 Priorities for representatives

- Check MSL SKUs first on every visit, starting with Tier 3 accounts where compliance is lowest.
- When an MSL SKU is missing, agree an order and, for today, use the substitution guide (MCPG-02).
- Never remove an MSL SKU to make room for an optional one.

## 4 Planogram compliance

### 4.1 Definition

A **planogram** is the agreed shelf layout: which product goes where, with how many facings. A SKU is **planogram compliant** at an audit when it is placed as the planogram specifies. Planogram compliance measures **placement quality**, not availability. A store can have every MSL SKU in stock and still place them in the wrong position, and the reverse.

### 4.2 Current performance

*(data)* Planogram compliance across audited SKU visits is 83.0%. By tier and channel:

| Tier | Modern Trade | Traditional Trade | Distributor |
|---|---|---|---|
| Tier 1 | 95.2% | 87.5% | 86.9% |
| Tier 2 | 80.2% | 73.1% | 73.2% |
| Tier 3 | 61.1% | 52.9% | 54.0% |

Small-format stores have limited shelf space, less regular restocking and no dedicated shelf team, which is why Traditional Trade Tier 3 is the weakest group. Chains usually have planogram enforcement teams and retailer agreements.

## 5 Shelf share

**Shelf share** (`shelf_share_pct`) is recorded at each audit for each audited SKU. In the current audit data it is the **allocation of shelf space among the Marvel SKUs audited in that category during the visit**: the shares of the SKUs in one category visit add up to 100%. A SKU's share is therefore roughly 100 divided by the number of SKUs audited (about 14.6% on average).

How to read it:

- Use it to compare how space is split between Marvel SKUs, for example whether the degreaser gets a fair share of the Surface Cleaners space.
- It is **not** Marvel's share of the whole category shelf against competitors. The data has no competitor information.
- Compare shelf share only between visits with a similar number of audited SKUs.

## 6 Secondary displays

A **secondary display** is any placement off the main shelf: an endcap, a free-standing island or a checkout display. A "featured endcap" counts as a secondary display. It is recorded as present or absent at each audited SKU visit.

*(data)* Secondary displays were present at 49.0% of audited SKU visits. By tier and channel:

| Tier | Modern Trade | Traditional Trade | Distributor |
|---|---|---|---|
| Tier 1 | 60.6% | 59.1% | 68.8% |
| Tier 2 | 35.1% | 34.0% | 42.9% |
| Tier 3 | 16.8% | 16.6% | 25.8% |

Secondary displays and extra shelf share are the execution commitments that can unlock a discount above the cap (MCPG-05, section 6).

## 7 The store visit standard

**Before the visit**

1. Open the account in the dashboard: current stock-outs, MSL gaps, days since the last audit, and whether the latest data is Observed, Estimated or Unknown (MCPG-08).
2. Check open orders and expected deliveries.
3. Prepare one objective: fix an MSL gap, recover a planogram, agree an order, or present a proposal.

**In the store**

1. Check every MSL SKU on the shelf; note empty facings.
2. Check planogram placement for the main categories; reset where needed.
3. Record the audit: planogram compliance, shelf share and secondary display for each audited SKU.
4. Agree the order. Order before the shelf is empty: delivery takes 1 to 7 days depending on the channel (MCPG-02, section 8).

**After the visit**

1. Confirm the order and any commitment in writing.
2. Note follow-ups for the next visit, at the planned frequency for the account's tier.

## 8 Distributors

Distributors redistribute to smaller stores that are not in Marvel's account list. Their audits are depot or warehouse visits, so planogram compliance and shelf share mean much less for them. Report Distributor KPIs separately from retail KPIs (MCPG-08). Distributor negotiations are about volume (MCPG-05, section 7).

## Document control

| Item | Value |
|---|---|
| Document | MCPG-06, version 1.0.0 |
| Effective date | 2026-10-20 |
| Owner | Team 1 knowledge base (Sanika Najan) |
| Status | Draft for team review |
| Source | Store master `dim_stores` and product master `dim_products` |
| Source | Tier definitions, MSL and planogram definitions: capstone brief and official client clarifications |
| Source | Performance figures: project dataset, Authorized V3 dataset (dataset hash f5403651d8aa...), reported sell-out rows 1 Jan 2025 - 31 May 2026; June 2026 held out |

**Open points and review notes**

- The product master has one global MSL flag, while the tier model expects selective MSL for Tier 2 and a partial assortment for Tier 3. Whether MSL lists differ by channel or tier is an open decision; this document uses the global list.
- The shelf-share definition in section 5 follows the current audit data, where shares sum to 100% per category visit. It replaces the earlier reading of shelf share as Marvel against competitors and needs client confirmation.
