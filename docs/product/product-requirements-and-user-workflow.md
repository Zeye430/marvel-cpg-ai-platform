# Product Requirements and User Workflow

## Document Purpose

This document defines the initial product direction for the Marvel CPG AI Commercial Execution Platform. It describes the users, decisions, workflows, functional scope, and expected outcomes of the integrated application. It is a working draft for team and client review, not a final implementation contract.

The central product decision is to build one connected decision-support system rather than four independent demonstrations. The platform should help commercial users move from identifying a problem to selecting and executing an action.

## Product Objective

Build an integrated commercial execution platform that helps Marvel CPG field representatives and regional managers:

- Monitor commercial execution and product availability.
- Predict store-SKU out-of-stock risk during the next seven days.
- Prioritize store visits according to business impact.
- Evaluate discount and execution proposals against commercial guardrails.
- Query commercial data and product knowledge using natural language.

## Business Problems

### Reactive Out-of-Stock Management

Field representatives often discover stockouts only after arriving at a store. The platform should identify risk before the visit and focus field activity on stores where intervention can protect the most sales or close the most important MSL gaps.

### Inconsistent In-Store Execution

MSL availability, planogram compliance, shelf share, and secondary displays vary by channel, tier, and region. Managers need comparable KPIs and the ability to drill down from regional performance to a specific store and SKU.

### Unstructured Commercial Negotiation

Representatives need to understand whether a proposed discount preserves gross margin and satisfies the relevant commercial guardrails. The system should make the financial and execution trade-offs visible before a proposal is offered.

## Product Principles

- One application with a shared data layer and common navigation.
- Decision support rather than passive reporting.
- Store-SKU detail behind every aggregate KPI.
- Explicit distinction between Observed, Estimated, and Unknown data, with the last reporting date shown for freshness.
- Transparent model explanations and business formulas.
- Role-relevant information for field representatives and regional managers.
- Documented assumptions whenever the supplied data cannot establish a fact.

## User Personas

### Field Representative

The field representative manages a defined territory and needs to decide where to go, what problem to address, and what proposal to make.

Because the supplied data does not include representative-to-store assignments, the prototype will use documented synthetic territories and assignments approved for demonstration purposes.

Primary decisions:

- Which stores should I visit today?
- Which SKUs are currently unavailable or at risk?
- What is driving the risk?
- What replenishment or execution action should I discuss?
- What discount can I propose without violating a guardrail?
- What product argument or substitute should I use?

### Regional Manager

The regional manager monitors performance across stores and representatives and manages important exceptions.

Primary decisions:

- Which regions, channels, tiers, stores, or SKUs require attention?
- Are MSL, OOS, planogram, and shelf-share results improving?
- Which stores account for the greatest expected commercial impact?
- Which proposals require review or escalation?
- Where is data too stale or incomplete to support a confident decision?

## Current and Future Workflow Comparison

The current-state workflow is inferred from the supplied data and client explanation. It should be presented as a team hypothesis until the client confirms it.

### Field Representative Workflow

| Stage | Before the Platform | With the Platform | Expected Improvement |
| --- | --- | --- | --- |
| Start of day | Reviews spreadsheets, messages, recent visits, and personal experience. | Opens a territory-specific priority list with current OOS, predicted risk, MSL gaps, expected impact, and data freshness. | Less time assembling information and more consistent prioritization. |
| Select stores | Chooses stores mainly from routine visit cadence, urgent messages, or intuition. | Uses a ranked list and can filter by risk, tier, MSL urgency, or reporting freshness. | Visits are focused on preventable risk and commercial value. |
| Prepare for visit | Looks for product and sales information across multiple files. | Opens a Store 360 view with inventory, sales velocity, recent invoices, execution history, and risk drivers. | Better preparation and clearer visit objectives. |
| Inspect store | Identifies OOS or shelf issues during the visit and records an audit. | Confirms or corrects estimated status, reviews affected SKUs, and records execution findings. | The visit becomes both an intervention and a data-quality feedback event. |
| Negotiate | Calculates discounts manually or relies on experience. | Compares at least three discount and execution scenarios with price, margin, guardrail, and approval results. | More consistent and defensible commercial proposals. |
| Handle objections | Relies on memory, colleagues, or separate product documents. | Uses the commercial copilot for product substitutions, policy guidance, and objection-handling content with sources. | Faster, more consistent responses. |
| Follow up | Reports activity separately and may not see the later business result. | Returns to the store view and dashboard to monitor inventory, OOS, and execution outcomes. | Creates a measurable action-and-result loop. |

### Regional Manager Workflow

| Stage | Before the Platform | With the Platform | Expected Improvement |
| --- | --- | --- | --- |
| Review performance | Combines periodic reports and spreadsheets. | Opens a role-based dashboard with consistent KPI definitions and coverage indicators. | Faster identification of exceptions and trends. |
| Diagnose problems | Requests additional analysis or manually filters data. | Drills from region and channel to store, SKU, representative, and contributing risk factors. | Shorter path from symptom to cause. |
| Allocate field effort | Uses fixed visit cycles and manager judgment. | Reviews prioritized stores, MSL gaps, expected value at risk, and stale-data cases. | Field capacity is directed toward higher-impact work. |
| Review proposals | Applies commercial rules manually or after the fact. | Reviews proposals that exceed representative authority or require conditions. | More consistent governance, subject to the final approval policy. |
| Coach the team | Uses aggregate performance and anecdotal visit feedback. | Compares outcomes, risk drivers, execution gaps, and representative portfolios. | More specific coaching and resource allocation. |
| Ask ad hoc questions | Requests reports from analysts. | Uses controlled Text-to-SQL for approved analytical views. | Faster access to governed answers. |

## Swimlane Source Material

The following sequence can be converted into a one-page current-state and future-state swimlane diagram.

### Current State

```text
Store / Customer
  Experiences sales, inventory, and shelf-execution issues
        |
Field Representative
  Selects stores from routine, intuition, or urgent messages
  Visits store and discovers the issue
  Records an audit and negotiates manually
        |
Regional Manager
  Reviews delayed or aggregated reports
  Follows up on major exceptions
        |
Data and Reporting
  Receives partial POS, weekly invoice, and intermittent audit data
  Produces separate reports and spreadsheets
```

### Future State

```text
Data Platform
  Validates data, measures coverage, calculates KPIs, and scores OOS risk
        |
Regional Manager
  Reviews regional exceptions and field priorities
        |
Field Representative
  Receives ranked stores and opens Store 360
  Reviews recommended action and simulates commercial scenarios
        |
Store / Customer
  Discusses replenishment, assortment, execution, or price proposal
        |
Field Representative
  Records visit outcome and updated execution evidence
        |
Data Platform and Regional Manager
  Monitor results, data freshness, and unresolved exceptions
```

## Integrated Product Journey

```text
Commercial Dashboard
    -> OOS Risk and Visit Ranking
    -> Store 360 and Recommended Action
    -> Negotiation Simulator
    -> Commercial Copilot
    -> Outcome Monitoring
```

The Store 360 view is the connective layer. It should show:

- Store, channel, region, tier, and assigned representative.
- Latest observed or estimated inventory by SKU.
- MSL gaps and current OOS status.
- Seven-day OOS probability and main risk drivers.
- Recent sales velocity and weekly Sell-in signals.
- Latest execution audit and audit age.
- Units and trade value at risk.
- Recommended visit objective.
- Link to the negotiation simulator.
- Link to context-aware product and policy assistance.

## Functional Scope

### Module 1 Commercial Execution Analytics

Required capabilities:

- Display MSL Compliance, OOS Rate, Sell-in versus Sell-out Gap, Shelf Share, and Planogram Compliance.
- Filter by date, channel, region, tier, category, SKU, and representative where applicable.
- Provide a Field Representative view limited to assigned stores.
- Provide a Regional Manager view with aggregation and drill-down.
- Use at least four appropriate visualization types.
- Display reporting coverage and freshness alongside affected KPIs.

### Module 2 OOS Risk and Visit Planning

Required capabilities:

- Predict whether a store-SKU will experience any OOS event during the following seven calendar days.
- Meet the fixed minimum thresholds of precision 0.75, recall 0.70, and AUC-ROC 0.80.
- Explain important risk drivers at the store-SKU level.
- Rank stores for field visits using MSL urgency and expected commercial impact.
- Identify whether input data is Observed, Estimated, or Unknown and show the last reporting date.

### Module 3 Commercial Negotiation Simulator

Required capabilities:

- Calculate net price and gross margin from trade list price, proposed discount, and unit cost.
- Validate proposals against channel-category guardrails.
- Compare at least three combined scenarios.
- Display assumptions for price elasticity and execution uplift.
- Use a separate wholesale-oriented experience for Distributor accounts.
- Identify proposals requiring approval once the approval decision tree is confirmed.

### Module 4 Commercial Copilot

Required capabilities:

- Translate natural-language questions into read-only SQL over approved analytical views.
- Return a concise answer and an appropriate visualization.
- Answer product, policy, substitution, and objection-handling questions using RAG.
- Cite the retrieved knowledge source.
- Pass at least 80% of a 20-question evaluation set containing 10 NLQ and 10 RAG questions.

## Core User Stories and Initial Acceptance Criteria

| User story | Initial acceptance criterion |
| --- | --- |
| As a field representative, I want a prioritized store list so that I can plan today's visits. | The user can select a representative and see only assigned stores ranked with risk, impact, MSL gap, and data freshness. |
| As a field representative, I want to understand why a store is high risk. | The Store 360 view identifies affected SKUs and shows the principal observed or modeled risk drivers. |
| As a field representative, I want to compare proposals before negotiating. | At least three scenarios display discount, net price, margin, commitment, guardrail, and status side by side. |
| As a regional manager, I want to find commercial execution exceptions. | The dashboard supports drill-down from region or channel to affected stores and SKUs. |
| As a regional manager, I want to distinguish facts from estimates. | Affected views display observation coverage, the last reporting date, and Observed/Estimated/Unknown status. |
| As a user, I want to ask questions without knowing the schema. | The copilot answers approved questions using governed views and prevents write operations. |

## Navigation Proposal

1. Executive Overview
2. OOS Risk and Visit Planning
3. Negotiation Simulator
4. Commercial Copilot

Store 360 should be accessible from both the dashboard and visit-ranking pages.

## Nonfunctional Requirements

- The application must run through Docker or provide the required setup mechanism.
- Python dependencies must be reproducible through `uv` and the committed lock file.
- The application must use a common data and KPI layer.
- SQL generated by the copilot must be read-only and validated before execution.
- No credentials, API keys, or confidential raw files may be committed.
- Model training and evaluation must be reproducible.
- Time-dependent transformations must prevent future-data leakage.
- Important assumptions must appear in the README and the relevant UI.

## Out of Scope for the Course Prototype

- Production identity management and real user authentication.
- Live POS, ERP, distributor, or order-management integration.
- Automatic order placement or automatic approval of commercial proposals.
- A production-grade geographic routing engine.
- Causal proof of price elasticity or execution uplift.
- Replacement of physical inventory counts or field audits.

## Open Product Decisions

- Complete approval logic for discounts above `max_discount_pct`.
- Whether `min_target_margin` is an absolute floor or can be overridden.
- Whether a proposed discount is total discount or additional discount.
- Distributor volume thresholds and approval conditions.
- Invoice-to-physical-delivery timing.
- Audit validity or carry-forward period.
- Whether MSL requirements vary by channel or tier despite the global `is_msl` field.
- Final weighting of MSL urgency, OOS probability, units at risk, trade value, and store tier in visit ranking.

## Definition of Product Success

The final demonstration should support one coherent story:

1. A manager identifies an OOS or MSL problem in a region.
2. The user opens the prioritized store list.
3. The user selects a high-impact store and reviews the affected SKUs and risk drivers.
4. A field representative compares commercial scenarios for that store.
5. The copilot supplies a product, policy, or objection-handling answer with sources.
6. The user returns to the dashboard to understand the expected business and execution outcome.
