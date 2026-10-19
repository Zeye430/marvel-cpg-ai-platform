# Module 3: integrate the shared negotiation data

Read the [shared-data quickstart](README.md) first. This guide assumes a
verified serving snapshot and an explicitly assigned application principal.
It describes the current interface, not an already implemented simulator.

## 1. What you do not need to rebuild

Do not load CSVs in the Module 3 application, rerun EDA, estimate missing stock,
join raw delivery tables or redefine common KPI formulas. Use the shared serving
database. Database generation is a separate data job; it must not run on every
page load or user negotiation request.

The main input is `serving.vw_negotiation_context` (reader name
`negotiation_context`), one row per store/SKU at the publication cutoff, qualified
by rule version. It is a current-context snapshot, **not a daily historical table**.
An arbitrary historical date request will not reconstruct an older scenario.

## 2. Data inputs and their meaning

| Need | View / important fields | Handling |
| --- | --- | --- |
| Account/product identity | `negotiation_context`: store_id, sku_id, channel, segment_tier, category, is_msl | Store/SKU are keys; account scope comes from verified identity |
| Reference unit economics | reference_list_price, reference_unit_cost, reference_consumer_price, cost_basis, currency_status | Static product references; not historical cost or an identified currency |
| Current stock | inventory_observed, inventory_data_status | Reported Unknown stays unknown; separate candidate views require explicit opt-in and Candidate labeling |
| Recent consumer movement | sell_out_units_observed_28d, observed_sales_days_28d, expected_days_28d | Reported sales, not a complete 28-day demand estimate |
| Recent trade business | sell_in_units_reported_28d, gross_sales_reported_28d, net_sales_reported_28d, reported_net_trade_price_28d | Reported invoice events; retain invoice_coverage_status |
| Historical discount/margin context | weighted_trade_discount_pct_28d, historical_static_cost_margin_pct | Weighted arithmetic from reported amounts, using static cost reference |
| Execution evidence | last_audit_id/date, days_since_last_audit, planogram_compliance_latest, secondary_display_latest, shelf_share_pct_latest | Latest known evidence, not an approved current-state expiry policy |
| Policy source | max_discount_pct_source, min_target_margin_source, required_exec_tradeoff, rule_version, policy_status, policy_limitation | Source context only; Requires review, not platform approval |
| More commercial history | `commercial_period` | Choose a single period_type and alignment; do not mix overlapping week/month/biweek rows |
| More audit detail | `execution_visits` | Choose one scope_label; category/all/SKU scopes overlap |
| Product reference lookup | `products` | Shared reference dimensions; do not confuse consumer with trade price |

The exact field types and grains are in the [Gold dictionary](v3-gold-data-dictionary.md).
Dates in the context are interpreted relative to `as_of_date`, not the wall clock
on the developer's laptop. Current business cutoff is June 30, 2026; model-development cutoff is separate.

## 3. Runnable local check and application pattern

After building with an approved scope file, run from the repo root:

```bash
uv run python -m examples.read_shared_data \
  --principal-id team-local-dev --view negotiation_context --limit 5
```

This local principal must match your scope file. In application code, replace the
CLI identity with the principal from the authenticated session:

```python
from examples.read_shared_data import open_reader

def load_negotiation_context(principal_id, *, publication_path, selected_store_id):
    reader = open_reader(publication=publication_path)
    as_of = reader.record["as_of"]
    # The current table reader has no per-store argument or pagination. This
    # bounded call is suitable only when the full scoped context fits the limit.
    rows = reader.read("negotiation_context", principal_id,
                       start=as_of, end=as_of, limit=10000)
    if len(rows) == 10000:
        raise RuntimeError("Potentially truncated context; add a reviewed scoped query endpoint")
    selected = [row for row in rows if row["store_id"] == selected_store_id]
    return {
        "artifact_id": reader.record["artifact_id"],
        "dataset_hash": reader.record["dataset_hash"],
        "as_of": as_of,
        "rows": selected,
    }
```

Store selection narrows already scoped rows; it never grants access to another
store. Empty selection means no accessible matching context, not zero stock.
The table reader does not provide a deployed API or a general aggregation engine.
If you need a reusable filtered/aggregate query, propose it to the shared layer
with scope, grain, inputs, output and tests rather than opening the internal DB.

## 4. Which calculations belong to Module 3?

The simulator can calculate explicitly labeled **hypothetical** scenarios from
user inputs, for example a proposed discount and order quantity. Given an agreed
total trade discount definition, a simple reference calculation is:

```text
scenario net unit price = reference list price × (1 − total discount pct / 100)
scenario margin pct = 100 × (net unit price − reference cost) / net unit price
```

This is illustrative arithmetic, not an approved interpretation of the supplied
guardrails or extra discount. A zero denominator gives N/A. Decimal/money rounding,
returns, tax, currency and cost-date conventions require explicit product rules.
Do not silently add trade discount and extra discount or infer consumer promotion
from invoice discounts. Do not assume secondary display guarantees a sales uplift.

Shared metrics already supplied by Gold should be read, not recalculated with a
different denominator. Module-specific scenario outputs can have their own
versioned formulas without overwriting historical reported facts.

## 5. Must remain visible in the UI/output

- Snapshot date and version; all scenarios pin one accepted publication.
- Reported Observed/Unknown inventory and coverage. If deliberately using the
  separate Estimated view, preserve Observed/Estimated/Unknown and Candidate status;
  the negotiation context itself remains reported-only.
- Static reference cost/list price basis and unspecified currency.
- Audit date/age rather than an unsupported "execution currently compliant" claim.
- `policy_status=Requires review` until rule units/composition/version are approved.
- OOS predictions and visit ranking are unavailable until a Module 2 release;
  do not replace them with zeros or retrain a model inside Module 3.

A historical source `guardrail_violation` flag is evidence about an invoice,
not automatic approval/rejection of a new proposed scenario. The simulator can
be developed with a clearly review-required state without inventing approvals.

## 6. Read database versus write database

`business.duckdb` is immutable and read-only. Do not insert scenario records,
chat messages or edited discounts into it. Module 3 owns a separate application
store for drafts/scenarios/approval actions; its persistence implementation is
outside this data-layer delivery. Choose it in the module/application design.

A scenario should retain at least: scenario_id, verified principal/store, SKU,
creation time, serving artifact_id/dataset_hash, as_of date, input values,
formula/policy versions, computed outputs and review state. Saving a hypothetical
scenario does not make it a Gold fact or approve it. A later publication/export
requires its own reviewed interface.

## 7. Module 3 acceptance checklist

- Application imports shared data access instead of raw CSV/legacy ETL logic.
- Cross-store access is denied, including manager identities without assignments.
- Unknown inventory, incomplete sales and zero-denominator margins remain explicit.
- Guardrail ambiguity never becomes an automatic pass.
- Scenarios retain the exact data/formula version and are saved outside the snapshot.
- Publication changes refresh context deliberately; existing scenarios retain their
  pinned provenance rather than silently changing with the latest data.
- No source CSVs, real scope maps, databases, secrets or generated scenarios enter Git.
