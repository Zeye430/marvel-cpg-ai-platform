# V3 state/sales feature validation

Validated the `oos_features.v1-state_sales` slice and isolated input handoff
against the authorized V3 dataset. This is engineering validation, not model
evaluation, estimator adoption or application publication.

## Input and scope

All eight active `data/raw` hashes and headers match the authorized V3 contract.
Their dataset fingerprint matches the accepted parent build:

```text
f5403651d8aa3e9492071a14706dd7a7f31aec88247f7af2f083ab9dca782675
```

Parent: `20261018T190828-f1c6ca9b2d85` from the
[Stage 4 validation](v3-medallion-stage-4-validation.md).
Code tested: `3f5732f`, following the isolated handoff/CLI commits. All 52 platform
tests passed before upload. The real-data export command was:

```bash
uv run python -m data_platform export-feature-input \
  --source-build-dir outputs/v3/platform/kpi-candidates/builds/20261018T190828-f1c6ca9b2d85 \
  --output-root outputs/v3/platform/feature-inputs \
  --as-of 2026-04-30
```

The parent contains authorized realized data through May, but this projection
and all feature validation stop at April 30. No May validation metrics or June
outcomes/labels were opened. Source-file hashing is an integrity check.

## Results

| Item | Result |
| --- | ---: |
| Projected input rows / feature rows | 2,121,506 / 2,121,506 |
| First / last scoring date | 2025-01-01 / 2026-04-30 |
| Stores | 221 |
| Store-SKU pairs | 4,375 |
| Historical rows with observed inventory | 1,948,765 |
| Historical rows with candidate estimated inventory | 172,722 |
| Historical rows with Unknown inventory | 19 |
| Rows with an eligible 28-day in-stock velocity | 2,078,608 |
| Required real-data checks / failures | 40 / 0 |

The full static calendar has 2,121,875 rows through April. The projection excludes
369 dates before a pair's first confirmed membership; it does not create
historically available assortment from a future first report. On April 30,
4,022 pair inventories are Observed and 353 are candidate Estimated.

Feature calculation used a separate working copy with a 1GB memory limit and
two DuckDB threads. The exporter used its existing default settings. No feature
calculation connected to the original Bronze/Silver database.

Checks cover grain, lineage, cutoff, exact input allowlist, row parity,
Observed/Estimated/Unknown separation, coverage bounds and N/A on zero demand
velocity. No source/evaluation tables or target/label columns are present in the
isolated feature artifact. Missing sales do not become observed zero sales;
candidate estimates do not enter observation-only historical velocity.

### Independent calculations

Selected 128 deterministic hash-sampled rows across the authorized history,
without using model labels or outcomes for sample selection. Independently
recomputed 18 fields using date-filtered aggregates rather than the feature
builder's window expressions:

- Expected/observed days and observed sales over seven and 28 days.
- Valid in-stock sample counts and velocity, including previous-seven-day history.
- Historical observed OOS counts and exact seven-day inventory changes.
- Sales acceleration and observed/candidate days cover, including NULL handling.

All sampled comparisons pass. Integer/NULL comparisons are exact; floating
comparisons use absolute tolerance `1e-10`.

### Temporal replay

Selected one store from each channel and compared every historical feature row
through March 31: **27,755 rows per replay mode**.

1. Rebuilt from input truncated at March 31: zero differing rows.
2. Changed sales/inventory/OOS after March 31 and rebuilt: zero differing
   historical rows through March 31.

This is a three-store real-data causal regression check alongside the synthetic
tests, not a claim that every upstream simulation assumption has been proven.
It checks feature transformations over supplied daily state; it does not certify
the natural missing-gap estimator or future model performance.

## Integrity and local evidence

The parent database, parent run/quality reports, accepted Silver pointer,
accepted projection database/Parquet and active V3 inputs are unchanged after
validation. The tested code-tree fingerprint is unchanged. The feature database
was built on a separate working copy, never inside the accepted input artifact.

Ignored local evidence:

```text
outputs/v3/platform/feature-inputs/feature-input-195fc1d2addc40b19ab311aed449bae6/
outputs/v3/platform/evaluation/feature-replay/check-f2b33a8a306a/
  baseline.json, projection.json, feature-build.json
  direct-checks.json, causality.json, quality.json, run.json
  features.duckdb, causal_sample.parquet, attempt-1.json
```

The first diagnostic summary used a reserved SQL alias after feature calculation
had completed. Its Failed report is retained as `attempt-1.json`; the summary
query was corrected and all checks completed. No business transformation or
client data was changed to obtain the passing result.

Final feature database SHA-256:
`794c9e19b7fe139cf79bfdcae5461ed240513147e446d3c46efe4824682144c2`.
Quality report SHA-256:
`fa075554974fca7f52288ff6d63c0ce83763a8308f149744ccb5f4446ea11d5f`.

## Remaining work

The full feature set still needs receipt/pending-order, promotion and audit
enrichment, followed by a governed feature-build runner and separate restricted
labels. Gold-only serving and negotiation context remain separate deliverables.
No model, threshold, ranking or Module 2 target result is produced here.
`application_ready` remains false; candidate missing-state limitations remain
explicit. Raw data, generated artifacts and the local development journal are
not included in the Git upload.
