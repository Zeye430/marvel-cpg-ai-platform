# Module 1: integrate shared KPIs and Store 360

Start with the [shared-data quickstart](README.md): provision the authorized
V3 inputs, build/publish a snapshot and configure an approved principal/store
scope. This guide describes data integration, not an already migrated dashboard.
The legacy Module 1 app and its Aiven/ETL path have not been switched to V3 here.

## 1. Responsibilities and database connection

Module 1 consumes the shared `business.duckdb` snapshot through the controlled
reader. Do not read raw CSVs, connect to internal `build.duckdb`, independently
fill missing days or recreate the pipeline on every dashboard refresh. Database
generation is a separate job. The user interface/filters/charts live in the
Module 1 branch; shared definitions and transformations live in the data layer.

The local path needs no connection string: resolve the local published serving
manifest and open one verified, read-only version. A shared PostgreSQL release
with the same reader API is available as an opt-in path ([quickstart section 5](README.md#5-connect-your-module--module-developers));
Module 1 has **not** been migrated to it, and it is unrelated to the legacy Aiven
raw tables that the old app reads. See the
quickstart for local rebuild versus an authorized copied artifact. Applications
must take the principal from a verified session; a dropdown or URL cannot grant
store access. Pin one publication per request/job and key caches by artifact ID
plus verified principal/scope, filters and metric view.

## 2. Which views to use

| Dashboard need | Reader view / important fields | Grain and caveat |
| --- | --- | --- |
| Store/day KPI tiles and trends | `store_day`: oos_sku_count, known_inventory_sku_count, expected_sku_count, observed_inventory_sku_count, MSL components | Date × store × metric view; all-active-SKU scope |
| Store 360 SKU inventory/sales | `store_sku_daily`: inventory_observed, units_sold_observed, oos_observed, status fields, last_report_date | Date × store × SKU; missing report remains Unknown |
| Sell-in/Sell-out and trade economics | `commercial_period`: period_type, alignment_basis, invoice amounts, comparable_* components, coverage/comparison status | Choose one week/biweek/month family; partial periods and invoice timing remain visible |
| Reported invoices or receipts | `sell_in_events`, `delivery_events` | Transaction/event IDs; invoice units are not stock receipts and there is no invented invoice-delivery key |
| Execution compliance/display | `execution_visits`: audited_row_count, compliant_audit_row_count, secondary_display_row_count, shelf_allocation_pct, scope_label | Store × audit date × category × SKU scope; scopes overlap |
| Account/product/campaign filters | `stores`, `products`, `promotions` | Promotion eligibility is not proof of execution; product prices/costs are static references |
| Shared formulas and interpretation | `metric_definitions` | Formula, units, numerator/denominator and limitations |
| Future risk cards | `oos_predictions`, `visit_priority` | Unreleased typed interfaces; do not invent risk or rankings |

The [Gold dictionary](v3-gold-data-dictionary.md) provides exact fields and types.
`reader.schema()` inspects 16 views. Reported remains the default. An explicit
Estimated toggle can read `estimated_store_sku_daily`, `estimated_store_day` or
`estimated_commercial_period` with `allow_candidate_estimates=True`. Label values
Estimated/Unknown, show coverage/methods and Candidate status; this is not an
approved operational default and is not complete latent demand.

## 3. Runnable local checks

After the quickstart's scope-configured publication, run from repo root:

```bash
uv run python -m examples.read_shared_data --schema-only
uv run python -m examples.read_shared_data \
  --principal-id team-local-dev --view store_day \
  --start 2026-06-30 --end 2026-06-30 --limit 1000
uv run python -m examples.read_shared_data \
  --principal-id team-local-dev --view store_sku_daily \
  --start 2026-06-30 --end 2026-06-30 --limit 10
```

The local identity must match your approved scope file; these are bounded reads,
not a way to obtain unrestricted data or a complete population automatically.

```python
from examples.read_shared_data import open_reader

def load_store_day_data(principal_id, publication_path, start_date, end_date):
    reader = open_reader(publication=publication_path)
    rows = reader.read("store_day", principal_id,
                       start=start_date, end=end_date, limit=10000)
    if len(rows) == 10000:
        raise RuntimeError("Potentially truncated result; use a reviewed scoped aggregate endpoint")
    return {"artifact_id": reader.record["artifact_id"],
            "dataset_hash": reader.record["dataset_hash"],
            "as_of": reader.record["as_of"],
            "metric_view": reader.record["metric_view"],
            "rows": rows}
```

The reader has no pagination, category/SKU argument or aggregate API. If the
requested population cannot fit its limit, implement a reviewed scoped query/
aggregate interface rather than summing a preview or removing access checks.
An empty result is not a measured zero for the requested stores/date range.

## 4. Aggregate components, not already calculated percentages

For one consistent store/date/SKU population:

```text
Reported OOS % = 100 × SUM(oos_sku_count) / SUM(known_inventory_sku_count)
Reporting coverage % = 100 × SUM(observed_inventory_sku_count) / SUM(expected_sku_count)
MSL compliance % = 100 × SUM(msl_compliant_store_count) / SUM(msl_assessable_store_count)
MSL assessment coverage % = 100 × SUM(msl_assessable_store_count) / SUM(msl_eligible_store_count)
```

Zero denominators give N/A, not zero. Unknown inventory is excluded from the
reported OOS denominator but stays in coverage/unknown counts. Unknown MSL is
not a measured failure; no applicable MSL items gives N/A. Summing across dates
produces SKU-day/store-day measures, not a distinct-store count for a month.

Additional rules:

- `store_day` is all-active-SKU scope. For a category/SKU-filtered KPI, recompute
  components from complete scoped `store_sku_daily` detail; filtering precomputed
  store totals cannot produce a category denominator.
- Commercial date bounds in the reader use **period_start**, not interval overlap.
  Select one period_type and official event_date alignment. Do not sum week,
  biweek and month rows together. Respect period_is_complete, covered_start/end,
  invoice_extract_complete_assumed and comparison_status.
- Sell-in/out gap uses comparable components where both flows are known; a
  missing invoice is not automatically zero. Trade discount is gross-value
  weighted; margin is labeled with the static product-cost basis. Consumer
  revenue uses observed consumer prices, not invoice price/reference substitution.
- Audit scopes all_category, msl_subset and individual SKU scopes overlap; choose
  one. Planogram compliance uses compliant/audited row counts. Category shelf
  allocation near 100% is not Marvel's competitor market share. No audit on a day
  means no new observation, not automatically noncompliant.

See [KPI specification](../solution-specifications/kpi-and-missing-store-day-handling.md) and `metric_definitions` instead of
introducing alternate definitions in each chart.

## 5. Dates, missing data and prediction cards

Use the accepted snapshot cutoff as the current data date: **2026-06-30** in
this delivery, not the laptop's wall clock. This is separate from May model development. Show date
range, last report date, coverage and data status where relevant. Historical
commercial/event views are not point-in-time ML features.

Preserve these UI states:

- Observed facts versus Unknown reports; NULL inventory is not available stock.
- Candidate estimates have separate opt-in views, not approval. Use per-value
  status, estimator version and estimated-state coverage; never silently replace
  reported cards or hide Unknown values. An approved default needs later adoption.
- Risk/visit outputs raise `DataUnavailable` until a model release. Label cards
  unavailable or hide them with a reason; never show 0% risk or fabricated ranks.
- Audit age, MSL applicability assumptions and static cost/currency limitations.

Module 1 does not train a model or independently calculate OOS probabilities.
After Module 2 publishes accepted results, Module 1 reads a selected versioned
run through the shared output interface; do not join all scoring runs together.

## 6. Migration and acceptance checklist

1. Implement a data adapter in the Module 1 branch using verified shared serving
   (local DuckDB, or the shared PostgreSQL reader via
   `data_platform.readers.open_business_reader`); keep legacy access separate
   until parity is reviewed. This guide itself does
   not change Aiven credentials, ETL jobs or the old application.
2. Map each old card/chart to its shared view, grain, fields and denominator.
   Do not mix V1/V2 outputs with the active V3 publication.
3. Test fixture examples, component totals and category/period/audit filters.
4. Test unauthorized stores, manager scopes, empty ranges, Unknown and N/A.
5. Verify complete populations for aggregates and version/scope-aware caches.
6. Expose source cutoff, reporting coverage and unavailable predictions clearly.
7. Keep CSVs, databases, real scope maps and secrets outside Git; no writes to
   an accepted serving snapshot from the dashboard.
