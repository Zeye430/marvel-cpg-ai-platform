# V1 Data Baseline Freeze

**Status:** Ready for team review

**Data version:** v1 — original client dataset

**Data snapshot:** `marvel_data/`

## Purpose

This baseline preserves the analysis completed against the original six-table client dataset before the updated data was received. It provides a reproducible historical reference; it is not the active evidence base for v2 development.

## Frozen scope

### Data snapshot

The six tracked CSV files under `marvel_data/` are the authoritative v1 inputs. Their row counts and SHA-256 checksums are recorded in `data/manifests/v1.md`.

### Analysis notebooks

- `notebooks/exploratory/initial_data_review_william.ipynb`
- `notebooks/exploratory/marvel_deep_eda_product_insights.ipynb`
- `notebooks/modeling/missing_and_oos_pipeline.ipynb`

The deep EDA reads `marvel_data/`. The modeling notebook is also pinned to `marvel_data/` so that a future rerun cannot silently use the active v2 files in `data/raw/`.

### Stable project documentation

The existing requirements, workflow, data-model, KPI, and bottleneck documents remain part of the project history. Any numerical claim derived from the original data must be treated as a v1 finding and revalidated on v2.

## V1 findings that require v2 revalidation

- Sell-out store-day coverage and missingness mechanism
- OOS rate and seven-day target-label coverage
- Missing-day sales and inventory imputation results
- Sell-in, inventory, and replenishment relationships
- Discount, margin, and guardrail statistics
- Distributor behavior relative to retail channels
- Module 2 validation metrics, feature importance, and threshold

## Freeze rule

After this baseline is approved and merged, tag the reviewed Main commit as `v1-data-baseline`. Do not edit the tag. All new data-dependent work should identify itself as v2 and use a separate development branch.

## Excluded local work

The following are intentionally outside the frozen team baseline:

- `data/raw/`
- `work/` and `outputs/`
- unreviewed local `modeling/`, `tests/`, and `docs/modeling/`
- personal context, schedules, and temporary Codex artifacts
