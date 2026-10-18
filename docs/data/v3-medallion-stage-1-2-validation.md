# V3 Medallion stages 1-2 validation

Validated locally on 2026-10-18 using DuckDB 1.5.6. This is ingestion/Silver
acceptance, not model validation or application release.

Command: `uv run python -m data_platform build --publish-silver`.

Accepted build: `20261018T145910-58733de4a09c`. Input fingerprint:
`f5403651d8aa3e9492071a14706dd7a7f31aec88247f7af2f083ab9dca782675`.
Execution metadata records the code tree hash because the implementation has not
yet been committed. The accepted database/report hashes were checked against the
manifest after build completion.

## Results

| Check / population | Result |
| --- | --- |
| Required quality checks | 183 passed; zero required failures |
| Authorized realized-event cutoff | 2026-05-31 |
| Typed source tables | 8 |
| Additional shared Silver tables | 6 |
| Sell-out observed rows through cutoff | 2,073,376 |
| Static active store-SKU pairs | 4,375 |
| Calendar rows, Jan 1 2025–May 31 2026 | 2,257,500 |
| Missing pair-days with null observed measures | 184,124 |
| Receipts through cutoff | 134,742 |
| Receipts corroborated by observed Sell-out restocks | 123,623 |
| Receipts on unreported Sell-out days | 11,119 |
| Unexplained positive receipt mismatches | 0 |
| Consecutive observed inventory transitions checked | 1,899,743; all exact |
| Orders known by cutoff | 135,701 |
| Known orders without a posted receipt by cutoff | 959; planned fields retained |

Only full-file engineering integrity checks access complete Bronze. Persistent
Silver realized facts do not include June outcomes. No June outcome analytics,
future labels, model fitting or model evaluation is performed in this build.

Two local V3 builds reused the same immutable input snapshot and retained the
previous accepted build. The latest Silver manifest identifies its predecessor.

## Synthetic integration verification

Eight tests pass with `uv run python -m unittest discover -s tests -p
'test_data_platform.py' -v`. They cover missing-day receipts, unknown observed
values, planned/realized separation, overlapping campaigns, duplicate keys,
fractional units, changed headers, inventory mismatch, snapshot tampering,
concurrent writers, June cutoff rejection and failed-publication retention.
Replay compares business values across every Silver table, excluding execution
metadata, and checks that the previous accepted database bytes remain unchanged.

## Remaining work

Guardrail approval is the one outstanding warning: `Requires review`. It does
not block inventory acceptance and cannot authorize automated negotiation decisions.

The static assortment and source availability assumptions remain explicit;
unconfirmed historical membership cannot silently enter model features. Receipt
reconciliation does not establish the quality of a missing-sales estimator.

Stage 3 must implement the historical estimator, the four-day limit and shared
Gold daily state, then validate account-day block masking after recomputing all
historical inputs. Gold marts, controlled feature access, model release and
Gold-only serving are not part of this accepted Silver build.
