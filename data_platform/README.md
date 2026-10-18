# Shared V3 data platform

Implemented: architecture stages 1-2, an immutable input snapshot, text-only Bronze,
typed Silver sources, receipt reconciliation, common calendar, planned-order
projection and promotion calendar. Stage 3 adds a candidate Gold daily state and
corrected account-day masking diagnostics. Stage 4 adds shared store, commercial
and execution KPI marts. Models, serving views and incremental scheduling remain
subsequent stages.

See the [architecture](../docs/data/v3-medallion-architecture.md),
[source contract](contracts/v3/sources.json) and
[KPI/missing-day specification](../docs/solution-specifications/kpi-and-missing-store-day-handling.md).

## Run

Place the eight authorized CSV files in `data/raw/`, then run from the repository root:

```bash
uv sync
uv run python -m data_platform build
```

This builds and validates a candidate without changing the accepted pointer. To
accept the Silver handoff after the same checks:

```bash
uv run python -m data_platform build --publish-silver
```

Default calendar: 2025-01-01 through 2026-05-31. All realized Silver facts are
limited to the authorized cutoff. The CLI rejects a cutoff after May 31 while
June remains locked. An earlier simulation cutoff can be supplied:

```bash
uv run python -m data_platform build --as-of 2026-04-30
```

`--source-dir`, `--snapshot-root`, `--output-root`, `--calendar-start`,
`--memory-limit` and `--threads` are configurable. Paths are local; no cloud or
legacy Aiven connection is used. The current writer lock uses Unix `flock`.

For the stage 3 candidate and development-only experiment:

```bash
uv run python -m data_platform build --include-daily-state
uv run python -m data_platform evaluate-missing --per-length 100
```

See [historical estimation and corrected masking](../docs/data/v3-daily-state-and-masking.md)
for the estimator policy, outputs and release limitations. The optional candidate
does not authorize application use or establish the Module 2 prediction target.

For shared KPI marts and data version verification:

```bash
uv run python -m data_platform audit-data
uv run python -m data_platform build --include-kpi-marts
```

See [KPI marts and formulas](../docs/data/v3-shared-kpi-marts.md) and the
[Stage 4 validation record](../docs/data/v3-medallion-stage-4-validation.md). `data/raw`
is ignored and is not refreshed by a Git pull. The audit compares actual active
hashes to V3 and understands Git LFS content identities for the legacy path.

## Artifacts

The next data-layer increment is specified in the
[OOS feature contract](../docs/data/v3-oos-feature-contract.md). Its first isolated
state/sales feature slice has synthetic tests; complete enrichment, a safe input
exporter, pipeline integration, restricted labels and Gold-only serving remain
pending. Model/ranking development belongs to Module 2.

```text
data/raw/_snapshots/v3/<dataset_hash>/       byte-identical copies of all eight CSVs
outputs/v3/platform/builds/<build_id>/
  build.duckdb                             internal meta/Bronze/Silver build
  quality.json                             checks, tolerances and failure examples
  run.json                                 status, configuration, counts and lineage
outputs/v3/platform/published/
  silver.json                              atomic pointer to the accepted Silver build
  history/<publication_id>.json            immutable publication history
```

Generated artifacts remain ignored by Git. Each execution creates a new build;
the same input reuses its byte-preserved snapshot and does not append duplicate
Bronze records. A candidate contains one copy of each input record. Old accepted
builds are retained. This is deterministic snapshot rebuilding, not incremental
CDC or an Airflow DAG yet.

`build.duckdb` contains full original Bronze, including restricted June records.
It is an internal engineering artifact, **not** an application database or model
feature connection. Full-file header/type/key checks are ingestion integrity
checks; June outcome analysis, labels and model evaluation are not performed.
Persistent Silver realized facts stop at the development cutoff. A separate
Gold-only serving artifact and a controlled feature access layer are still to
be built. `application_ready` is therefore `false` in the Silver manifest.

## Implemented Silver relations

The eight typed client sources are accompanied by:

| Relation | Meaning |
| --- | --- |
| `active_assortment` | Static simulation membership, with first-observation availability retained |
| `store_sku_calendar` | Expected pair-days; missing observed sales/inventory/OOS stay null |
| `replenishment_events` | One event per actual delivery, with observed-restock corroboration |
| `order_schedule_events` | Only planned fields for orders known by the cutoff, including later-arriving orders |
| `inventory_reconciliation` | Exact balance checks on consecutive observed days only |
| `promotion_calendar` | Campaign membership by day/channel/category; overlapping campaigns remain separate |

`execution_latest_asof` is added when `--include-daily-state` is selected.
Representative assignments and future labels/model predictions are not generated.

The static assortment is derived from Sell-out pairs within the authorized
cutoff, not from June. Calendar rows before a pair's first report explicitly have
`assortment_is_confirmed_asof = false`; a historical feature builder must exclude
these unconfirmed rows unless an approved initial assortment registry establishes
prior membership. `last_observed_date` is snapshot metadata, not a historical
feature. Production requires effective-dated membership inputs.

Same-day event availability and the initial order schedule are simulation
assumptions, registered in `meta.policy_registry`. The file has no historical
schedule revisions or actual source availability timestamps. Campaign availability
is conservatively assumed at its start date. These assumptions must be replaced
with real source history when available.

## Checks and failure behavior

Required gates cover ordered headers, parser accounting, exact types (including
fractional-unit rejection), non-null values, natural keys, foreign keys, ranges,
invoice arithmetic, campaign scope/discount, delivery labels/fill rate, OOS state,
restock identity, receipt double counting, missing-day receipts, shelf allocation,
calendar cardinality and consecutive inventory balance.

Reference hashes/counts identify the original V3 delivery; they are informational
for subsequent inputs. Accepted snapshot bytes are checked on reuse. Guardrail
approval remains `Requires review`, which does not block valid inventory data.
Consumer reference price is not a supplied historical regular-price series;
the pipeline does not invent an exact retail-price identity from that reference.

On failure, the process exits nonzero, retains the snapshot and available failed
build evidence, and leaves `published/silver.json` unchanged. Inspect `quality.json`
for failed keys and `meta.quality_results` for SQL-accessible evidence. No rows are
silently discarded or repaired. A failure before a complete snapshot is retained
in its pending directory or build report; missing input files cannot be archived.

## Verification

```bash
uv run python -m unittest discover -s tests -v
```

Synthetic integration fixtures exercise missing-day receipts, future outcome
projection, duplicate keys/fractional units, changed headers, snapshot tampering,
inventory failures, replay, immutable prior builds, failed publication and writer
locking. The client-data build is a separate local validation, not a CI fixture.

The [stage 1-2 validation record](../docs/data/v3-medallion-stage-1-2-validation.md)
reports the accepted V3 build and checks, with remaining policy assumptions.
