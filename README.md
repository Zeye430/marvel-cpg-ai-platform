# Marvel CPG Commercial Workspace

One web app for Marvel CPG's commercial team: regional managers and field sales reps sign in and see
business performance, stockout risk, a deal simulator and a copilot, all on the same shared data release.

The app is [`integrated_app.py`](integrated_app.py) (Python, Shiny). It is what the Docker image and the Render
deployment start. The older `app.py` is kept only for reference (see [Legacy app](#legacy-app)).

---

## The four modules

### Module 1 · Business Performance
For managers and reps, on the stores their account can see.
- **Five KPIs**: MSL compliance, out-of-stock (OOS) rate, sell-in vs sell-out gap, audited shelf allocation and
  planogram compliance, each with its definition, formula and limits in the page.
- **Store ranking**: a 0–100 score per store from the five KPIs (each a percentile among the stores in view,
  equal weights), with best and weakest stores and their move since the previous period.
- **Store cards**: one store's scorecard with a 13-week line and trend per KPI, a radar compared with up to four
  other stores, and its field rep.
- **Geography & flow**: a state map with city-level store bubbles, and sell-in vs sell-out per biweekly period with
  how long a gap has lasted.
- **Reported / Estimated view**: one switch fills short reporting gaps with the data platform's estimates; observed
  values come first and unknown stock stays unknown.
- Further pages: out-of-stock, shelf execution and trade terms.

Shelf share in the current data is the split of shelf space among Marvel's own audited products, not a share
against competitors, so the page calls it "Shelf allocation".

### Module 2 · Stockout Risk
Predicts, at the end of a day, whether a store-SKU will be out of stock on any of the next 7 days, and turns the
risks into a store visit priority list (visit plan for reps, regional and channel view for managers).
- Models are benchmarked offline (LightGBM, XGBoost, random forest and a logistic baseline). A model is released
  only if it passes the gate **precision ≥ 0.75, recall ≥ 0.70 and ROC-AUC ≥ 0.80 together** on held-out data.
  These are targets; the page states the status of the model behind the predictions it shows.
- Missing predictions are shown as unreleased, never as zero risk.
- Details: [Module 2 README](modules/module_2/README.md).

### Module 3 · Negotiation Simulator
- **Deal simulator**: three proposals side by side for one store and product, each checked against the channel ×
  category guardrail (maximum extra discount, minimum margin, required execution commitment). Each proposal gets
  one of five verdicts: **Approved**, **Approved with conditions**, **Manager review**, **Rejected** or
  **Cannot assess**, with the extra volume needed to keep gross profit at the standard discount.
- **Price change**: volume, revenue and gross-profit response to a list-price change, from an elasticity
  estimated on the user's stores or set by the user.
- The guardrail rules are a proposal pending client confirmation and are fixed in the page; the only assumption a
  user can switch is the volume uplift. Nothing is approved or sent anywhere automatically.
- Details: [integrated app guide](docs/integrated-app.md#negotiation-simulator-module-3).

### Module 4 · Commercial Copilot
- **Data questions** in plain English become one checked SQL query over the signed-in user's stores only, with a
  written answer, an automatic chart, the result table and the SQL.
- **Product and policy questions** are answered only from the 12 authorized documents in
  [`knowledge_base/`](knowledge_base/README.md), with citations (document, version, section, PDF page).
- **Language model**: a local model through Ollama by default (nothing leaves the machine), or a hosted model
  through OpenAI (`gpt-4.1-mini`) for the deployed site. With the hosted model, questions and the rows behind each
  answer are sent to OpenAI.
- Details: [Module 4 README](modules/module_4/README.md).

---

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). Dependency versions are locked in `uv.lock`.

```bash
git clone https://github.com/Zeye430/marvel-cpg-ai-platform.git
cd marvel-cpg-ai-platform
```

**With a data release** (a verified local publication built by the data platform):

```bash
SHARED_DATA_PUBLICATION=<path to published/serving.json> \
  uv run --extra app --extra module4 shiny run integrated_app.py --port 8000
```

**Without data**: start the app on generated synthetic data with a placeholder model:

```bash
uv run --extra app --extra module4 --extra postgres python tests/browser/run_integrated_acceptance.py --serve --port 8000
```

Open http://127.0.0.1:8000 and choose a demo identity:

| Identity | Role | Sees |
| --- | --- | --- |
| Elena Rostova | Regional manager | All stores in the demo scope |
| Alex Mercer | Field sales rep | Only the stores assigned to Alex |
| Beatrice Vance | Field sales rep | Only the stores assigned to Beatrice |

Sign-in is for demos only. Switch modules in the sidebar.

All settings (data source, shared PostgreSQL, language model) are in the
[integrated app guide](docs/integrated-app.md#settings). Keep passwords and API keys in a private environment file
outside the checkout or in the host's secret settings, never in Git.

### Copilot model

- **Local (default)**: install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b` and `ollama serve`.
- **Hosted**: set `MODULE4_LLM_PROVIDER=openai` and `MODULE4_LLM_API_KEY` (from a private file, not the command line).

Without a reachable model the copilot says so and the other modules keep working.

---

## Docker

The image starts `integrated_app.py`. It holds no business data, so give it a source:

```bash
docker build -t marvel-cpg-dashboard .
docker run --rm -p 127.0.0.1:8000:8000 -v <serving build>:/data:ro \
  -e SHARED_DATA_ARTIFACT_DIR=/data marvel-cpg-dashboard
```

See [Run it in Docker](docs/integrated-app.md#run-it-in-docker) for the model settings and file permissions.
`docker compose up --build` uses the same image.

---

## Deployment on Render

Create a **Web Service** from this repository with environment **Docker** and branch `main`, then set:

| Variable | Value |
| --- | --- |
| `SHARED_DATA_BACKEND` | `postgres` |
| `SHARED_DATA_DATABASE_URL` | Read-only application account on the shared PostgreSQL release |
| `SHARED_DATA_SSLROOTCERT` | Path to the CA file for verified TLS |
| `MODULE4_LLM_PROVIDER` | `openai` |
| `MODULE4_LLM_API_KEY` | OpenAI API key, as a secret value |

`PORT` is set by Render. Sign-in is demo-only, so do not expose a site holding client data publicly.

---

## Shared data platform

All modules read one shared, versioned data release built by the data platform (raw files → Bronze/Silver/Gold →
serving views, as DuckDB locally or PostgreSQL when shared). Modules do not run their own ETL.

- Start with the [shared-data guide](docs/data/README.md): architecture, local build, database access and the
  integration guide for each module.
- Field definitions: [Gold dictionary](docs/data/v3-gold-data-dictionary.md).
- Package commands: [data_platform README](data_platform/README.md).

Business data runs through June 30, 2026. Estimated values are separate, opt-in views and never replace
reported facts.

---

## Repository layout

```text
integrated_app.py         The app: all four modules on one shared data release
shared/                   Shell, sign-in, runtime (pinned data source) and the shared design (ui_kit)
modules/module_1/         Business Performance
modules/module_2/         Stockout Risk: modelling, scoring, ranking, monitoring and pages
modules/module_3/         Negotiation Simulator
modules/module_4/         Commercial Copilot: routing, Text-to-SQL sandbox, cited RAG
knowledge_base/           The Copilot's 12 authorized documents and index
data_platform/            Shared data build, validation and publishing
docs/                     Integrated app guide, data guides, project notes
notebooks/                Exploratory analysis
tests/                    Unit tests and the browser acceptance suite
app.py, www/              Legacy app (reference only)
```

---

## Tests

```bash
uv run --extra app --extra module4 python -m unittest discover -s tests
uv run --extra app --extra module4 --extra postgres python tests/browser/run_integrated_acceptance.py --out <evidence folder>
```

The browser suite builds synthetic data and uses a placeholder model; it needs Node and Google Chrome.

---

## Status and limitations

- The data is synthetic demo data for a course project; store-to-rep assignments are demo assignments.
- Module 2 predictions come from a review-candidate model until one passes the release gate; the page labels them.
- Module 3's guardrail rules await client confirmation.
- The Copilot's knowledge base is being updated to the new guardrail rules (standard plus additional discount).

---

## Legacy app

`app.py` is the earlier root app with its own login and CSV/PostgreSQL loader (`shared/data.py`, `shared/auth.py`,
`www/`). It is no longer deployed and its Business Performance and Negotiation Simulator tabs point to the
integrated app. Use `integrated_app.py` for anything new.

---

## Team workflow

- Keep `main` stable; work on a branch for each task.
- Open a pull request when ready and ask at least one teammate to review it before merging.
- Never commit API keys, passwords, `.env` files or other secrets.
