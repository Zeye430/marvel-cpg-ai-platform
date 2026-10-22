# Module 2: integrate features, labels and the OOS product workflow

Read the [shared-data quickstart](README.md) first. Module 2 has two distinct
workflows: **private model development/evaluation** and **application scoring/
risk/visit planning**. A model-metrics screen alone is not the Module 2 product.
This data-layer branch supplies inputs and typed future-output interfaces, not
a released model, scoring service, monitoring system or ranking implementation.

For new rolling Module 2 development, use the explicit `build-all --rolling-evaluation`
workflow below. No-flag commands and fixed-fold examples remain available for
legacy reproducibility; they do not automatically opt into the rolling policy.

## 1. Inputs, consumers and ownership

| Workflow | Input | Access boundary |
| --- | --- | --- |
| Historical features | `features.duckdb`, `gold.features_oos_daily` | Trusted analytical job; confirmed pair-days, end-of-day known-at rules |
| Supervised truth | `labels.duckdb`, `evaluation.labels_oos_next7` | Restricted model training/evaluation only; never a feature or UI connection |
| Split/eligibility | `evaluation.temporal_folds`, `evaluation.fold_samples`, `evaluation.label_coverage` | One declared fold/partition and complete mature labels |
| Latest-date scoring | Feature rows with scoring_date equal to the explicit scoring date | No future labels are required; do not drop rows based on future reporting |
| Sales UI | `business.duckdb` via scoped serving reader | Read released predictions/visit priorities and supporting reported business facts |
| Monitoring | Versioned historical predictions plus later matured observed outcomes | Separate scheduled evaluation, not future truth in the scoring request |

Do not train against raw CSVs/internal `build.duckdb`, and do not let an app
principal browse the private feature/label files. The trusted ML job can process
the approved analytical population; end-user store scope is enforced separately
when displaying its released outputs. Physical separation is not itself an OS
permission system: deploy restricted artifacts only to authorized runtimes.

Module 2 owns model-specific feature selection, train-fitted transformations,
segment/shared-model comparison, thresholds, calibration, explanations, model
acceptance and ranking policy. Do not rewrite shared ETL, mutate accepted files
or silently change KPI/label/imputation definitions in the module branch.

## 2. Locate and inspect accepted artifacts

The shared `build-all` output includes a workflow run manifest with all component
paths. Use a successful explicit workflow, not the newest directory by name:

```text
outputs/v3/platform/delivery/runs/<accepted-run-id>/run.json
  components.features.directory   -> features.duckdb + run.json + quality.json
  components.labels.directory     -> DEVELOPMENT labels.duckdb, cutoff May 31
  components.scoring_features.directory -> SCORING features.duckdb, cutoff June 30
```

For the published June business snapshot, the serving manifest identifies the
source build and latest SCORING feature artifact ID, not the May development artifact. Do not mix an April projection with
May labels/business data or different source builds merely because IDs overlap.
Verify Accepted status, hashes, dataset_hash, source_build_id and cutoff before
reading. A copied serving file alone is insufficient for model development.

To rebuild the analytical artifacts independently, use accepted base paths:

```bash
uv run python -m data_platform build-features \
  --source-build-dir /YOUR/accepted/base/build-directory \
  --as-of 2026-05-31 --assume-complete-snapshot
uv run python -m data_platform build-labels \
  --source-build-dir /YOUR/accepted/base/build-directory --as-of 2026-05-31
```

These commands create separate artifacts, not model weights or predictions. The
completeness flag concerns order/receipt feeds, not labels or imputation approval.

## 3. What the feature table contains

`gold.features_oos_daily` has one row per build/scoring date/store/SKU/feature
version. Complete version is `oos_features.v1-complete`, with component versions
retained. It contains:

- Channel/tier/category context, observed inventory/sales/OOS and report age.
- Calendar-day reporting coverage, observed sales totals, prior in-stock velocity,
  acceleration, exact observed inventory trajectory and past OOS counts.
- Posted receipts and known pending orders, next-seven-day scheduled units and
  overdue orders. Plans are not guaranteed arrival quantities/times.
- Known campaign eligibility/overlap, same-day observed consumer price/discount,
  latest already-known audit and its age/unknown status.
- Separately named candidate inventory/sales/stock-cover estimates and provenance.

The [feature rules below](#9-feature-window-and-availability-rules) summarize
windows, minimum history and availability semantics. A score at end of t predicts **t+1 to t+7**;
morning scoring cannot reuse t's closing inventory. Do not use future actual
delivery quantity/date, closure diagnostics, future audit evidence or all-panel
target statistics. Unknown events/counts do not become zero unless the recorded
feed assumption justifies it. IDs/provenance fields are not automatically predictors.

Start with an observed-only predictor baseline, then explicitly test candidate
estimate features as a challenger; candidate availability does not approve the
operational estimated KPI view. Fit imputers, encoders, scalers, class weights or
resampling on each training partition only. No target/fold/outcome-known-at field
may enter X. Declare the current-OOS/Unknown cohort policy before comparisons;
the data layer does not automatically choose the final model cohort for you.

## 4. Labels and the time split

The target is 1 if **all seven future days are observed** and any is OOS, 0 if
all seven are observed and none is OOS, NULL otherwise. Immature/partial windows
are not negatives. Partial observed positives are retained as metadata but are
excluded from the accepted complete-window baseline. Never impute future truth.
Future-label eligibility does not require a reported current scoring day: a
currently missing predictor row can still have seven fully observed future days.
Retain that distinction and apply the separately frozen input/cohort policy.

| Fold | Last training scoring date | Validation scoring dates | Outcome cutoff |
| --- | --- | --- | --- |
| march_development | 2026-02-21 | 2026-03-01–2026-03-24 | 2026-03-31 |
| april_development | 2026-03-23 | 2026-04-01–2026-04-23 | 2026-04-30 |
| may_validation | 2026-04-23 | 2026-05-01–2026-05-24 | 2026-05-31 |

The seven-day purge prevents training outcome windows crossing the next
validation scoring period. Use one fold/partition and `eligible=true`, check
`outcomes_available`, and report label eligibility by segment. Do not concatenate
all fold_samples rows as independent samples: folds intentionally reuse history.
No random row train/test split, no validation resampling, and no June access.

March/April are for architecture/features/segment/threshold development. Once
the development policy is frozen, evaluate the declared May validation gate;
do not keep choosing models/thresholds based on May outcomes. Development
artifacts stop May 31. June business/scoring inputs are separate and available,
while June test truth requires the [evidence-gated release below](#10-june-independent-test-release).
June test scoring dates are June 1–23; June 24–30 targets remain immature.

### Rolling-30-day planning preview

The new shared calendar planner defines test as the latest **30 calendar dates**,
not a calendar month; validation reserves the preceding non-overlapping 30 dates.
The planner itself does not unlock outcomes. Default calls still use the fixed
profile above. Plan-aware development features/labels and an explicit rolling
build-all mode are available below; rolling test-release integration remains
follow-on work.

```bash
uv run python -m data_platform plan-evaluation \
  --source-build-dir /YOUR/accepted/base/build-directory
```

This verifies the explicit base artifact and prints JSON using its recorded
cutoff/history. It does not query its database, open labels, write files, build
features, approve a model or unlock test. Do not use a feature/label/serving
directory or a date override as a substitute for the accepted source build.

| Source cutoff | Train scoring cap | Validation reserved / calendar-eligible | Test reserved / calendar-eligible |
| --- | --- | --- | --- |
| 2026-06-30 | 2026-04-24 | May2–31 / May2–24 | June1–30 / June1–23 |
| 2026-08-31 (date-arithmetic example only) | 2026-06-25 | July3–August1 / July3–25 | August2–31 / August2–24 |

H=7 leaves seven purged validation dates and seven pending test dates. Actual
labels still require seven observed future days; calendar eligibility is not
label completeness or sample size. Latest June30 inputs can predict July1–7
without labels. The August example does not provision future client data or
remove the current source verifier's June30 cap.

The result is `oos_temporal_plan.v1`, `planning_status=Temporal_only`,
`test_access=Locked`, with source hashes/identity and deterministic plan digest.
The pure `TemporalPlan` API in `data_platform.temporal_plan` supports roundtrip
validation and reserved-window overlap counts; declared inputs/digests are not
authorization. Advancing the cutoff one day overlaps 29 test dates, not a fresh
independent test. Subsequent label/feature/release integration must preserve
test-exposure history and freeze the model before controlled evaluation.

### Build development labels with the plan

Save the planner's JSON outside Git as `YOUR_TEMPORAL_PLAN.json`. Then use the
same accepted source and the exact validation outcome cutoff recorded in it:

```bash
uv run python -m data_platform build-labels \
  --source-build-dir /YOUR/accepted/base/build-directory \
  --as-of 2026-05-31 \
  --temporal-plan /YOUR/local/YOUR_TEMPORAL_PLAN.json \
  --output-root outputs/v3/platform/rolling-development-labels
```

May31 here applies to a plan from the current June30 snapshot, not a permanent
date rule. The builder rejects a mismatched source/hash/history/cutoff or altered
plan before opening source SQL. The artifact records the plan, derived folds and
fold-policy version; generic verification checks that binding again.

`evaluation.temporal_folds` contains `rolling_validation` and up to two earlier
adjacent 30-day windows (`rolling_dev_1` / `rolling_dev_2`), omitting only earlier
windows without even one calendar training date. These names describe temporal
partitions, not statistical sufficiency or model acceptance. For each fold,
select only `eligible=true` rows of one partition. Training outcome windows and
known-at timestamps must precede that fold's validation start. A currently
missing predictor day can still have a complete observed future label.

No reserved-test-period observations enter these development exports. Do not
pass this plan with `--purpose test` or legacy test evidence: rolling test release
is still locked. No model training or latest-scoring label access is performed.
Current source verification still enforces the supplied dataset's June limit;
future client snapshots and rolling test-release gates need separate integration.
Existing label calls without `--temporal-plan` retain fixed folds.

### Build matching features and the full rolling workflow

Individual `build-features` and `export-feature-input` accept the same
`--temporal-plan FILE`. Use `--purpose development` with as_of equal to the
validation outcome end, or `--purpose scoring` with as_of equal to source_as_of.
The source must match the full plan, not merely have overlapping store/SKU IDs.
The plan is passed through all feature slices; it does not change their formulas
or authorize future actual delivery/audit/outcome inputs.

For a fresh end-to-end candidate, let the workflow generate its own matching plan:

```bash
uv run python -m data_platform build-all \
  --as-of 2026-06-30 --rolling-evaluation --assume-complete-snapshot \
  --output-root outputs/v3/platform/rolling-delivery
```

The workflow run stores `temporal-plan.json`, `evaluation_profile=trailing_30d`
and locked test status. Its development features/labels use the same plan and
validation outcome cutoff; its separate latest-scoring features and serving DB
reach the latest source day. Missing latest inputs are retained, not dropped for
missing future labels. For June15, development stops May16 while scoring/business
data reaches June15: no month-end snapping and no truncation of business freshness.

Default `build-all` remains the legacy profile for compatibility. The command
does not train models, produce risk/ranking or open real test truth. Publication
requires explicit `--publish`; the example leaves accepted publications unchanged.
Current source verification still caps actual data at the supplied June end;
August examples are calendar arithmetic, not newly provisioned client data.

## 5. Read prepared rolling data directly

Use the two offline readers in `modules.module_2.data_adapter`. They consume
existing shared tables; they do not rebuild ETL, features, labels or splits.
Supply explicit accepted component directories from one rolling workflow:

```python
from modules.module_2.data_adapter import (
    load_development_partition,
    load_scoring_inputs,
)

predictors = [
    "inventory_observed", "in_stock_velocity_28d",
    "days_since_inventory_report", "scheduled_units_next7d",
]
train = load_development_partition(
    "/YOUR/accepted/development/features",
    "/YOUR/accepted/development/labels",
    fold_id="rolling_dev_2", partition="train", predictors=predictors,
)
development_check = load_development_partition(
    "/YOUR/accepted/development/features",
    "/YOUR/accepted/development/labels",
    fold_id="rolling_dev_2", partition="validation", predictors=predictors,
)
latest = load_scoring_inputs(
    "/YOUR/accepted/scoring/features", predictors=predictors,
)
# train.X / train.y are aligned; fit preprocessing only on training data.
# latest.X contains only the scoring artifact's latest day; latest has no y.
# Inspect train.metadata, latest.metadata and latest.context for provenance.
```

The readers reuse artifact integrity/quality verification and canonical manifest
plans, check source compatibility and align explicit columns one-to-one by
build/date/store/SKU. Missing inputs and current OOS are retained; no imputation,
encoding or model cohort is silently selected. No random split or row sampling.
A fixed safe predictor allowlist prevents IDs, future labels and extra diagnostic
columns entering X. The example feature list is illustrative, not model selection.

Only `rolling_dev_1` and `rolling_dev_2` inner folds are readable for tuning.
The outer `rolling_validation` gate and test are deliberately not reader modes;
they need separate controlled evaluation after freezing the development policy.
Incomplete observed labels are excluded through the existing `eligible` flag,
not recalculated or filled. Metadata reports candidate/eligible/returned counts.

Plan/cutoff come from the accepted manifests, not the machine clock or another
date configuration. Development features/labels must match source, plan and
cutoff. Latest scoring requires its separate `purpose=scoring` artifact, reads
the manifest cutoff only and never receives/discovers labels. Invalid inputs
raise `ValueError`; a valid empty selection raises `RuntimeError`, not zero risk.

Candidate `inventory_candidate`, `sales_candidate` and `days_cover_candidate`
require both explicit selection and `include_candidate_estimates=True`.
Observed values are never overwritten, and this does not approve estimated KPI
views. These are trusted offline readers, not an app authorization endpoint.

No Shiny/preload import, model training or network is needed to read a batch.
The existing legacy fixed profile remains reproducible in the platform, but
these new readers intentionally require planned rolling artifacts; do not pass
legacy fold names or use a scoring artifact to bypass development isolation.

### First offline training experiment

`modules.module_2.training` provides a bounded two-stage experiment using these
readers. It does not install packages, expose test truth or promote a model.
Export in the locked data environment; fit in an explicitly provisioned ML
environment with LightGBM, sklearn and pyarrow. The main project lock does not
yet declare those ML packages. Actual runtime versions are saved in the report;
do not mix environments by adding another environment's site-packages to sys.path.

```bash
.venv/bin/python -m modules.module_2.training export \
  --workflow-run /YOUR/accepted/rolling-workflow/run.json \
  --output-dir outputs/v3/module2/experiments/YOUR_NEW_RUN/inputs

/PATH/TO/ML_ENV/bin/python -m modules.module_2.training fit \
  --input-dir outputs/v3/module2/experiments/YOUR_NEW_RUN/inputs \
  --output-dir outputs/v3/module2/experiments/YOUR_NEW_RUN/results
```

Use new directories; existing inputs/results are never overwritten. Exported
training Parquet combines features and observed targets for this restricted
offline job only, not a serving/feature publication or an application input.

The initial experiment uses all complete-label samples, including current OOS
and unknown inventory, with separate current-status reports. It compares pooled
models/global thresholds, pooled models/segment thresholds and segment models/
segment thresholds. Small/unseen segments fall back to pooled behavior. All
parameters/support rules are fixed before fitting; categorical levels use only
training rows. Earlier-inner-fold threshold tuning is labeled as such; the later
inner fold transfers those thresholds without retuning. There is no outer gate
or test mode. Native missing handling is not approval of estimated KPI values.

Outputs: `report.json`, `metrics.csv`, restricted per-row development scores,
LightGBM text models/train-only category vocabularies and `thresholds.json`.
These are exploratory candidates, not a model release or calibrated live risks.
Keep coverage/current-status/segment results alongside headline metrics;
meeting numeric targets in development alone is not formal acceptance.

### Controlled rolling outer validation

`modules.module_2.validation` evaluates the selected **pooled model with segment
thresholds** without opening test. It consumes the same accepted rolling workflow
and completed inner experiment. Dates come from its canonical plan, not fixed
month names. For the June30 snapshot, training scoring ends April24; validation
scoring is May2–24 with observed outcomes through May31.

Run these stages in order, using a new local directory. `DATA_PYTHON` is the
locked data environment and `ML_PYTHON` the provisioned LightGBM/sklearn/pyarrow
environment described above:

```bash
DATA_PYTHON=.venv/bin/python
ML_PYTHON=/PATH/TO/ML_ENV/bin/python
VALIDATION_RUN=outputs/v3/module2/validation/YOUR_NEW_RUN

"$DATA_PYTHON" -m modules.module_2.validation freeze \
  --workflow-run /YOUR/accepted/rolling-workflow/run.json \
  --development-dir outputs/v3/module2/experiments/YOUR_COMPLETED_RUN \
  --run-dir "$VALIDATION_RUN"
"$DATA_PYTHON" -m modules.module_2.validation export-training --run-dir "$VALIDATION_RUN"
"$ML_PYTHON" -m modules.module_2.validation fit --run-dir "$VALIDATION_RUN"
"$DATA_PYTHON" -m modules.module_2.validation export-validation --run-dir "$VALIDATION_RUN"
"$ML_PYTHON" -m modules.module_2.validation evaluate --run-dir "$VALIDATION_RUN"
```

Freeze records the 47 predictors, fixed training parameters, exact development
thresholds, source/plan identities, code and evidence hashes. Fitting uses only
the exported training partition and train-only category levels; there is no
validation early stopping. Only after the candidate is frozen can validation
export record its access marker and read eligible labels. Evaluation reloads
that same model and applies unchanged thresholds, with no refitting or tuning.
The ordinary development reader still refuses the outer fold.

Outputs are `policy/frozen-policy.json`, restricted `train-inputs/` and
`validation-inputs/`, `candidate/` model/category files, `access/outer-validation.json`
and `evaluation/report.json`, `metrics.csv`, restricted per-row predictions.
The report includes eligible/candidate coverage, overall/channel×tier/current
OOS-status metrics and undefined-metric handling. Numeric success does not prove
good performance for newly emerging OOS or missing-input subgroups, nor calibrated
probabilities. Uncalibrated scores are not yet released UI risks.

Existing outputs are not overwritten; failed attempts remain recorded. The
access marker is a **per-run audit record**, not filesystem security or a global
holdout-exposure ledger. Do not create a fresh run to retune after seeing the
gate. A passing report does not authorize test, promotion or publication. This
rolling report is intentionally not a legacy `may_validation` release report:
do not rename it or fabricate compatibility with the fixed-date gate in §10.
Rolling test-release integration and actual owner authorization remain separate.

## 6. Models, segments and acceptance

Compare a shared model and channel×tier models/thresholds using the same temporal
folds/cohort. Report segment sample size/base rate, TP/FP/FN, precision/recall,
PR-AUC, calibration and missing-input coverage. Small/empty/one-class segments
need an explicit fallback/undefined-metric policy, not an asserted improvement.
Choose thresholds in development and apply them unchanged at the May gate.

The specification requires simultaneous **Precision ≥0.75, Recall ≥0.70 and
ROC-AUC ≥0.80** at the declared operating point/cohort. Document the development
recall buffer and segment tradeoffs; precision alone is insufficient. Historical
notebook numbers/thresholds are experimental references, not certification of
this corrected feature pipeline. Re-run and version its own model results.

## 7. Latest scoring, UI and model monitoring are different jobs

For the current snapshot, use the separate `purpose=scoring` artifact and filter
to scoring_date **2026-06-30**, predicting July 1–7 without loading future outcomes. This does **not**
unlock June test data. Preserve missing-input states and versioned preprocessing;
do not require a mature label to score a store/SKU. Do not train on later dates
than the approved model release permits.

Sales should see current risk probabilities, explanations and a versioned visit
priority list for their assigned stores. Priority is not necessarily raw SKU
probability: risk/value/MSL/business weights and store aggregation require a
declared ranking policy. Do not call an uncalibrated max SKU risk a calibrated
store-level probability, and do not invent value-at-risk units or prices.

Monitoring evaluates **previously issued predictions** after their seven-day
outcomes mature, on declared complete-label cohorts. Metric changes may indicate
performance degradation but do not alone establish data drift: track input/
segment/reporting distributions and label coverage separately. A rolling month
can be a monitoring window, not an untouched test repeatedly used for tuning.
Module 2 implements this as separate batch commands (daily scoring, issued-prediction
monitoring, drift checks and challenger requests); see the
[Module 2 guide](../../modules/module_2/README.md). No scheduler is installed.

## 8. Output publication and acceptance checklist

Base serving builds contain empty typed `gold.predictions_oos`,
`gold.mart_visit_priority`, `gold.model_health` and `gold.model_monitoring`; reads of
the first two raise `DataUnavailable` until outputs are published. `build-serving`
never fills them. `data_platform.model_outputs.build_serving_with_model_outputs`
copies a verified base snapshot into a **new** artifact, adds one selected scoring
run, its ranking and aggregated health/monitoring rows, checks fact parity, grain,
coverage and source identity, requires every package (health, scoring, ranking,
monitoring) to name the same model bundle and source and a passing qualifying report
inside the health package before predictions become available, derives every served
`model_health` row from the packaged evidence (bundle manifest, benchmark report,
validation/holdout reports; development rows keep their own benchmark identity and the
model's historical plan) and refuses supplied rows that differ, and publishes only into an isolated candidate root
(existing formal roots are refused). `model_health` / `model_monitoring` are
manager-only with full-population scope in `ServingReader.read` and `read_page`.
Never insert directly into an accepted business.duckdb.

`ServingReader.read_page(name, principal_id, *, start, end, store_id, sku_id, after,
page_size)` pages the Module 2 views with a stable key order; the opaque cursor is
bound to the artifact, view, filters and principal and never widens scope.

A controlled output handoff provides:

- Matching dataset/build/date and explicit scoring_run_id; probability in [0,1],
  declared threshold/decision, model/feature versions, drivers and input status.
- Ranking run/store/date, unique priority ranks within the declared population,
  ranking_policy_version and reasons; one deliberately selected scoring run.
- New immutable serving publication with quality/grain/scope/version checks,
  safe unavailable behavior, history and rollback; coordinate shared-interface
  changes with the data-layer owner.
- Private training artifacts, preprocessing/model/threshold manifests, evaluated
  metrics/cohorts and clear monitoring/retraining ownership.
- Future-mutation/truncated replay, temporal/label isolation, segment fallback
  and end-user scope tests. No labels, private artifacts or secrets in app/Git.

Module 1/3/4 can consume accepted outputs after that publication. They should
not train their own duplicate OOS models to fill a missing shared output.

## 9. Feature window and availability rules

All windows are calendar days and scoring is at **end of day t**. Use only
confirmed assortment dates and source records known by that time; not future
outcomes that happen to be present in the full source file. These are shared
input definitions, not permission to fit preprocessing on all dates.

| Group | Contract |
| --- | --- |
| Reporting, sales and past OOS | Inclusive `[t-6,t]` / `[t-27,t]`; retain valid-observation and expected-day counts; all-unknown sums/counts are NULL, not zero |
| In-stock velocity | Mean observed non-OOS sales in `[t-7,t-1]`, `[t-14,t-8]`, `[t-28,t-1]`; minimum 3, 3, 5 observations respectively; current-day sales excluded |
| Acceleration / days cover | Latest 7-day velocity divided by previous 7-day velocity; inventory divided by 28-day velocity for cover; NULL on unknown or zero denominator |
| Inventory trajectory | Inventory at exactly t minus exactly t-7, only when both observed |
| Posted receipts | Delivery receipt date and known-at <= t/end of t; trailing 7/28-day totals; never add Sell-out restock a second time |
| Pending orders | Order/schedule known by t and no receipt already posted by t; next-7 scheduled dates `[t+1,t+7]`; overdue planned dates <= t; never use eventual actual arrival/quantity/status |
| Promotion | Retain eligible campaign overlaps; eligibility is not proven execution; realized price/discount requires an observed row at t |
| Audits | Latest audit business date already known by t, deterministic tie-break; late older evidence does not replace a newer audit; retain age/unknown expiry, no invented validity period |
| Candidate estimates | Separate observed/candidate columns and methods; at most four days since last inventory report, using prior-only history and known receipts; insufficient evidence stays Unknown |

Zero empty order/receipt counts require the explicitly recorded complete-snapshot
assumption. V3 has one receipt event per delivery: a posted short/zero receipt
closes that event; do not invent a remaining backorder. Plans are not guaranteed
future availability. The velocity is an observed demand proxy, not a causal
promotion uplift or uncensored latent demand estimate.

Exact SQL and column definitions are versioned with the code:
[state/sales](../../data_platform/features.py),
[receipts/orders](../../data_platform/replenishment_features.py),
[promotion/audits](../../data_platform/context_features.py),
[candidate estimation](../../data_platform/estimation.py).
Missing-day assumptions, masking limits and operational adoption remain governed
by the [KPI/missing-day specification](../solution-specifications/kpi-and-missing-store-day-handling.md).
Changes need future-mutation, truncated-history replay, sparse-window and
cross-artifact isolation tests, not only a model score comparison.

## 10. June independent test release

Development features/labels are capped May 31. June business/scoring files are
not development inputs; restrict their runtime access accordingly. June truth
requires a genuine passed-May report and a frozen model/feature/threshold policy.
The data job creates no real model evidence or test metrics on your behalf.

The Module 2 owner supplies a local release JSON with these fields:

| Fields | Required value / meaning |
| --- | --- |
| `release_version`, `status` | `oos_test_release.v1`, `Approved` |
| `approved_by`, `approved_at`, `model_frozen_at` | Named approver; timezone-aware ISO timestamps, freeze no later than approval |
| `dataset_hash`, `model_version`, `feature_version`, `threshold_policy_version` | Explicit matching frozen identities |
| `model_file`, `model_sha256` | Existing model file and SHA-256 of its actual bytes |
| `validation_report_file`, `validation_report_sha256` | Existing May report and its actual SHA-256 |

The validation report must match the release's dataset/model/hash/feature/
threshold identities and specify `status=Passed`, `fold_id=may_validation`,
`outcome_end=2026-05-31`, `label_policy=complete_window`. Its measured
`metrics.precision`, `metrics.recall`, `metrics.roc_auc` must simultaneously reach
0.75, 0.70, 0.80. Boolean/NaN/non-finite metric values are rejected. Paths may be
absolute or relative to the release JSON. See the
[exact validator](../../data_platform/test_release.py) and
[synthetic gate tests](../../tests/test_business_cutoffs.py) for executable examples.

Do not fabricate a passing report or copy target values into it to open test.
This is a local auditable engineering gate, not a signature/authentication
service: the owner must control approval/evidence and filesystem access.

Only after genuine approval:

```bash
uv run python -m data_platform build-labels \
  --source-build-dir /YOUR/accepted/JUNE/base/build-directory \
  --as-of 2026-06-30 --purpose test \
  --test-release work/approved-june-test-release.json \
  --output-root outputs/v3/platform/evaluation/june-test
```

The accepted manifest pins evidence hashes. Test partitions contain June
scoring dates only, with no training partition. Mature eligible dates end June
23; June 24–30 remain NULL/not mature, and incomplete observed future windows
remain NULL. Never use candidate estimates as test truth. Freeze test policy
before evaluation; later monitoring/retraining does not make a reused window
an untouched test again. The development loader above rejects scoring/test
artifacts. Currently no model/evidence is released by this branch, so real June
test materialization remains locked; successful gate fixtures are synthetic.

### Rolling test release (oos_test_release.v2)

Rolling plans use `data_platform.test_release.authorize_rolling_test_release` and the
project exposure ledger `data_platform.test_exposure` instead of the fixed v1 gate.
The v2 release binds the canonical plan/window/source, the frozen bundle and its
component hashes, the development benchmark (re-derived from its trial records:
minimum complete trials per algorithm, budgets, search space and the selected winner),
the approved rules and code identity, the iterative validation report (P/R/AUC re-checked from counts)
and either a post-freeze manual approval or a preauthorized owner execution policy
plus a program resolution. A durable reservation is fsynced in the ledger before any
truth query, overlapping windows are refused across runs/sources/models (only an
identical-evidence replay with a reason is allowed), and failed builds keep their
reservation. Module 2 binds the ledger path once with a receipt beside the file; a bound
ledger that is missing, replaced, truncated or malformed is refused, never re-created. Labels contain only the plan's test window (`rolling_test`, pending
days NULL):

```bash
uv run python -m data_platform build-labels --source-build-dir <accepted base build> \
  --as-of <plan D> --purpose test --temporal-plan <plan.json> \
  --test-release <approved v2 release.json> --test-exposure-ledger <initialized ledger> \
  --output-root <new restricted label root>
```

Module 2's `evaluate-holdout` command performs this end to end for one frozen bundle
(see the [Module 2 guide](../../modules/module_2/README.md)).
