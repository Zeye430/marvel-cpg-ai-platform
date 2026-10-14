# Data-Version Analysis Policy

## v1 archive

The original data, notebooks, exported figures, and V1 dashboard are stored on the `archive/v1-old-data` branch. The `v1-repository-final` tag identifies the complete archived state; `v1-data-baseline` identifies the earlier analytical freeze. V1 conclusions are historical baselines only.

## v2 active work

All new analysis must use the active v2 files in `data/raw/`. New notebooks should be created under `notebooks/v2/`, and generated results should use `outputs/v2/`.

Before reusing a v1 conclusion, reproduce the calculation on v2. This applies in particular to:

- missing store-day rate and missingness mechanism;
- OOS rate and seven-day target coverage;
- inventory forward-fill experiments;
- sell-in versus sell-out comparisons;
- discount and margin analysis;
- replenishment inference;
- Module 2 model metrics and thresholds.

Product requirements that do not depend on measured data can remain in the main documentation, but numerical claims must identify the data version.
