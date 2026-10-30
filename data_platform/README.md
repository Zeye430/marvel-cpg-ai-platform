# Shared V3 data-platform package

**The shared business data is published to Aiven.** Module developers should
start with the [Aiven connection tutorial](../docs/aiven-connection.md) to configure
their read-only connection and test the existing views. No raw-data rebuild or
new upload is needed. This page is mainly the maintainer's package reference;
module developers own their backend data reads and UI integration.

For **all four modules**, start with the [shared-data guide](../docs/data/README.md):
architecture, source provisioning, complete build, scoped reads and module routing.
This page maps the implementation to its responsibilities and provides command
reference. Setup and per-module instructions remain in the shared-data guide.

## Start here: reuse the pipeline, integrate your module

The shared implementation already covers **CSV extraction, Bronze/Silver/Gold
transformation, validation, local business snapshots and optional PostgreSQL
publication**. Module developers do not need to build another ETL pipeline.
One shared implementation keeps dates, missing-data handling, KPI denominators
and field meanings consistent across all four modules.

**Alternative local data path:** obtain a verified
serving directory and approved account scope from the data maintainer. The
directory contains `business.duckdb`, `run.json` and `quality.json` from the same
accepted build. Git checkout supplies code, not these ignored data artifacts.
No Aiven account, database URL, Docker or PostgreSQL server is needed for this
local data-reading path; install additional application dependencies from your
module's guide.

```python
from data_platform.readers import open_business_reader

# Replace with the accepted serving directory supplied by the maintainer.
reader = open_business_reader(artifact_dir="/YOUR/accepted/serving/directory")
schema = reader.schema()
# Business-row reads require a principal_id from the authenticated session.
# An unconfigured build denies access until account scope is assigned.
```

For a publication pointer or copied artifact, use the runnable
[local read example](../examples/read_shared_data.py). Read your module guide
below for view names and business caveats. Choose a model-output snapshot when
predictions/ranking are required: `build-all` alone does not train a model or
create those outputs.

The first shared Aiven release is active and its TLS, reader permissions and
scoped reads have been checked. Future publications remain explicit operator
work, not a prerequisite for each developer's module work. Aiven and self-managed
PostgreSQL use the same shared publication contract; there is no second ETL.
Database availability does not certify app deployment or production auth. The supplied
PostgreSQL Compose file is **test-only**, not a finished production deployment
or backup configuration.

## How the existing pieces fit together

```text
audit-data                                  separate input/version preflight
build-all -> workflow.build_all()
  -> pipeline.build()                       raw snapshot + Bronze/Silver/internal Gold
  -> build_features() + build_labels()      isolated development artifacts
  -> build_features(purpose="scoring")      latest point-in-time scoring inputs
  -> serving.build_serving()                verified business.duckdb package
       -> shared reader -> module services / UI

Separately verified Module 2 outputs
  -> model_outputs                           new business snapshot with model outputs

Later, for the selected verified business snapshot:
postgres_cli -> postgres_publisher           inspect / import / validate / activate
  -> PostgreSQL shared release -> shared reader -> module services / UI
```

`build-all` does not call `audit-data`, open a cloud connection or unlock test
labels. Local `--publish` selects a local serving snapshot; it does **not** upload
anything to PostgreSQL. Cloud publication has separate explicit commands below.

## Code map: which script does what?

Paths below are relative to `data_platform/` unless stated otherwise. These are
existing building blocks, not scripts every module developer must run separately.

| Responsibility | File and main entry | Input -> output / boundary |
| --- | --- | --- |
| Command routing | [__main__.py](__main__.py): `main()` | Parses `python -m data_platform ...` and calls existing functions |
| Input audit | [version_audit.py](version_audit.py): `audit_data()` | Local files + source contract -> header/hash report; inspect the report, not only exit code; does not clean data |
| Full orchestration | [workflow.py](workflow.py): `build_all()` | Approved sources -> component artifacts and workflow `run.json`; no training/cloud publication |
| Internal build | [pipeline.py](pipeline.py): `BuildConfig`, `build()` | Coordinates ingestion, Silver and requested internal Gold -> `build.duckdb`, quality report and provenance |
| Extract / Bronze | [ingestion.py](ingestion.py): `snapshot_inputs()`, `load_bronze()` | Eight CSVs -> immutable raw snapshot, original Bronze records and source lineage |
| Silver | [silver.py](silver.py): `typed_sources()`, `source_business_checks()`, `shared_relations()` | Bronze -> typed facts, checked relationships, assortment/calendar and receipt foundations |
| Daily state / KPI Gold | [estimation.py](estimation.py): `build_daily_state()`; [marts.py](marts.py): `build_kpi_marts()` | Silver -> daily states, coverage/Unknown handling, separate candidate estimates and shared KPI/period/audit marts |
| ML feature preparation | [feature_handoff.py](feature_handoff.py), [feature_runner.py](feature_runner.py): `build_features()` | Allowlisted projections -> isolated development or latest-scoring `features.duckdb`; no future outcomes as predictors |
| ML outcomes | [labels.py](labels.py): `build_labels()` | Observed outcomes -> isolated `labels.duckdb`; development and controlled test access are separate; never an app input |
| Local business delivery | [serving.py](serving.py): `build_serving()`; [negotiation.py](negotiation.py): `build_negotiation_context()` | Matching accepted source/scoring artifacts -> curated `business.duckdb`, including negotiation context; base prediction/ranking outputs are unreleased |
| Artifact / quality verification | [artifacts.py](artifacts.py), [quality.py](quality.py) | Check identity, integrity and quality; directory existence or a familiar table name is not verification |
| Model-output delivery | [model_outputs.py](model_outputs.py): `build_serving_with_model_outputs()` | Matching base snapshot + verified scoring/ranking/evidence packages -> new business snapshot; no model training or estimator approval |
| Module read interface | [readers.py](readers.py): `open_business_reader()`, `open_configured_reader()`; [access.py](access.py): `ServingReader` | Verified DuckDB artifact or published PostgreSQL release -> bounded, principal-scoped reads; not arbitrary SQL |
| PostgreSQL operator entry | [postgres_cli.py](postgres_cli.py): `main()` | Explicit publisher commands -> operator results; not called on page/chat requests |
| PostgreSQL publication / Load | [postgres_publisher.py](postgres_publisher.py): `inspect_serving_source()`, `load_postgres_candidate()`, `validate_postgres_release()`, `activate_postgres_release()` | Selected verified business package -> isolated PostgreSQL candidate and explicit publication; no raw-data transformation |
| PostgreSQL read / support | [postgres_reader.py](postgres_reader.py): `PostgresServingReader`; [postgres_config.py](postgres_config.py), [postgres_schema.py](postgres_schema.py), [SQL definitions](sql/postgres_serving.sql) | Fixed release + explicit configuration/type mapping -> compatible shared reads; app and publisher credentials are separate |

Read examples: [examples/read_shared_data.py](../examples/read_shared_data.py)
(local pointer/artifact), [examples/read_postgres_data.py](../examples/read_postgres_data.py)
(shared PostgreSQL reads). These are trusted development tools, not login APIs.

### What teammates should reuse versus develop

| Role / task | Reuse | Develop or configure |
| --- | --- | --- |
| Data maintainer | `audit-data`, `build-all`, shared contracts and quality checks | Approved inputs/scopes; deliver one identified accepted snapshot |
| Module developer | Shared business views, reader and module guide | Data adapter, UI, scoped queries and module-specific behavior; no independent raw-to-Gold ETL |
| Offline Module 2 developer | Isolated features/labels, temporal policy and existing model workflow | Authorized model work and verified output delivery, separate from UI connections |
| Database operator | Existing PostgreSQL publisher and reader | Approved target, CA, capacity, credentials, scopes, publication and consumer checks; not new Silver/Gold SQL |

Joining/renaming published fields, building a reviewed scoped SQL sandbox, and
calculating labeled hypothetical negotiation scenarios are application work.
Preserve shared metric definitions, data status and provenance; do not overwrite
shared facts. Store scenario/chat/action records outside immutable snapshots.

If a wrapper is useful, call existing entry points rather than copy their SQL
into `ETL/` or a module. Similarly named Silver/Gold tables are not automatically
compatible with the shared reader. The shared cloud path publishes approved
business views into `platform_*` namespaces, not every internal table. Extend
shared contracts/tests for new requirements; do not use `--drop-schemas` or a
legacy raw-table loader to publish a shared release.

## Entry points

| Need | Reference |
| --- | --- |
| Module 1: KPIs / Store 360 | [Module 1 guide](../docs/data/v3-module-1-integration.md) |
| Module 2: features, labels, scoring, risk / visits | [Module 2 guide](../docs/data/v3-module-2-integration.md) |
| Module 3: negotiation context | [Module 3 guide](../docs/data/v3-module-3-integration.md) |
| Module 4: controlled business queries / knowledge assistant | [Module 4 guide](../docs/data/v3-module-4-integration.md) |
| Serving fields / grains | [Gold dictionary](../docs/data/v3-gold-data-dictionary.md) |
| Governed business rules | [KPI/missing-day specification](../docs/solution-specifications/kpi-and-missing-store-day-handling.md) |
| Executable interfaces | [Source contract](contracts/v3/sources.json), [module contract](contracts/v3/module-data-contract.json), [read example](../examples/read_shared_data.py) |
| Shared PostgreSQL (opt-in) | [PostgreSQL publication](#shared-postgresql-publication-opt-in), [column/type contract](contracts/v3/postgres-serving.json), [PostgreSQL read example](../examples/read_postgres_data.py) |

## Commands

Run from the repository root in the locked Python 3.13 environment.
Every command exposes `--help`; the recommended full workflow is in the guide.

| Command | Purpose / boundary |
| --- | --- |
| `audit-data` | Source/header/hash diagnostic; inspect JSON, exit 0 alone is not a match |
| `plan-evaluation` | Metadata-only strict rolling-30-day calendar plan from an explicit accepted base build; prints JSON, does not unlock test or alter builders |
| `build-all` | Source/Gold, development features/labels, latest scoring features and serving; fixed profile by default, `--rolling-evaluation` opts into one shared 30-day plan |
| `build` | Internal Bronze/Silver; `--include-kpi-marts` adds dependent internal Gold; not an application DB |
| `build --publish-silver` | Publish internal Silver, not business serving |
| `export-feature-input` | Allowlisted projection; explicit source/cutoff; optional `--temporal-plan FILE` and replenishment/context inputs |
| `build-features` | Legacy fixed cap by default; `--temporal-plan FILE` enforces plan development/scoring boundaries without labels |
| `build-labels` | Legacy fixed folds by default; `--temporal-plan FILE` builds source-bound rolling development folds only; June legacy test remains evidence-gated |
| `build-serving` | New immutable business snapshot from matching source and latest-scoring feature artifacts; optional access policy |
| `publish-serving` / `rollback-serving` | Promote a verified snapshot or republish a verified historical version |
| `evaluate-missing` | Restricted March/April masking diagnostics; not model training or estimator approval |

Base-only commands default to `outputs/v3/platform/`; recommended `build-all`
explicitly uses `outputs/v3/platform/delivery/`. Do not confuse their pointers.
`build-serving` requires matching dataset/source and exact cutoff: June business
uses June **scoring**, not May development, features.

To preview rolling evaluation dates without querying outcomes or writing files:

```bash
uv run python -m data_platform plan-evaluation \
  --source-build-dir /YOUR/accepted/base/build-directory
```

The cutoff/history come from the verified source manifest, not a machine clock
or date override. Output `oos_temporal_plan.v1` includes provenance, deterministic
plan identity, reserved/eligible/purged/pending dates and latest prediction dates.
It remains `Temporal_only` / `test_access=Locked`: not model approval, a complete
evaluation release or observed-label coverage. `build-labels --temporal-plan FILE`
can consume a saved canonical plan for development labels/partitions. Its
explicit `--as-of` must equal the plan's validation outcome end and its source
must match exactly. Controlled plan-aware test access is separate and requires
its own release evidence and exposure-ledger checks. `build-features` and
`export-feature-input` also accept `--temporal-plan FILE`, enforcing the exact
purpose cutoff/source. Default calls retain the fixed V3 profile. To build all
components with one plan:

```bash
uv run python -m data_platform build-all \
  --as-of 2026-06-30 --rolling-evaluation --assume-complete-snapshot \
  --output-root outputs/v3/platform/rolling-delivery
```

The workflow saves `temporal-plan.json` beside its run manifest. Development
features/labels stop at the validation outcome cutoff, latest-scoring features
and business serving reach the source cutoff, and test remains locked. No model
is trained and no real test-release evidence is generated. Omit `--publish` to
keep this an unpublished candidate. Future-source verification/provisioning
and rolling test release remain separate work.
See the [Module 2 guide](../docs/data/v3-module-2-integration.md) for examples.

## Publication and rollback

Use accepted directories/IDs from your own manifests, not another machine's paths:

```bash
uv run python -m data_platform publish-serving \
  --output-root outputs/v3/platform/delivery/serving \
  --artifact-dir /YOUR/accepted/serving/build-directory
uv run python -m data_platform rollback-serving \
  --output-root outputs/v3/platform/delivery/serving \
  --publication-id YOUR_PREVIOUS_PUBLICATION_ID
```

Both operations verify artifacts and retain versioned history; do not edit
accepted databases/manifests, delete good builds or select the newest directory.
A copied artifact is opened explicitly with the reader's `--artifact-dir`;
a machine-specific publication pointer is not a portable deployment config.

## Shared PostgreSQL publication (opt-in)

Processing stays local in DuckDB. A verified, application-ready serving snapshot
can then be copied **once** into PostgreSQL (`shared_postgres_serving.v1`) so every
module can read the same release over the network with the same scoped reader
API. Install the optional driver with `uv sync --frozen --extra postgres`.

```text
verified business.duckdb --inspect-source--> load (candidate) --> validate --> activate
                                                         rollback <-- publication history
```

| Command (`python -m data_platform.postgres_cli ...`) | Effect |
| --- | --- |
| `inspect-source --artifact-dir DIR` | Verifies the local snapshot against the column/type contract; no database |
| `preflight` | SELECT-only: server version, privileges, platform state, release list and sizes |
| `init` | Creates `platform_meta`, `platform_access`, `platform_serving` and role `platform_serving_reader` (NOLOGIN) once |
| `load --artifact-dir DIR` | Streams every approved view into a new isolated `platform_release_<id>` schema (≤10,000-row COPY chunks), then validates it; identical input (same run record, quality report and database bytes) is re-verified and reused, never copied twice |
| `validate --release-id ID` | Re-verifies types, view definitions, write guards, row counts, keys/grains, ranges, KPI components and full typed content digests; a failure exits non-zero and marks the release `Failed` |
| `activate --release-id ID` | Re-verifies the payload and points `platform_serving.vw_*` and the current-release record at it in one transaction |
| `rollback --publication-id ID` | Re-verifies and re-publishes an earlier publication's release; history is appended, never edited |
| `configure-scopes --policy-file FILE` | Replaces store-scope assignments (same JSON format as the DuckDB access policy) |

`inspect-source` and `preflight` are read-only. `init`, `load`, `activate`,
`rollback` and `configure-scopes` are **dry runs** unless given `--apply` and
`--confirm-database <target database name>`. `validate` has no dry run: it is an
explicit operator operation that records its result in the release registry and,
on failure, marks the release `Failed` and withdraws reader access to it (exit
status 1). Connection settings come only from environment variables (never from
arguments or a legacy `DATABASE_URL`):

| Variable | Used by |
| --- | --- |
| `SHARED_DATA_PUBLISH_DATABASE_URL` | Operator CLI (schema owner credential) |
| `SHARED_DATA_DATABASE_URL` | Applications and the read example (read-only credential) |
| `SHARED_DATA_SSLROOTCERT` | CA certificate file; remote targets require `sslmode=verify-full` |
| `SHARED_DATA_BACKEND` | `duckdb` (default) or `postgres` for applications that support both |
| `SHARED_DATA_RELEASE_ID` | Optional: pin an earlier *published* release instead of the current one |
| `SHARED_DATA_PUBLISH_STATEMENT_TIMEOUT_SECONDS` | Optional operator statement timeout (default 3600) |

Only the 18 named serving views are exported (`gold.account_scope`, source quality
tables, Bronze/Silver, features, labels and model files never are). Store scopes
live in `platform_access`, configured separately, and are not changed by
activation or rollback; no assignment means no access. Existing tables in other
schemas (for example `public.*`) are never read, altered or dropped. The
application login must be a member of `platform_serving_reader` only: it can read
published views and release metadata, and cannot write, create objects or read
candidates. Readers pin one published release for their lifetime (restart the
app to pick up a newly activated release).

A release is identified by its source run record, quality report and database
bytes: the same source artifact ID with different bytes is refused as a conflict.
Imported payload tables reject writes (a statement trigger); verification also
detects changes made by bypassing it. A release that fails verification is marked
`Failed` and is never activated. If it is published, applications lose access to
it immediately (pinned readers and `platform_serving.vw_*` fail closed); history
and the current-publication record are left as they are. Recover with
`rollback --publication-id <earlier publication>` or by loading and activating a
newly built serving artifact; a published release is never reloaded in place.

A release schema is touched only if this publisher created it (its recorded
schema OID matches); a pre-existing schema with the same name is refused and left
untouched, and dropping an own failed candidate never cascades into other objects.
At most three release payloads may be stored at once, **in any state** (current,
rollback, candidates, failed or interrupted imports); a further distinct load is
refused and lists the stored payloads and their sizes. This version has no
clean-up command and no automatic pruning: agree an explicit clean-up procedure
before a fourth payload is needed.

Integration tests use a disposable local server only, never a shared database:

```bash
docker compose -f compose.postgres-test.yml up -d --wait
export SHARED_POSTGRES_TEST_DATABASE_URL=postgresql://synthetic_admin:synthetic-disposable-only@127.0.0.1:55437/shared_serving_synthetic_test
SHARED_POSTGRES_REQUIRE_TEST_DB=1 uv run --frozen --extra postgres --extra app --extra module2 \
  python -m unittest tests.test_postgres_integration -v
docker compose -f compose.postgres-test.yml down -v   # removes only this test container and its volume
```

The tests refuse any target that is not a numeric loopback address, the marked
test database and a server started with the disposable-test marker.

## Validation and ownership

```bash
uv run python -m unittest discover -s tests -v
git diff --check
```

Checks cover keys/arithmetic/receipts, calendar and KPI consistency, time windows,
future-mutation/replay, label isolation, scopes and artifact integrity. Each build
retains `run.json` / `quality.json`. Passing these checks is not model target
certification, guardrail approval or candidate-estimator adoption.

The current supplied business data reaches June30. Legacy development is capped
at May31; explicit rolling mode derives the cutoff from its source-bound plan
(also May31 for a June30 source). Neither mode unlocks real test automatically.
Reported is default; three separate Estimated candidate views
need explicit read opt-in and UI labeling. Access defaults to deny all. Missing
model outputs are unavailable, not zero. No application should connect to the
internal source or evaluation files.

Pipeline exits: 0 success (audit may still report mismatch), 2 invalid arguments,
1 runtime/quality failure, 130 interruption. Reader example additionally exits
3 for unavailable outputs or candidate estimates without opt-in.

Raw data, generated databases/evidence, real scopes and local notes stay outside
Git. Shared interface changes belong here; models, UI, simulator, NLQ/RAG,
scheduling and deployment belong to reviewed follow-on module/infrastructure work.
