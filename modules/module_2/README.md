# Module 2 — OOS risk and store visit priorities

Module 2 predicts, at end of day *t*, whether a store-SKU will be out of stock on
**any** day in *t+1 … t+7*, turns those risks into a transparent store visit
priority list, publishes the results into shared serving data, and shows them to
Sales and Managers in a scoped Shiny UI. It also monitors issued predictions and
raises challenger requests. It never trains from the UI and never reads future
labels during daily scoring.

Shared data meanings (KPIs, missing days, receipts, labels) are owned by the data
platform; see the [Module 2 integration guide](../../docs/data/v3-module-2-integration.md)
and the [Gold dictionary](../../docs/data/v3-gold-data-dictionary.md).

## 1. Install

Python 3.13 and [uv](https://docs.astral.sh/uv/). The base install stays data-only;
Module 2 adds two optional extras (versions are locked in `uv.lock`):

```bash
uv sync                                  # data platform only (unchanged)
uv sync --extra module2 --extra app      # + modelling and the Shiny app
uv run python -m modules.module_2 doctor # read-only readiness check
```

To keep an existing data-only `.venv` untouched, install the full set elsewhere:
`UV_PROJECT_ENVIRONMENT=.venv-module2 uv sync --frozen --extra module2 --extra app`.

## 2. What runs where

```text
shared data platform (accepted rolling workflow: features, labels, scoring features, serving)
  -> benchmark      LightGBM / XGBoost / RandomForest (Optuna) + logistic baseline, development folds only
  -> freeze         one winner refit on the outer training cap -> model bundle (hash-bound)
  -> validate       one evaluation of the frozen bundle on the outer validation window
  -> holdout        one disclosed holdout_confirmation through the v2 release gate + exposure ledger
  -> score/rank     label-free latest-day scoring of every confirmed store-SKU, visit_priority.v1
  -> publish        new immutable serving artifact in an isolated candidate root
  -> UI / monitor   scoped Sales/Manager views; issued-prediction monitoring and drift
```

| File | Responsibility |
| --- | --- |
| `config.py` | Every fixed rule: 47 inputs, search spaces, budgets, targets, guardrails, versions |
| `preprocessing.py` | Train-only median imputation + missing indicators, one-hot with unknown=ignore (JSON state) |
| `models.py` | Algorithm adapters: fit, raw probability, native save/verified load |
| `calibration.py` | Global sigmoid calibration on logit(raw score); Brier, ECE, reliability table |
| `benchmark.py` | Optuna studies in isolated worker processes, budgets, fair selection |
| `bundles.py` | Final fit and `module2.model_bundle.v1` freeze/load |
| `evaluation.py` | Metrics, thresholds, `module2.evaluation_report.v2`, validation and holdout runners |
| `inputs.py` | Governed exports from the accepted shared artifacts |
| `governance.py` | Owner execution policy transcription and one-time exposure-ledger binding |
| `scoring.py`, `ranking.py` | Latest-day scoring with drivers; store ranking |
| `publishing.py`, `monitoring.py` | Health packages / publisher call; issued history, drift, challenger requests |
| `service.py`, `ui.py`, `server.py`, `demo_app.py` | Scoped service and Shiny UI (root app tab and standalone demo) |
| `delivery.py`, `__main__.py` | Orchestration and the command line |
| `synthetic.py` | Clearly labelled synthetic V3-shaped sources for demos and tests |
| `data_adapter.py`, `training.py`, `validation.py` | Earlier frozen experiment code, kept byte-for-byte for reproducibility |

## 3. Modelling rules (fixed, not tuned on test data)

- **Target**: positive if all seven future days are reported and at least one is OOS;
  negative if all seven are reported with no OOS; anything else is excluded (never 0).
  Current OOS and unknown-input rows stay in every cohort.
- **Time**: dates come only from the shared `TemporalPlan` (strict trailing 30 days):
  for D=2026-06-30, development folds end April 24, outer validation is May 2–24
  (outcomes ≤ May 31), the holdout window is June 1–30 (mature rows ≤ June 23), and
  latest scoring is June 30 → July 1–7. A later D shifts all windows; nothing is
  hard-coded to June.
- **Benchmark**: one Optuna TPE study per main algorithm (seed 650, ≤20 trials,
  ≤45 min scheduling, ≤20 min per two-fold trial, 2 threads, ≤4 h total) plus a
  3-value logistic baseline. Each trial fits on `rolling_dev_1`, calibrates and
  chooses thresholds there (recall ≥ .75, best precision; segments need ≥200 rows
  and ≥20 of each class, else global fallback), then refits on `rolling_dev_2` and
  evaluates with the transferred calibrator/thresholds. Selection maximises dev2
  precision subject to recall ≥ .70 and ROC-AUC ≥ .80. Fewer than three complete
  trials for any main algorithm ⇒ `Benchmark_incomplete`: no validation, holdout or
  real candidate publication.
- **Retention**: trial models are never written; only the winner's final bundle is
  kept. Parameters, metrics and evaluation scores of every trial are kept.
- **Gate**: precision ≥ .75, recall ≥ .70 and ROC-AUC ≥ .80 **simultaneously** on the
  overall complete-label cohort, at validation and at the holdout. Segment and
  current-state results are always reported; undefined metrics never pass.

## 4. Run

All paths are explicit; nothing selects the "newest" directory.

```bash
# Synthetic end-to-end (generates SYNTHETIC sources inside the new root; ~5 min)
uv run python -m modules.module_2 deliver --profile synthetic --output-root outputs/v3/module2/delivery/synthetic-demo

# Real V3: requires a genuine owner execution policy (see section 6) and a NEW root
uv run python -m modules.module_2 deliver --profile v3 \
  --authority-file outputs/v3/module2/governance/owner-execution-policy.json \
  --output-root outputs/v3/module2/delivery/<new-run-id>

# Local demo app on the run's isolated serving candidate (loopback only)
uv run python -m modules.module_2 demo --serving-root outputs/v3/module2/delivery/<run>/serving

# Root app: point the Module 2 tab at a candidate root
MODULE2_SERVING_ROOT=outputs/v3/module2/delivery/<run>/serving uv run shiny run app.py

# Opt-in: read the published shared PostgreSQL release instead (needs --extra postgres)
SHARED_DATA_BACKEND=postgres SHARED_DATA_DATABASE_URL='postgresql://<read-only login>@<host>:<port>/<db>?sslmode=verify-full' \
  SHARED_DATA_SSLROOTCERT=<ca.pem> uv run shiny run app.py
```

The backend is explicit: DuckDB (`MODULE2_SERVING_ROOT`) is the default, and
`demo --serving-root` always stays offline on DuckDB. With `SHARED_DATA_BACKEND=postgres`
the service reads one pinned published release (restart to pick up a newly
activated one); a connection or configuration problem shows an unavailable state
and never falls back to local files. Publishing to PostgreSQL is a data-platform
operator step (`python -m data_platform.postgres_cli`), not a Module 2 command.

Step commands (`benchmark`, `freeze`, `validate`, `evaluate-holdout`, `score`, `rank`,
`build-serving-candidate`, `monitor`, `challenger-request`, `rollback-candidate`) have
`--help`. `help` and `doctor` never train, open labels, use the network or write pointers.

## 5. Daily operation, monitoring and rollback

```bash
# Idempotent: no-op only when a verified completion record exists for this source/date/bundle; never trains
uv run python -m modules.module_2 daily --run-dir <run> --workflow-run <accepted workflow run.json>

# Example cron line (not installed by this project)
# 30 6 * * * cd /path/to/repo && uv run python -m modules.module_2 daily --run-dir <run> --workflow-run <run.json> >> logs/m2-daily.log 2>&1

uv run python -m modules.module_2 challenger-request --run-dir <run>
uv run python -m modules.module_2 rollback-candidate --serving-root <run>/serving --publication-id <id>
```

- `daily` keeps `daily-state.json`: one record per (dataset, source build, date, bundle). A retry
  after a crash resumes from the verified immutable packages already written (the issued scoring
  package is reused, an existing publication of the same packages is adopted only if its history
  record and serving artifact verify), never from a bare directory. A completed record is re-verified
  (all required steps, identity links, issued history, serving database/quality hashes) before a no-op;
  a corrupt publication is recovered by republishing and the recovery is recorded. A corrected source for the same date gets its own record that names what it supersedes.
- Monitoring uses **issued** predictions only. Outcomes mature seven days later; until
  then the status is `Pending_outcomes`. Performance needs ≥1000 matured rows, ≥100
  positives and ≥100 alerts.
- Input drift compares current inputs with the winner's training distribution
  (decile bins + missing bucket, PSI; warning ≥ .20, severe ≥ .30; unknown category
  ≥ 5%; missing rate +10 pp). Fewer than 1000 rows ⇒ `Insufficient_support`.
- A trigger writes `Challenger_requested`; rerun the same benchmark under a newly
  approved scope. The champion is never replaced automatically.
- Rollback re-publishes an earlier verified artifact inside the isolated candidate
  root; existing artifacts and exposure records are never edited or refunded.

## 6. Test release, approval and exposure

The holdout uses the shared `oos_test_release.v2` gate
(`data_platform.test_release.authorize_rolling_test_release`) and the project-wide
exposure ledger (`data_platform.test_exposure`). Two explicit approval modes exist:
`post_freeze_manual` (a human approves after validation) and `preauthorized_policy`
(an owner execution policy approved *before* fitting; the program records its own
`resolved_at` and never claims a human signed the unknown future model).

```bash
uv run python -m modules.module_2 authority --output outputs/v3/module2/governance/owner-execution-policy.json \
  --workflow-run <accepted workflow run.json> --approver "<real approver>" \
  --approved-at "<actual approval time, ISO with timezone>" --message "<verbatim approval text>" \
  --message-reference "<where the approval was given>" --allowed-output-root outputs/v3/module2/delivery \
  --ledger outputs/v3/module2/governance/test-exposure.jsonl --known-history "<honest history note>" \
  --prior-exposures prior-exposures.json
```

The policy also records the frozen Module 2 code identity at transcription; the gate refuses
a bundle fitted by different code, recomputes the benchmark winner from the trial records
(completeness, budgets, search space, fixed starts) and checks the bundle's calibrator and
thresholds against that winner. Publication requires health, scoring, ranking and monitoring
packages that name the same bundle and source, and a passing qualifying report inside the health
package; a status string alone never makes predictions available. Served model-health rows are
derived from the packaged bundle manifest, benchmark and reports (development rows keep their own
benchmark identity and the model's original plan), never taken as supplied.

Never fill these fields with invented values. Windows that were explored before are
evaluated as a disclosed `holdout_confirmation`, not an independent test; overlapping
windows cannot be re-tested by changing run IDs. The ledger is a local audit trail,
not access control: keep one copy and back it up.

The policy's ledger path is bound once: the first holdout creates the ledger and a
`<ledger>.binding.json` receipt beside it (an existing valid ledger without a receipt is
adopted as found). After that, a missing, moved, replaced, truncated or malformed ledger
stops the holdout before any release or truth access; it is never re-created. Restore the
original file (or reconcile manually) and keep the receipt with its backups.

## 7. UI and access

Standalone (`demo_app.py`), Module 2 opens as its own workspace: a collapsible Module 2 sidebar (*Overview*, *Store ranking*, *Analytics*, Help and Settings
marked "coming soon", the user card and **Sign out**, which uses the host's own server-side logout),
and a top bar with store search, the signed-in user (initials, name, title), a **Copilot** button and
placeholder buttons for messages, notifications and calendar (visible, labelled "coming soon", no
behaviour). In an app that also hosts the Commercial Copilot (`integrated_app.py`, see
[the integrated app guide](../../docs/integrated-app.md)) the Copilot button opens it; no question or
store context is passed along. Elsewhere it stays a "coming soon" placeholder.
Hosted in the integrated app (`hosted` option), the app's single sidebar replaces Module 2's own: it
lists Module 2's pages under **Stockout Risk** and holds the user card and the one **Sign out**; the
breadcrumb reads "Stockout Risk / page". Module 2's design is the baseline for every module: its
tokens, Inter font, icons, logo and base components are in the shared workspace design
(`shared/ui_kit.py`, `shared/assets/ui_kit.css`), which Module 2 reads, so its pages look the same
standalone and hosted. Text uses Inter 4.1 (SIL OFL 1.1), bundled in `shared/assets/fonts` with its
licence and registered as "Workspace Inter". The logo is an original placeholder mark, not a client brand asset.

- **Overview**: key figures (stores needing action, act urgently, prevent stockouts, verify data,
  estimated Marvel sales at risk, current MSL-gap SKUs; no period-over-period arrows), Today's focus,
  the SKUs most out of stock now (MSL / non-MSL / unknown), the 10 highest-priority stores with what to do
  at each (a rep's visit plan) and, for managers, estimated sales at risk by region with a region and channel
  breakdown. All follow the channel / region / tier / urgency filters. When hosted with the copilot, the overview
  offers one-click questions per role and a store page has "Ask Copilot about this store".
- **Store ranking**: the full list in overall priority-score order, sortable by score, sales at risk,
  shelf risk, MSL risk and urgency in either direction (unavailable values last, ranks unchanged),
  views, 10 / 50 / 100 rows, export of the selected view, filters and sort. A store opens from its
  name (a keyboard-accessible link) or by selecting its row; back returns to the same page, view,
  sort and position.
- **Analytics**: SKUs most out of stock in the stores you can see — *out of stock now* (the reported
  state on the as-of date only) or, on request, *past 28 days* (distinct reported out-of-stock
  store x SKU x day observations in [as-of − 27, as-of]); split MSL / non-MSL / unknown from each
  observation; a donut of that split; daily out-of-stock observations with the number of reports per
  day (counts, not rates, because the applicable assortment is not confirmed); and the stores behind
  a SKU (choose it in the list or select its bar). The MSL filter applies to the bars, the SKU list
  and the store list alike (each observation keeps its own store x SKU x day MSL value); the totals
  and the donut always cover all observations and say so. Unreported observations are listed as
  unknown, never as in stock.
- **Store**: key figures incl. the estimate confidence, recommended actions, why the store ranks where
  it does, then tabs: *SKUs* (reported now and next 7 days, sortable, filterable, 10 / 50 / 100 rows),
  *Inventory trend* (30 days of reported inventory and units sold with gaps for unreported days,
  joined to the 7-day scenario used for sales at risk — not a model forecast), *Out-of-stock SKUs*
  (reported OOS days per SKU in the past 28 days, rows without a reported state counted separately
  as *unreported*, and the number of days with data; days without any row are not counted as
  missing reports because the SKU's applicable assortment on those days is not confirmed),
  *Shelf execution* and *Estimate quality*. MSL on the store page is the as-of report's own
  store x SKU value; without an as-of report, or with a blank value, it is *Unknown* (never the
  product default), and actions name MSL SKUs only when that value is known.

The 28-day analysis is computed on request from the paged daily view, page by page, over your
scope and filters; it is discarded (never shown partially) if a page fails or your scope or the data
release changes meanwhile, and only completed results are cached (bounded, per scope).
`tests/benchmarks/module2_oos_benchmark.py` measures it on a generated synthetic release (DuckDB and
the disposable PostgreSQL test server).

The business-priority score (`modules/module_2/business_priority.py`, policy
`business-priority.v2-proposed`) is a display-time projection over the published data; it does not
change stored ranks, scores or thresholds. Its weights and cut-offs are proposals awaiting business
approval:

- *Sales at risk* is a seven-day scenario, not a revenue forecast: demand at the prior 28-day
  in-stock daily rate, covered first by reported inventory and then by units on the known schedule
  (assumed to arrive on the next scheduled receipt day, not guaranteed); uncovered units are valued
  at the reported net trade price of the past 28 days (USD). Missing inputs leave a SKU
  unestimated (shown as *Unavailable* or a "≥" lower bound), never zero.
- *Shelf risk* is the share of SKUs non-compliant at the latest audit within 28 days; it has no
  dollar value. *MSL risk* averages current MSL stockouts and the model risk of in-stock MSL SKUs.
- Score = 80% sales-at-risk position among the stores you can see + 10% shelf + 10% MSL. Scores and
  ranks never use stores outside your scope. Stores without a score (sales at risk unavailable, or
  no usable model score) are *Not ranked*: they stay visible, are listed last in stored visit order
  in every sort, and export an empty rank.

`get_oos_analytics(principal, store_filters, "current" | "28d")` returns the analysis. The
service API accepts `filters={"view": "all" | "current_msl" | "prevention" |
"verify_data", "urgency": "urgent" | "high" | "monitor"}` and `sort=(key, "asc" | "desc")` with key
`rank`, `score`, `sales_at_risk`, `shelf_risk`, `msl_risk` or `urgency`; with a view the default is
score, high to low. Without `view` the legacy filters (`msl_only`, `alert_only`, `unknown_only`)
and the stored order behave as before, and combining both is rejected.

Model quality is not part of the daily page. **Model health** and **Monitoring**
are a separate, read-only **ML Operations** entry (the standalone demo's "Open ML
Operations (demo)" button and the root app's "ML Operations (demo)" quick entry).
That demo entry reuses the demo manager's full-store data permission; it is not
production authentication and cannot train, change thresholds, publish or approve.
Health/monitoring data remain enforced manager-only with full-population scope in
the shared reader (both backends). Every request re-checks the principal's current
scope, so a changed or revoked assignment is never answered from cache; if that check
fails (denied or the data service is unavailable) the request ends without data. Requests that
read several views (priorities, store detail, analytics) check scope, data release and session
again after their last read: if anything changed, the result is discarded and not cached, and a
sign-out during a load is never undone by that load. Demo identities (`demo:rep-a` … `demo:rep-f`, `demo:manager`)
use a generated round-robin store assignment, not customer territories, and are
not production authentication.

Missing predictions are shown as **unreleased**, never as zero risk; unknown inputs
are a data follow-up, never "stable"; synthetic data is always labelled.

## 8. Tests

```bash
uv run python -m unittest discover -s tests -v                     # all unit/integration tests
MODULE2_SLOW=1 uv run python -m unittest discover -s tests -p test_module2_delivery.py   # synthetic end-to-end
uv run python tests/browser/run_acceptance.py --candidate-root <synthetic deliver root> --out <evidence dir>
```

The browser acceptance drives the real app with the local Google Chrome (headless,
temporary profile, loopback only) and Node's built-in WebSocket; it covers three
widths, Sales/Manager/ML Operations, the four views and their summary counts,
view-based export, details and back-to-position, pagination, sign-out and the
unavailable/unreleased/stale/error/empty/denied/pending states. It also checks that
the business page carries no model-quality content.

## 9. Limitations

- Results describe simulated V3 data and only rows with complete seven-day labels.
- Calibration estimates probabilities; reliability is reported, not guaranteed.
- The visit ranking is a transparent heuristic, not route optimisation; the sales
  proxy uses static list prices without currency and is not lost net sales.
- Demo identity and scope are local review aids, not production authentication.
