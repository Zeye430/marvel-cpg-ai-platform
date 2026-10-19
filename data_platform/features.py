"""First OOS feature slice, built only from an isolated daily-state projection.

No model, label access, source-file reader or production publication is provided.
The caller must prepare a separate connection containing feature_input.daily_state
and optional meta quality tables, not the internal Bronze/Silver build database.
"""

from datetime import date
from .time_policy import DEVELOPMENT_CUTOFF, validate_feature_cutoff

FEATURE_VERSION = "oos_features.v1-state_sales"
INPUT_COLUMNS = (
    "build_id", "dataset_hash", "data_version", "date", "store_id", "sku_id",
    "estimator_version", "as_of_timestamp", "availability_assumption_id",
    "source_lineage_set_id", "assortment_is_confirmed_asof", "channel",
    "segment_tier", "region", "category", "is_msl", "inventory_observed",
    "inventory_estimated", "inventory_data_status", "units_sold_observed",
    "units_sold_estimated", "sales_data_status", "oos_observed", "last_report_date",
    "days_since_inventory_report", "estimation_release_status",
)


def require_isolated_feature_connection(connection):
    """Engineering allowlist shared by feature slices, not an SQL sandbox."""
    relations = connection.execute("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
    """).fetchall()
    allowed = {
        ("feature_input", "daily_state"), ("feature_input", "order_schedule_events"),
        ("feature_input", "receipt_events"), ("gold", "features_oos_daily"),
        ("gold", "features_oos_replenishment"),
        ("feature_input", "promotion_calendar"), ("feature_input", "execution_audits"),
        ("feature_input", "consumer_promotions"), ("gold", "features_oos_context"),
    }
    if any(schema != "meta" and (schema, name) not in allowed for schema, name in relations):
        raise ValueError("Feature builder requires an isolated input connection, not source/evaluation tables")


def build_oos_state_features(connection, as_of, quality, *, purpose="development"):
    """Materialize the state/sales slice; the complete feature contract is pending.

    `as_of` bounds realized input, not a request to expose only that scoring date:
    the output includes historical confirmed pair-days through that date.
    """
    validate_feature_cutoff(as_of, purpose)
    require_isolated_feature_connection(connection)
    # The first slice has a deliberately narrow source projection. Unknown
    # columns (including labels) cannot enter the SQL windows or output schema.
    projection = ", ".join(INPUT_COLUMNS)
    connection.execute(f"""
        CREATE TEMP TABLE _feature_state_input AS
        SELECT {projection} FROM feature_input.daily_state
        WHERE date <= ? AND assortment_is_confirmed_asof
    """, [as_of])
    quality.query("feature_input_grain", "features_oos_daily", """
        SELECT date, store_id, sku_id FROM _feature_state_input
        GROUP BY 1,2,3 HAVING count(*) <> 1
    """)
    quality.query("feature_input_lineage", "features_oos_daily", """
        SELECT date, store_id, sku_id FROM _feature_state_input
        WHERE build_id IS NULL OR dataset_hash IS NULL OR data_version <> 'v3'
           OR data_version IS NULL OR store_id IS NULL OR sku_id IS NULL
           OR as_of_timestamp IS NULL OR cast(as_of_timestamp AS DATE) <> date
           OR source_lineage_set_id IS NULL OR availability_assumption_id IS NULL
    """)
    quality.query("feature_single_build", "features_oos_daily", """
        SELECT 1 FROM _feature_state_input
        HAVING count(DISTINCT build_id) > 1 OR count(DISTINCT dataset_hash) > 1
    """)
    quality.require_pass()
    connection.execute("CREATE SCHEMA IF NOT EXISTS gold")
    connection.execute(f"""
        CREATE TABLE gold.features_oos_daily AS
        WITH eligible AS (
            SELECT *, CASE WHEN units_sold_observed IS NOT NULL AND oos_observed = false
                           THEN units_sold_observed END AS in_stock_sales
            FROM _feature_state_input
        ), history AS (
            SELECT *,
                count(*) OVER current7 AS expected_days_7d,
                count(*) OVER current28 AS expected_days_28d,
                count(units_sold_observed) OVER current7 AS observed_days_7d,
                count(units_sold_observed) OVER current28 AS observed_days_28d,
                sum(units_sold_observed) OVER current7 AS sales_units_observed_7d,
                sum(units_sold_observed) OVER current28 AS sales_units_observed_28d,
                count(in_stock_sales) OVER prior7 AS velocity_days_7d,
                count(in_stock_sales) OVER previous7 AS velocity_days_prev7d,
                count(in_stock_sales) OVER prior28 AS velocity_days_28d,
                CASE WHEN count(in_stock_sales) OVER prior7 >= 3
                     THEN avg(in_stock_sales) OVER prior7 END AS in_stock_velocity_7d,
                CASE WHEN count(in_stock_sales) OVER previous7 >= 3
                     THEN avg(in_stock_sales) OVER previous7 END AS in_stock_velocity_prev7d,
                CASE WHEN count(in_stock_sales) OVER prior28 >= 5
                     THEN avg(in_stock_sales) OVER prior28 END AS in_stock_velocity_28d,
                sum(cast(oos_observed AS BIGINT)) OVER current7 AS oos_days_observed_7d,
                sum(cast(oos_observed AS BIGINT)) OVER current28 AS oos_days_observed_28d
            FROM eligible
            WINDOW current7 AS (PARTITION BY store_id, sku_id ORDER BY date
                       RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW),
                   current28 AS (PARTITION BY store_id, sku_id ORDER BY date
                       RANGE BETWEEN INTERVAL 27 DAY PRECEDING AND CURRENT ROW),
                   prior7 AS (PARTITION BY store_id, sku_id ORDER BY date
                       RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING),
                   previous7 AS (PARTITION BY store_id, sku_id ORDER BY date
                       RANGE BETWEEN INTERVAL 14 DAY PRECEDING AND INTERVAL 8 DAY PRECEDING),
                   prior28 AS (PARTITION BY store_id, sku_id ORDER BY date
                       RANGE BETWEEN INTERVAL 28 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING)
        )
        SELECT h.build_id, h.dataset_hash, h.data_version,
               '{FEATURE_VERSION}' AS feature_version, h.estimator_version,
               h.date AS scoring_date, h.store_id, h.sku_id, h.as_of_timestamp,
               h.availability_assumption_id, h.source_lineage_set_id,
               h.channel, h.segment_tier, h.region, h.category, h.is_msl,
               h.inventory_observed, h.inventory_estimated AS inventory_candidate,
               h.inventory_data_status, h.units_sold_observed AS sales_observed,
               h.units_sold_estimated AS sales_candidate, h.sales_data_status,
               h.oos_observed, h.last_report_date, h.days_since_inventory_report,
               h.estimation_release_status,
               h.expected_days_7d, h.expected_days_28d,
               h.observed_days_7d, h.observed_days_28d,
               h.sales_units_observed_7d, h.sales_units_observed_28d,
               h.velocity_days_7d, h.velocity_days_prev7d, h.velocity_days_28d,
               h.in_stock_velocity_7d, h.in_stock_velocity_prev7d,
               h.in_stock_velocity_28d,
               h.in_stock_velocity_7d / nullif(h.in_stock_velocity_prev7d, 0)
                   AS sales_acceleration_ratio,
               h.inventory_observed / nullif(h.in_stock_velocity_28d, 0)
                   AS days_cover_observed,
               h.inventory_estimated / nullif(h.in_stock_velocity_28d, 0)
                   AS days_cover_candidate,
               h.inventory_observed - prior.inventory_observed
                   AS inventory_change_7d_observed,
               h.oos_days_observed_7d, h.oos_days_observed_28d
        FROM history h LEFT JOIN _feature_state_input prior
          ON h.store_id = prior.store_id AND h.sku_id = prior.sku_id
         AND prior.date = h.date - INTERVAL 7 DAY
    """)
    quality.query("feature_coverage", "features_oos_daily", """
        SELECT scoring_date, store_id, sku_id FROM gold.features_oos_daily
        WHERE observed_days_7d > expected_days_7d OR expected_days_7d > 7
           OR observed_days_28d > expected_days_28d OR expected_days_28d > 28
    """)
    quality.require_pass()
    connection.execute("DROP TABLE _feature_state_input")
    return {
        "feature_version": FEATURE_VERSION,
        "feature_slice": "state_sales_only",
        "rows": connection.execute("SELECT count(*) FROM gold.features_oos_daily").fetchone()[0],
        "as_of": str(as_of), "application_ready": False,
    }
