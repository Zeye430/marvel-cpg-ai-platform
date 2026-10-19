"""Past-only short-gap state estimation shared by all four modules.

This creates a candidate daily state, not supervised OOS labels. The optional
promotion adjustment is fitted on an earlier warm-up period and never applied
to earlier rows. Input observations may be masked before calling this function.
"""

from dataclasses import asdict, dataclass
from datetime import date

from .ingestion import json_hash, literal


@dataclass(frozen=True)
class EstimatorConfig:
    max_gap_days: int = 4
    weekday_min_count: int = 2
    recent_min_count: int = 5
    group_min_count: int = 20
    promotion_fit_cutoff: date = date(2025, 12, 31)
    promotion_min_count: int = 100
    promotion_max_factor: float = 2.0

    def validate(self):
        if self.max_gap_days != 4:
            raise ValueError("The current shared contract allows at most four missing days")
        if min(self.weekday_min_count, self.recent_min_count, self.group_min_count,
               self.promotion_min_count) < 1:
            raise ValueError("Estimator minimum counts must be positive")
        if self.promotion_max_factor < 1:
            raise ValueError("Promotion factor upper bound must be at least one")

    @property
    def version(self):
        values = {**asdict(self), "promotion_fit_cutoff": str(self.promotion_fit_cutoff)}
        return "historical_state.v1-" + json_hash(values)[:12]


def build_daily_state(connection, build_id, as_of, quality, config=None):
    """Read only Silver relations; no Bronze, hidden observations or future labels."""
    config = config or EstimatorConfig()
    config.validate()
    c = config
    version = literal(c.version)
    fit_cutoff = literal(c.promotion_fit_cutoff)
    as_of_sql = literal(as_of)
    # Fitting a factor requires strictly earlier observations. The fit itself
    # is skipped when the requested build has not reached the warm-up cutoff.
    connection.execute(f"""
        CREATE TABLE gold.estimator_promotion_factors AS
        SELECT s.channel, p.category,
               median(o.units_sold_daily) FILTER (WHERE o.promo_id IS NULL) AS regular_median,
               median(o.units_sold_daily) FILTER (WHERE o.promo_id IS NOT NULL) AS promo_median,
               count(*) FILTER (WHERE o.promo_id IS NULL) AS regular_count,
               count(*) FILTER (WHERE o.promo_id IS NOT NULL) AS promo_count,
               CASE WHEN regular_count >= {c.promotion_min_count}
                          AND promo_count >= {c.promotion_min_count} AND regular_median > 0
                    THEN least({c.promotion_max_factor}, greatest(1.0, promo_median / regular_median))
                    ELSE 1.0 END AS factor,
               CASE WHEN regular_count >= {c.promotion_min_count}
                          AND promo_count >= {c.promotion_min_count} AND regular_median > 0
                    THEN 'Fitted descriptive ratio' ELSE 'Insufficient warm-up evidence' END AS fit_status,
               DATE {fit_cutoff} AS fit_cutoff, DATE {fit_cutoff} + 1 AS effective_from,
               {version} AS estimator_version, {literal(build_id)} AS build_id,
               any_value(o.dataset_hash) AS dataset_hash
        FROM silver.fact_sell_out o JOIN silver.dim_stores s USING (store_id)
        JOIN silver.dim_products p USING (sku_id)
        WHERE o.date <= DATE {fit_cutoff} AND DATE {fit_cutoff} < DATE {as_of_sql}
          AND NOT o.is_out_of_stock
        GROUP BY s.channel, p.category
    """)
    # Campaign eligibility is not proof of consumer participation. Keep all
    # overlaps; candidate uplift is disabled when multiple campaigns are eligible.
    connection.execute(f"""
        CREATE TABLE gold.daily_promotion_membership AS
        SELECT sha256(cast(date AS VARCHAR) || '|' || channel || '|' || category || '|' ||
                      string_agg(promo_id, ',' ORDER BY promo_id) OVER (PARTITION BY date, channel, category)) AS promo_set_id,
               date, channel, category, promo_id, discount_pct, source_known_at,
               source_file_hash, source_row_number, dataset_hash,
               {literal(build_id)} AS build_id, build_id AS source_build_id
        FROM silver.promotion_calendar
    """)
    connection.execute(f"""
        CREATE TABLE gold.receipt_set_membership AS
        SELECT sha256(cast(receipt_date AS VARCHAR) || '|' || store_id || '|' || sku_id || '|' ||
                      string_agg(delivery_id, ',' ORDER BY delivery_id) OVER (PARTITION BY receipt_date, store_id, sku_id)) AS receipt_set_id,
               receipt_date AS date, store_id, sku_id, delivery_id, receipt_units,
               source_known_at, source_file_hash, source_row_number, dataset_hash,
               {literal(build_id)} AS build_id, build_id AS source_build_id
        FROM silver.replenishment_events
    """)
    connection.execute("""
        CREATE TEMP TABLE _state_inputs AS
        WITH campaigns AS (
            SELECT date, channel, category, any_value(promo_set_id) AS promo_set_id,
                   count(*) AS eligible_promotion_count,
                   max(source_known_at) AS promotion_known_at
            FROM gold.daily_promotion_membership GROUP BY 1,2,3
        ), receipts AS (
            SELECT date, store_id, sku_id, any_value(receipt_set_id) AS receipt_set_id,
                   sum(receipt_units) AS receipt_units_from_deliveries,
                   count(*) AS receipt_delivery_count, max(source_known_at) AS receipt_known_at
            FROM gold.receipt_set_membership GROUP BY 1,2,3
        ), reporting AS (
            SELECT DISTINCT date, store_id FROM silver.fact_sell_out
        ), first_seen AS (
            SELECT store_id, sku_id, min(date) AS first_observed_date
            FROM silver.fact_sell_out GROUP BY 1,2
        )
        SELECT d.date, d.store_id, d.sku_id, d.dataset_hash, d.data_version,
               o.units_sold_daily::DOUBLE AS units_sold_observed,
               o.inventory_level::DOUBLE AS inventory_observed, o.is_out_of_stock AS oos_observed,
               o.restock_units AS restock_units_observed, o.restock_delivery_id,
               o.consumer_price_actual AS consumer_price_observed,
               o.promo_id AS observed_promo_id, o.promo_discount_pct AS observed_promo_discount_pct,
               o.source_file_hash AS sell_out_source_file_hash,
               o.source_row_number AS sell_out_source_row_number,
               o.source_known_at AS sell_out_known_at,
               o.date IS NOT NULL AS sales_is_observed, o.date IS NOT NULL AS inventory_is_observed,
               r.date IS NOT NULL AS is_store_day_reported,
               coalesce(d.date >= f.first_observed_date, false) AS assortment_is_confirmed_asof,
               s.channel, s.region, s.segment_tier, p.category, p.is_msl,
               a.promo_set_id, coalesce(a.eligible_promotion_count, 0) AS eligible_promotion_count,
               a.promotion_known_at, x.receipt_set_id,
               coalesce(x.receipt_units_from_deliveries, 0)::DOUBLE AS receipt_units_from_deliveries,
               coalesce(x.receipt_delivery_count, 0) AS receipt_delivery_count, x.receipt_known_at,
               coalesce(pf.factor, 1.0) AS fitted_promotion_factor,
               coalesce(pf.fit_status, 'Insufficient warm-up evidence') AS promotion_fit_status,
               CASE WHEN NOT o.is_out_of_stock THEN o.units_sold_daily::DOUBLE END AS eligible_sales_raw,
               CASE WHEN NOT o.is_out_of_stock THEN o.units_sold_daily::DOUBLE /
                        CASE WHEN o.promo_id IS NOT NULL THEN coalesce(pf.factor, 1.0) ELSE 1.0 END
                    END AS eligible_sales_normalized
        FROM silver.store_sku_calendar d
        LEFT JOIN silver.fact_sell_out o USING (date, store_id, sku_id)
        JOIN silver.dim_stores s USING (store_id) JOIN silver.dim_products p USING (sku_id)
        LEFT JOIN campaigns a ON d.date=a.date AND s.channel=a.channel AND p.category=a.category
        LEFT JOIN receipts x ON d.date=x.date AND d.store_id=x.store_id AND d.sku_id=x.sku_id
        LEFT JOIN reporting r ON d.date=r.date AND d.store_id=r.store_id
        LEFT JOIN first_seen f ON d.store_id=f.store_id AND d.sku_id=f.sku_id
        LEFT JOIN gold.estimator_promotion_factors pf ON s.channel=pf.channel AND p.category=pf.category
    """)
    connection.execute("""
        CREATE TEMP TABLE _group_frequency AS
        SELECT date, channel, category, mode, value, count(*) AS n
        FROM (
            SELECT date, channel, category, 'raw' AS mode, eligible_sales_raw AS value FROM _state_inputs
            UNION ALL
            SELECT date, channel, category, 'normalized' AS mode, eligible_sales_normalized AS value FROM _state_inputs
        ) WHERE value IS NOT NULL GROUP BY 1,2,3,4,5
    """)
    # Exact empirical medians from compact historical frequency tables avoid
    # maintaining a huge expanding median window for every SKU row. Frequencies
    # exclude the entire current date, including other stores' same-day sales.
    connection.execute("""
        CREATE TEMP TABLE _group_fallback_daily AS
        WITH days AS (SELECT DISTINCT date, channel, category FROM _state_inputs),
        vals AS (SELECT DISTINCT channel, category, mode, value FROM _group_frequency),
        cumulative AS (
            SELECT d.date, d.channel, d.category, v.mode, v.value,
                   coalesce(sum(coalesce(f.n, 0)) OVER (
                       PARTITION BY d.channel, d.category, v.mode, v.value ORDER BY d.date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS n
            FROM days d JOIN vals v USING(channel, category)
            LEFT JOIN _group_frequency f ON d.date=f.date AND d.channel=f.channel
                AND d.category=f.category AND v.mode=f.mode AND v.value=f.value
        ), ranks AS (
            SELECT *, sum(n) OVER (PARTITION BY date,channel,category,mode ORDER BY value) AS cdf,
                   sum(n) OVER (PARTITION BY date,channel,category,mode) AS total
            FROM cumulative
        ), middle_values AS (
            SELECT date, channel, category, mode, max(total) AS total,
                   (max(value) FILTER (WHERE cdf >= floor((total+1)/2.0) AND cdf-n < floor((total+1)/2.0))
                    + max(value) FILTER (WHERE cdf >= floor((total+2)/2.0) AND cdf-n < floor((total+2)/2.0))) / 2 AS median_value
            FROM ranks GROUP BY 1,2,3,4
        ), history_dates AS (
            SELECT date, channel, category,
                   max(CASE WHEN count(eligible_sales_raw) > 0 THEN date END) OVER (
                       PARTITION BY channel,category ORDER BY date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS group_last_date
            FROM _state_inputs GROUP BY 1,2,3
        )
        SELECT d.date, d.channel, d.category, d.group_last_date,
               max(m.total) AS group_count,
               max(m.median_value) FILTER (WHERE m.mode='raw') AS group_raw,
               max(m.median_value) FILTER (WHERE m.mode='normalized') AS group_normalized
        FROM history_dates d LEFT JOIN middle_values m USING(date,channel,category)
        GROUP BY 1,2,3,4
    """)
    connection.execute("""
        CREATE TEMP TABLE _state_windows AS
        SELECT *,
            median(eligible_sales_raw) OVER weekday_history AS weekday_raw,
            median(eligible_sales_normalized) OVER weekday_history AS weekday_normalized,
            count(eligible_sales_raw) OVER weekday_history AS weekday_count,
            max(CASE WHEN eligible_sales_raw IS NOT NULL THEN date END) OVER weekday_history AS weekday_last_date,
            median(eligible_sales_raw) OVER recent_history AS recent_raw,
            median(eligible_sales_normalized) OVER recent_history AS recent_normalized,
            count(eligible_sales_raw) OVER recent_history AS recent_count,
            max(CASE WHEN eligible_sales_raw IS NOT NULL THEN date END) OVER recent_history AS recent_last_date,
            max(CASE WHEN inventory_is_observed THEN date END) OVER pair_to_date AS last_report_date,
            last_value(inventory_observed IGNORE NULLS) OVER pair_to_date AS last_observed_inventory,
            last_value(sell_out_source_row_number IGNORE NULLS) OVER pair_to_date AS last_report_source_row_number,
            count(inventory_observed) OVER pair_to_date AS report_episode
        FROM _state_inputs LEFT JOIN _group_fallback_daily USING(date,channel,category)
        WINDOW weekday_history AS (PARTITION BY store_id, sku_id, isodow(date) ORDER BY date
                   RANGE BETWEEN INTERVAL 56 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING),
               recent_history AS (PARTITION BY store_id, sku_id ORDER BY date
                   RANGE BETWEEN INTERVAL 28 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING),
               pair_to_date AS (PARTITION BY store_id, sku_id ORDER BY date
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    """)
    connection.execute(f"""
        CREATE TEMP TABLE _state_baselines AS
        WITH selected AS (
            SELECT *, date_diff('day', last_report_date, date) AS days_since_inventory_report,
                   CASE WHEN weekday_count >= {c.weekday_min_count} THEN weekday_raw
                        WHEN recent_count >= {c.recent_min_count} THEN recent_raw
                        WHEN group_count >= {c.group_min_count} THEN group_raw END AS sales_baseline_unadjusted,
                   CASE WHEN weekday_count >= {c.weekday_min_count} THEN weekday_normalized
                        WHEN recent_count >= {c.recent_min_count} THEN recent_normalized
                        WHEN group_count >= {c.group_min_count} THEN group_normalized END AS sales_baseline_normalized,
                   CASE WHEN weekday_count >= {c.weekday_min_count} THEN 'Prior 8 weekdays median'
                        WHEN recent_count >= {c.recent_min_count} THEN 'Prior 28 calendar days median'
                        WHEN group_count >= {c.group_min_count} THEN 'Historical channel-category median'
                        ELSE 'Insufficient history' END AS sales_baseline_method,
                   CASE WHEN weekday_count >= {c.weekday_min_count} THEN weekday_last_date
                        WHEN recent_count >= {c.recent_min_count} THEN recent_last_date
                        WHEN group_count >= {c.group_min_count} THEN group_last_date END AS sales_history_max_date,
                   date > DATE {fit_cutoff} AS promotion_fit_is_available,
                   CASE WHEN date > DATE {fit_cutoff} AND eligible_promotion_count = 1
                        THEN fitted_promotion_factor ELSE 1.0 END AS current_promotion_factor
            FROM _state_windows
        )
        SELECT *, CASE WHEN promotion_fit_is_available
                       THEN sales_baseline_normalized * current_promotion_factor
                       ELSE sales_baseline_unadjusted END AS sales_baseline_promotion_adjusted,
               NOT sales_is_observed AND assortment_is_confirmed_asof
                   AND days_since_inventory_report BETWEEN 1 AND {c.max_gap_days} AS fresh_missing_day
        FROM selected
    """)
    connection.execute("""
        CREATE TEMP TABLE _state_gaps AS
        SELECT *,
               sum(CASE WHEN NOT inventory_is_observed THEN receipt_units_from_deliveries ELSE 0 END)
                   OVER gap_to_date AS cumulative_gap_receipts,
               sum(CASE WHEN NOT sales_is_observed THEN coalesce(sales_baseline_unadjusted, 0) ELSE 0 END)
                   OVER gap_to_date AS cumulative_gap_sales_unadjusted,
               sum(CASE WHEN NOT sales_is_observed THEN coalesce(sales_baseline_promotion_adjusted, 0) ELSE 0 END)
                   OVER gap_to_date AS cumulative_gap_sales_adjusted,
               count(*) FILTER (WHERE NOT sales_is_observed AND sales_baseline_promotion_adjusted IS NULL)
                   OVER gap_to_date AS gap_unknown_sales_days
        FROM _state_baselines
        WINDOW gap_to_date AS (PARTITION BY store_id, sku_id, report_episode ORDER BY date
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    """)
    connection.execute(f"""
        CREATE TABLE gold.mart_store_sku_daily AS
        WITH estimated AS (
            SELECT *,
                CASE WHEN fresh_missing_day THEN sales_baseline_promotion_adjusted END AS units_sold_estimated,
                CASE WHEN fresh_missing_day AND gap_unknown_sales_days = 0
                     THEN last_observed_inventory + cumulative_gap_receipts - cumulative_gap_sales_adjusted END AS inventory_unclipped
            FROM _state_gaps
        ), states AS (
            SELECT *, CASE WHEN inventory_unclipped IS NOT NULL THEN greatest(0, inventory_unclipped) END AS inventory_estimated
            FROM estimated
        )
        SELECT {literal(build_id)} AS build_id, dataset_hash, data_version, date, store_id, sku_id,
               'daily_state.v1' AS transform_version, {version} AS estimator_version,
               units_sold_observed, inventory_observed, oos_observed, restock_units_observed,
               restock_delivery_id, consumer_price_observed, observed_promo_id, observed_promo_discount_pct,
               is_store_day_reported, sales_is_observed, inventory_is_observed,
               assortment_is_confirmed_asof, last_report_date, last_observed_inventory, days_since_inventory_report,
               units_sold_estimated, inventory_estimated, inventory_unclipped,
               coalesce(units_sold_observed, units_sold_estimated) AS sales_model_state,
               coalesce(inventory_observed, inventory_estimated) AS inventory_model_state,
               CASE WHEN sales_is_observed THEN 'Observed' WHEN units_sold_estimated IS NOT NULL THEN 'Estimated' ELSE 'Unknown' END AS sales_data_status,
               CASE WHEN inventory_is_observed THEN 'Observed' WHEN inventory_estimated IS NOT NULL THEN 'Estimated' ELSE 'Unknown' END AS inventory_data_status,
               CASE WHEN inventory_estimated IS NOT NULL THEN inventory_estimated = 0 END AS oos_estimated,
               CASE WHEN NOT sales_is_observed AND units_sold_estimated IS NOT NULL THEN sales_baseline_method END AS sales_estimation_method,
               CASE WHEN inventory_estimated IS NOT NULL THEN 'Cumulative known receipts minus estimated sales; zero floor' END AS inventory_estimation_method,
               CASE WHEN inventory_is_observed THEN NULL
                    WHEN NOT assortment_is_confirmed_asof OR last_report_date IS NULL THEN 'No prior inventory report'
                    WHEN days_since_inventory_report > {c.max_gap_days} THEN 'Inventory report older than four days'
                    WHEN gap_unknown_sales_days > 0 THEN 'Insufficient sales history within gap'
                    ELSE 'Eligible short gap' END AS inventory_state_reason,
               receipt_units_from_deliveries, receipt_delivery_count, receipt_set_id,
               channel, region, segment_tier, category, is_msl, promo_set_id, eligible_promotion_count,
               eligible_promotion_count > 1 AS has_promotion_overlap,
               'Eligible campaign applies when unique; candidate assumption' AS promotion_application_assumption,
               current_promotion_factor,
               CASE WHEN promotion_fit_is_available THEN promotion_fit_status
                    ELSE 'Warm-up; factor unavailable' END AS promotion_fit_status,
               CASE WHEN promotion_fit_is_available THEN DATE {fit_cutoff} END AS promotion_factor_fit_cutoff,
               sales_baseline_unadjusted, sales_baseline_promotion_adjusted, sales_baseline_method,
               sales_history_max_date, weekday_count, recent_count, group_count,
               cumulative_gap_receipts, cumulative_gap_sales_unadjusted, cumulative_gap_sales_adjusted,
               gap_unknown_sales_days, sell_out_source_file_hash, sell_out_source_row_number,
               last_report_source_row_number, sell_out_known_at, receipt_known_at, promotion_known_at,
               cast(date AS TIMESTAMP) + INTERVAL 1 DAY - INTERVAL 1 MICROSECOND AS as_of_timestamp,
               'same_day_end_simulation_v1' AS availability_assumption_id,
               sha256(dataset_hash || '|' || {literal(build_id)} || '|' || cast(date AS VARCHAR) || '|' || store_id || '|' || sku_id) AS source_lineage_set_id,
               'Candidate; corrected masking review required' AS estimation_release_status
        FROM states
    """)
    # Only prior or same-day audit evidence is joined; unknown expiry is explicit.
    connection.execute("""
        CREATE TABLE silver.execution_latest_asof AS
        WITH audits AS (
            SELECT *, count(*) OVER (PARTITION BY date, store_id, sku_id) AS same_day_audit_count
            FROM silver.fact_execution_audits
            QUALIFY row_number() OVER (PARTITION BY date, store_id, sku_id ORDER BY audit_id) = 1
        )
        SELECT d.date, d.store_id, d.sku_id, a.audit_id AS last_audit_id,
               a.date AS last_audit_date, date_diff('day', a.date, d.date) AS days_since_last_audit,
               a.same_day_audit_count, a.source_known_at AS audit_known_at,
               a.source_file_hash AS audit_source_file_hash, a.source_row_number AS audit_source_row_number,
               'Requires review; no approved audit expiry policy' AS expiry_status,
               d.dataset_hash, d.build_id
        FROM gold.mart_store_sku_daily d ASOF LEFT JOIN audits a
          ON d.store_id = a.store_id AND d.sku_id = a.sku_id AND d.date >= a.date
    """)
    connection.execute("ALTER TABLE gold.mart_store_sku_daily ADD COLUMN last_audit_id VARCHAR")
    connection.execute("ALTER TABLE gold.mart_store_sku_daily ADD COLUMN days_since_last_audit BIGINT")
    connection.execute("""
        UPDATE gold.mart_store_sku_daily d SET last_audit_id = a.last_audit_id,
               days_since_last_audit = a.days_since_last_audit
        FROM silver.execution_latest_asof a WHERE d.date=a.date AND d.store_id=a.store_id AND d.sku_id=a.sku_id
    """)
    connection.execute("""
        CREATE TABLE gold.daily_lineage AS
        SELECT d.source_lineage_set_id, d.date, d.store_id, d.sku_id, d.dataset_hash, d.build_id,
               d.sell_out_source_file_hash, d.sell_out_source_row_number, d.last_report_source_row_number,
               d.receipt_set_id, d.promo_set_id, d.last_audit_id,
               a.audit_source_file_hash, a.audit_source_row_number,
               d.estimator_version, d.sales_baseline_method, d.sales_history_max_date,
               d.promotion_factor_fit_cutoff, d.as_of_timestamp, d.availability_assumption_id
        FROM gold.mart_store_sku_daily d
        JOIN silver.execution_latest_asof a USING (date, store_id, sku_id)
    """)
    validate_daily_state(connection, quality)
    return {"estimator_version": c.version, "configuration": asdict(c),
            "rows": connection.execute("SELECT count(*) FROM gold.mart_store_sku_daily").fetchone()[0],
            "status": "Candidate; masking diagnostics do not establish Module 2 performance"}


def validate_daily_state(connection, quality):
    base = "gold.mart_store_sku_daily"
    population = connection.execute(f"SELECT count(*) FROM {base}").fetchone()[0]
    checks = {
        "daily_grain": f"SELECT date, store_id, sku_id FROM {base} GROUP BY 1,2,3 HAVING count(*) <> 1",
        "observed_not_overwritten": f"""SELECT date, store_id, sku_id FROM {base} WHERE sales_is_observed AND
            (units_sold_estimated IS NOT NULL OR inventory_estimated IS NOT NULL OR
             sales_model_state <> units_sold_observed OR inventory_model_state <> inventory_observed OR oos_estimated IS NOT NULL)""",
        "past_only_history": f"SELECT date, store_id, sku_id FROM {base} WHERE sales_history_max_date >= date OR promotion_factor_fit_cutoff >= date",
        "four_day_limit": f"""SELECT date, store_id, sku_id FROM {base} WHERE NOT inventory_is_observed
            AND (days_since_inventory_report > 4 OR days_since_inventory_report IS NULL OR NOT assortment_is_confirmed_asof)
            AND (inventory_estimated IS NOT NULL OR units_sold_estimated IS NOT NULL)""",
        "nonnegative_estimates": f"SELECT date, store_id, sku_id FROM {base} WHERE inventory_estimated < 0 OR units_sold_estimated < 0",
        "no_future_receipt": f"SELECT date, store_id, sku_id FROM {base} WHERE receipt_known_at > as_of_timestamp",
        "unknown_state_is_null": f"""SELECT date, store_id, sku_id FROM {base} WHERE
            (inventory_data_status='Unknown' AND inventory_model_state IS NOT NULL) OR
            (sales_data_status='Unknown' AND sales_model_state IS NOT NULL)""",
        "audit_asof": "SELECT date, store_id, sku_id FROM silver.execution_latest_asof WHERE last_audit_date > date",
        "lineage_cardinality": f"""SELECT d.date, d.store_id, d.sku_id FROM {base} d
            LEFT JOIN gold.daily_lineage l USING(source_lineage_set_id) GROUP BY 1,2,3 HAVING count(l.source_lineage_set_id) <> 1""",
    }
    for name, sql in checks.items():
        quality.query(name, "mart_store_sku_daily", sql, checked_count=population)
    quality.require_pass()
