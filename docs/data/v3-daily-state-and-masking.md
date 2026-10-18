# V3 shared daily state and corrected masking

Stage 3 creates candidate `gold.mart_store_sku_daily` plus receipt/promotion
membership and daily lineage tables. It is a common input for all four modules,
not a trained Module 2 predictor or an application release.

## Run

```bash
uv run python -m data_platform build --include-daily-state
uv run python -m data_platform evaluate-missing --per-length 100
```

The first command leaves the accepted Silver pointer unchanged. Its build lives
under `outputs/v3/platform/builds/<build_id>/`. A separate `--output-root` may be
used for isolated candidates. `application_ready` remains false. The second
command reads the accepted Silver manifest, verifies its database hash, and
writes a restricted artifact under `outputs/v3/platform/evaluation/missing/`.
It does not modify the accepted Silver database or publish an estimator/model.

## Historical estimator

Only observed non-OOS days enter sales history. Actual sales on OOS days remain
observed facts but are not treated as uncensored historical demand. A missing
day uses the median of the previous eight weekday occurrences (at least two
eligible observations), then the prior 28 calendar days (at least five), then
the channel/category historical median (at least 20). The entire current date
is excluded from the group fallback, including other stores' observations.

The group median is calculated exactly from cumulative empirical frequency
tables. It is not a full-panel median, an approximate quantile or a future-fitted
statistic. Estimated days never enter observed history.

Promotion factors use observed non-OOS warm-up data ending 2025-12-31. The
channel/category ratio of promotional to regular median sales requires at least
100 observations in each group and a positive regular median; otherwise factor
1 is retained. Ratios are constrained to [1, 2]. This is a descriptive candidate,
not a causal estimate of promotion uplift. It must be tested against no adjustment.

The fitted factor is available only from 2026-01-01. Earlier output rows use raw
history and expose no fitted promotion status/cutoff. After the fit cutoff,
promotional history is normalized before rolling estimation, avoiding a second
application of uplift to an already elevated baseline. A unique eligible
campaign is assumed to apply to a missing row; overlaps disable candidate
uplift. Eligibility does not prove actual consumer participation or establish
minimum-volume qualification. Those assumptions remain visible in the daily state.

For missing inventory, use the latest *visible* inventory report, add each known
receipt once, subtract cumulative estimated sales, then apply a zero floor.
Keep the unclipped value. This is the documented cumulative-gap convention;
flooring each intermediate day would be a different method.

Both candidate sales and inventory estimates are limited to days 1-4 since the
last report. Day 5, missing leading history, unconfirmed assortment and inventory
gaps with an unknown sales component remain Unknown. An actual report resets
the state. Independent receipts do not reset inventory-report age. Estimates
remain null on observed rows, which always take precedence in resolved values.
The sales estimate is a historical proxy; no separate lost-demand recovery or
intraday availability assumption is introduced.

Receipt and promotion collections resolve to membership tables without
multiplying daily rows. Audit context joins only backward in time. Multiple
same-day audit observations are flagged; no approved audit expiry is invented.
Original values, estimation methods, fitting cutoff, source lineage and the
Observed/Estimated/Unknown status remain available to later consumers.

## Corrected experiment

Method selection is restricted to March/April 2026. May and June are rejected
by the masking entry point. For each gap length 1-4, sample 100 nonoverlapping
account blocks with complete original reporting and an observed prior-day
anchor. Select using reporting completeness, not OOS or inventory outcomes.
Hide all SKU observations for every selected account-day *before* rebuilding
all windows, fallbacks and inventory anchors. Delivery and audit feeds remain
available under their explicit same-day simulation assumptions.

Six inventory comparisons are retained: zero fill as a diagnostic, LOCF,
sales depletion, promotion-adjusted depletion, delivery-reconciled depletion
without promotion adjustment, and delivery-reconciled depletion with adjustment.
Report coverage on all hidden rows and head-to-head metrics on the common
eligible population. The report includes sales MAE/WAPE, inventory MAE/WAPE/bias,
signed-error quantiles and current-state OOS precision/recall. Segment outputs
cover channel, tier, SKU, block length, day within block and receipt occurrence.
Overall 95% uncertainty intervals resample stores, preserving within-store
SKU/day dependence. Signed-error quantiles are diagnostics, not calibrated
per-row inventory confidence intervals.

Hidden observations, block membership, method predictions and segment metrics
are in `masking.duckdb`; summaries are in `report.json` and
`segment_metrics.csv`. These artifacts are restricted and ignored by Git.
They are independent of application serving outputs.

The restricted artifact also records retrospective natural-gap balance closure,
using the next actual report and receipt totals to calculate an implied gap
sales quantity under the simplified inventory equation. It never supplies
features or daily observed values. A zero inferred net balance does not prove
zero real-world sales or identify individual missing-day outcomes.

Current-state imputation metrics do not measure the next-seven-day model target.
Artificially hidden reported days also need not behave like naturally missing
days in this simulation. No automatic adoption threshold is inferred from the
older notebook's provisional figures. Review coverage, errors and segment
differences before operational promotion or downstream model experiments.

The [stage 3 validation record](v3-medallion-stage-3-validation.md) contains the
V3 results and the natural-gap balance pattern that still requires review.
