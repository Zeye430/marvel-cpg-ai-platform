"""Point-in-time posted receipts and pending plans; no future actual ETA model."""

from datetime import date

from .features import DEVELOPMENT_CUTOFF, require_isolated_feature_connection
from .time_policy import validate_feature_cutoff

ORDER_COLUMNS = (
    "delivery_id", "store_id", "sku_id", "order_date", "scheduled_delivery", "units_ordered",
    "source_known_at", "schedule_version", "availability_assumption_id",
    "dataset_hash", "data_version", "build_id", "source_file_hash", "source_row_number",
)
RECEIPT_COLUMNS = (
    "delivery_id", "store_id", "sku_id", "receipt_date", "receipt_units", "source_known_at",
    "availability_assumption_id", "dataset_hash", "data_version", "build_id",
    "source_file_hash", "source_row_number",
)
FEATURE_VERSION = "oos_replenishment.v1"


def build_oos_replenishment_features(connection, as_of, quality, *, assume_complete_snapshot=False, purpose="development"):
    """Build a separate feature group to join by build/date/store/SKU later.

    Zero-event quantities stay NULL unless feed completeness is explicitly
    assumed. Posted short/zero-unit deliveries close the single V3 delivery
    event; this does not infer that a production backorder has been fulfilled.
    """
    validate_feature_cutoff(as_of, purpose)
    if type(assume_complete_snapshot) is not bool:
        raise ValueError("assume_complete_snapshot must be an explicit boolean")
    require_isolated_feature_connection(connection)
    scope = "features_oos_replenishment"
    connection.execute("""
        CREATE TEMP TABLE _replen_days AS
        SELECT build_id,dataset_hash,data_version,date AS scoring_date,store_id,sku_id,as_of_timestamp
        FROM feature_input.daily_state WHERE date<=? AND assortment_is_confirmed_asof
    """, [as_of])
    connection.execute("CREATE TEMP TABLE _replen_identity AS SELECT DISTINCT build_id,dataset_hash FROM _replen_days")
    for name, columns, event in (
        ("orders", ORDER_COLUMNS, "order_date"), ("receipts", RECEIPT_COLUMNS, "receipt_date"),
    ):
        relation = "order_schedule_events" if name == "orders" else "receipt_events"
        connection.execute(f"""
            CREATE TEMP TABLE _replen_{name} AS SELECT {', '.join(columns)}
            FROM feature_input.{relation}
            WHERE ({event}<=? OR {event} IS NULL)
              AND (source_known_at<cast(? AS DATE)+INTERVAL 1 DAY OR source_known_at IS NULL)
        """, [as_of, as_of])
        quality.query(name+"_grain", scope, f"""
            SELECT delivery_id FROM _replen_{name} GROUP BY delivery_id HAVING count(*)<>1
        """)
        quality.query(name+"_lineage", scope, f"""
            SELECT delivery_id FROM _replen_{name} e
            WHERE e.delivery_id IS NULL OR e.store_id IS NULL OR e.sku_id IS NULL
               OR e.source_known_at IS NULL OR e.source_file_hash IS NULL OR e.source_row_number IS NULL
               OR e.data_version IS DISTINCT FROM 'v3' OR e.build_id IS NULL OR e.dataset_hash IS NULL
               OR EXISTS (SELECT 1 FROM _replen_identity d
                          WHERE d.build_id IS DISTINCT FROM e.build_id
                             OR d.dataset_hash IS DISTINCT FROM e.dataset_hash)
        """)
    quality.query("day_identity", scope, """
        SELECT scoring_date,store_id,sku_id FROM _replen_days
        WHERE build_id IS NULL OR dataset_hash IS NULL OR data_version IS DISTINCT FROM 'v3'
           OR store_id IS NULL OR sku_id IS NULL OR as_of_timestamp IS NULL
           OR cast(as_of_timestamp AS DATE)<>scoring_date
        UNION ALL SELECT scoring_date,store_id,sku_id FROM _replen_days
        GROUP BY 1,2,3 HAVING count(*)<>1
    """)
    quality.query("single_build", scope, """
        SELECT 1 FROM _replen_days
        HAVING count(DISTINCT build_id)>1 OR count(DISTINCT dataset_hash)>1
    """)
    quality.query("schedule_values", scope, """
        SELECT delivery_id FROM _replen_orders WHERE units_ordered IS NULL OR units_ordered<0 OR order_date IS NULL
           OR scheduled_delivery IS NULL OR scheduled_delivery<order_date
           OR source_known_at<cast(order_date AS TIMESTAMP) OR schedule_version IS NULL
           OR availability_assumption_id IS NULL
    """)
    quality.query("receipt_values", scope, """
        SELECT delivery_id FROM _replen_receipts WHERE receipt_units IS NULL OR receipt_units<0 OR receipt_date IS NULL
           OR source_known_at<cast(receipt_date AS TIMESTAMP) OR availability_assumption_id IS NULL
    """)
    quality.query("receipt_order_identity", scope, """
        SELECT r.delivery_id FROM _replen_receipts r JOIN _replen_orders o USING(delivery_id)
        WHERE r.store_id<>o.store_id OR r.sku_id<>o.sku_id OR r.receipt_date<o.order_date
    """)
    quality.require_pass()

    # Receipt measures and order measures are aggregated independently. Joining
    # both event streams first would multiply units when multiple orders exist.
    connection.execute("""
        CREATE TEMP TABLE _replen_receipt_stats AS
        SELECT d.scoring_date,d.store_id,d.sku_id,
            count(r.delivery_id) FILTER (WHERE r.receipt_date>=d.scoring_date-INTERVAL 6 DAY) AS events7,
            count(r.delivery_id) FILTER (WHERE r.receipt_date>=d.scoring_date-INTERVAL 27 DAY) AS events28,
            sum(r.receipt_units) FILTER (WHERE r.receipt_date>=d.scoring_date-INTERVAL 6 DAY) AS units7,
            sum(r.receipt_units) FILTER (WHERE r.receipt_date>=d.scoring_date-INTERVAL 27 DAY) AS units28,
            max(r.receipt_date) AS last_receipt_date,
            max(r.source_known_at) AS receipt_history_max_known_at
        FROM _replen_days d LEFT JOIN _replen_receipts r
          ON r.store_id=d.store_id AND r.sku_id=d.sku_id
         AND r.receipt_date<=d.scoring_date AND r.source_known_at<=d.as_of_timestamp
        GROUP BY 1,2,3
    """)
    connection.execute("""
        CREATE TEMP TABLE _replen_pending_stats AS
        SELECT d.scoring_date,d.store_id,d.sku_id,
            count(o.delivery_id) FILTER (WHERE r.delivery_id IS NULL) AS pending_count,
            sum(o.units_ordered) FILTER (WHERE r.delivery_id IS NULL) AS pending_units,
            count(o.delivery_id) FILTER (WHERE r.delivery_id IS NULL AND o.scheduled_delivery>d.scoring_date
                    AND o.scheduled_delivery<=d.scoring_date+INTERVAL 7 DAY) AS next7_count,
            sum(o.units_ordered) FILTER (WHERE r.delivery_id IS NULL AND o.scheduled_delivery>d.scoring_date
                    AND o.scheduled_delivery<=d.scoring_date+INTERVAL 7 DAY) AS next7_units,
            count(o.delivery_id) FILTER (WHERE r.delivery_id IS NULL AND o.scheduled_delivery<=d.scoring_date) AS overdue_count,
            sum(o.units_ordered) FILTER (WHERE r.delivery_id IS NULL AND o.scheduled_delivery<=d.scoring_date) AS overdue_units,
            min(o.scheduled_delivery) FILTER (WHERE r.delivery_id IS NULL
                    AND o.scheduled_delivery>d.scoring_date) AS next_scheduled_date,
            max(o.source_known_at) FILTER (WHERE r.delivery_id IS NULL) AS pending_orders_max_known_at
        FROM _replen_days d LEFT JOIN _replen_orders o
          ON o.store_id=d.store_id AND o.sku_id=d.sku_id
         AND o.order_date<=d.scoring_date AND o.source_known_at<=d.as_of_timestamp
        LEFT JOIN _replen_receipts r ON r.delivery_id=o.delivery_id
         AND r.receipt_date<=d.scoring_date AND r.source_known_at<=d.as_of_timestamp
        GROUP BY 1,2,3
    """)
    mode = "assumed_complete_snapshot" if assume_complete_snapshot else "unconfirmed_extract"
    version = FEATURE_VERSION + "-" + mode
    known_zero = "true" if assume_complete_snapshot else "false"
    connection.execute("CREATE SCHEMA IF NOT EXISTS gold")
    connection.execute(f"""
        CREATE TABLE gold.features_oos_replenishment AS
        SELECT d.*, '{version}' AS feature_version,
            '{mode}' AS replenishment_feed_status,
            'same_day_posted_receipts_initial_order_schedule_v1' AS availability_assumption_id,
            CASE WHEN {known_zero} OR r.events7>0 THEN coalesce(r.units7,0) END AS receipt_units_7d,
            CASE WHEN {known_zero} OR r.events28>0 THEN coalesce(r.units28,0) END AS receipt_units_28d,
            r.last_receipt_date,
            date_diff('day',r.last_receipt_date,d.scoring_date) AS days_since_receipt,
            CASE WHEN {known_zero} OR p.pending_count>0 THEN p.pending_count END AS pending_order_count,
            CASE WHEN {known_zero} OR p.pending_count>0 THEN coalesce(p.pending_units,0) END AS pending_units_ordered,
            CASE WHEN {known_zero} OR p.next7_count>0 THEN coalesce(p.next7_units,0) END AS scheduled_units_next7d,
            CASE WHEN {known_zero} OR p.overdue_count>0 THEN coalesce(p.overdue_units,0) END AS overdue_units_ordered,
            date_diff('day',d.scoring_date,p.next_scheduled_date) AS days_until_next_scheduled_receipt,
            r.receipt_history_max_known_at,p.pending_orders_max_known_at
        FROM _replen_days d JOIN _replen_receipt_stats r USING(scoring_date,store_id,sku_id)
        JOIN _replen_pending_stats p USING(scoring_date,store_id,sku_id)
    """)
    quality.query("output_grain", scope, """
        SELECT scoring_date,store_id,sku_id FROM gold.features_oos_replenishment
        GROUP BY 1,2,3 HAVING count(*)<>1
    """)
    expected = connection.execute("SELECT count(*) FROM _replen_days").fetchone()[0]
    actual = connection.execute("SELECT count(*) FROM gold.features_oos_replenishment").fetchone()[0]
    quality.record("row_parity", scope, abs(expected-actual), checked_count=expected)
    quality.query("output_values_and_known_at", scope, """
        SELECT scoring_date,store_id,sku_id FROM gold.features_oos_replenishment
        WHERE receipt_units_7d<0 OR receipt_units_28d<receipt_units_7d
           OR pending_order_count<0 OR pending_units_ordered<0 OR scheduled_units_next7d<0
           OR overdue_units_ordered<0 OR scheduled_units_next7d>pending_units_ordered
           OR overdue_units_ordered>pending_units_ordered OR days_since_receipt<0
           OR days_until_next_scheduled_receipt<=0
           OR receipt_history_max_known_at>as_of_timestamp OR pending_orders_max_known_at>as_of_timestamp
    """)
    quality.require_pass()
    for name in ("days", "identity", "orders", "receipts", "receipt_stats", "pending_stats"):
        connection.execute(f"DROP TABLE _replen_{name}")
    return {"feature_version": version, "feature_slice": "receipts_and_pending_orders",
            "rows": actual, "as_of": str(as_of), "application_ready": False,
            "assume_complete_snapshot": assume_complete_snapshot}
