# V3 stage 4 validation

Validated 2026-10-18 with DuckDB 1.5.6. This is an internal shared-KPI build,
not an application deployment, estimator adoption or new model benchmark.

## Input identity

All eight active `data/raw` file hashes match the authorized V3 contract.
The four corrected fact files were also compared directly with their entries
in the original client `marvel_data 1.zip`; all four match byte-for-byte.
The retained dimensions, including the reviewed-as-pending guardrail baseline,
are deliberately not blindly replaced from the ZIP.

Input fingerprint:
`f5403651d8aa3e9492071a14706dd7a7f31aec88247f7af2f083ab9dca782675`.
Realized development events stop at 2026-05-31. June outcome evaluation is locked.
A separate 2026-10-18 audit of fetched `origin/main` at `77374fd` found old V2
fact content at the legacy `new_marvel_data` path. Git LFS content OIDs, rather
than pointer-text hashes, were compared with the local V2 archive. This does not
describe the live Aiven database, which was not queried. The repeatable
`audit-data` command compares the active inputs with a locally fetched Git ref.

## Accepted candidate and checks

Command used:

```bash
uv run python -m data_platform build --include-kpi-marts --output-root outputs/v3/platform/kpi-candidates
```

Build: `20261018T190828-f1c6ca9b2d85`. All 204 required checks pass, with one
existing nonblocking guardrail-review warning. Twenty-eight synthetic and
integration tests pass. Build and quality hashes match the stored manifest;
the code tree fingerprint matches the tested implementation.

| Relation | Rows |
| --- | ---: |
| `gold.mart_store_sku_daily` | 2,257,500 |
| `gold.mart_store_day` | 228,072 |
| `gold.mart_commercial_period` | 1,776,250 |
| `gold.mart_execution_visit` | 254,566 |
| `gold.metric_definitions` | 11 |

## Source-to-mart parity

The comparison selects one view, period family and scope at a time. Reported
weekly event-date flows, for example, are never summed across overlapping
weekly/monthly/diagnostic representations.

| Component | Direct Silver total and matching mart total |
| --- | ---: |
| Reported store-SKU-days | 2,073,376 |
| Reported OOS rows | 161,243 |
| Consumer units | 11,199,213 |
| Invoice units | 10,812,806 |
| Delivered units | 12,601,494 |
| Audit observations, all-category scope | 196,930 |

Fixture tests establish that a known zero MSL item gives Noncompliant even
when another item is Unknown; all unknown-required states do not become failure;
stores without applicable MSL are N/A; aggregate OOS/planogram use components
rather than averages of subgroup percentages; missing invoice flow is not zero
without an explicit assumption; zero Sell-out yields N/A gap percentage; invoice
lines and receipts do not multiply each other; and a partial first source-week
comparison retains its later invoice without shifting official event history.

The gap rollup has matched `comparable_*` components: unknown flows are excluded
from both numerator and denominator, not only from one side.

## Resource failure and correction

The first full attempt (`20261018T190644-d43b8bd49158`) failed at the default
1GB memory limit while aggregating all commercial periods together. The failed
run and its evidence are retained; no accepted pointer changed.

The corrected implementation processes period families separately and adds
repeated build/dimension attributes after aggregation. Insertion order is
preserved while assigning original Bronze row ordinals, then disabled for
analytical work whose order is defined by keys/windows. The successful replay
used the same 1GB limit and two threads; increasing memory was not required.
Consumer financial totals use decimal arithmetic, and interrupt tests now
verify a terminal `Interrupted` status rather than leaving a run as Running.

## Remaining boundaries

Invoice extract completeness is **not** assumed in this accepted candidate.
Source-week diagnostics are explicitly unsafe as period-end model features.
Operational estimates remain candidate because natural missing-gap semantics
are unresolved. Guardrail interpretation, actual historical costs, approved MSL
applicability and audit freshness rules are still policy questions.

The existing accepted Silver publication is unchanged. No raw files, Git
commits, remote branch contents, Aiven data, legacy Module 1 paths, model
thresholds or application serving database are updated by this build.
