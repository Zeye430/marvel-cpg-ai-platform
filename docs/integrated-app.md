# Integrated app: all four modules (local)

`integrated_app.py` runs **Business Performance (Module 1)**, **Stockout Risk (Module 2)**, **Negotiation
Simulator (Module 3)** and **Commercial Copilot (Module 4)** in one local Shiny app with a demo sign-in,
one app sidebar for switching modules and one shared business source. The legacy CSV loader is never
used.

## Run it

From the repository root, with the app, Module 4 and (for PostgreSQL) postgres extras:

```bash
# A verified local publication (the build-all delivery pointer, or any published serving.json)
SHARED_DATA_PUBLICATION=<path to published/serving.json> \
  uv run --extra app --extra module4 shiny run integrated_app.py --port 8000
```

Open http://127.0.0.1:8000, choose a demo identity, and switch modules in the sidebar (or with the
**Copilot** button in the top bar of Business Performance and Stockout Risk). Business Performance opens first,
with a welcome greeting while its data loads. The active module's own pages
(for Stockout Risk: Overview, Store ranking, Analytics; for Business Performance: Overview, Geography &
flow, Out-of-stock, Shelf execution, Trade terms) are listed under it. On a phone the sidebar opens from the
button at the top left. Without any setting, the app looks for the default
local delivery pointer `outputs/v3/platform/delivery/serving/published/serving.json`.

**No data at hand?** This starts the app on generated synthetic data with a deterministic model stub
(no real model; answers are placeholders that exercise the plumbing):

```bash
uv run --extra app --extra module4 --extra postgres python tests/browser/run_integrated_acceptance.py --serve --port 8000
```

## Run it in Docker

The image starts this app. It holds no business data, so give it a source with the settings below:

```bash
docker build -t marvel-cpg-dashboard .
# <serving build> is the folder named by "directory" in published/serving.json. Use SHARED_DATA_ARTIFACT_DIR here:
# serving.json holds that absolute path from the machine that built it, so it does not resolve inside the container.
docker run --rm -p 127.0.0.1:8000:8000 -v <serving build>:/data:ro \
  -e SHARED_DATA_ARTIFACT_DIR=/data \
  -e MODULE4_LLM_BASE_URL=http://host.docker.internal:11434 \
  marvel-cpg-dashboard
```

The container runs as user 1000, which must be able to read that folder (its `run.json` and `quality.json`
are private to their owner). Without a source the app still starts and shows "unavailable". For a hosted model
instead of Ollama, pass `--env-file <private file>` with `MODULE4_LLM_PROVIDER=openai` and `MODULE4_LLM_API_KEY`
(never put the key on the command line or in Git). Without a reachable model the copilot says
so and Module 2 keeps working. The image has no model server and uses keyword-only document retrieval
(`MODULE4_RAG_DENSE=0`). Sign-in is demo-only, so do not expose a container holding client data publicly.

## Settings

| Setting | Meaning |
| --- | --- |
| `SHARED_DATA_PUBLICATION` | Local published `serving.json` pointer (DuckDB). |
| `SHARED_DATA_ARTIFACT_DIR` | A verified (copied) serving artifact directory instead. Use one of the two. |
| `SHARED_DATA_BACKEND=postgres` | Read the shared PostgreSQL release instead, with `SHARED_DATA_DATABASE_URL` (read-only application account), `SHARED_DATA_SSLROOTCERT` (CA file for verified TLS) and optionally `SHARED_DATA_RELEASE_ID`. Keep these values in a private environment file outside the checkout. |
| `MODULE4_LLM_PROVIDER`, `MODULE4_LLM_BASE_URL`, `MODULE4_LLM_MODEL`, `MODULE4_LLM_API_KEY` | The copilot's language model: local Ollama by default, `openai` (hosted, needs the API key) for a deployed site; see the Module 4 README. Keep the key in a private environment file or the host's secret settings. |
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
- **Demo sign-in only.** Three demo accounts with fictional names and illustrated portraits: Elena
  Rostova (regional manager, `elena.rostova`), Alex Mercer and Beatrice Vance (field sales reps A and B,
  `alex.mercer`, `beatrice.vance`). The sign-in page asks for a user name and password and fills in the
  chosen account's (demo password `marvel-demo`); the server matches them to that account's fixed demo
  identity. This is not production authentication. Store access comes from the account scope configured
  for that identity in the shared data. To use photo portraits, add `manager`, `rep_a` and `rep_b`
  images to `shared/assets/avatars/` (see its README). Signing out (at the bottom of
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
  offered. A stockout question about one named day ("on Saturday", "this weekend") gets a clear answer instead:
  predictions cover a whole window, not a single day. Ordinary history rankings (sales, out-of-stock rate, shelf share) keep working.
  Probabilities are shown as percentages, and a small risk is never shown as zero. Each such answer
  states the model status, scoring run, scoring date and prediction window. The stored ranking is
  renumbered within the user's stores (a rep never sees chain-wide positions); the business ranking is
  read from the Module 2 service, not recomputed, and is labelled as a proposed policy and a different
  ordering. If predictions are not released, or the prediction and ranking rows do not all come from
  the publication's selected scoring run, the copilot says the predictions are unavailable (not zero
  risk) and business-data questions keep working.

## Negotiation Simulator (Module 3)

Two pages under Negotiation Simulator in the sidebar (brief Scenarios A and B). Both read the signed-in
identity's scoped copy that Business Performance builds, so they open at once after the dashboard has loaded.

- **Deal simulator.** Choose an account (only stores in your scope) and a product. The context shows the
  reference list price and cost (static; currency not supplied), the channel × category standard discount,
  the recent 28-day invoiced discount (history only, not the standard), reported stock, sell-out, the last
  audit, and the guardrail (standard, maximum additional discount and its commitment, minimum margin)
  marked *Requires review*. Three proposals side by side each take an additional discount on top of the
  standard and an execution commitment, and show total discount (standard + additional), net price, gross
  margin, money for the order quantity, the checks and a verdict under the Owner's provisional rules
  (`m3-rules.v1-provisional`, pending client confirmation and confirmation of Sales authority):
  - **APPROVED**: no additional discount, margin at or above the minimum;
  - **APPROVED WITH CONDITIONS**: an additional discount up to and including the maximum, with the
    required commitment (any listed option), margin at or above the minimum;
  - **MANAGER REVIEW**: above the maximum with the margin at or above the minimum; for distributors any
    additional discount (their commitments are not confirmed). A status only: nothing is sent or saved;
  - **REJECTED**: below the minimum margin, a total of 100% or more, or an additional discount within the
    maximum without the commitment;
  - **CANNOT ASSESS**: a price, cost, standard, maximum, minimum or commitment is missing, or the
    additional discount is empty or negative. A data release from before the standard discount existed
    shows this for every proposal, with a note.

  Each proposal also shows the volume uplift needed to keep the gross profit at the standard discount and
  whether the commitment's assumed uplift covers it. Under *Assumptions and rules* you can switch the
  execution uplift (the client's V3 note: display +8%, planogram +5%, +3% per 10 points of shelf share; or
  the measured ≈ 0%).
- **Price change.** A list-price increase (0–15%) on a category or one product, with the share passed on
  to the shelf price. The baseline is reported sell-out on in-stock days in the last 13 weeks for your
  retail accounts (distributors are wholesale accounts without meaningful sell-out data). The elasticity is estimated on the same scoped history (log units on log shelf price within
  store × product × month; approximate interval shown) or set by you; a positive estimate means no
  measurable price response and is used as 0. The page shows volume, revenue and gross-profit change, the
  response curve with the elasticity band, the elasticity at which revenue stays flat, and an
  extrapolation note when the increase is beyond the prices seen in the data (which vary almost only
  through temporary promotions).

## Business Performance (Module 1)

- **Content.** The dashboard's five KPI tiles (MSL compliance, reported out-of-stock rate, sell-in vs
  sell-out gap, audited SKU shelf allocation, planogram compliance), its filters and its ten charts. The
  filters are one row of pills (date range with presets in whole Monday-to-Sunday weeks: last week and
  last 4 weeks are the latest complete weeks, then month, quarter and year to date and all data; channel;
  region; MSL products, which narrows the SKU list to those products; product SKU with search): each pill
  shows its current value, a changed filter is highlighted and "Reset filters" restores the defaults (last
  4 weeks, everything selected). A KPI change is green when it is better for that KPI and red when worse
  (a falling OOS rate is green). Each chart's "?" explains how to read it and what the data is; the
  Stockout Risk and Negotiation Simulator filters use the same pills. The charts: state execution map and sell-in/sell-out
  flow (Geography & flow); stock heatmap, lost-sales heuristic trend and out-of-stock duration
  (Out-of-stock); planogram/MSL by brand, shelf allocation vs invoiced sales and the execution scorecard
  (Shelf execution); discount vs margin and trade-off evidence (Trade terms). The regional manager sees
  all ten; a field rep sees the heatmap, brand compliance and discount charts for their stores, and the
  others say they belong to the manager view.
- **Data.** Read only from the shared publication's Reported views (`stores`, `products`, `store_day`,
  `store_sku_daily`, `commercial_period`, `execution_visits`, `sell_in_events`, `negotiation_context`),
  limited to the signed-in identity's stores. Business Performance opens after sign-in and builds a
  complete copy of those stores' rows in the background, showing a welcome greeting (by the browser's
  local time of day) until the KPIs are ready; Stockout Risk and the copilot stay usable meanwhile. A copy too large for the row budget shows "unavailable" instead of partial figures.
  KPIs aggregate the shared numerator and denominator components; unknown values stay N/A, never zero.
  The gap KPI compares complete biweekly periods inside the selected range (shown on the tile) and says
  that invoice completeness is unconfirmed. Tile targets are team display targets, not client-approved
  thresholds; the shelf tile is allocation among the supplied SKUs, not competitor shelf share.
- **Charts** use the workspace palette and the local plotly.js. The US state map's outline data is bundled
  (`modules/module_1/assets/geo`), so no page requests anything outside the app. A chart that cannot be
  drawn shows a message; no value that cannot be encoded reaches the browser.
- The legacy root `app.py` no longer renders Module 1: its tab points to this app.

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
- The Negotiation Simulator does not save proposals: there is no submission or manager approval workflow
  yet, and its guardrail rules are a proposal pending client confirmation (switchable in the page).
- Business Performance keeps one copy of the scoped data per recently signed-in identity (at most three)
  in the app process; it is rebuilt after a restart or an account-scope change.

## Tests

```bash
python -m unittest tests.test_integrated_app tests.test_module1_shared tests.test_module1_render \
  tests.test_module2_review_fixes tests.test_module4_predictions
# With the disposable local PostgreSQL test server (compose.postgres-test.yml):
SHARED_POSTGRES_REQUIRE_TEST_DB=1 python -m unittest tests.test_integrated_postgres
# Browser smoke (headless Chrome + Node, synthetic data, deterministic model stub):
python tests/browser/run_integrated_acceptance.py --out <evidence dir> [--with-postgres]
```
