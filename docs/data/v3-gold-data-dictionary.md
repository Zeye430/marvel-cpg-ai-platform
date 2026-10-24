# V3 Gold serving data dictionary

Exact field names and DuckDB types below were inspected from the accepted V3
`business.duckdb` snapshot with business cutoff 2026-06-30 (16 views / 413 exposed columns). This is the application-facing
subset, not the internal Bronze/Silver/feature/evaluation dictionary. Retrieve
current machine-readable schemas with `ServingReader.schema()`.

See the [shared-data quickstart](README.md),
[metric specification](../solution-specifications/kpi-and-missing-store-day-handling.md),
[Module 2 feature rules](v3-module-2-integration.md#9-feature-window-and-availability-rules) and
[module contract](../../data_platform/contracts/v3/module-data-contract.json).
The feature and isolated label schemas are deliberately not served here.

## Shared meanings

- `build_id` identifies the source build; `dataset_hash` identifies all authorized input bytes. Do not mix publications or cutoffs.
- `*_observed` / `*_reported*` mean source-reported facts. Missing values are unknown, not zeros. `inventory_data_status` / `sales_data_status` retain that distinction.
- `*_pct` is percentage points on a 0–100 scale, except **raw** `min_target_margin_source`, whose scale is unresolved. Model `probability` and `threshold` use 0–1.
- `*_units` / unit counts are quantities, not money. Financial amounts have no supplied currency code. Product list/cost/consumer prices are static references, not historical price histories.
- Reported views remain default. Three separate `estimated_*` candidate views require explicit reader opt-in and UI Estimated/Unknown/Candidate labels. Daily candidate detail declares `operational_estimate_candidate`; component marts retain their source `operational_estimate` value and release metadata. This is not operational approval. Commercial views retain official `alignment_basis=event_date`.
- `period_is_complete`, `covered_start/end`, coverage and `comparison_status` qualify period comparisons. An absent invoice is not automatically zero flow without the completeness assumption.
- MSL applicability/assessment and missing inventory remain explicit; see numerator/denominator components and `msl_applicability_policy`. Aggregate ratios from summed components, never averages of percentages.
- Execution scopes overlap: choose one `scope_label`/`sku_scope_id` level. Shelf allocation is within a category visit, not market share.
- Negotiation `policy_status` is Requires review; raw guardrail values and source violation flags do not constitute scenario approval. Audit age is retained; no expiry policy is invented.
- Future prediction and priority interfaces contain zero rows. This means unavailable, not zero risk. Model selection/calibration and ranking algorithms are outside this branch.
- Global references still require an assigned authenticated principal; store-scoped views additionally filter to explicitly assigned stores. `date_column` below is the reader's bound column, not a claim that all other dates are historical event dates.

## Shared PostgreSQL copy (opt-in)

A published PostgreSQL release contains the same views, columns, order, rows and
grains as the source snapshot: `platform_serving.vw_<name>` follows the current
release, and `platform_release_<release_id>.vw_<name>` is one immutable release
(what the shared reader pins). Types map losslessly, as fixed in the
[PostgreSQL column/type contract](../../data_platform/contracts/v3/postgres-serving.json):
VARCHAR → `text COLLATE "C"`, BOOLEAN/DATE/INTEGER/BIGINT unchanged, DOUBLE →
`double precision`, DECIMAL(p,s) → `numeric(p,s)`, HUGEINT → `numeric(39,0)`.
The reader returns the same Python values as the DuckDB reader; `schema()` reports
the DuckDB type names below plus `backend_type`. Account scopes are not part of a
release.

## serving.vw_store_sku_daily

Grain: date × store_id × sku_id; confirmed assortment dates only.
Account scoped: yes.
Reader date column: `date`.

| Field | DuckDB type |
| --- | --- |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `date` | `DATE` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_msl` | `BOOLEAN` |
| `units_sold_observed` | `DOUBLE` |
| `inventory_observed` | `DOUBLE` |
| `oos_observed` | `BOOLEAN` |
| `inventory_data_status` | `VARCHAR` |
| `sales_data_status` | `VARCHAR` |
| `last_report_date` | `DATE` |
| `days_since_inventory_report` | `BIGINT` |
| `consumer_price_observed` | `DECIMAL(18,2)` |
| `receipt_units_from_deliveries` | `DOUBLE` |
| `receipt_delivery_count` | `BIGINT` |

## serving.vw_store_day

Grain: date × store_id × metric_view.
Account scoped: yes.
Reader date column: `date`.

| Field | DuckDB type |
| --- | --- |
| `date` | `DATE` |
| `store_id` | `VARCHAR` |
| `metric_view` | `VARCHAR` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `expected_sku_count` | `BIGINT` |
| `known_inventory_sku_count` | `BIGINT` |
| `observed_inventory_sku_count` | `BIGINT` |
| `estimated_inventory_sku_count` | `BIGINT` |
| `unknown_inventory_sku_count` | `BIGINT` |
| `oos_sku_count` | `BIGINT` |
| `known_sales_sku_count` | `BIGINT` |
| `observed_sales_sku_count` | `BIGINT` |
| `estimated_sales_sku_count` | `BIGINT` |
| `sell_out_units_available` | `DOUBLE` |
| `required_msl_sku_count` | `BIGINT` |
| `known_msl_sku_count` | `BIGINT` |
| `unavailable_msl_sku_count` | `BIGINT` |
| `unknown_msl_sku_count` | `BIGINT` |
| `max_days_since_inventory_report` | `BIGINT` |
| `release_status` | `VARCHAR` |
| `estimator_version` | `VARCHAR` |
| `msl_state` | `VARCHAR` |
| `msl_eligible_store_count` | `INTEGER` |
| `msl_assessable_store_count` | `INTEGER` |
| `msl_compliant_store_count` | `INTEGER` |
| `msl_noncompliant_store_count` | `INTEGER` |
| `msl_unknown_store_count` | `INTEGER` |
| `metric_version` | `VARCHAR` |
| `oos_rate_pct` | `DOUBLE` |
| `reporting_coverage_pct` | `DOUBLE` |
| `known_inventory_coverage_pct` | `DOUBLE` |
| `estimated_inventory_share_of_known_pct` | `DOUBLE` |
| `msl_compliance_pct` | `DOUBLE` |
| `msl_assessment_coverage_pct` | `DOUBLE` |
| `msl_lower_bound_pct` | `DOUBLE` |
| `msl_upper_bound_pct` | `DOUBLE` |
| `msl_applicability_policy` | `VARCHAR` |

## serving.vw_commercial_period

Grain: period_type × period_start × store_id × sku_id × metric_view × alignment_basis; choose one period family.
Account scoped: yes.
Reader date column: `period_start`.

| Field | DuckDB type |
| --- | --- |
| `period_type` | `VARCHAR` |
| `period_start` | `DATE` |
| `period_end` | `DATE` |
| `alignment_basis` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `metric_view` | `VARCHAR` |
| `covered_start` | `DATE` |
| `covered_end` | `DATE` |
| `expected_sku_days` | `BIGINT` |
| `known_sell_out_days` | `BIGINT` |
| `reported_sell_out_days` | `BIGINT` |
| `estimated_sell_out_days` | `BIGINT` |
| `sell_out_units_available` | `DOUBLE` |
| `consumer_sales_value_observed` | `DECIMAL(38,2)` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `estimator_version` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `category` | `VARCHAR` |
| `release_status` | `VARCHAR` |
| `invoice_line_count` | `BIGINT` |
| `invoice_units_observed` | `DOUBLE` |
| `invoice_gross_sales` | `DECIMAL(38,2)` |
| `invoice_net_sales` | `DECIMAL(38,2)` |
| `invoice_discount_value` | `DECIMAL(38,2)` |
| `invoice_cost_at_static_reference` | `DECIMAL(38,2)` |
| `extra_discount_unit_weighted_sum` | `DECIMAL(38,4)` |
| `source_violation_line_count` | `BIGINT` |
| `last_invoice_date` | `DATE` |
| `delivered_units` | `DOUBLE` |
| `delivery_event_count` | `BIGINT` |
| `invoice_extract_complete_assumed` | `BOOLEAN` |
| `expected_invoice_date` | `DATE` |
| `data_cutoff` | `DATE` |
| `period_is_complete` | `BOOLEAN` |
| `sell_in_units_available` | `DOUBLE` |
| `metric_version` | `VARCHAR` |
| `unknown_sell_out_days` | `BIGINT` |
| `reporting_coverage_pct` | `DOUBLE` |
| `known_sales_coverage_pct` | `DOUBLE` |
| `sell_in_out_gap_units` | `DOUBLE` |
| `flow_is_comparable` | `BOOLEAN` |
| `comparable_sell_in_units` | `DOUBLE` |
| `comparable_sell_out_units` | `DOUBLE` |
| `comparable_gap_units` | `DOUBLE` |
| `sell_in_out_gap_pct` | `DOUBLE` |
| `delivered_out_gap_units` | `DOUBLE` |
| `trade_discount_weighted_pct` | `DOUBLE` |
| `net_trade_unit_price` | `DOUBLE` |
| `trade_margin_value_at_static_cost` | `DECIMAL(38,2)` |
| `trade_margin_pct_at_static_cost` | `DOUBLE` |
| `source_extra_discount_unit_weighted_pct` | `DOUBLE` |
| `comparison_status` | `VARCHAR` |
| `is_point_in_time_safe_for_period_end` | `BOOLEAN` |
| `margin_cost_basis` | `VARCHAR` |
| `discount_interpretation` | `VARCHAR` |
| `recommended_channel_period` | `BOOLEAN` |
| `biweek_anchor` | `VARCHAR` |
| `receipt_coverage_policy` | `VARCHAR` |

## serving.vw_execution_visits

Grain: store_id × audit_date × category × sku_scope_id; choose one scope_label.
Account scoped: yes.
Reader date column: `audit_date`.

| Field | DuckDB type |
| --- | --- |
| `store_id` | `VARCHAR` |
| `audit_date` | `DATE` |
| `category` | `VARCHAR` |
| `sku_scope_id` | `VARCHAR` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `scope_label` | `VARCHAR` |
| `audited_row_count` | `BIGINT` |
| `audited_sku_count` | `BIGINT` |
| `compliant_audit_row_count` | `BIGINT` |
| `secondary_display_row_count` | `BIGINT` |
| `shelf_allocation_pct` | `DECIMAL(38,4)` |
| `visit_count` | `INTEGER` |
| `planogram_compliance_pct` | `DOUBLE` |
| `metric_version` | `VARCHAR` |
| `shelf_share_scope` | `VARCHAR` |
| `release_status` | `VARCHAR` |

## serving.vw_delivery_events

Grain: delivery_id.
Account scoped: yes.
Reader date column: `actual_delivery`.

| Field | DuckDB type |
| --- | --- |
| `delivery_id` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `order_date` | `DATE` |
| `scheduled_delivery` | `DATE` |
| `actual_delivery` | `DATE` |
| `delay_days` | `BIGINT` |
| `delivery_status` | `VARCHAR` |
| `units_ordered` | `BIGINT` |
| `units_delivered` | `BIGINT` |
| `fill_rate_pct` | `DECIMAL(9,4)` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |

## serving.vw_sell_in_events

Grain: transaction_id.
Account scoped: yes.
Reader date column: `date`.

| Field | DuckDB type |
| --- | --- |
| `transaction_id` | `VARCHAR` |
| `date` | `DATE` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `units_sold` | `BIGINT` |
| `gross_sales` | `DECIMAL(18,2)` |
| `discount_pct` | `DECIMAL(9,4)` |
| `extra_discount_pct` | `DECIMAL(9,4)` |
| `net_sales` | `DECIMAL(18,2)` |
| `promo_id` | `VARCHAR` |
| `guardrail_violation` | `BOOLEAN` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |

## serving.vw_negotiation_context

Grain: as_of_date × store_id × sku_id × rule_version.
Account scoped: yes.
Reader date column: `as_of_date`.

| Field | DuckDB type |
| --- | --- |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `as_of_date` | `DATE` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_msl` | `BOOLEAN` |
| `product_name` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `reference_list_price` | `DECIMAL(18,2)` |
| `reference_unit_cost` | `DECIMAL(18,2)` |
| `reference_consumer_price` | `DECIMAL(18,2)` |
| `currency_status` | `VARCHAR` |
| `cost_basis` | `VARCHAR` |
| `inventory_observed` | `DOUBLE` |
| `inventory_data_status` | `VARCHAR` |
| `sell_out_units_observed_28d` | `DOUBLE` |
| `observed_sales_days_28d` | `BIGINT` |
| `expected_days_28d` | `BIGINT` |
| `last_invoice_date` | `DATE` |
| `invoice_lines_28d` | `BIGINT` |
| `sell_in_units_reported_28d` | `HUGEINT` |
| `gross_sales_reported_28d` | `DECIMAL(38,2)` |
| `net_sales_reported_28d` | `DECIMAL(38,2)` |
| `reported_net_trade_price_28d` | `DOUBLE` |
| `weighted_trade_discount_pct_28d` | `DOUBLE` |
| `static_reference_margin_pct` | `DOUBLE` |
| `historical_static_cost_margin_pct` | `DOUBLE` |
| `source_violation_lines_28d` | `BIGINT` |
| `invoice_coverage_status` | `VARCHAR` |
| `max_discount_pct_source` | `DECIMAL(9,4)` |
| `min_target_margin_source` | `DECIMAL(9,4)` |
| `required_exec_tradeoff` | `VARCHAR` |
| `rule_version` | `VARCHAR` |
| `policy_status` | `VARCHAR` |
| `policy_limitation` | `VARCHAR` |
| `last_audit_id` | `VARCHAR` |
| `last_audit_date` | `DATE` |
| `days_since_last_audit` | `BIGINT` |
| `planogram_compliance_latest` | `BOOLEAN` |
| `secondary_display_latest` | `BOOLEAN` |
| `shelf_share_pct_latest` | `DECIMAL(9,4)` |
| `context_version` | `VARCHAR` |

## serving.vw_metric_definitions

Grain: metric_id × metric_version.
Account scoped: global reference, authenticated assignment still required.
Reader date column: none.

| Field | DuckDB type |
| --- | --- |
| `metric_id` | `VARCHAR` |
| `metric_version` | `VARCHAR` |
| `unit` | `VARCHAR` |
| `numerator_column` | `VARCHAR` |
| `denominator_column` | `VARCHAR` |
| `formula` | `VARCHAR` |
| `source_relation` | `VARCHAR` |
| `scope_note` | `VARCHAR` |
| `zero_denominator_policy` | `VARCHAR` |
| `release_note` | `VARCHAR` |

## serving.vw_stores

Grain: store_id.
Account scoped: yes.
Reader date column: none.

| Field | DuckDB type |
| --- | --- |
| `store_id` | `VARCHAR` |
| `store_name` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `state` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `city` | `VARCHAR` |

## serving.vw_products

Grain: sku_id.
Account scoped: global reference, authenticated assignment still required.
Reader date column: none.

| Field | DuckDB type |
| --- | --- |
| `sku_id` | `VARCHAR` |
| `product_name` | `VARCHAR` |
| `category` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `is_msl` | `BOOLEAN` |
| `unit_cost` | `DECIMAL(18,2)` |
| `list_price` | `DECIMAL(18,2)` |
| `consumer_price` | `DECIMAL(18,2)` |

## serving.vw_promotions

Grain: promo_id.
Account scoped: global reference, authenticated assignment still required.
Reader date column: `start_date`.

| Field | DuckDB type |
| --- | --- |
| `promo_id` | `VARCHAR` |
| `promo_name` | `VARCHAR` |
| `promo_type` | `VARCHAR` |
| `category` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `start_date` | `DATE` |
| `end_date` | `DATE` |
| `discount_pct` | `DECIMAL(9,4)` |
| `min_volume_units` | `BIGINT` |

## serving.vw_oos_predictions

Grain: scoring_run_id × scoring_date × store_id × sku_id; empty in base serving builds. Filled only in a new serving artifact built by `data_platform.model_outputs` for one selected scoring run of a frozen model; `drivers_json` is rule-based context (`explanation_type=rule_based_context`), not causal attribution.
Account scoped: yes.
Reader date column: `scoring_date`.

| Field | DuckDB type |
| --- | --- |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `scoring_run_id` | `VARCHAR` |
| `scoring_date` | `DATE` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `probability` | `DOUBLE` |
| `threshold` | `DOUBLE` |
| `is_predicted_oos` | `BOOLEAN` |
| `drivers_json` | `VARCHAR` |
| `model_version` | `VARCHAR` |
| `feature_version` | `VARCHAR` |
| `input_status` | `VARCHAR` |

## serving.vw_visit_priority

Grain: scoring_run_id × scoring_date × store_id × ranking_policy_version; empty in base serving builds. `priority_rank` is unique 1..N for the published run (policy `visit_priority.v1`); `risk_score` is the maximum SKU probability, a risk index rather than a calibrated store probability. `reason_json` carries the urgency bucket, percentiles, fallback flags and the reference sales proxy status.
Account scoped: yes.
Reader date column: `scoring_date`.

| Field | DuckDB type |
| --- | --- |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `scoring_run_id` | `VARCHAR` |
| `scoring_date` | `DATE` |
| `store_id` | `VARCHAR` |
| `priority_rank` | `BIGINT` |
| `risk_score` | `DOUBLE` |
| `msl_gap_count` | `BIGINT` |
| `ranking_policy_version` | `VARCHAR` |
| `reason_json` | `VARCHAR` |



## serving.vw_model_health

Grain: model_version × report_id × group_kind × group_id; aggregated counts and metrics only (no row-level
targets, file paths or model bytes). Additive in this contract version; empty in base builds.
Access: **manager-only with full-population scope**, enforced by the shared reader on both `read` and
`read_page`. Reader date column: `outcome_cutoff`.

`evaluation_kind` is one of `development`, `iterative_validation`, `holdout_confirmation`,
`independent_test`, `prospective_monitoring`; kinds are never relabelled to imply independence.
`group_kind`/`group_id`: `all`/`all`; `segment`/JSON `[channel, tier]`; `current_status`/`not_oos|oos|unknown`.
Undefined metrics (single class, zero alerts) are NULL and `meets_numeric_targets` is false; unevaluated rows
have NULL counts. `selected` marks the development-selected candidate.

| Field | DuckDB type |
| --- | --- |
| `dataset_hash` | `VARCHAR` |
| `source_build_id` | `VARCHAR` |
| `model_version` | `VARCHAR` |
| `algorithm` | `VARCHAR` |
| `candidate_id` | `VARCHAR` |
| `report_id` | `VARCHAR` |
| `evaluation_kind` | `VARCHAR` |
| `fold_id` | `VARCHAR` |
| `window_start` | `DATE` |
| `window_end` | `DATE` |
| `outcome_cutoff` | `DATE` |
| `group_kind` | `VARCHAR` |
| `group_id` | `VARCHAR` |
| `sample_count` | `BIGINT` |
| `positive_count` | `BIGINT` |
| `tp` | `BIGINT` |
| `fp` | `BIGINT` |
| `fn` | `BIGINT` |
| `tn` | `BIGINT` |
| `alert_count` | `BIGINT` |
| `candidate_count` | `BIGINT` |
| `precision` | `DOUBLE` |
| `recall` | `DOUBLE` |
| `roc_auc` | `DOUBLE` |
| `average_precision` | `DOUBLE` |
| `label_coverage` | `DOUBLE` |
| `brier_score` | `DOUBLE` |
| `ece` | `DOUBLE` |
| `meets_numeric_targets` | `BOOLEAN` |
| `selected` | `BOOLEAN` |
| `parameters_json` | `VARCHAR` |
| `fit_seconds` | `DOUBLE` |
| `calibration_method` | `VARCHAR` |
| `evaluation_status` | `VARCHAR` |
| `limitations_json` | `VARCHAR` |
| `report_sha256` | `VARCHAR` |
| `schema_version` | `VARCHAR` |

## serving.vw_model_monitoring

Grain: monitoring_run_id × model_version; one row per model version present in the issued-prediction history.
Additive in this contract version; empty in base builds. Access: manager-only with full-population scope.
Reader date column: `observation_cutoff`.

Counts refer to predictions actually issued by scoring runs. `performance_status` is
`Pending_outcomes`, `Insufficient_support` or `Evaluated`; `drift_status` is `Stable`, `Warning`, `Severe`
or `Insufficient_support`; `retrain_status` is `Not_requested` or `Challenger_requested` (a request, not an
automatic model change).

| Field | DuckDB type |
| --- | --- |
| `dataset_hash` | `VARCHAR` |
| `source_build_id` | `VARCHAR` |
| `monitoring_run_id` | `VARCHAR` |
| `model_version` | `VARCHAR` |
| `observation_cutoff` | `DATE` |
| `window_start` | `DATE` |
| `window_end` | `DATE` |
| `issued_count` | `BIGINT` |
| `eligible_count` | `BIGINT` |
| `pending_count` | `BIGINT` |
| `unknown_outcome_count` | `BIGINT` |
| `tp` | `BIGINT` |
| `fp` | `BIGINT` |
| `fn` | `BIGINT` |
| `tn` | `BIGINT` |
| `alert_count` | `BIGINT` |
| `precision` | `DOUBLE` |
| `recall` | `DOUBLE` |
| `roc_auc` | `DOUBLE` |
| `average_precision` | `DOUBLE` |
| `label_coverage` | `DOUBLE` |
| `brier_score` | `DOUBLE` |
| `ece` | `DOUBLE` |
| `performance_status` | `VARCHAR` |
| `drift_status` | `VARCHAR` |
| `retrain_status` | `VARCHAR` |
| `drift_json` | `VARCHAR` |
| `reasons_json` | `VARCHAR` |
| `issued_runs_json` | `VARCHAR` |
| `report_sha256` | `VARCHAR` |
| `schema_version` | `VARCHAR` |

## serving.vw_estimated_store_sku_daily

Grain: date × store_id × sku_id.
Account scoped: yes. Reader date column: `date`.
Release status: Candidate_not_approved. Requires `allow_candidate_estimates=True`.
Observed values take precedence; estimates/unknowns never become source reports.

| Field | DuckDB type |
| --- | --- |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `date` | `DATE` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `category` | `VARCHAR` |
| `is_msl` | `BOOLEAN` |
| `units_sold_observed` | `DOUBLE` |
| `inventory_observed` | `DOUBLE` |
| `oos_observed` | `BOOLEAN` |
| `units_sold_estimated` | `DOUBLE` |
| `inventory_estimated` | `DOUBLE` |
| `oos_estimated` | `BOOLEAN` |
| `sales_resolved` | `DOUBLE` |
| `inventory_resolved` | `DOUBLE` |
| `oos_resolved` | `BOOLEAN` |
| `sales_data_status` | `VARCHAR` |
| `inventory_data_status` | `VARCHAR` |
| `sales_estimation_method` | `VARCHAR` |
| `inventory_estimation_method` | `VARCHAR` |
| `inventory_state_reason` | `VARCHAR` |
| `last_report_date` | `DATE` |
| `days_since_inventory_report` | `BIGINT` |
| `estimator_version` | `VARCHAR` |
| `estimation_release_status` | `VARCHAR` |
| `receipt_units_from_deliveries` | `DOUBLE` |
| `receipt_delivery_count` | `BIGINT` |
| `metric_view` | `VARCHAR` |

## serving.vw_estimated_store_day

Grain: date × store_id × metric_view.
Account scoped: yes. Reader date column: `date`.
Release status: Candidate_not_approved. Requires `allow_candidate_estimates=True`.
Observed values take precedence; estimates/unknowns never become source reports.

| Field | DuckDB type |
| --- | --- |
| `date` | `DATE` |
| `store_id` | `VARCHAR` |
| `metric_view` | `VARCHAR` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `expected_sku_count` | `BIGINT` |
| `known_inventory_sku_count` | `BIGINT` |
| `observed_inventory_sku_count` | `BIGINT` |
| `estimated_inventory_sku_count` | `BIGINT` |
| `unknown_inventory_sku_count` | `BIGINT` |
| `oos_sku_count` | `BIGINT` |
| `known_sales_sku_count` | `BIGINT` |
| `observed_sales_sku_count` | `BIGINT` |
| `estimated_sales_sku_count` | `BIGINT` |
| `sell_out_units_available` | `DOUBLE` |
| `required_msl_sku_count` | `BIGINT` |
| `known_msl_sku_count` | `BIGINT` |
| `unavailable_msl_sku_count` | `BIGINT` |
| `unknown_msl_sku_count` | `BIGINT` |
| `max_days_since_inventory_report` | `BIGINT` |
| `release_status` | `VARCHAR` |
| `estimator_version` | `VARCHAR` |
| `msl_state` | `VARCHAR` |
| `msl_eligible_store_count` | `INTEGER` |
| `msl_assessable_store_count` | `INTEGER` |
| `msl_compliant_store_count` | `INTEGER` |
| `msl_noncompliant_store_count` | `INTEGER` |
| `msl_unknown_store_count` | `INTEGER` |
| `metric_version` | `VARCHAR` |
| `oos_rate_pct` | `DOUBLE` |
| `reporting_coverage_pct` | `DOUBLE` |
| `known_inventory_coverage_pct` | `DOUBLE` |
| `estimated_inventory_share_of_known_pct` | `DOUBLE` |
| `msl_compliance_pct` | `DOUBLE` |
| `msl_assessment_coverage_pct` | `DOUBLE` |
| `msl_lower_bound_pct` | `DOUBLE` |
| `msl_upper_bound_pct` | `DOUBLE` |
| `msl_applicability_policy` | `VARCHAR` |

## serving.vw_estimated_commercial_period

Grain: period_type × period_start × store_id × sku_id × metric_view × alignment_basis; choose one period family.
Account scoped: yes. Reader date column: `period_start`.
Release status: Candidate_not_approved. Requires `allow_candidate_estimates=True`.
Observed values take precedence; estimates/unknowns never become source reports.

| Field | DuckDB type |
| --- | --- |
| `period_type` | `VARCHAR` |
| `period_start` | `DATE` |
| `period_end` | `DATE` |
| `alignment_basis` | `VARCHAR` |
| `store_id` | `VARCHAR` |
| `sku_id` | `VARCHAR` |
| `metric_view` | `VARCHAR` |
| `covered_start` | `DATE` |
| `covered_end` | `DATE` |
| `expected_sku_days` | `BIGINT` |
| `known_sell_out_days` | `BIGINT` |
| `reported_sell_out_days` | `BIGINT` |
| `estimated_sell_out_days` | `BIGINT` |
| `sell_out_units_available` | `DOUBLE` |
| `consumer_sales_value_observed` | `DECIMAL(38,2)` |
| `build_id` | `VARCHAR` |
| `dataset_hash` | `VARCHAR` |
| `data_version` | `VARCHAR` |
| `estimator_version` | `VARCHAR` |
| `channel` | `VARCHAR` |
| `region` | `VARCHAR` |
| `segment_tier` | `VARCHAR` |
| `category` | `VARCHAR` |
| `release_status` | `VARCHAR` |
| `invoice_line_count` | `BIGINT` |
| `invoice_units_observed` | `DOUBLE` |
| `invoice_gross_sales` | `DECIMAL(38,2)` |
| `invoice_net_sales` | `DECIMAL(38,2)` |
| `invoice_discount_value` | `DECIMAL(38,2)` |
| `invoice_cost_at_static_reference` | `DECIMAL(38,2)` |
| `extra_discount_unit_weighted_sum` | `DECIMAL(38,4)` |
| `source_violation_line_count` | `BIGINT` |
| `last_invoice_date` | `DATE` |
| `delivered_units` | `DOUBLE` |
| `delivery_event_count` | `BIGINT` |
| `invoice_extract_complete_assumed` | `BOOLEAN` |
| `expected_invoice_date` | `DATE` |
| `data_cutoff` | `DATE` |
| `period_is_complete` | `BOOLEAN` |
| `sell_in_units_available` | `DOUBLE` |
| `metric_version` | `VARCHAR` |
| `unknown_sell_out_days` | `BIGINT` |
| `reporting_coverage_pct` | `DOUBLE` |
| `known_sales_coverage_pct` | `DOUBLE` |
| `sell_in_out_gap_units` | `DOUBLE` |
| `flow_is_comparable` | `BOOLEAN` |
| `comparable_sell_in_units` | `DOUBLE` |
| `comparable_sell_out_units` | `DOUBLE` |
| `comparable_gap_units` | `DOUBLE` |
| `sell_in_out_gap_pct` | `DOUBLE` |
| `delivered_out_gap_units` | `DOUBLE` |
| `trade_discount_weighted_pct` | `DOUBLE` |
| `net_trade_unit_price` | `DOUBLE` |
| `trade_margin_value_at_static_cost` | `DECIMAL(38,2)` |
| `trade_margin_pct_at_static_cost` | `DOUBLE` |
| `source_extra_discount_unit_weighted_pct` | `DOUBLE` |
| `comparison_status` | `VARCHAR` |
| `is_point_in_time_safe_for_period_end` | `BOOLEAN` |
| `margin_cost_basis` | `VARCHAR` |
| `discount_interpretation` | `VARCHAR` |
| `recommended_channel_period` | `BOOLEAN` |
| `biweek_anchor` | `VARCHAR` |
| `receipt_coverage_policy` | `VARCHAR` |


