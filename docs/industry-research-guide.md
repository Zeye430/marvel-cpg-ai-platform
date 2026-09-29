# Marvel CPG Industry Research and Reading Guide

## Purpose

This guide gives the three-person project team a shared commercial foundation before development begins. Marvel operates in the consumer packaged goods industry, more specifically the fast-moving consumer goods and household care categories. The project covers surface cleaning, dishwashing, and laundry products.

All team members should understand the industry value chain, channel structure, field-sales workflow, retail execution metrics, and commercial decision logic. After completing the common reading, each member should study the material most relevant to the module they may lead. Module ownership is not yet assigned, so the labels Member A, Member B, and Member C are temporary.

External research should inform product design, metric definitions, analytical reasoning, and presentation context. It should not be treated as an additional project dataset or added to the RAG knowledge base unless the client or instructor confirms that this is allowed.

## Industry and Commercial Context

### Industry classification

Marvel fits within the following hierarchy:

```text
Consumer Packaged Goods
└── Fast-Moving Consumer Goods
    └── Household Care
        ├── Surface Cleaning
        ├── Dishwashing
        └── Laundry Care
```

Household care products are recurring-purchase products sold through retail channels. Commercial success depends on consumer demand and on the manufacturer's ability to secure distribution, maintain availability, execute the correct assortment and planogram, obtain sufficient shelf visibility, and invest in pricing and promotions without destroying margin.

### Value chain

```text
Manufacturer
    │
    │ Sell-in
    ▼
Distributor or retail chain
    │
    │ Replenishment, distribution, and shelf execution
    ▼
Retail store
    │
    │ Sell-out
    ▼
Shopper and consumer
```

- **Customer** refers to the distributor, retailer, or store that buys from Marvel.
- **Consumer** refers to the person who ultimately purchases and uses the product.
- **Sell-in** measures shipments or invoiced sales from Marvel to a distributor or retail chain.
- **Sell-out** measures purchases made by end consumers at the point of sale.
- A positive sell-in versus sell-out gap may indicate inventory accumulation rather than healthy consumer demand.
- Sell-out above sell-in may indicate inventory depletion and increasing replenishment risk.

### Commercial execution funnel

The team should understand the project as a connected commercial funnel:

```text
Distribution
→ Correct assortment and MSL presence
→ On-shelf availability
→ Planogram compliance
→ Shelf visibility and secondary displays
→ Consumer purchase
→ Sell-out
→ Replenishment and new sell-in
```

The four application modules support different decisions within this funnel:

| Module | Primary business decision |
| --- | --- |
| Module 1 | Where are commercial performance and execution problems occurring? |
| Module 2 | Which stores and SKUs are most likely to go out of stock, and where should a representative visit first? |
| Module 3 | Is a proposed discount financially acceptable, and what execution commitment should be required? |
| Module 4 | How can users investigate the data and product knowledge without knowing the schema or SQL? |

## Common Reading Required for All Members

Every team member should complete the common reading before module ownership is finalized. The target is approximately six to eight hours per person, followed by a team discussion.

### 1. Consumer goods categories and business models

#### P&G annual report portfolio overview

- **Source:** P&G annual report, portfolio of daily-use categories
- **Link:** [P&G Portfolio of Daily-Use Categories](https://us.pg.com/annualreport2024/a-portfolio-of-daily-use-categories-where-performance-drives-brand-choice/)
- **Why it matters:** Shows how a large consumer goods manufacturer organizes Fabric Care and Home Care within a broader CPG portfolio. It also introduces the relationship among product performance, consumer value, price tiers, brand choice, and innovation.
- **Reading focus:** Category structure, price tiers, consumer value, brand portfolio, and product superiority.

#### Unilever Home Care business review

- **Source:** Unilever Annual Report and Accounts 2025
- **Link:** [Unilever Annual Report and Accounts](https://www.unilever.com/investors/annual-report-and-accounts/)
- **Why it matters:** Provides a current example of how a major CPG company describes Home Care categories, category growth, innovation, volume, pricing, margin, distribution, and operational strategy.
- **Reading focus:** Home Care category definition, Fabric Cleaning, Fabric Enhancers, Home and Hygiene, category growth, volume, pricing, and execution.

#### Clorox business and category portfolio

- **Source:** The Clorox Company investor portfolio
- **Link:** [Clorox Business Snapshot](https://investors.thecloroxcompany.com/company-information/portfolio/default.aspx)
- **Why it matters:** Provides a focused view of household cleaning brands and explains how a major cleaning-products company groups its categories and evaluates consumer value.
- **Reading focus:** Cleaning category structure, brand leadership, consumer value, and portfolio management.

### 2. Channel management and route to market

#### Global customer and channel management

- **Source:** McKinsey & Company
- **Link:** [Global Customer and Channel Management](https://www.mckinsey.com/industries/consumer-packaged-goods/our-insights/global-customer-and-channel-management-what-the-best-cpg-companies-do)
- **Why it matters:** Explains how CPG companies manage customers, channels, revenue growth, route-to-market models, store execution, and field-sales capabilities.
- **Reading focus:** Outlet-specific execution, channel differences, store segmentation, sales-force tools, customer management, and cost to serve.

#### Commercial excellence in consumer goods

- **Source:** McKinsey & Company
- **Link:** [Four Keys to Winning in Europe's Consumer Goods Market](https://www.mckinsey.com/industries/consumer-packaged-goods/our-insights/leading-european-consumer-goods-with-commercial-excellence)
- **Why it matters:** Discusses outlet execution, field-sales productivity, store segmentation, data use, and the connection between merchandising execution and promotion performance.
- **Reading focus:** How data changes store prioritization, sales visits, and field execution.

#### Consumer goods distribution and channel requirements

- **Source:** Strategy& / PwC
- **Link:** [Reshaping the Future of Consumer Goods Distribution](https://www.strategyand.pwc.com/m1/en/strategic-foresight/sector-strategies/consumer-retail/reshaping-the-future-of-consumer-goods/consumer-goods.pdf)
- **Why it matters:** Explains why Modern Trade requires strong key-account capabilities while Traditional Trade requires route-to-market discipline, store coverage, SKU selection, and field execution.
- **Reading focus:** Modern Trade, Traditional Trade, distributors, channel economics, distribution coverage, and service models.

### 3. Out-of-stock and retail execution

#### Retail out-of-stock reduction

- **Source:** Thomas W. Gruen and Daniel Corsten
- **Link:** [A Comprehensive Guide to Retail Out-of-Stock Reduction](https://www.dataventures.com/docs/OOS_execsummary.pdf)
- **Why it matters:** Provides a business explanation of OOS causes, consumer responses, process failures, and manufacturer-retailer responsibilities.
- **Reading focus:** OOS definitions, root causes, measurement, consumer substitution, replenishment, shelf execution, and lost sales.

#### Shelf space and planogram design

- **Source:** European Journal of Operational Research
- **Link:** [Joint Shelf Design and Shelf Space Allocation Problem for Retailers](https://www.sciencedirect.com/science/article/pii/S030504832200041X)
- **Why it matters:** Connects limited shelf capacity, assortment, product placement, planograms, revenue, and profit.
- **Reading focus:** Shelf-space constraints, product families, facings, visibility, assortment, and optimization.

#### Planogram maintenance and monitoring

- **Source:** Sensors, open-access research article
- **Link:** [Embedded Vision Sensor Network for Planogram Maintenance](https://pmc.ncbi.nlm.nih.gov/articles/PMC4610585/)
- **Why it matters:** Explains what a planogram represents and why actual shelf layouts frequently differ from the intended design.
- **Reading focus:** Product position, relative placement, category space, SKU space, compliance, and monitoring.

### 4. Revenue growth management and trade promotion

#### Revenue growth management fundamentals

- **Source:** McKinsey & Company
- **Link:** [Revenue Growth Management: The Next Horizon](https://www.mckinsey.com/capabilities/growth-marketing-and-sales/our-insights/revenue-growth-management-the-next-horizon)
- **Why it matters:** Introduces the four major RGM levers: pricing, promotions, assortment, and trade investment.
- **Reading focus:** Pricing decisions, promotion effectiveness, assortment, pack-price architecture, and trade-spend allocation.

#### Sales promotions and channel coordination

- **Source:** Journal of the Academy of Marketing Science
- **Link:** [Sales Promotions and Channel Coordination](https://link.springer.com/article/10.1007/s11747-009-0161-1)
- **Why it matters:** Explains why manufacturers and retailers may optimize their own profit separately and produce a suboptimal total-channel outcome.
- **Reading focus:** Manufacturer-retailer incentives, promotion profitability, coordination, and commercial negotiation.

#### Trade spend and promotion profitability

- **Source:** Journal of Economics and Management Strategy
- **Link:** [Trade Spends and Profitability of Promotions](https://onlinelibrary.wiley.com/doi/full/10.1111/jems.12525)
- **Why it matters:** Examines how manufacturers finance retailer discounts and advertising and how trade-spend mechanisms affect manufacturer and retailer profit.
- **Reading focus:** Trade funds, wholesale price, retail promotion, advertising support, and profit allocation.

### 5. Retail demand forecasting

#### M5 forecasting competition findings

- **Source:** International Journal of Forecasting
- **Link:** [M5 Accuracy Competition: Results, Findings, and Conclusions](https://doi.org/10.1016/j.ijforecast.2021.11.013)
- **Why it matters:** Uses large-scale retail item-store data and explains the value of global models, machine learning, forecast combinations, hierarchy, and out-of-time evaluation.
- **Reading focus:** Retail forecasting, intermittent demand, SKU-store granularity, time-based testing, and the relationship between forecast accuracy and inventory decisions.

### 6. Product and AI system design

These sources are required for all members because the deliverable is an integrated decision-support product, not a collection of disconnected analyses. The team should understand user needs, graceful failure, system boundaries, testing, and trust before choosing detailed architecture.

#### People and AI Guidebook

- **Source:** Google People + AI Research
- **Link:** [People + AI Guidebook](https://pair.withgoogle.com/guidebook-v2/)
- **Why it matters:** Provides practical methods for identifying user needs, defining success, setting expectations, explaining AI behavior, designing user control, and handling errors.
- **Reading focus:** User needs, mental models, explainability, feedback, control, and graceful failure.

#### Guidelines for Human-AI Interaction

- **Source:** Microsoft Research, CHI 2019
- **Link:** [Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/project/guidelines-for-human-ai-interaction/)
- **Paper:** [Full paper PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf)
- **Why it matters:** Defines 18 validated design guidelines for AI systems before, during, and after an interaction, including how the product should behave when the AI is wrong.
- **Reading focus:** Setting expectations, showing relevant context, supporting correction, communicating uncertainty, and learning from user feedback.

#### Hidden technical debt in machine learning systems

- **Source:** NeurIPS 2015
- **Link:** [Hidden Technical Debt in Machine Learning Systems](https://research.google/pubs/hidden-technical-debt-in-machine-learning-systems/)
- **Paper:** [Full paper PDF](https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf)
- **Why it matters:** Explains why a successful notebook is not the same as a maintainable ML product. It covers data dependencies, feedback loops, configuration debt, undeclared consumers, and system-level failure modes.
- **Reading focus:** Data contracts, pipeline ownership, reproducibility, hidden dependencies, and integration risk.

#### Streamlit architecture and application behavior

- **Source:** Official Streamlit documentation
- **Link:** [Streamlit Architecture and Execution Model](https://docs.streamlit.io/develop/concepts/architecture)
- **Additional link:** [Streamlit Multipage Applications](https://docs.streamlit.io/develop/concepts/multipage-apps)
- **Why it matters:** Helps the team design one integrated application with predictable state, caching, navigation, and server-client behavior.
- **Reading focus:** Application reruns, caching, session state, forms, multipage navigation, and deployment constraints.

## Module-Specific Reading

Module ownership is not assigned yet. The team should use this section to select deeper reading after the common foundation is complete.

### Module 1: Commercial Execution Analytics Dashboard

The future owner should study:

- Retail execution KPI definitions
- Store and channel segmentation
- On-shelf availability and MSL measurement
- Planogram and shelf-share measurement
- Sell-in versus sell-out analysis
- Role-based dashboard design
- Decision-oriented data visualization

Recommended sources:

1. [Global Customer and Channel Management](https://www.mckinsey.com/industries/consumer-packaged-goods/our-insights/global-customer-and-channel-management-what-the-best-cpg-companies-do)
2. [Four Keys to Winning in Europe's Consumer Goods Market](https://www.mckinsey.com/industries/consumer-packaged-goods/our-insights/leading-european-consumer-goods-with-commercial-excellence)
3. [A Comprehensive Guide to Retail Out-of-Stock Reduction](https://www.dataventures.com/docs/OOS_execsummary.pdf)
4. [Estimation of Product Category Sales Responsiveness to Allocated Shelf Space](https://www.sciencedirect.com/science/article/pii/S0167811698000184)
5. [10 Best Practices for Building Effective Dashboards](https://www.tableau.com/learn/whitepapers/building-effective-dashboards-it-user)
6. [Tableau Visual Best Practices](https://help.tableau.com/current/blueprint/en-gb/bp_visual_best_practices.htm)
7. [People + AI Guidebook: User Needs and Defining Success](https://pair.withgoogle.com/guidebook-v2/chapters)

Expected learning output:

- A proposed KPI dictionary
- A diagram of Field Representative and Regional Manager decisions
- A list of dashboard questions each visualization must answer
- A low-fidelity dashboard wireframe for both user roles
- A definition of loading, empty, stale-data, and unavailable-result states

### Module 2: Predictive Out-of-Stock Risk Engine

The future owner should study:

- OOS root causes and censored demand
- Store-SKU demand forecasting
- Time-based validation and leakage prevention
- Lag and rolling-window features
- Class imbalance and decision thresholds
- Probability calibration
- Business-value ranking
- Model explainability

Recommended sources:

1. [M5 Accuracy Competition: Results, Findings, and Conclusions](https://doi.org/10.1016/j.ijforecast.2021.11.013)
2. [A Comprehensive Guide to Retail Out-of-Stock Reduction](https://www.dataventures.com/docs/OOS_execsummary.pdf)
3. [M5 Competition Uncertainty and Distributional Forecasting](https://www.sciencedirect.com/science/article/pii/S0169207021001527)
4. [Scalable Probabilistic Forecasting in Retail with Gradient Boosted Trees](https://arxiv.org/abs/2311.00993)
5. [Rules of Machine Learning](https://developers.google.com/machine-learning/guides/rules-of-ml)
6. [The ML Test Score](https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/)
7. [Model Cards for Model Reporting](https://research.google/pubs/model-cards-for-model-reporting/)
8. [Datasheets for Datasets](https://www.microsoft.com/en-us/research/publication/datasheets-for-datasets/)

Expected learning output:

- A target-definition proposal
- A leakage-safe validation design
- A first feature list
- A business formula for store-visit priority
- A model card outline and dataset documentation plan
- A test plan covering data, features, model behavior, and output contracts

### Module 3: Commercial Negotiation Simulator

The future owner should study:

- Revenue growth management
- Gross and net margin calculations
- Trade promotion and trade spend
- Manufacturer-retailer incentives
- Price elasticity and promotion lift
- Price-pack architecture
- Cannibalization and cross-SKU effects
- Scenario simulation and commercial guardrails

Recommended sources:

1. [Revenue Growth Management: The Next Horizon](https://www.mckinsey.com/capabilities/growth-marketing-and-sales/our-insights/revenue-growth-management-the-next-horizon)
2. [Sales Promotions and Channel Coordination](https://link.springer.com/article/10.1007/s11747-009-0161-1)
3. [Trade Spends and Profitability of Promotions](https://onlinelibrary.wiley.com/doi/full/10.1111/jems.12525)
4. [Determinants of Store-Level Price Elasticity](https://journals.sagepub.com/doi/10.1177/002224379503200104)
5. [Varying Parameter Models to Accommodate Dynamic Promotion Effects](https://doi.org/10.1016/S0304-4076%2898%2900063-3)
6. [Estimating Promotional Effects with Retailer-Level Scanner Data](https://www.ftc.gov/sites/default/files/documents/reports/estimating-promotional-effects-retailer-level-scanner-data/wp264_0.pdf)
7. [People + AI Guidebook](https://pair.withgoogle.com/guidebook-v2/)
8. [Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/project/guidelines-for-human-ai-interaction/)

Expected learning output:

- A margin and approval-logic specification
- A list of assumptions requiring client approval
- A defensible elasticity-analysis plan
- A three-scenario comparison design
- A wireframe showing inputs, calculated outputs, assumptions, and approval reasons
- A design for invalid inputs, boundary values, missing evidence, and uncertain elasticity

### Module 4: Conversational GenAI Agent

The future owner should study:

- Text-to-SQL architecture
- Semantic layers and business metric definitions
- SQL validation and read-only execution
- RAG document design
- Chunking and metadata
- Embedding and retrieval
- Citation-grounded generation
- Hallucination controls
- Deterministic evaluation

Recommended sources:

1. [Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks](https://arxiv.org/abs/2005.11401)
2. [Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL](https://aclanthology.org/D18-1425/)
3. [BIRD: A Benchmark for Text-to-SQL in Realistic Big Data Environments](https://arxiv.org/abs/2305.03111)
4. [RAGAS: Automated Evaluation of Retrieval-Augmented Generation](https://arxiv.org/abs/2309.15217)
5. [OWASP Top 10 for Large Language Model Applications](https://owasp.org/projects/top-10-for-large-language-model-applications)
6. [OWASP Top 10 for LLM Applications 2025 PDF](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)
7. [NIST Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
8. [People + AI Guidebook: Explainability, Feedback, and Graceful Failure](https://pair.withgoogle.com/guidebook-v2/chapters)

Expected learning output:

- A safe Text-to-SQL execution flow
- A proposed semantic layer
- A RAG document and metadata design
- A fixed evaluation plan for ten NLQ and ten RAG questions
- A threat model covering prompt injection, sensitive information disclosure, excessive agency, and unsafe SQL
- A product design for clarification, refusal, citations, corrections, and insufficient-evidence responses

## Product and System Design Questions

The team should answer these questions before detailed implementation:

1. What decision should the user make on each page?
2. What information must appear before the user can make that decision?
3. Which outputs are facts, calculations, model predictions, assumptions, or generated explanations?
4. How will the interface distinguish missing data from a real zero?
5. How will users understand why a store or SKU received a high-risk score?
6. What happens when the model is uncertain or an expected source record is missing?
7. What happens when the simulator receives an invalid or commercially impossible scenario?
8. What happens when the NLQ agent generates invalid, unsafe, or ambiguous SQL?
9. How will an RAG answer show its sources and communicate insufficient evidence?
10. Which components own each calculation, and which components only display results?
11. How will all modules use the same metric definitions and data contracts?
12. What must be tested before the application is considered demonstration-ready?

## Proposed Temporary Reading Allocation

This allocation is intended only to prepare for role selection. It does not finalize development ownership.

| Member | Common responsibility | Temporary deeper-reading focus | Team briefing |
| --- | --- | --- | --- |
| Member A | Complete all common reading | Channel management, commercial execution, and dashboard KPIs | Explain the CPG value chain and retail-execution funnel |
| Member B | Complete all common reading | OOS, demand forecasting, model evaluation, and store prioritization | Explain OOS causes, target design, and leakage risks |
| Member C | Complete all common reading | Revenue growth management, negotiation, Text-to-SQL, and RAG | Explain discount logic, elasticity limits, and agent architecture |

Because there are three members and four modules, Module 3 and Module 4 should not automatically be assigned together. The final assignment should consider workload, technical strengths, and integration responsibilities after the client meeting.

## Reading and Discussion Process

### Individual reading note

For each assigned source, create a short note with the following structure:

```markdown
# Source title

## Citation and link

## Business problem

## Industry context

## Data and method

## Main findings

## Connection to Marvel CPG

## Useful KPIs or variables

## Limitations

## Questions for the client
```

### Team discussion questions

After the common reading, the team should be able to answer:

1. How do manufacturer, distributor, retailer, shopper, and consumer interests differ?
2. Why can strong sell-in coexist with weak consumer demand?
3. What operational problems can cause an OOS event?
4. What is the difference between MSL availability and full-store MSL compliance?
5. Why should a store visit be ranked by expected commercial impact rather than risk probability alone?
6. What should Marvel receive in exchange for an additional discount?
7. When does a promotion increase incremental profit rather than only unit volume?
8. What data is required for a defensible price-elasticity estimate?
9. Which metrics can be computed directly from the supplied data, and which require assumptions?
10. Which external research sources, if any, may be used in the RAG knowledge base?

## Reference Management and Copyright

- Do not commit paper PDFs to the repository by default.
- Store stable links, DOI links, citations, and original team notes.
- A private GitHub repository does not automatically provide permission to redistribute copyrighted papers.
- If the team later stores an open-access PDF, verify its license and record the source.
- A shared Zotero library is recommended for citation management.
- External research is for learning and design unless the client explicitly approves additional uses.

## Completion Criteria

The common research phase is complete when:

- All three members can explain the industry value chain and channel differences.
- The team agrees on the commercial meaning of every required KPI.
- Each member has presented the temporary deeper-reading topic.
- Open questions have been added to the client-meeting agenda.
- The team can explain how the four modules form one commercial decision workflow.
