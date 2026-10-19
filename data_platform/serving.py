"""Gold-only immutable serving snapshots, controlled promotion and rollback."""

import json
import re
import uuid
from pathlib import Path
import duckdb

from .artifacts import artifact_attempt, verified_artifact, require_separate_root
from .ingestion import digest, literal
from .negotiation import build_negotiation_context
from .pipeline import ROOT, PACKAGE, atomic_json, now, writer_lock
from .quality import Quality

# Relation -> scoped date column; None denotes non-store global reference data.
VIEWS = {
    "store_sku_daily":("mart_store_sku_daily","date",True),
    "store_day":("mart_store_day","date",True),
    "commercial_period":("mart_commercial_period","period_start",True),
    "execution_visits":("mart_execution_visit","audit_date",True),
    "delivery_events":("mart_delivery_events","actual_delivery",True),
    "sell_in_events":("mart_sell_in_events","date",True),
    "negotiation_context":("mart_negotiation_context","as_of_date",True),
    "metric_definitions":("metric_definitions",None,False),
    "stores":("dim_stores",None,True),"products":("dim_products",None,False),
    "promotions":("dim_promotions","start_date",False),
    "oos_predictions":("predictions_oos","scoring_date",True),
    "visit_priority":("mart_visit_priority","scoring_date",True),
    "estimated_store_sku_daily":("mart_store_sku_daily_estimated","date",True),
    "estimated_store_day":("mart_store_day_estimated","date",True),
    "estimated_commercial_period":("mart_commercial_period_estimated","period_start",True),
}
ESTIMATED_VIEWS = frozenset(name for name in VIEWS if name.startswith("estimated_"))


def build_serving(source_build_dir, feature_build_dir, output_root=None, *, access_policy=None,
                  memory_limit="1GB",threads=2,publish=False):
    source, source_db, cutoff = verified_artifact(source_build_dir)
    features, feature_db, feature_cutoff = verified_artifact(feature_build_dir,kind="features")
    if threads<1 or features["source_build_id"]!=source["build_id"] or features["dataset_hash"]!=source["dataset_hash"] or cutoff!=feature_cutoff:
        raise ValueError("Serving requires a matching full-feature/source build and identical cutoff")
    root=Path(output_root or ROOT/"outputs/v3/platform/serving")
    require_separate_root(root,source_build_dir,feature_build_dir)
    policy=json.loads(Path(access_policy).read_text()) if access_policy else {"version":"unconfigured-deny-all","assignments":[]}
    if not isinstance(policy.get("version"),str) or not isinstance(policy.get("assignments"),list):
        raise ValueError("Access policy needs a version and assignments list")
    with artifact_attempt(root,"serving",{
        "as_of":str(cutoff),"dataset_hash":source["dataset_hash"],"source_build_id":source["build_id"],
        "feature_artifact_id":features["artifact_id"],"metric_view":"reported_only",
        "feature_purpose":features.get("purpose","development"),
        "estimate_policy":"Separate opt-in candidate views; observed precedence, explicit Estimated/Unknown states",
        "estimate_views_status":"Candidate_not_approved",
        "available_metric_views":["reported_only","operational_estimate_candidate"],
        "access_policy_version":policy["version"],
    }) as (directory,record):
        exports={
            "mart_store_sku_daily":"""SELECT build_id,dataset_hash,data_version,date,store_id,sku_id,
                channel,region,segment_tier,category,is_msl,units_sold_observed,inventory_observed,oos_observed,
                CASE WHEN inventory_is_observed THEN 'Observed' ELSE 'Unknown' END AS inventory_data_status,
                CASE WHEN sales_is_observed THEN 'Observed' ELSE 'Unknown' END AS sales_data_status,
                last_report_date,days_since_inventory_report,consumer_price_observed,
                receipt_units_from_deliveries,receipt_delivery_count
                FROM gold.mart_store_sku_daily WHERE assortment_is_confirmed_asof""",
            "mart_store_day":"SELECT * FROM gold.mart_store_day WHERE metric_view='reported_only'",
            "mart_commercial_period":"SELECT * FROM gold.mart_commercial_period WHERE metric_view='reported_only' AND alignment_basis='event_date'",
            "mart_store_sku_daily_estimated":"""SELECT build_id,dataset_hash,data_version,date,store_id,sku_id,
                channel,region,segment_tier,category,is_msl,units_sold_observed,inventory_observed,oos_observed,
                units_sold_estimated,inventory_estimated,oos_estimated,
                sales_model_state AS sales_resolved,inventory_model_state AS inventory_resolved,
                coalesce(oos_observed,oos_estimated) AS oos_resolved,
                sales_data_status,inventory_data_status,sales_estimation_method,inventory_estimation_method,
                inventory_state_reason,last_report_date,days_since_inventory_report,
                estimator_version,estimation_release_status,receipt_units_from_deliveries,receipt_delivery_count,
                'operational_estimate_candidate' AS metric_view
                FROM gold.mart_store_sku_daily WHERE assortment_is_confirmed_asof""",
            "mart_store_day_estimated":"SELECT * FROM gold.mart_store_day WHERE metric_view='operational_estimate'",
            "mart_commercial_period_estimated":"SELECT * FROM gold.mart_commercial_period WHERE metric_view='operational_estimate' AND alignment_basis='event_date'",
            "mart_execution_visit":"SELECT * FROM gold.mart_execution_visit",
            "metric_definitions":"SELECT * FROM gold.metric_definitions",
            "dim_stores":"SELECT store_id,store_name,channel,region,state,segment_tier,city FROM silver.dim_stores",
            "dim_products":"SELECT sku_id,product_name,category,brand,is_msl,unit_cost,list_price,consumer_price FROM silver.dim_products",
            "dim_promotions":f"SELECT promo_id,promo_name,promo_type,category,channel,start_date,end_date,discount_pct,min_volume_units FROM silver.dim_promotions WHERE start_date<=DATE {literal(cutoff)}",
            "mart_delivery_events":"SELECT delivery_id,store_id,sku_id,order_date,scheduled_delivery,actual_delivery,delay_days,delivery_status,units_ordered,units_delivered,fill_rate_pct,build_id,dataset_hash FROM silver.fact_deliveries",
            "mart_sell_in_events":"SELECT transaction_id,date,store_id,sku_id,units_sold,gross_sales,discount_pct,extra_discount_pct,net_sales,promo_id,guardrail_violation,build_id,dataset_hash FROM silver.fact_sell_in",
            "_guardrails":"SELECT * FROM silver.dim_guardrails",
        }
        with duckdb.connect(str(source_db),read_only=True) as raw:
            for name,sql in exports.items():
                raw.execute(f"COPY ({sql}) TO {literal(directory/(name+'.parquet'))} (FORMAT PARQUET)")
        with duckdb.connect(str(feature_db),read_only=True) as raw:
            raw.execute(f"""COPY (SELECT store_id,sku_id,last_audit_id,last_audit_date,days_since_last_audit,
                planogram_compliance_latest,secondary_display_latest,shelf_share_pct_latest
                FROM gold.features_oos_daily WHERE scoring_date=DATE {literal(cutoff)})
                TO {literal(directory/'_latest_context.parquet')} (FORMAT PARQUET)""")
        con=duckdb.connect(str(directory/"business.duckdb"),config={"memory_limit":memory_limit,"threads":threads})
        con.execute("CREATE SCHEMA gold; CREATE SCHEMA serving; CREATE SCHEMA meta")
        # Only metadata quality DDL, never the source schemas or source registry.
        ddl=(PACKAGE/"sql/meta.sql").read_text().split("CREATE TABLE meta.quality_results",1)[1].split("CREATE TABLE meta.publications",1)[0]
        con.execute("CREATE TABLE meta.quality_results"+ddl)
        q=Quality(con,record["artifact_id"])
        try:
            for name in exports:
                if name.startswith("_"):
                    con.execute(f"CREATE TEMP TABLE {name} AS SELECT * FROM read_parquet({literal(directory/(name+'.parquet'))})")
                else:
                    con.execute(f"CREATE TABLE gold.{name} AS SELECT * FROM read_parquet({literal(directory/(name+'.parquet'))})")
            con.execute("CREATE TEMP VIEW _products AS SELECT * FROM gold.dim_products")
            con.execute("CREATE TEMP VIEW _invoices AS SELECT * FROM gold.mart_sell_in_events")
            con.execute(f"CREATE TEMP TABLE _latest_context AS SELECT * FROM read_parquet({literal(directory/'_latest_context.parquet')})")
            build_negotiation_context(con,cutoff,q)
            con.execute("""CREATE TABLE gold.predictions_oos (
                build_id VARCHAR,dataset_hash VARCHAR,scoring_run_id VARCHAR,scoring_date DATE,store_id VARCHAR,sku_id VARCHAR,
                probability DOUBLE,threshold DOUBLE,is_predicted_oos BOOLEAN,drivers_json VARCHAR,
                model_version VARCHAR,feature_version VARCHAR,input_status VARCHAR);
                CREATE TABLE gold.mart_visit_priority (
                build_id VARCHAR,dataset_hash VARCHAR,scoring_run_id VARCHAR,scoring_date DATE,store_id VARCHAR,
                priority_rank BIGINT,risk_score DOUBLE,msl_gap_count BIGINT,ranking_policy_version VARCHAR,reason_json VARCHAR);
                CREATE TABLE gold.account_scope (principal_id VARCHAR,store_id VARCHAR,role VARCHAR,scope_version VARCHAR,
                    PRIMARY KEY(principal_id,store_id));""")
            valid_stores={r[0] for r in con.execute("SELECT store_id FROM gold.dim_stores").fetchall()}
            for row in policy["assignments"]:
                if set(row)!={"principal_id","store_id","role"} or not row["principal_id"] or row["store_id"] not in valid_stores or row["role"] not in {"sales","manager"}:
                    raise ValueError("Invalid or unknown account-scope assignment")
                con.execute("INSERT INTO gold.account_scope VALUES (?,?,?,?)",[row["principal_id"],row["store_id"],row["role"],policy["version"]])
            for name,(relation,_,_) in VIEWS.items():
                con.execute(f"CREATE VIEW serving.vw_{name} AS SELECT * FROM gold.{relation}")
            q.query("no_restricted_tables","serving","SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('bronze','silver','feature_input','evaluation')")
            q.query("daily_grain","serving","SELECT date,store_id,sku_id FROM gold.mart_store_sku_daily GROUP BY 1,2,3 HAVING count(*)<>1")
            q.query("reported_only","serving","SELECT store_id FROM gold.mart_store_day WHERE metric_view<>'reported_only'")
            q.query("candidate_observed_precedence","serving","""SELECT date,store_id,sku_id
                FROM gold.mart_store_sku_daily_estimated
                WHERE (inventory_observed IS NOT NULL AND (inventory_estimated IS NOT NULL
                       OR inventory_resolved IS DISTINCT FROM inventory_observed OR inventory_data_status<>'Observed'))
                   OR (units_sold_observed IS NOT NULL AND (units_sold_estimated IS NOT NULL
                       OR sales_resolved IS DISTINCT FROM units_sold_observed OR sales_data_status<>'Observed'))""")
            q.query("candidate_grain","serving","SELECT date,store_id,sku_id FROM gold.mart_store_sku_daily_estimated GROUP BY 1,2,3 HAVING count(*)<>1")
            q.record("source_immutable","serving",int(digest(source_db)!=source["database_sha256"]))
            q.record("features_immutable","serving",int(digest(feature_db)!=features["database_sha256"]))
            # Descriptive parity is independent of model/estimator acceptance.
            expected=con.execute("SELECT count(inventory_observed),count(*) FILTER(WHERE oos_observed) FROM gold.mart_store_sku_daily").fetchone()
            actual=con.execute("SELECT sum(observed_inventory_sku_count),sum(oos_sku_count) FROM gold.mart_store_day").fetchone()
            # First-confirmation filtering changes leading unknown denominators,
            # but never observed/OOS numerators.
            q.record("oos_component_parity","serving",sum(a!=b for a,b in zip(expected,actual)))
            q.require_pass()
            record.update(database="business.duckdb",application_ready=True,
                readiness={"module1":"Reported KPI ready; separate candidate-estimate views require explicit opt-in and labels",
                           "module2":"Feature artifact ready; no released predictions or ranking",
                           "module3":"Context ready; guardrails require review",
                           "module4":"Curated views ready; authentication/NLQ/RAG runtime outside data layer"},
                model_outputs_status="Unavailable_model_not_released",
                rows={name:con.execute(f"SELECT count(*) FROM gold.{relation}").fetchone()[0] for name,(relation,_,_) in VIEWS.items()})
            con.execute("CREATE TABLE meta.publication_info AS SELECT ?::VARCHAR AS dataset_hash,?::DATE AS as_of,?::VARCHAR AS model_outputs_status,?::VARCHAR AS metric_view",
                        [source["dataset_hash"],cutoff,record["model_outputs_status"],"reported_only"])
        finally:
            atomic_json(directory/"quality.json",q.report())
            con.close()
    if publish:
        record={**record,"publication":publish_serving(root,record["directory"])}
    return record


def publish_serving(output_root,artifact_dir):
    root=Path(output_root).resolve()
    record,_,_=verified_artifact(artifact_dir,kind="serving")
    if Path(artifact_dir).resolve().parent!=root/"builds" or not record.get("application_ready"):
        raise ValueError("Only accepted serving builds in this root may be published")
    with writer_lock(root/".publication.lock"):
        return _promote(root,record,"publish")


def _promote(root,record,action):
    pointer=root/"published/serving.json"
    previous=json.loads(pointer.read_text()) if pointer.exists() else None
    publication={**record,"publication_id":uuid.uuid4().hex,"action":action,"published_at":str(now()),
                 "previous_publication_id":previous["publication_id"] if previous else None}
    atomic_json(root/"published/history"/(publication["publication_id"]+".json"),publication)
    atomic_json(pointer,publication)
    return publication


def rollback_serving(output_root,publication_id):
    if not re.fullmatch(r"[0-9a-f]{32}",publication_id):
        raise ValueError("Invalid publication ID")
    root=Path(output_root).resolve()
    with writer_lock(root/".publication.lock"):
        previous=json.loads((root/"published/history"/(publication_id+".json")).read_text())
        record,_,_=verified_artifact(previous["directory"],kind="serving")
        if Path(record["directory"]).parent!=root/"builds" or not record.get("application_ready"):
            raise ValueError("Rollback target is not an approved local serving artifact")
        return _promote(root,record,"rollback:"+publication_id)
