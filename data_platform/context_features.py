"""Known campaign eligibility, observed consumer prices and backward audit evidence."""

from datetime import date
from .features import DEVELOPMENT_CUTOFF, require_isolated_feature_connection
from .time_policy import validate_feature_cutoff

LINEAGE = ("build_id", "dataset_hash", "data_version", "source_file_hash", "source_row_number", "source_known_at")
CONTEXT_SOURCES = (
    ("promotion_calendar", "promotion_calendar",
     ("date", "channel", "category", "promo_id", "discount_pct", "min_volume_units") + LINEAGE),
    ("execution_audits", "fact_execution_audits",
     ("date", "store_id", "sku_id", "audit_id", "planogram_compliance", "shelf_share_pct", "secondary_display") + LINEAGE),
    ("consumer_promotions", "fact_sell_out",
     ("date", "store_id", "sku_id", "promo_id", "promo_discount_pct", "consumer_price_actual") + LINEAGE),
)
FEATURE_VERSION = "oos_context.v1"


def build_oos_context_features(con, as_of, quality, *, purpose="development"):
    validate_feature_cutoff(as_of, purpose)
    require_isolated_feature_connection(con)
    scope = "features_oos_context"
    con.execute("""CREATE TEMP TABLE _ctx_days AS
        SELECT build_id,dataset_hash,data_version,date AS scoring_date,store_id,sku_id,
               channel,category,as_of_timestamp
        FROM feature_input.daily_state WHERE date<=? AND assortment_is_confirmed_asof""", [as_of])
    quality.query("days_grain", scope, "SELECT scoring_date,store_id,sku_id FROM _ctx_days GROUP BY 1,2,3 HAVING count(*)<>1")
    quality.query("days_identity", scope, """SELECT 1 FROM _ctx_days
        HAVING count(DISTINCT build_id)>1 OR count(DISTINCT dataset_hash)>1""")
    for name, _, columns in CONTEXT_SOURCES:
        con.execute(f"""CREATE TEMP TABLE _ctx_{name} AS SELECT {', '.join(columns)}
            FROM feature_input.{name} WHERE date<=? AND source_known_at<cast(? AS DATE)+INTERVAL 1 DAY""", [as_of, as_of])
        temporal_check = "" if name=="promotion_calendar" else "OR e.source_known_at<cast(e.date AS TIMESTAMP)"
        quality.query(name+"_identity", scope, f"""SELECT source_row_number FROM _ctx_{name} e
            WHERE e.data_version IS DISTINCT FROM 'v3' OR e.build_id IS NULL OR e.dataset_hash IS NULL
               OR e.source_known_at IS NULL {temporal_check}
               OR EXISTS(SELECT 1 FROM (SELECT DISTINCT build_id,dataset_hash FROM _ctx_days) d
                         WHERE d.build_id IS DISTINCT FROM e.build_id OR d.dataset_hash IS DISTINCT FROM e.dataset_hash)""")
    for table, keys in (("promotion_calendar", "date,channel,category,promo_id"),
                        ("execution_audits", "audit_id"), ("consumer_promotions", "date,store_id,sku_id")):
        quality.query(table+"_grain", scope,
                      f"SELECT {keys} FROM _ctx_{table} GROUP BY {keys} HAVING count(*)<>1")
    quality.require_pass()
    # Precompute the best *business-date* audit whenever knowledge changes. A
    # late-reported old audit must not replace a newer already-known audit.
    con.execute("""CREATE TEMP TABLE _ctx_audit_changes AS
        WITH steps AS (
            SELECT DISTINCT store_id,sku_id,greatest(date,cast(source_known_at AS DATE)) AS effective_date
            FROM _ctx_execution_audits
        )
        SELECT s.store_id,s.sku_id,s.effective_date,a.date AS last_audit_date,a.audit_id,
               a.planogram_compliance,a.shelf_share_pct,a.secondary_display,a.source_known_at AS audit_known_at,
               count(*) OVER(PARTITION BY s.store_id,s.sku_id,s.effective_date,a.date) AS same_day_audit_count
        FROM steps s JOIN _ctx_execution_audits a
          ON a.store_id=s.store_id AND a.sku_id=s.sku_id AND a.date<=s.effective_date
         AND a.source_known_at<cast(s.effective_date AS TIMESTAMP)+INTERVAL 1 DAY
        QUALIFY row_number() OVER(PARTITION BY s.store_id,s.sku_id,s.effective_date
                                  ORDER BY a.date DESC,a.audit_id)=1""")
    con.execute("""CREATE TEMP TABLE _ctx_promo_stats AS
        SELECT d.scoring_date,d.store_id,d.sku_id,count(p.promo_id) AS eligible_promotion_count
        FROM _ctx_days d LEFT JOIN _ctx_promotion_calendar p
          ON p.date=d.scoring_date AND p.channel=d.channel AND p.category=d.category
         AND p.source_known_at<=d.as_of_timestamp GROUP BY 1,2,3""")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.execute(f"""CREATE TABLE gold.features_oos_context AS
        SELECT d.build_id,d.dataset_hash,d.data_version,d.scoring_date,d.store_id,d.sku_id,d.as_of_timestamp,
               '{FEATURE_VERSION}' AS feature_version,
               p.eligible_promotion_count,p.eligible_promotion_count>1 AS has_promotion_overlap,
               c.promo_id AS observed_promo_id,c.promo_discount_pct AS observed_promo_discount_pct,
               c.consumer_price_actual AS consumer_price_observed,
               CASE WHEN c.date IS NOT NULL THEN 'Observed' ELSE 'Unknown' END AS consumer_promo_data_status,
               a.audit_id AS last_audit_id,a.last_audit_date,
               date_diff('day',a.last_audit_date,d.scoring_date) AS days_since_last_audit,
               a.planogram_compliance AS planogram_compliance_latest,
               a.secondary_display AS secondary_display_latest,a.shelf_share_pct AS shelf_share_pct_latest,
               a.same_day_audit_count,a.audit_known_at,
               CASE WHEN a.audit_id IS NULL THEN 'No observed audit'
                    ELSE 'Historical evidence; expiry policy requires review' END AS audit_policy_status,
               'Known-at-start schedule eligibility, not proof of execution' AS promotion_eligibility_status
        FROM _ctx_days d JOIN _ctx_promo_stats p USING(scoring_date,store_id,sku_id)
        ASOF LEFT JOIN _ctx_audit_changes a
          ON d.store_id=a.store_id AND d.sku_id=a.sku_id AND d.scoring_date>=a.effective_date
        LEFT JOIN _ctx_consumer_promotions c ON c.store_id=d.store_id AND c.sku_id=d.sku_id
          AND c.date=d.scoring_date AND c.source_known_at<=d.as_of_timestamp""")
    quality.query("past_only", scope, """SELECT scoring_date,store_id,sku_id FROM gold.features_oos_context
        WHERE last_audit_date>scoring_date OR audit_known_at>as_of_timestamp OR days_since_last_audit<0""")
    expected = con.execute("SELECT count(*) FROM _ctx_days").fetchone()[0]
    actual = con.execute("SELECT count(*) FROM gold.features_oos_context").fetchone()[0]
    quality.record("row_parity", scope, abs(expected-actual), checked_count=expected)
    quality.require_pass()
    for name in ("days", "promotion_calendar", "execution_audits", "consumer_promotions", "audit_changes", "promo_stats"):
        con.execute(f"DROP TABLE _ctx_{name}")
    return {"feature_version": FEATURE_VERSION, "rows": actual, "application_ready": False}
