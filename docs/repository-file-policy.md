# Repository File Policy

This policy separates team-shared project assets from local analysis and Codex working files.

## 1. Team-shared files

The following files should be committed after team review.

### Project and environment

- `README.md`
- `.gitignore`
- `.python-version`
- `pyproject.toml`
- `uv.lock`
- Docker and application files under the approved module folders

Only dependencies required by shared project code should be added to `pyproject.toml` and `uv.lock`.

### Product and project documentation

- Client brief and approved project documentation under `docs/`
- Product requirements and workflow specifications
- Data dictionary and analytical data model
- KPI definitions and approved missing-data policy
- Project bottlenecks and client decisions
- Data-version policy, manifests, and migration notes

Personal planning notes and Codex context files are excluded.

### Reviewed analysis

- Notebooks that the team has reviewed and can rerun
- Concise analysis reports that support team decisions
- Small charts or tables needed to explain an approved conclusion
- Final model metrics and documentation required by the course deliverables

Every reviewed notebook must state the data version it uses. V1 and V2 results must not be mixed.

### Shared source code and tests

- Application and module source code used by the team
- Reusable data-processing and model-training pipelines
- Tests that validate shared production or module code

Experimental code should be promoted to these folders only after review. Tests should be committed with the shared code they validate.

### Data metadata

- Data manifests with filenames, row counts, schemas, checksums, and version dates
- Data-loading instructions
- Small synthetic fixtures or approved samples for testing

## 2. Local-only files

The following files should remain on the developer's computer and should not be committed.

### Raw and restricted data

- `data/raw/`
- Updated client CSV files downloaded from Teams, OneDrive, or another approved location
- Temporary extracts containing client records

Raw v2 data should be distributed through the team's approved private file-sharing location. The updated `fact_sell_out.csv` is larger than GitHub's normal 100 MB single-file limit.

### Codex and experimental workspace

- `work/`
- One-off diagnostic scripts
- Notebook builder scripts
- Rendered document and slide previews
- Temporary experiment code that is not part of the selected product pipeline
- Local experiment tests that only validate temporary Codex analysis

### Generated results

- `outputs/`
- Intermediate predictions
- Cached features
- Serialized development models
- Temporary charts and reports

Team-approved final artifacts should be copied to an intentional shared location rather than committing the entire output directory.

### Local environment and caches

- `.venv/`
- `.pytest_cache/`
- `__pycache__/`
- `.ipynb_checkpoints/`
- Local package and tool caches
- Environment variables, credentials, and secrets

### Personal working documents

- `docs/project-context-handoff.md`
- `docs/eight-week-development-plan.md`, unless the team later approves it as the shared schedule
- Personal meeting notes and draft speaking scripts

## 3. Current local classification

### Share after review

- `data/README.md`
- `data/manifests/v2.md`
- `docs/data-versions/README.md`
- `docs/data-versions/v2-migration-file-classification.md`
- `docs/repository-file-policy.md`
- `notebooks/v2/README.md`

### Keep local for now

- `data/raw/`
- `work/`
- `outputs/`
- `modeling/` — current unreviewed V1 experiment implementation
- `tests/` — current tests for the local V1 experiment implementation
- `docs/modeling/` — current detailed V1 experiment notes
- `docs/project-context-handoff.md`
- `docs/eight-week-development-plan.md`
- Modified dependencies that are only needed by the local V1 experiments

The `modeling/`, `tests/`, and `docs/modeling/` folders may later be replaced or selectively promoted when the team approves the V2 pipeline. Do not upload them with a blanket `git add .`.

## 4. Data-version workflow

### V1 archive

1. The complete V1 repository is stored on `archive/v1-old-data`.
2. `v1-repository-final` identifies the complete immutable archive; `v1-data-baseline` identifies the earlier analytical freeze.
3. Do not merge V1 notebooks, raw data, exported figures, or metrics back into the active branch.

### Start V2

1. Create `data-v2-migration` from the latest reviewed Main commit.
2. Add the data manifests, version policy, schema changes, and local setup instructions.
3. Share the raw v2 data through the approved private file-sharing location.
4. Place the downloaded files in the Git-ignored `data/raw/` directory.
5. Create new analysis under `notebooks/v2/` and write generated outputs under `outputs/v2/`.
6. Merge only reviewed V2 analysis and reusable project code through pull requests.

## 5. Staging rule

Do not use `git add .` for this migration. Stage explicit reviewed paths, for example:

```bash
git add data/README.md
git add data/manifests/v2.md
git add docs/data-versions docs/repository-file-policy.md
git add notebooks/v2/README.md
```

Always run `git status --short` before committing.
