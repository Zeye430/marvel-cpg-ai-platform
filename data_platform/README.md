# Shared V3 data-platform package

For **all four modules**, start with the [shared-data guide](../docs/data/README.md):
architecture, source provisioning, complete build, scoped reads and module routing.
This page is the package command reference, not a second onboarding guide.

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

## Commands

Run from the repository root in the locked Python 3.13 environment.
Every command exposes `--help`; the recommended full workflow is in the guide.

| Command | Purpose / boundary |
| --- | --- |
| `audit-data` | Source/header/hash diagnostic; inspect JSON, exit 0 alone is not a match |
| `build-all` | Business source/Gold, May development features/labels, June scoring features, serving; optional publication |
| `build` | Internal Bronze/Silver; `--include-kpi-marts` adds dependent internal Gold; not an application DB |
| `build --publish-silver` | Publish internal Silver, not business serving |
| `export-feature-input` | Allowlisted projection; explicit source build/cutoff; optional replenishment/context inputs |
| `build-features` | Development capped May 31; `--purpose scoring` permits June past-only features without labels |
| `build-labels` | Development capped May 31; June `--purpose test --test-release ...` requires approved freeze/May evidence |
| `build-serving` | New immutable business snapshot from matching source and latest-scoring feature artifacts; optional access policy |
| `publish-serving` / `rollback-serving` | Promote a verified snapshot or republish a verified historical version |
| `evaluate-missing` | Restricted March/April masking diagnostics; not model training or estimator approval |

Base-only commands default to `outputs/v3/platform/`; recommended `build-all`
explicitly uses `outputs/v3/platform/delivery/`. Do not confuse their pointers.
`build-serving` requires matching dataset/source and exact cutoff: June business
uses June **scoring**, not May development, features.

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

## Validation and ownership

```bash
uv run python -m unittest discover -s tests -v
git diff --check
```

Checks cover keys/arithmetic/receipts, calendar and KPI consistency, time windows,
future-mutation/replay, label isolation, scopes and artifact integrity. Each build
retains `run.json` / `quality.json`. Passing these checks is not model target
certification, guardrail approval or candidate-estimator adoption.

Business reaches June 30; model development stops May 31 and June test stays
evidence-gated. Reported is default; three separate Estimated candidate views
need explicit read opt-in and UI labeling. Access defaults to deny all. Missing
model outputs are unavailable, not zero. No application should connect to the
internal source or evaluation files.

Pipeline exits: 0 success (audit may still report mismatch), 2 invalid arguments,
1 runtime/quality failure, 130 interruption. Reader example additionally exits
3 for unavailable outputs or candidate estimates without opt-in.

Raw data, generated databases/evidence, real scopes and local notes stay outside
Git. Shared interface changes belong here; models, UI, simulator, NLQ/RAG,
scheduling and deployment belong to reviewed follow-on module/infrastructure work.
