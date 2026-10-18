# V3 shared KPI marts

Stage 4 provides common KPI components for the four modules. It does not migrate
the legacy Module 1 app, release a model or create a serving database.

## Run and verify the input version

From the repository root:

```bash
uv run python -m data_platform audit-data --output-path outputs/v3/platform/version-audit.json
uv run python -m data_platform build --include-kpi-marts
```

`audit-data` reads the active `data/raw` hashes and the content OIDs of tracked
Git LFS pointers at the locally fetched `origin/main`. Run `git fetch origin`
first when a fresh GitHub comparison is needed. Source hashes, not filenames,
notebook labels or Git branch names, establish input identity. An audit does
not download raw files, change a branch or modify any database.

`--include-kpi-marts` also builds its shared daily-state dependency. Artifacts
remain internal and ignored. No accepted pointer is changed without an explicit
publication flag, and `application_ready` remains false. The default realized
event cutoff stays 2026-05-31; June outcome analysis is not opened.

## Store-day components

`gold.mart_store_day` has one row per accepted input build, date, account and
metric view. `reported_only` uses observed states; `operational_estimate` uses
observed states first and eligible candidate estimates second. It records
expected, observed, estimated, known and unknown counts rather than only rates.

MSL state is Noncompliant if any applicable MSL item is known zero, Compliant
if every applicable MSL item is known positive, and Unknown otherwise. No
applicable MSL item gives N/A. The applicability assumption is the active
assortment combined with global `is_msl`, and remains subject to review.

For any store/region/date rollup, sum components before dividing:

```sql
SELECT date,metric_view,
       100.0*sum(oos_sku_count)/nullif(sum(known_inventory_sku_count),0) AS oos_rate_pct,
       100.0*sum(msl_compliant_store_count)/nullif(sum(msl_assessable_store_count),0) AS msl_pct,
       100.0*sum(msl_assessable_store_count)/nullif(sum(msl_eligible_store_count),0) AS msl_assessment_coverage_pct
FROM gold.mart_store_day
GROUP BY date,metric_view;
```

Unknown inventory is excluded from the OOS rate and separately counted.
Unknown MSL is not a measured failure. Lower/upper bounds and assessment
coverage accompany compliance. Zero denominators produce NULL/N/A.

The store-day mart has an all-active-SKU scope. A SKU/category-filtered KPI must
first recompute its components from the daily detail for that scope; filtering
store-day totals is not a valid substitute.

## Commercial periods

`gold.mart_commercial_period` has a declared week/biweek/month, store, SKU,
metric view and alignment basis. Weeks begin Monday; biweeks use the fixed
Monday 2025-01-06 anchor; months use calendar boundaries. Traditional Trade
weekly rows remain available but are flagged as not the recommended primary
period. Partial beginning/end periods are explicitly identified.

`event_date` is the official view: invoice date for Sell-in and actual sale
date for Sell-out. The weekly `source_week_diagnostic` maps an invoice to the
prior week only for retrospective comparison. It keeps the original invoice
date and is explicitly unsafe for period-end historical features. An invoice
expected after the build cutoff remains Awaiting invoice date, never zero.
The diagnostic keeps invoices for a partial first week even when its Monday
precedes the available calendar start; that week remains partial.

Invoices, sales and receipts are each aggregated before joining, preventing
many-to-many multiplication. Invoice units never replace Delivery receipt
units. `delivered_out_gap_units` is a separate diagnostic from the official
invoiced Sell-in/Sell-out gap.

Missing invoice flow stays Unknown unless extract completeness is explicitly
assumed. Only if justified for a simulation, run:

```bash
uv run python -m data_platform build --include-kpi-marts --invoice-extract-complete
```

That flag is recorded as an assumption; it does not establish production feed
completeness. Existing invoice totals remain reported-row totals, with extract
completeness and comparison status visible. Missing Sell-out also remains
explicit. `comparable_*` components include only rows where both flows are known,
so an unknown invoice cannot enter only the denominator of an aggregate gap:

```sql
SELECT 100.0*sum(comparable_gap_units)/nullif(sum(comparable_sell_out_units),0) AS gap_pct
FROM gold.mart_commercial_period
WHERE period_type='month' AND alignment_basis='event_date' AND metric_view='reported_only';
```

Consumer sales value is calculated only from actual observed consumer prices.
Candidate sales units are not silently assigned a reference consumer price.
Trade discount is weighted by gross invoice value. Source extra-discount values
and violation flags are retained independently; additive/compound policy is not
inferred. Margin uses a visibly labeled static product-cost assumption, not an
invented historical invoice cost.

## Audit visits

`gold.mart_execution_visit` is store/audit date/category/SKU scope. Available
scopes are `all_category`, `msl_subset` and an individual `sku:<id>`.
These overlap: select one consistent scope before aggregation. Compliance uses
compliant audit rows divided by audited rows, not average visit percentages.
Shelf allocation is summed within a visit and averaged across visits of the
same scope. All category SKUs sum to approximately 100% by construction; this
is not Marvel-versus-competitor market share. No audit is interpolated or
fabricated for unaudited days.

## Definitions, release and progress

`gold.metric_definitions` exposes formulas, source relations, numerator/denominator,
units, scope and N/A policy. This supplies a shared basis for later serving views
and controlled natural-language queries. A single declared view/period/scope
must be selected before calculation.

Operational estimates remain candidate because natural-gap semantics are still
unresolved. The shared metric code is tested independently of that uncertainty;
reported observations remain available. No new seven-day model result is claimed.

The [stage 4 validation record](v3-medallion-stage-4-validation.md) reports the
accepted V3 candidate, fixture tests, memory correction and source-to-mart parity.
