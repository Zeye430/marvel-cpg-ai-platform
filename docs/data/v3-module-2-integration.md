# Module 2: integrate features, labels and the OOS product workflow

Read the [shared-data quickstart](README.md) first. Module 2 has two distinct
workflows: **private model development/evaluation** and **application scoring/
risk/visit planning**. A model-metrics screen alone is not the Module 2 product.
This data-layer branch supplies inputs and typed future-output interfaces, not
a released model, scoring service, monitoring system or ranking implementation.

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

## 5. Development-only loading example

The following is a trusted offline reader pattern, not an app/API endpoint or a
model. It deliberately permits only March/April development folds. Supply the
matched accepted feature/label directories from your workflow:

```python
import duckdb
from data_platform.artifacts import verified_artifact

PREDICTORS = [
    "channel", "segment_tier", "category", "inventory_observed",
    "observed_days_28d", "in_stock_velocity_28d", "days_cover_observed",
    "receipt_units_7d", "pending_units_ordered", "scheduled_units_next7d",
    "days_since_last_audit",
]
KEYS = ["build_id", "scoring_date", "store_id", "sku_id"]

def load_development_partition(feature_dir, label_dir, fold_id, partition):
    if fold_id not in {"march_development", "april_development"}:
        raise ValueError("This example does not open the May gate or June test")
    if partition not in {"train", "validation"}:
        raise ValueError("Choose one partition")
    f, f_db, f_cutoff = verified_artifact(feature_dir, kind="features")
    l, l_db, l_cutoff = verified_artifact(label_dir, kind="labels")
    if f.get("purpose", "development") != "development" or l.get("purpose", "development") != "development":
        raise ValueError("Development loader must not read scoring/test artifacts")
    if (f["dataset_hash"], f["source_build_id"], f_cutoff) != (
            l["dataset_hash"], l["source_build_id"], l_cutoff):
        raise ValueError("Feature/label source and cutoff must match")
    with duckdb.connect(str(l_db), read_only=True) as con:
        fold = con.execute("""SELECT train_last,validation_start,validation_end,
            outcomes_available FROM evaluation.temporal_folds WHERE fold_id=?""",
            [fold_id]).fetchone()
        if not fold or not fold[3]:
            raise ValueError("Development outcomes not yet available")
        truth = con.execute("""SELECT build_id,scoring_date,store_id,sku_id,target_oos_7d
            FROM evaluation.fold_samples
            WHERE fold_id=? AND partition=? AND eligible""",
            [fold_id, partition]).df()
    # Labels are never attached to or written into the feature database.
    predicate = "scoring_date<=?" if partition == "train" else "scoring_date BETWEEN ? AND ?"
    bounds = [fold[0]] if partition == "train" else [fold[1], fold[2]]
    with duckdb.connect(str(f_db), read_only=True) as con:
        features = con.execute(
            "SELECT " + ",".join(KEYS + PREDICTORS) +
            " FROM gold.features_oos_daily WHERE " + predicate, bounds).df()
    data = features.merge(truth, on=KEYS, how="inner", validate="one_to_one")
    if len(data) != len(truth):
        raise ValueError("Eligible outcomes do not all have matching feature rows")
    return data

# In the model branch, X = data[PREDICTORS], y = data["target_oos_7d"].
# Fit any preprocessing using training data only; never use the full data as X.
```

This explicit predictor list is an illustrative starting point, not a selected
production feature set. Model frameworks/dependencies are owned by the Module 2
branch; the shared environment does not promise LightGBM/sklearn is installed.
May evaluation requires its own controlled entry point after development freeze.

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
Monitoring/retraining scheduling is not implemented by this shared branch.

## 8. Future output publication and acceptance checklist

The serving file contains empty typed `gold.predictions_oos` and
`gold.mart_visit_priority`, exposed as `oos_predictions` / `visit_priority`.
Until a future model-output publication is implemented, reads raise
`DataUnavailable`; `build-serving` does not import model results or fill these
tables automatically. Do not insert directly into an accepted business.duckdb.

The Module 2 branch must implement/test a controlled output handoff with:

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
