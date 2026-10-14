# Data Versioning

The project currently has two client-data versions. Do not combine rows from the two versions because the updated files were regenerated rather than appended to the original files.

## Active working data

`data/raw/` contains the local active dataset and is excluded from Git. It currently holds **v2**, received on 2026-10-09 and activated locally on 2026-10-13.

The v2 working set contains eight tables:

- `dim_guardrails.csv` — unchanged from v1
- `dim_products.csv` — updated in v2
- `dim_promotions.csv` — added in v2
- `dim_stores.csv` — unchanged from v1
- `fact_deliveries.csv` — added in v2
- `fact_execution_audits.csv` — unchanged from v1
- `fact_sell_in.csv` — regenerated in v2
- `fact_sell_out.csv` — regenerated in v2

## Archived v1 data

The original data, EDA, exported figures, and V1 dashboard are preserved on the `archive/v1-old-data` branch. The immutable `v1-repository-final` tag identifies the complete historical repository state. V1 raw data is not retained on the active V2 branch.

## Rules

1. New EDA, feature engineering, and models must use `data/raw/` and identify the data version as `v2`.
2. Existing v1 analyses remain historical references and must not be presented as v2 results.
3. Do not merge or append files retrieved from the V1 archive with v2 fact tables.
4. Keep raw client data out of new Git commits unless the team explicitly agrees on a secure data-storage policy.
5. Record row counts, columns, and SHA-256 hashes in `data/manifests/` whenever the client supplies a new version.
6. Write new analytical outputs under a version-specific path such as `outputs/v2/`.

See `docs/data-versions/` for the analysis archive and migration notes.
