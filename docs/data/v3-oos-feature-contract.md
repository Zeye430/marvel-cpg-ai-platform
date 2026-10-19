# V3 Gold OOS feature contract

Status: state/sales slice and isolated input exporter, including its CLI,
implemented with synthetic tests; full feature orchestration and real-data
validation remain pending.
Version: `oos_features.v1`. This document prepares a data-layer handoff to
Module 2. It does not approve an estimator, train a model, select thresholds,
generate probabilities or implement visit ranking.

References: [architecture](v3-medallion-architecture.md),
[KPI and label policy](../solution-specifications/kpi-and-missing-store-day-handling.md),
[daily-state limitations](v3-medallion-stage-3-validation.md).

## 1. Grain, time and isolation

Target relation: `gold.features_oos_daily`.
Unique key: `(build_id, scoring_date, store_id, sku_id, feature_version)`.
Each row represents information available at the **end of scoring date t**,
for a later prediction about `t+1` through `t+7`. Morning scoring is a different
availability contract and must not reuse same-day closing inventory.

Include only pairs with `assortment_is_confirmed_asof = true` at t. Missing
current observations do not remove a confirmed pair. Retain Unknown states and
coverage; do not filter samples using future label availability.

The future builder must read a restricted projection of daily state, dimensions,
known schedules, posted receipts and audits. It must not connect to full Bronze,
future realized events, gap-closure diagnostics or label artifacts. Filter each
input by its event/known-at boundary **before** aggregating or joining. Static
dimension and same-day availability assumptions must remain identified.

Default development cutoff remains 2026-05-31. June outcomes remain locked.
Orders known by t with later scheduled dates remain eligible: an eventual June
arrival is not a reason to remove a historical pending order.

## 2. Proposed columns and calculations

All trailing windows below are **calendar days**, not the last N reported rows.
Observation-based sums and averages exclude missing values and always carry
counts. No valid observations means NULL, not zero. Static attributes remain
categorical; model-specific encoding belongs to the Module 2 branch.

| Group | Columns | Calculation / constraint |
| --- | --- | --- |
| Identity and provenance | `build_id`, `dataset_hash`, `data_version`, `feature_version`, `estimator_version`, `scoring_date`, `store_id`, `sku_id`, `as_of_timestamp`, `availability_assumption_id`, `source_lineage_set_id` | Pin one input build and its daily-state lineage; provenance fields are not predictors |
| Segment context | `channel`, `segment_tier`, `region`, `category`, `is_msl` | Authorized dimension snapshot; no target encoding or full-panel segment OOS rate |
| Current state | `inventory_observed`, `inventory_candidate`, `inventory_data_status`, `sales_observed`, `sales_candidate`, `sales_data_status`, `oos_observed`, `last_report_date`, `days_since_inventory_report`, `estimation_release_status` | Observations stay separate from candidate estimates; map candidates from `inventory_estimated` / `units_sold_estimated`; NULL is not in-stock |
| Reporting coverage | `observed_days_7d`, `observed_days_28d`, `expected_days_7d`, `expected_days_28d` | Windows `[t-6,t]` and `[t-27,t]`; expected days count only confirmed assortment dates in available history |
| Observed sales | `sales_units_observed_7d`, `sales_units_observed_28d` | Same inclusive windows; sum reported sales, not an estimate of complete-period sales |
| Demand proxy | `in_stock_velocity_7d`, `in_stock_velocity_prev7d`, `in_stock_velocity_28d`, `velocity_days_7d`, `velocity_days_prev7d`, `velocity_days_28d`, `sales_acceleration_ratio` | Mean observed sales on observed non-OOS days in `[t-7,t-1]`, `[t-14,t-8]`, `[t-28,t-1]`; require respectively 3, 3, 5 valid days; acceleration = first / second, NULL if either unavailable or denominator zero |
| Stock coverage | `days_cover_observed`, `days_cover_candidate` | Current observed/candidate inventory divided by `in_stock_velocity_28d`; NULL when inventory unknown or velocity missing/zero; no claim of refill-aware OOS probability |
| Inventory trajectory | `inventory_change_7d_observed` | Inventory at exactly t minus inventory at exactly t-7, only when both observed; no forward-fill disguised as observation |
| Past OOS | `oos_days_observed_7d`, `oos_days_observed_28d` | Count observed zero-inventory days in `[t-6,t]` / `[t-27,t]`; use reporting counts alongside them; all-unknown window yields NULL |
| Posted receipts | `receipt_units_7d`, `receipt_units_28d`, `last_receipt_date`, `days_since_receipt` | Delivery is the single receipt source; receipt date and known-at must be <= t/end-of-day t; do not add Sell-out restock again |
| Pending orders | `pending_order_count`, `pending_units_ordered`, `scheduled_units_next7d`, `overdue_units_ordered`, `days_until_next_scheduled_receipt` | Rules below; these are planned units, not predicted accepted receipt quantities |
| Current campaign context | `eligible_promotion_count`, `has_promotion_overlap`, `observed_promo_discount_pct` | Eligibility from plans known at t, actual discount only from an observed row at t; no assumption that every eligible campaign was executed |
| Audit context | `last_audit_id`, `last_audit_date`, `days_since_last_audit`, `planogram_compliance_latest`, `secondary_display_latest`, `shelf_share_pct_latest`, `same_day_audit_count`, `audit_policy_status` | Latest eligible audit at/before t, same deterministic tie-break as Silver; no interpolation, unknown expiry remains Requires review; shelf allocation is not market share |

The initial demand proxy is unadjusted observed in-stock sales, not a claim of
latent demand or a causal promotion uplift. Existing estimator baselines may be
added later under separately named candidate fields with fit provenance. They
must not silently replace these observation-based definitions.

### Pending-order semantics

Start from `silver.order_schedule_events` with `order_date <= t` and
`source_known_at <= end_of_day(t)`. Remove an order only if a receipt for that
delivery ID has **already been posted by t**. Never use future `actual_delivery`,
`delivery_status`, `units_delivered`, `fill_rate_pct` or eventual delay to
classify an order as pending.

- Pending count/units include all remaining known orders, including overdue.
- Next-seven-day scheduled units use inclusive planned dates `[t+1,t+7]`.
- Overdue units use planned dates `<= t` with no posted receipt at t.
- Next scheduled receipt uses the earliest pending planned date `> t`;
  return NULL if none, not a negative or fabricated arrival time.
- Aggregate schedules and receipts separately before joining daily rows.
  Zero pending quantities require a known-order projection; missing feed
  coverage must not be interpreted as confirmed zero in a future live feed.

The static initial schedule is assumed known at order creation. Cancellation,
schedule revisions and partial-receipt balance histories are not supplied. Do
not invent them or label scheduled units as guaranteed future replenishment.

## 3. Labels and module ownership

`target_oos_7d` belongs only in a separate restricted evaluation artifact:
all seven future Sell-out dates observed => any OOS gives 1, otherwise 0;
incomplete windows => NULL. Never create labels from estimated future inventory.
Complete-window coverage is an evaluation condition, not a serving-row filter.
The label builder and temporal split manifests are separate subsequent tasks;
the exact March/April/May boundaries remain in the KPI specification.

Module 2 owns feature selection, train-fitted encodings/scalers, segment models,
thresholds, model acceptance, probabilities, drivers and visit-ranking policy.
Store/SKU identifiers are join keys, not automatically approved predictors.
Publishing their eventual outputs to Gold does not make model training part of
this data-layer branch. Risk/value/MSL inputs alone are not a final visit rank.

## 4. Required tests before a feature table can be accepted

1. Grain, lineage and version checks; one row per confirmed pair/date, including
   missing current reports without creating rows before first confirmed membership.
2. Future-mutation test: changing outcomes after t cannot change features at t.
3. Truncated-history replay equals the same date in a later authorized build,
   comparing business values rather than build IDs or execution metadata.
4. Sparse/calendar-boundary fixtures verify exact windows, minimum counts,
   current-day exclusion for velocity, zero denominators and Unknown states.
5. Receipt double-counting, multiple orders, overdue schedules and known orders
   crossing month/cutoff boundaries; future receipt quantity/date never exposed.
6. Audit as-of/tie handling, campaign overlap and absence of future campaign
   realization; no label, target-derived statistic or retrospective diagnostic
   in the feature schema.

These are full-table acceptance criteria; not all have been implemented yet.

## 5. First implemented slice

`data_platform.features.build_oos_state_features(connection, as_of, quality)`
materializes the current-state, segment, coverage, observed-sales, demand-proxy,
stock-coverage, exact inventory-trajectory and past-OOS groups above. Its version
is `oos_features.v1-state_sales`, deliberately distinct from the complete design.
Receipts, pending orders, promotions and audit enrichment are not included yet.

The caller must prepare a separate connection containing the allowlisted
`feature_input.daily_state` projection and optional `meta` quality tables. The
function rejects source/evaluation tables, explicitly selects input columns and
does not attach or read the internal Bronze/Silver database. This is an internal
engineering guard, not an application authorization sandbox. The separate exporter
below now prepares that projection through a function or CLI; feature-build
orchestration remains pending.

The partial table contains all confirmed historical pair-days through `as_of`,
not just the latest day. Current observations and candidate estimates remain
separate; historical velocity uses only observed non-OOS sales. Results remain
`application_ready = false`. No label, model or serving publication is produced.

Synthetic tests cover calendar windows, minimum history, missing/candidate states,
zero denominators, cold start, exact inventory anchors, store isolation, future
mutation, truncated replay, duplicate/mixed builds, June lock and output-column
exclusion. No full V3 feature build or new prediction metric is claimed.

Run the focused fixtures with:

```bash
uv run python -m unittest discover -s tests -p test_oos_features.py -v
```

## 6. Isolated input handoff

`data_platform.feature_handoff.export_feature_input(source_build_dir, output_root,
as_of)` is the only component in this handoff that opens the internal source
database. It opens it read-only, requires an Accepted build containing daily
state, verifies database/quality hashes and passing required checks, then copies
only `INPUT_COLUMNS` for confirmed pair-days through the requested cutoff.
Requests beyond the source cutoff or May 31 are rejected.

Each successful export creates a new immutable directory containing:

```text
feature-input-<id>/
  projection.duckdb        feature_input.daily_state plus empty meta tables
  daily_state.parquet     the same allowlisted projection, retaining native types
  run.json                source identity, cutoff, hashes, columns and code version
```

No Bronze/Silver relations, labels, future realized rows or attached source
database are copied. Source hashes are rechecked before the pending directory is
renamed to its final name. Failures retain a `.pending-feature-*` report without
publishing it; repeated exports never overwrite accepted directories. Empty
Bronze/Silver schemas created by the metadata DDL contain no source tables.

For feature calculation, verify the projection hash and use a separate working
copy of `projection.duckdb`; never mutate the accepted input file, whose hash is
part of the handoff. Do not attach the original source database. The exporter is
not a model/evaluation reader or an application security boundary.

Six synthetic integration tests cover projection usability, field/date isolation,
source immutability, nullable types, unconfirmed membership, source/quality
tampering, failed checks/builds, identity mismatch, cutoff lock and independent
repeated exports. This is not yet a real V3 replay or a Gold serving release.

```bash
uv run python -m unittest discover -s tests -p test_feature_handoff.py -v
```

### Command-line entry point

`python -m data_platform export-feature-input` invokes only the isolated exporter.
Specify `--source-build-dir` and an explicit ISO date `--as-of`; optionally set
`--output-root` (default `outputs/v3/platform/feature-inputs`). See the
[runbook example](../../data_platform/README.md#export-isolated-oos-feature-inputs).
Successful stdout is a JSON result containing the final directory and hashes.
Parse errors exit 2, export/integrity failures exit 1 and interruption exits 130;
failure messages go to stderr without reporting a successful artifact.

Eight CLI tests cover required/typed arguments, defaults, help without execution,
an actual `python -m` subprocess export over synthetic data, cutoff failures,
integrity failure, missing source and interruption. Existing command help is
also checked. These tests do not constitute a client-data replay or a feature
build: this command only prepares the safe input projection.

```bash
uv run python -m unittest discover -s tests -p test_feature_handoff_cli.py -v
```
