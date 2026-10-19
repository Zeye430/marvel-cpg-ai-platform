# Marvel CPG AI Commercial Execution Platform

An enterprise-grade, multi-module AI commercial execution platform designed for **Marvel CPG**, unifying executive analytics, predictive machine learning, algorithmic trade negotiation, and natural language database assistance into an integrated web platform.

---

## 🏛️ Platform Architecture & Modular Design

The platform is structured into four decoupled, independent business modules orchestrated by a shared executive shell:

```text
marvel-cpg-ai-platform/
│
├── app.py                      # Root Application Shell: Executive Auth & Multi-Module Nav Tabset
├── requirements.txt            # Root dependencies (Shiny, Plotly, Pandas, Psycopg2, etc.)
├── Dockerfile                  # Production container definition for local & Render deployments
├── docker-compose.yml          # Container service definition with environment variable injection
│
├── www/                        # Static Web Assets (zero Python string parsing overhead)
│   ├── styles.css              # 5-color luxury Apple design system (#73fbd3, #44e5e7, #59d2fe, #4a8fe7, #5c7aff)
│   ├── app.js                  # Asynchronous KPI number-shuffling animations
│   ├── marvel_logo.jpg         # Brand monogram logo
│   └── marvel_logo_thumb.jpg   # Brand thumbnail
│
├── shared/                     # Cross-Module Infrastructure
│   ├── data.py                 # Centralized, non-blocking Aiven PostgreSQL loader & CSV fallback
│   └── auth.py                 # Role-based auth (Director Elena Rostova & 6 Field Rep profiles)
│
└── modules/
    ├── module_1/               # Module 1: Commercial Execution Analytics Dashboard
    │   ├── ui.py               # Portfolio filters, 5 KPI hero tiles, and 4 visualization sections
    │   └── server.py           # Reactive scopes (SO, SI, AU) & 10 Plotly chart renderers
    │
    ├── module_2/               # Module 2: Predictive Out-of-Stock Risk Engine (t+7 Days)
    │   ├── ui.py               # ML benchmark scorecard, feature gain chart, and route ranking table
    │   └── server.py           # Supervised classification / time-series inference & priority ranking
    │
    ├── module_3/               # Module 3: Commercial Negotiation Simulator
    │   ├── ui.py               # Proposed deal terms, discount slider, and margin realization layout
    │   └── server.py           # Contractual guardrail validation (dim_guardrails) & waterfall economics
    │
    └── module_4/               # Module 4: Commercial Copilot (Text-to-SQL & RAG)
        ├── ui.py               # Chat query prompt bar, preset queries, and document citations tray
        └── server.py           # Natural language to SQL translation & warehouse query execution
```

Run Python commands through `uv run` so they use the project environment. Commit `pyproject.toml`, `uv.lock`, and `.python-version`, but do not commit `.venv/`.

## Team Workflow

- Keep `main` stable and do not develop directly on it.
- Create a branch for each assigned task.
- Open a pull request when the work is ready for review.
- Ask at least one teammate to review the pull request before merging.
- Do not commit API keys, passwords, local environment files, or other secrets.

## Data and Confidentiality

The supplied Marvel CPG data and challenge brief are for academic use only. Do not add the raw client files to this repository unless the instructor explicitly approves it. Document how authorized team members can obtain and place the data locally.

## Status

The initial Module 1 implementation and project environment configuration are available. The remaining modules are under development.

## Shared V3 Data Platform

Start with the [shared-data guide](docs/data/README.md) for architecture, local
build, database access and **all four module integration guides**. Look up exact
fields in the [Gold dictionary](docs/data/v3-gold-data-dictionary.md); package
commands are in [data_platform](data_platform/README.md).

The common Bronze/Silver/Gold layer supplies reported business data through
June 30 and separate opt-in Estimated candidates. Module 2 development stops
May 31; June independent test remains evidence-gated. Model/ranking release and
module applications are separate work. Inputs/databases are provisioned privately,
not by Git checkout. Existing legacy Module 1 ETL is not automatically migrated.
