# Integrated app: Stockout Risk and Commercial Copilot (local)

`integrated_app.py` runs **Stockout Risk (Module 2)** and **Commercial Copilot (Module 4)** in one
local Shiny app with a demo sign-in, one app sidebar for switching modules and one shared business
source. **Business Performance (Module 1)** and **Negotiation Simulator (Module 3)** are listed in the
sidebar but cannot be opened in this build: their code is not imported and the legacy CSV loader is
never used.

## Run it

From the repository root, with the app, Module 4 and (for PostgreSQL) postgres extras:

```bash
# A verified local publication (the build-all delivery pointer, or any published serving.json)
SHARED_DATA_PUBLICATION=<path to published/serving.json> \
  uv run --extra app --extra module4 shiny run integrated_app.py --port 8000
```

Open http://127.0.0.1:8000, choose a demo identity, and switch modules in the sidebar (or with the
**Copilot** button in Stockout Risk's top bar). The active module's own pages (for Stockout Risk:
Overview, Store ranking, Analytics) are listed under it. On a phone the sidebar opens from the
button at the top left. Without any setting, the app looks for the default
local delivery pointer `outputs/v3/platform/delivery/serving/published/serving.json`.

**No data at hand?** This starts the app on generated synthetic data with a deterministic model stub
(no real model; answers are placeholders that exercise the plumbing):

```bash
uv run --extra app --extra module4 --extra postgres python tests/browser/run_integrated_acceptance.py --serve --port 8000
```

## Settings

| Setting | Meaning |
| --- | --- |
| `SHARED_DATA_PUBLICATION` | Local published `serving.json` pointer (DuckDB). |
| `SHARED_DATA_ARTIFACT_DIR` | A verified (copied) serving artifact directory instead. Use one of the two. |
| `SHARED_DATA_BACKEND=postgres` | Read the shared PostgreSQL release instead, with `SHARED_DATA_DATABASE_URL` (read-only application account), `SHARED_DATA_SSLROOTCERT` (CA file for verified TLS) and optionally `SHARED_DATA_RELEASE_ID`. Keep these values in a private environment file outside the checkout. |
| `MODULE4_LLM_PROVIDER`, `MODULE4_LLM_BASE_URL`, `MODULE4_LLM_MODEL` | The copilot's language model (local Ollama by default); see the Module 4 README. |
| `MODULE4_RAG_DENSE=0` | Keyword-only knowledge retrieval when the embedding package is not installed. |
| `MODULE2_LATEST_SOURCE_DATE` | Newest accepted source date, for the "out of date" notice. |
| `MODULE2_SERVING_ROOT`, `MODULE4_SERVING_PUBLICATION`, `MODULE4_SERVING_ARTIFACT_DIR` | Optional module-specific sources. They must name the same publication as the shared source; otherwise that module shows "unavailable" rather than mixing two releases. |

## What to expect

- **One design.** Every module uses the shared workspace design (`shared/ui_kit.py`,
  `shared/assets/ui_kit.css`), which is Module 2's design: the same sidebar, top bar, page header,
  cards, buttons, notices, tables and bundled Inter font. Red and white are the frame (chrome, primary
  action, focus); charts in every module use one multi-hue palette in a fixed order
  (`shared/chart_palette.py`, checked for colour-blind separation), not brand red. Modules added later should build
  their pages from it.
- **One source for both modules.** The sidebar shows the source (local publication or shared
  PostgreSQL) and its data date. Module 2's page header and the copilot's status show the same date
  and publication; both use the signed-in identity's account scope from that publication.
- **Demo sign-in only.** Manager, Sales Rep A and Sales Rep B are fixed demo identities chosen on the
  server; no password is checked and this is not production authentication. Store access comes from
  the account scope configured for that identity in the shared data. Signing out (at the bottom of
  the sidebar) or switching identity clears both modules; a copilot answer still running for
  the previous identity is cancelled and never shown.
- **Failures are explicit.** If the source cannot be opened, a notice above the module explains what to configure,
  Module 2 shows "unavailable" (never another source or old data) and the copilot refuses data
  questions while document questions still work where possible. If the language model is not
  running, the copilot says so and Module 2 keeps working. Revoked or changed access fails closed in
  both modules.
- **Model status is disclosed.** Module 2 keeps its synthetic / review-candidate labels and its
  "predictions not released" state; nothing here approves a model or a release.
- **The copilot can query Module 2's published outputs.** When the publication has released
  predictions, the copilot can answer questions about predicted stockout probability and alerts
  (`sku_risk`), the stored visit ranking (`store_priority`) and, in this app, Module 2's business
  ranking (`business_priority`), all limited to the signed-in identity's stores. In this app "visit first" and
  "priority ranking" mean the business ranking shown in Stockout Risk, so both modules give the same order; the
  stored v1 rank is used only when the question asks for it. Questions about what will
  happen (for example "Which SKUs will run out next week?") are answered only from these published
  predictions: if they are not released, or the generated query reads only observed history, the
  copilot says so instead of answering, and other forecasts (for example next month's sales) are not
  offered. Ordinary history rankings (sales, out-of-stock rate, shelf share) keep working.
  Probabilities are shown as percentages, and a small risk is never shown as zero. Each such answer
  states the model status, scoring run, scoring date and prediction window. The stored ranking is
  renumbered within the user's stores (a rep never sees chain-wide positions); the business ranking is
  read from the Module 2 service, not recomputed, and is labelled as a proposed policy and a different
  ordering. If predictions are not released, or the prediction and ranking rows do not all come from
  the publication's selected scoring run, the copilot says the predictions are unavailable (not zero
  risk) and business-data questions keep working.

## Current limits

- The copilot answers from the shared business views (sales, stock, shelf audits, deliveries,
  promotions), Module 2's published predictions and rankings, and the knowledge base. Other published
  views (price/audit context, metric definitions, candidate estimates, model evaluation reports) are not
  in its sandbox, so not every business question can be answered. Module 2's estimated sales at risk reaches
  the copilot with the business ranking, so both show the same figure; without that ranking the copilot says it
  is unavailable and never estimates it from other data.
- The business ranking needs the Module 2 service; the standalone copilot (`python -m modules.module_4`)
  has only the stored ranking and says so.
- Module 2's overview questions and its "Ask Copilot about this store" button pass a question to the copilot.
  Switching modules from the sidebar carries no context.
- Copilot conversations last for the browser session; they are not saved.
- The same shell (`shared/shell.py`) is meant to host Modules 1 and 3 once they read the shared
  publication; until then use the root `app.py` for those modules.

## Tests

```bash
python -m unittest tests.test_integrated_app tests.test_module2_review_fixes tests.test_module4_predictions
# With the disposable local PostgreSQL test server (compose.postgres-test.yml):
SHARED_POSTGRES_REQUIRE_TEST_DB=1 python -m unittest tests.test_integrated_postgres
# Browser smoke (headless Chrome + Node, synthetic data, deterministic model stub):
python tests/browser/run_integrated_acceptance.py --out <evidence dir> [--with-postgres]
```
