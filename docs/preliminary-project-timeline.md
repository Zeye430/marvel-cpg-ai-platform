# Marvel CPG Preliminary Project Timeline and Client Alignment Plan

## Purpose

This document provides a preliminary plan for team discussion. It is based on the current challenge brief, the initial data review, and the team's early understanding of the commercial problem. It is not a final commitment. Dates, scope, module ownership, and acceptance criteria should be revised after the client meeting on Friday, October 2, 2026.

The recommended delivery model is:

```text
Shared business and data foundation
→ Early end-to-end prototype
→ Parallel module development
→ Weekly integration
→ Evaluation, hardening, and presentation
```

The team should not complete Modules 1 through 4 strictly in sequence. It should also avoid four isolated module implementations. Each module needs a primary owner and a reviewer, while business definitions, data contracts, integration, and testing remain shared responsibilities.

## Current Understanding of the Product

Marvel needs one integrated commercial execution application for two user groups:

- Field Representatives, who need assigned-store visibility, visit prioritization, execution information, and negotiation support.
- Regional Managers, who need aggregated commercial performance, risk, and execution visibility.

The product contains four connected modules:

1. A commercial execution dashboard
2. A seven-day OOS risk model and store-visit ranking
3. A negotiation and price scenario simulator
4. A conversational data and product-knowledge assistant

The intended business flow is:

```text
Detect performance and execution problems
→ Predict risk and prioritize stores
→ Prepare a commercially valid customer proposal
→ Investigate and explain the evidence through natural language
```

## Known Data Findings Affecting Scope

The initial review found six structurally clean CSV files with no missing values, duplicate primary keys, or orphaned product and store references. However, several issues require client clarification:

- The daily sell-out panel covers only about 70% of calendar dates within each store-SKU series.
- Approximately 31% of candidate observations do not have an exact record seven calendar days later for the specified `t+7` target.
- The OOS flag is equivalent to zero inventory, but some zero-ending-inventory dates still have positive sales. Inventory may therefore represent an end-of-day measurement.
- No field-representative identifier or store-assignment table is provided.
- Sell-out contains no consumer price or revenue field.
- The historical association between the available sell-in price proxy and sell-out volume is too weak to support a strong causal elasticity claim.
- Sell-in occurs on weekly Monday dates, while sell-out is recorded on an incomplete daily panel.
- The guardrail table does not define an absolute conditional-discount ceiling above the normal discount limit.

These findings do not prevent development, but they affect labels, KPIs, role-based access, simulation logic, and the claims the team can make.

## Immediate Plan Before the Client Meeting

### Tuesday, September 29

- Confirm the shared understanding of the four modules.
- Share the industry research guide.
- Review the challenge brief and data dictionary as a team.
- Record individual questions without resolving them through unsupported assumptions.

### Wednesday, September 30

- Review the actual data findings.
- Draft KPI definitions for OOS, MSL compliance, sell-in versus sell-out gap, shelf share, and planogram compliance.
- Draft the proposed OOS target and time-split methodology.
- Draft the negotiation approval logic.
- Identify the minimum information required for user roles and RAG documents.
- Review the common product and AI system design guidance.
- Draft one low-fidelity user flow connecting all four modules.

### Thursday, October 1

- Consolidate and prioritize client questions.
- Prepare a one-page system flow for the meeting.
- Assign one speaker for business questions, one for data and model questions, and one for product and delivery questions.
- Rehearse a 10-minute explanation of the current understanding.
- Agree on who records decisions and action items during the meeting.

### Friday, October 2: Client meeting

- Validate the business workflow and intended users.
- Resolve the highest-impact data and metric ambiguities.
- Confirm MVP expectations and demo priorities.
- Confirm what external information may be used in the RAG knowledge base.
- Confirm deployment, LLM, security, and evaluation constraints.

### Within 24 hours after the meeting

- Publish a client-meeting decision log.
- Update the KPI dictionary.
- Update the requirements and acceptance criteria.
- Convert unresolved questions into named risks or action items.
- Confirm the first development milestone and temporary ownership.

## Proposed Client Meeting Agenda

Recommended duration: 45 to 60 minutes.

| Time | Topic | Desired outcome |
| --- | --- | --- |
| 5 minutes | Restate the business problem and users | Confirm that the team understands the product correctly |
| 10 minutes | User workflows and role-based views | Confirm Field Representative and Regional Manager decisions |
| 15 minutes | Data, KPI, and modeling questions | Resolve definitions that affect architecture and evaluation |
| 10 minutes | Simulator, NLQ, and RAG expectations | Confirm acceptable assumptions, sources, and demo behavior |
| 5 minutes | Delivery priorities and success criteria | Define the MVP and presentation priorities |
| 5 minutes | Decisions, owners, and next actions | End with an explicit action list |

## Priority Questions for the Client

### Must resolve before implementation is finalized

1. Does the OOS target mean OOS exactly on day `t+7`, or any OOS event during the next seven days?
2. What does a missing store-SKU-date row mean: no observation, no sale, no assortment, or another condition?
3. Is `inventory_level` measured before sales, after sales, or at another time of day?
4. How are stores assigned to Field Representatives? Can an assignment file be provided?
5. Should MSL compliance mean the percentage of available MSL observations, or the percentage of stores carrying every required MSL SKU?
6. Should the sell-in versus sell-out gap be calculated in units, dollars, or both?
7. How should weekly sell-in and daily sell-out periods be aligned?
8. Does proposed additional discount mean additional percentage points above the recent discount, or a new total discount?
9. When a proposal exceeds `max_discount_pct`, does satisfying `required_exec_tradeoff` permit any discount that remains above the minimum margin, or is there another approval ceiling?
10. Is it acceptable to use an assumed or sensitivity-based elasticity when consumer price history is unavailable?
11. May external industry research be included in the RAG knowledge base, or must the RAG corpus contain only documents derived from the supplied data?

### Product and demonstration questions

12. Which user workflow is most important for the final live demonstration?
13. What does “real-time visibility” mean for a static historical dataset?
14. Does the application need real authentication, or are role-selection demo accounts sufficient?
15. Should visit ranking prioritize expected lost sales, MSL gaps, OOS probability, or a client-defined weighted score?
16. Does the ranking need to recommend individual store-SKU actions or only a store-level visit order?
17. What explanation must accompany an OOS risk prediction?
18. Should the negotiation simulator use the latest observed sell-in price as the baseline price?
19. What should happen when an NLQ request is ambiguous or cannot be answered from the available data?
20. What evidence will be accepted as a correct RAG answer during the live evaluation?

### Technical and delivery questions

21. Are there restrictions on the LLM provider, external API use, internet access, or API cost?
22. Must the final application run entirely locally through Docker?
23. Is cloud deployment expected, optional, or prohibited?
24. Must the exported vector index be portable across team machines?
25. Are there required technologies beyond the options listed in the brief?
26. Are generated product sheets and sales argument documents acceptable if every factual statement is derived from the supplied data?
27. How should the client approve metric definitions and requirement changes after the meeting?

## Expected Client Meeting Outputs

The meeting should produce decisions, not only discussion. The team should leave with:

- Confirmed user personas and primary workflows
- Approved KPI definitions or named owners for unresolved definitions
- Approved OOS target definition
- An agreed interpretation of missing dates and inventory timing
- A plan for Field Representative store assignments
- Approved negotiation status logic
- An agreed approach to price elasticity
- Approved RAG source boundaries
- LLM and deployment constraints
- A prioritized MVP and optional stretch scope
- A list of client and team action items with due dates

## Preliminary Ten-Week Development Timeline

The timeline uses relative project weeks because the current course week and final calendar deadline should be confirmed.

### Week 1: Business alignment and requirements

Objectives:

- Complete common industry reading.
- Hold the client-alignment meeting.
- Finalize user personas and key workflows.
- Create a KPI and terminology dictionary.
- Record approved assumptions and unresolved risks.
- Define MVP and stretch scope.
- Define the primary user decision, success condition, and failure behavior for each module.
- Draft low-fidelity user flows before detailed UI implementation.

Exit criteria:

- Written decision log
- Prioritized requirements
- Agreed KPI definitions
- Initial acceptance criteria
- Temporary team ownership

### Week 2: Data foundation and EDA

Objectives:

- Build reproducible data ingestion and validation.
- Produce the EDA notebook.
- Define curated analytical views.
- Define time-grain and as-of join rules.
- Define role configuration and demonstration users.
- Create initial test cases for KPIs.
- Define shared module interfaces, ownership boundaries, error states, and mock outputs.
- Document the dataset using a lightweight dataset-card or datasheet format.

Exit criteria:

- Reproducible cleaned data
- Documented data-quality findings
- Approved analytical schemas
- Unit-tested KPI queries

### Week 3: Walking skeleton

Build a minimal end-to-end application before deep module development.

- Module 1: one KPI and one filtered visualization
- Module 2: one baseline risk model with a prediction output table
- Module 3: one discount scenario with guardrail validation
- Module 4: one approved NLQ and one RAG question
- Application: one navigation shell containing all four modules
- Product behavior: visible loading, empty, invalid-input, and unavailable-result states

Exit criteria:

- All four modules run from one application
- Data interfaces are proven
- Major integration risks are visible early

### Week 4: Parallel development cycle 1

- Expand dashboard KPIs and filters.
- Build leakage-safe OOS features and baseline comparisons.
- Implement full Scenario A margin and approval logic.
- Build Text-to-SQL validation and curated semantic views.
- Begin RAG document preparation.

Exit criteria:

- Dashboard covers all required KPIs in draft form
- Baseline model report exists
- Three negotiation scenarios can be compared
- NLQ produces validated read-only SQL

### Week 5: Parallel development cycle 2

- Add required dashboard visualization types and role filtering.
- Tune the OOS model and classification threshold.
- Implement store-visit ranking and explanations.
- Complete elasticity diagnostics and agree on the Scenario B approach.
- Build the first version of the vector index and citation-grounded answers.

Exit criteria:

- First feature-complete draft of Modules 1 and 2
- Scenario B has an approved method or documented limitation
- RAG answers cite source documents

### Week 6: Midpoint integration and client review

- Integrate the OOS predictions into the dashboard.
- Connect ranking outputs to the Field Representative workflow.
- Connect guardrail explanations to the assistant where appropriate.
- Conduct an internal end-to-end demo.
- Review progress with the client or instructor if available.

Exit criteria:

- One coherent workflow across modules
- Updated risk register
- Confirmed remaining MVP scope
- No unresolved data-interface ownership

### Week 7: Evaluation and reliability

- Lock the chronological train, validation, and test split.
- Evaluate model metrics by channel, region, and MSL status.
- Build the fixed 10-question NLQ evaluation set.
- Build the fixed 10-question RAG evaluation set.
- Add SQL safety, fallback behavior, and error messages.
- Validate negotiation boundary cases.
- Review the application against the human-AI interaction guidelines.
- Complete an initial LLM security review covering prompt injection, information disclosure, unsafe SQL, and excessive agency.

Exit criteria:

- Reproducible evaluation results
- No hidden test-set tuning
- Initial 20-question agent score
- Tested guardrail boundaries

### Week 8: Product completion

- Improve usability and consistent visual design.
- Add model explanations and business-language recommendations.
- Complete the knowledge base and RAG pipeline documentation.
- Complete installation and usage instructions.
- Complete Docker packaging.
- Complete the model card, dataset documentation, system diagram, and product decision-flow documentation.

Exit criteria:

- Feature-complete MVP
- Full local setup works from a clean environment
- Documentation covers all four modules

### Week 9: Hardening and final deliverables

- Run end-to-end regression tests.
- Verify all required metrics and filters.
- Resolve performance and caching issues.
- Finalize the model card and metrics report.
- Draft the pitch presentation.
- Draft the five-minute demo script.
- Update `CONTRIBUTIONS.md`.

Exit criteria:

- Release candidate
- Complete deliverable checklist
- Presentation and demo drafts

### Week 10: Final validation and presentation

- Freeze features unless a critical defect is found.
- Rehearse the exact live evaluation questions.
- Record the system demonstration video.
- Finalize the ten-slide presentation.
- Verify repository access for instructors.
- Tag the final release.

Exit criteria:

- Final repository submission
- Final application and model artifacts
- Final presentation and video
- Team prepared for live questions

## Proposed Team Operating Model

The team has three members and four modules. A strict one-person-per-module assignment is not possible without overloading one member, and a fully sequential approach would delay integration. A hybrid model is recommended.

### Shared responsibilities

All three members participate in:

- Industry and business understanding
- Requirements and KPI definitions
- Data-quality decisions
- Architecture and interface decisions
- Pull Request review
- Weekly integration
- Evaluation design
- Final presentation and demo

### Discussion option for primary ownership

This is a discussion proposal, not a final assignment.

| Member | Possible primary ownership | Required secondary responsibility |
| --- | --- | --- |
| Member A | Data foundation and Module 1 | Review Module 2 data and feature logic |
| Member B | Module 2 | Review Module 3 calculations and evaluation |
| Member C | Application integration and Module 4 | Review Module 1 user experience |
| Shared A and C | Module 3 | Member B reviews formulas and boundary tests |

An alternative is to rotate ownership of Module 3 after the Module 1 walking skeleton is complete. The team should choose the model after reviewing individual skills and workloads.

## Dependency Management

Module 1 does not need to be complete before Module 2 begins. Both depend on a shared data foundation, but their implementation can proceed in parallel.

| Downstream work | Required early contract | Temporary unblocker |
| --- | --- | --- |
| Dashboard risk ranking | Prediction output schema | Mock prediction table |
| OOS model | Target and feature definitions | Baseline target table |
| Negotiation UI | Guardrail input and output schema | Fixed test scenarios |
| NLQ assistant | Curated view schemas and KPI definitions | Approved sample questions and SQL |
| RAG assistant | Document format and source policy | Small approved pilot corpus |

Recommended OOS model output contract:

```text
prediction_date
store_id
sku_id
oos_probability
units_at_risk
expected_lost_sales
risk_reason
model_version
```

The dashboard can use mock records matching this schema before the final model is ready.

## Weekly Team Cadence

### Beginning of week

- Confirm the weekly milestone.
- Assign tasks with acceptance criteria.
- Identify dependencies and required reviewers.

### Midweek

- Conduct a short blocker and integration check.
- Update interfaces before they surprise downstream work.

### End of week

- Demonstrate working software, not only notebooks or slides.
- Merge reviewed work.
- Run the integrated application.
- Update the decision log, risk register, and next milestone.

## GitHub Workflow

- Keep `main` stable.
- Create one feature branch per focused task.
- Open small Pull Requests with a clear description and test evidence.
- Require another member to review each Pull Request.
- Link Pull Requests to GitHub Issues.
- Merge continuously rather than waiting until the end of a module.
- Avoid committing raw client data, secrets, or unapproved research PDFs.

## Preliminary Milestones

| Milestone | Target | Evidence |
| --- | --- | --- |
| M1: Requirements baseline | End of Week 1 | Decision log, KPI dictionary, MVP definition |
| M2: Data and analytics foundation | End of Week 2 | EDA, validated tables, curated views |
| M3: Integrated walking skeleton | End of Week 3 | Four minimal modules in one application |
| M4: Feature-complete draft | End of Week 6 | End-to-end business workflow |
| M5: Evaluation complete | End of Week 8 | Model report and 20-question agent results |
| M6: Release candidate | End of Week 9 | Docker build and completed documentation |
| M7: Final submission | End of Week 10 | Repository, slides, video, and final artifacts |

## Initial Risk Register

| Risk | Impact | Current mitigation |
| --- | --- | --- |
| Ambiguous OOS target | Invalid training labels and evaluation | Resolve with client before target is finalized |
| Missing daily observations | Incorrect zero assumptions and leakage | Treat as unknown until semantics are confirmed |
| No Field Representative assignments | Role-based view cannot be implemented from source data | Request mapping or approve a deterministic demo configuration |
| No consumer price history | Elasticity claims may be unsupported | Present diagnostics and request approval for sensitivity assumptions |
| Four modules and three members | Uneven workload and isolated development | Use shared foundation, primary owners, reviewers, and weekly integration |
| GenAI live evaluation | Unstable answers during presentation | Freeze test questions, add deterministic checks, and design fallbacks |
| Late integration | Modules fail when combined | Deliver a walking skeleton in Week 3 |
| External research restrictions | RAG corpus may violate the data-source rule | Confirm allowed sources with client or instructor |
| LLM/API availability | Agent may fail in the final environment | Confirm provider and build a tested fallback path |
| Repository confidentiality | Accidental exposure of client material | Keep the repository private and do not commit raw data without approval |

## Decisions for the Next Internal Team Meeting

The team should decide:

1. Who leads each part of the Friday client meeting?
2. Which questions are mandatory if meeting time is limited?
3. Which member temporarily owns business/KPI documentation?
4. Which member temporarily owns data and modeling feasibility?
5. Which member temporarily owns product workflow and GenAI questions?
6. Which technologies will be proposed, but not finalized, before client confirmation?
7. What is the smallest Week 3 walking skeleton?
8. How will Pull Request review and weekly integration work?
9. What evidence will be required before a task is considered complete?
10. When will module ownership be finalized?

## Definition of Done for Planning

This preliminary planning phase is complete when:

- The client has answered or assigned owners to the must-resolve questions.
- The team has documented KPI definitions and approved assumptions.
- The MVP and stretch scope are separated.
- Every module has a primary owner and reviewer.
- Shared data interfaces are defined.
- The Week 3 walking skeleton is agreed.
- The timeline has been updated with actual course dates.
