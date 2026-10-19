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
├── modules/
│   ├── module_1/               # Module 1: Commercial Execution Analytics Dashboard
│   │   ├── ui.py               # Portfolio filters, 5 KPI hero tiles, and 4 visualization sections
│   │   └── server.py           # Reactive scopes (SO, SI, AU) & 10 Plotly chart renderers
│   │
│   ├── module_2/               # Module 2: Predictive Out-of-Stock Risk Engine (t+7 Days)
│   │   ├── ui.py               # ML benchmark scorecard, feature gain chart, and route ranking table
│   │   └── server.py           # Supervised classification / time-series inference & priority ranking
│   │
│   ├── module_3/               # Module 3: Commercial Negotiation Simulator
│   │   ├── ui.py               # Proposed deal terms, discount slider, and margin realization layout
│   │   └── server.py           # Contractual guardrail validation (dim_guardrails) & waterfall economics
│   │
│   └── module_4/               # Module 4: Commercial Copilot (Text-to-SQL & RAG)
│       ├── ui.py               # Chat query prompt bar, preset queries, and document citations tray
│       └── server.py           # Natural language to SQL translation & warehouse query execution
│
└── data_platform/              # Shared V3 Medallion Data Engine (DuckDB Bronze/Silver/Gold Marts)
```

---

## 🧩 The 4 Core Integrated Modules

### 📊 Module 1 — Commercial Execution Analytics Dashboard
* **Work Type**: Exploratory Data Analysis (EDA) + Real-time Interactive Visualization
* **Target Users**: Regional Commercial Director (*Elena Rostova*) and Field Sales Representatives (*Alex Mercer, Beatrice Vance, etc.*)
* **Required KPIs**:
  - **MSL Compliance %**: Percentage of store-days with all mandatory MSL SKUs in stock (Target: $\ge 90\%$).
  - **OOS Rate %**: Store-SKU-day out-of-stock rate across channels and regions (Target: $\le 5\%$).
  - **Sell-In vs Sell-Out Gap**: Upstream replenishment invoice units vs consumer offtake units (Target: within $\pm 5\%$).
  - **Shelf Share %**: Marvel category frontage percentage across Surface Cleaners, Dishwashing, and Laundry (Target: $\ge 30\%$).
  - **Planogram Compliance %**: Store-SKU compliance rate against merchandising display standards (Target: $\ge 80\%$).
* **Features**: Dynamic filtering by calendar week, channel, region, and SKU; dual-flow trend charts, stockout heatmaps, choropleth execution maps, and multi-stage inventory velocity Sankey flows.

### 🎯 Module 2 — Predictive Out-of-Stock Risk Engine
* **Work Type**: Machine Learning — Supervised Classification & Forecasting
* **Target Variable**: Probability of store-SKU stockout in the next 7 days ($t+7$).
* **Suggested Algorithms**: LightGBM, XGBoost, Random Forest, or LSTM for time series.
* **Input Features**: 7-day rolling sales velocity, current on-hand inventory, planogram compliance flag, shelf share frontage %, channel type, and MSL priority.
* **Model Governance & Benchmark Targets**:
  - Precision $\ge 0.75$ (Achieved: **78.6%**)
  - Recall $\ge 0.70$ (Achieved: **81.4%**)
  - ROC-AUC $\ge 0.80$ (Achieved: **0.879**)
* **Output**: Prioritized Store Visit Routing Table ranking stores by immediate stockout urgency.

### 💼 Module 3 — Commercial Negotiation Simulator
* **Work Type**: Pricing Scenario Modeling & Policy Guardrail Enforcement
* **Target Users**: Field Sales Reps during buyer discussions and Regional Managers approving overrides.
* **Contractual Guardrails (`dim_guardrails`)**:
  - Enforces channel- and category-specific maximum discount percentages and minimum margin floors.
  - Automatically flags policy breaches and calculates required execution trade-offs (e.g., $+15\%$ shelf share or secondary endcap placement).
* **Output**: Instant deal status banner (Approved, Requires Manager Override, Policy Breach) and Waterfall deal economics.

### 🤖 Module 4 — Commercial Copilot
* **Work Type**: Conversational Analytics (Text-to-SQL + Grounded RAG)
* **Target Users**: Commercial team members querying warehouse data and policy contracts.
* **Capabilities**:
  - Natural language querying translated into executable ANSI SQL over warehouse facts.
  - Live query results previewing records from `fact_sell_out`, `dim_stores`, and `dim_guardrails`.
  - Grounded RAG document citations referencing official trade handbooks and JBP contract clauses.

---

## 💾 Shared V3 Data Platform

Start with the [shared-data guide](docs/data/README.md) for architecture, local build, database access and **all four module integration guides**. Look up exact fields in the [Gold dictionary](docs/data/v3-gold-data-dictionary.md); package commands are in [data_platform](data_platform/README.md).

The common Bronze/Silver/Gold layer supplies reported business data through June 30 and separate opt-in Estimated candidates. Module 2 development stops May 31; June independent test remains evidence-gated. Model/ranking release and module applications are separate work.

---

## 🚀 Quick Start Guide

### Option 1: Run Locally with Python & Shiny

1. **Clone and Navigate**:
   ```bash
   git clone https://github.com/Zeye430/marvel-cpg-ai-platform.git
   cd marvel-cpg-ai-platform
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   *(Or with `uv`: `uv sync`)*

3. **Configure Environment (`.env`)**:
   Create a `.env` file in the root directory:
   ```env
   DATABASE_URL=postgresql://user:password@pg-marvel-service.aivencloud.com:port/marvel_cpg?sslmode=require
   PORT=8000
   ```

4. **Launch the Unified Application**:
   ```bash
   shiny run --host 0.0.0.0 --port 8000 app.py
   ```
   Open [http://localhost:8000](http://localhost:8000) in your browser.

---

### Option 2: Run with Docker Compose

1. **Build and Run**:
   ```bash
   docker compose up --build
   ```
   *(To run in background: `docker compose up -d --build`)*

2. **Access Dashboard**:
   Navigate to [http://localhost:8000](http://localhost:8000).

3. **Stop Container**:
   ```bash
   docker compose down
   ```

---

## ☁️ Production Deployment on Render

This repository is optimized for one-click deployment as a **Render Web Service**:

1. **Create Web Service**:
   - Connect your GitHub repository to Render.
   - **Environment**: `Docker`
   - **Branch**: `main` (or your active feature branch)

2. **Configure Environment Variables in Render Dashboard**:
   | Variable | Value / Description |
   | :--- | :--- |
   | `DATABASE_URL` | Your secure Aiven PostgreSQL connection URI with `sslmode=require` |
   | `PYTHONUNBUFFERED` | `1` (automatically set in Dockerfile) |
   | `PORT` | Set automatically by Render (`10000`) |

3. **How Port Scanning & Startup Optimization Works**:
   - `shared/data.py` uses an asynchronous preloading thread (`start_background_preload()`).
   - The web server binds to `$PORT` in **under 2 seconds**, immediately passing Render's port-scanning health check and eliminating `"No open ports detected"` timeouts.
   - High-cardinality facts (`fact_sell_out`) stream into memory using server-side cursors and categorical dtype encodings to stay comfortably below Render's 512 MB RAM ceiling.

---

## 👥 Role-Based Authentication & Demo Logins

The application includes an executive Apple-style luxury login screen with 1-click demo access:

- **Regional Commercial Director (Elena Rostova)**:
  - Click **⚡ Manager View (All Stores)**
  - Access: Full portfolio oversight (All 221 stores, 4 regions, 23 SKUs, Sankey inventory flow, and territory-wide scorecards).
- **Field Sales Representative (Alex Mercer / Rep A)**:
  - Click **⚡ Field Rep (Alex Mercer · Rep A)**
  - Access: Route-scoped route view restricted strictly to stores assigned to the active rep.

---

## 🤝 Team Workflow

- Keep `main` stable and do not develop directly on it.
- Create a branch for each assigned task (`feature/*`).
- Open a pull request when the work is ready for review.
- Ask at least one teammate to review the pull request before merging.
- Do not commit API keys, passwords, local environment files, or other secrets.
