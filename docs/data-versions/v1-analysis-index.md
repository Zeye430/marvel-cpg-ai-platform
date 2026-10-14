# v1 Analysis Archive Index

The following work was produced from client-data v1 and must not be presented as v2 evidence.

See `v1-freeze.md` for the frozen scope and tagging procedure.

## Notebooks

- `notebooks/exploratory/initial_data_review_william.ipynb`
- `notebooks/exploratory/marvel_deep_eda_product_insights.ipynb`
- `notebooks/modeling/missing_and_oos_pipeline.ipynb`

## Local-only modeling references

The following detailed experiment notes remain local and are not part of the frozen team baseline. They may be consulted during v2 redesign, but they should not be uploaded wholesale:

- `docs/modeling/missing-analysis-and-brief-baseline-results.md`
- `docs/modeling/module-2-development-protocol.md`
- `docs/modeling/module-2-development-results.md`
- `docs/modeling/replenishment-model-results.md`
- `docs/modeling/replenishment-training-analysis.md`

The team baseline retains the self-contained modeling notebook above. Any modeling documentation promoted later must be reviewed and updated for v2.

## Generated outputs

Existing modeling results under `outputs/` were generated from v1. They are retained locally for comparison but should be treated as archived. New v2 runs must write to `outputs/v2/` or a subdirectory below it.

## Documents requiring v2 review

The following documents mix stable product decisions with v1 data findings and should be updated after v2 EDA rather than archived wholesale:

- `docs/data/data-dictionary-and-analytical-data-model.md`
- `docs/project-bottlenecks.md`
- `docs/solution-specifications/kpi-and-missing-store-day-handling.md`
- `docs/project-context-handoff.md`

Until that review is complete, numerical references to approximately 30% missing store-days and v1 model performance should be read as v1-only findings.
