# V2 Migration File Classification

**Branch:** `data-v2-migration`

**Base:** latest reviewed `main`

**Purpose:** keep the active branch focused on the updated client dataset while preserving all v1 work in the archive.

## Migration prerequisite

Before removing v1 assets from the active branch, copy the following recently merged teammate work into `archive/v1-old-data`:

- `initial_data_review_sanika.ipynb`
- `eda_figures_sanika/`
- the latest v1-dependent Module 1 implementation and documentation

These files entered Main after the first V1 archive commit. They must not be deleted from the active branch until the archive branch has been verified to contain them.

## Remove from the active V2 branch

These files are direct outputs or inputs of the original dataset. They remain accessible through the V1 archive branch and Git history.

| Path | Reason |
|---|---|
| `marvel_data/` | Complete v1 raw-data snapshot, including the old six-table ERD |
| `initial_data_review_sanika.ipynb` | V1 EDA; reads `marvel_data/` and reports the old approximately 30% missingness |
| `eda_figures_sanika/` | Figures generated from the V1 EDA |
| `notebooks/exploratory/initial_data_review_william.ipynb` | V1 initial review |
| `notebooks/exploratory/marvel_deep_eda_product_insights.ipynb` | V1 deep EDA and product findings |
| `notebooks/modeling/missing_and_oos_pipeline.ipynb` | V1 missing-data and OOS experiments |

Do not replace these files in place with v2 analysis. Create reviewed v2 notebooks under `notebooks/v2/` so the data version is explicit.

## Keep without data-driven changes

These files describe the stable project, course requirements, or team workflow.

| Path | Treatment |
|---|---|
| `.gitignore` | Keep; confirm local raw data and generated outputs remain ignored |
| `.python-version` | Keep |
| `docs/Marvel_CPG_Challenge_Brief.docx` | Keep as the authoritative client brief |
| `docs/industry-research-guide.md` | Keep |
| `docs/product/product-requirements-and-user-workflow.md` | Keep; verify only if the client changes product requirements |
| `README.md` | Keep the stable project overview and team workflow; revise data-version instructions separately |

## Keep, but revise for V2

These are useful project assets, but they contain v1 paths, schemas, assumptions, numbers, or outputs.

| Path | Required V2 work |
|---|---|
| `docs/data/data-dictionary-and-analytical-data-model.md` | Add `dim_promotions` and `fact_deliveries`; update regenerated table schemas, grains, relationships, row counts, and coverage |
| `docs/solution-specifications/kpi-and-missing-store-day-handling.md` | Replace v1 coverage statistics; reassess the missing-day experiment and operational estimation policy using the approximately 8.1% v2 gap |
| `docs/project-bottlenecks.md` | Reclassify the old 30% missingness issue and add the new delivery/promotion implications |
| `module_1/app.py` | Module 1 owner follow-up: change the data source from `marvel_data/` to the authorized v2 loader and rerun every KPI and chart |
| `module_1/docker-compose.yml` | Module 1 owner follow-up: replace the v1 volume mount with the approved local v2 data path |
| `module_1/README.md` | Module 1 owner follow-up: replace v1 metric values and document v2 setup |
| `module_1/Dockerfile`, `module_1/requirements.txt`, `module_1/dockerignore` | Leave unchanged in this migration; the Module 1 owner will adjust them if required |
| `pyproject.toml`, `uv.lock` | Keep the shared environment; add only dependencies required by selected v2 code |

No v1 numerical result should be retained in a v2 document merely because its formula remains valid.

## Add to the active V2 branch

The first migration commit should contain documentation and scaffolding, not raw client data.

| Path | Purpose |
|---|---|
| `data/README.md` | Explain how authorized team members obtain and place v2 data locally |
| `data/manifests/v2.md` | Record v2 filenames, row counts, version status, and checksums |
| `docs/data-versions/README.md` | State that v2 is active and point to `archive/v1-old-data` and `v1-data-baseline` for historical work |
| `docs/repository-file-policy.md` | Define shared versus local-only assets |
| `docs/data-versions/v2-migration-file-classification.md` | Record this migration decision |
| `notebooks/v2/README.md` | Define where new versioned analysis belongs |

Later reviewed additions may include v2 EDA notebooks, reusable pipelines, production tests, and final model documentation.

## Keep local

Do not stage these paths during the migration:

- `data/raw/`
- `work/`
- `outputs/`
- current experimental `modeling/`, `tests/`, and `docs/modeling/`
- `docs/project-context-handoff.md`
- `docs/eight-week-development-plan.md` unless the team adopts it
- notebook checkpoints, caches, local environments, and temporary render files

Production code and its tests may be promoted later after review; the current local V1 experiment implementation is not part of the initial V2 migration.

## Migration order

1. Complete and verify the V1 archive with the teammate assets merged after the original freeze.
2. Remove the listed V1-only paths from `data-v2-migration`.
3. Add the V2 manifest, setup instructions, version policy, and notebook scaffold.
4. Revise the analytical data model and data-dependent product specifications.
5. Rebuild EDA from v2; coordinate the separate Module 1 migration with its owner.
6. Merge the migration branch only after team review.
