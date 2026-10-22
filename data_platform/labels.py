"""Restricted observed-only seven-day outcomes; never an application dependency."""

from datetime import date
import duckdb
from .artifacts import artifact_attempt, verified_artifact, require_separate_root
from .features import DEVELOPMENT_CUTOFF
from .ingestion import digest, literal
from .pipeline import ROOT, PACKAGE, atomic_json
from .quality import Quality
from .time_policy import BUSINESS_CUTOFF, TEST_START
from .test_release import authorize_test_release, authorize_rolling_test_release
from .test_exposure import reserve_test_exposure
from .temporal_plan import TemporalPlan, bind_source_plan, FOLD_POLICY_VERSION

TEST_FOLD_POLICY_VERSION = "trailing30.test_only.v1"
FOLDS = (
    ("march_development", "2026-02-21", "2026-03-01", "2026-03-24", "2026-03-31"),
    ("april_development", "2026-03-23", "2026-04-01", "2026-04-23", "2026-04-30"),
    ("may_validation", "2026-04-23", "2026-05-01", "2026-05-24", "2026-05-31"),
)


def test_fold(plan):
    """The single planned test-only fold: no training rows, pending days excluded."""
    test = plan.to_dict()["test"]
    return ("rolling_test", None, test["reserved_start"], test["calendar_eligible_end"], test["outcome_end"])


def build_labels(source_build_dir, as_of, output_root=None, *, memory_limit="1GB",threads=2,
                 purpose="development",test_release=None,temporal_plan=None,
                 test_exposure_ledger=None,test_replay_reason=None):
    if type(as_of) is not date or purpose not in {"development","test"} or threads<1:
        raise ValueError("Invalid label date/purpose/threads")
    plan = None
    rolling_test = temporal_plan is not None and purpose == "test"
    if not rolling_test and (test_exposure_ledger is not None or test_replay_reason is not None):
        raise ValueError("Exposure-ledger options apply only to an explicit planned test release")
    if temporal_plan is not None:
        plan = TemporalPlan.from_dict(temporal_plan)
        if rolling_test:
            if test_release is None or test_exposure_ledger is None:
                raise ValueError("Rolling test remains locked: a planned test needs an approved v2 release and an initialized exposure ledger")
            if as_of != plan.source_as_of:
                raise ValueError("Planned test labels use the plan's source date D")
        else:
            if test_release is not None:
                raise ValueError("Rolling test release remains locked; a temporal plan is not test authorization")
            if str(as_of) != plan.to_dict()["validation"]["outcome_end"]:
                raise ValueError("Development label cutoff must equal the temporal plan validation outcome end")
    if as_of>BUSINESS_CUTOFF or (purpose=="development" and as_of>DEVELOPMENT_CUTOFF):
        raise ValueError("June labels remain locked for development; available business end is June 30")
    if purpose=="test" and not rolling_test and as_of!=BUSINESS_CUTOFF:
        raise ValueError("June test release uses the complete June 30 snapshot")
    if purpose=="development" and test_release is not None:
        raise ValueError("Test evidence is not a development-mode override")
    source, database, cutoff = verified_artifact(source_build_dir)
    if plan is not None:
        plan = bind_source_plan(temporal_plan, source, cutoff)
    if as_of>cutoff:
        raise ValueError("Labels cannot exceed available outcomes")
    observation_start = ""
    if rolling_test:
        # Read-only evidence check before any artifact, reservation or truth SQL.
        approval = authorize_rolling_test_release(test_release, plan.to_dict())
        folds = (test_fold(plan),)
        calendar_start = f"AND date>=DATE {literal(folds[0][2])}"
        observation_start = f"AND date>DATE {literal(folds[0][2])}"
        plan_metadata = {"temporal_plan": plan.to_dict(), "fold_policy_version": TEST_FOLD_POLICY_VERSION,
                         "folds": [list(row) for row in folds],
                         "source_database_sha256": source["database_sha256"]}
    else:
        approval = authorize_test_release(test_release,source["dataset_hash"]) if purpose=="test" else None
        calendar_start = f"AND date>=DATE {literal(TEST_START)}" if purpose=="test" else ""
        folds = (("june_test",None,"2026-06-01","2026-06-23","2026-06-30"),) if purpose=="test" else FOLDS
        plan_metadata = {}
        if plan is not None:
            folds = plan.development_folds()
            calendar_start = f"AND date>=DATE {literal(plan.history_start)}"
            plan_metadata = {"temporal_plan": plan.to_dict(), "fold_policy_version": FOLD_POLICY_VERSION,
                             "folds": [list(row) for row in folds],
                             "source_database_sha256": source["database_sha256"]}
    require_separate_root(output_root or ROOT/"outputs/v3/platform/evaluation/labels",source_build_dir)
    with artifact_attempt(output_root or ROOT/"outputs/v3/platform/evaluation/labels", "labels", {
        "as_of":str(as_of),"dataset_hash":source["dataset_hash"],"source_build_id":source["build_id"],
        "label_version":"oos_next7.complete_window.v1","restricted":True,
        "purpose":purpose,"test_release":approval,
        **plan_metadata,
    }) as (directory,record):
        if rolling_test:
            # Durable, fsynced exposure reservation for this exact artifact before
            # the first truth query. Never refunded on later failure.
            record["test_exposure"] = reserve_test_exposure(
                test_exposure_ledger, authorization=approval, artifact_id=record["artifact_id"],
                fold_policy_version=TEST_FOLD_POLICY_VERSION, replay_reason=test_replay_reason,
                revalidate=lambda: authorize_rolling_test_release(test_release, plan.to_dict()))
            atomic_json(directory/"run.json", record)
        # The exporter alone sees the internal build, and projects only observed
        # truth/calendar. The label database contains no feature or serving table.
        with duckdb.connect(str(database),read_only=True) as raw:
            raw.execute(f"""COPY (SELECT date,store_id,sku_id,channel,segment_tier FROM gold.mart_store_sku_daily
                WHERE date<=DATE {literal(as_of)} AND assortment_is_confirmed_asof {calendar_start})
                TO {literal(directory/'calendar.parquet')} (FORMAT PARQUET)""")
            raw.execute(f"""COPY (SELECT date,store_id,sku_id,is_out_of_stock,source_known_at
                FROM silver.fact_sell_out WHERE date<=DATE {literal(as_of)} {observation_start}
                  AND source_known_at<DATE {literal(as_of)}+INTERVAL 1 DAY)
                TO {literal(directory/'observations.parquet')} (FORMAT PARQUET)""")
        con=duckdb.connect(str(directory/"labels.duckdb"),config={"memory_limit":memory_limit,"threads":threads})
        con.execute((PACKAGE/"sql/meta.sql").read_text())
        con.execute("CREATE SCHEMA evaluation")
        q=Quality(con,record["artifact_id"])
        try:
            con.execute(f"""CREATE TABLE evaluation.labels_oos_next7 AS
                WITH outcomes AS (
                    SELECT c.*,c.date+7 AS label_window_end,
                           count(o.date) AS observed_future_days,
                           count(*) FILTER(WHERE o.is_out_of_stock) AS observed_future_oos_days,
                           max(o.source_known_at) AS outcome_known_at
                    FROM read_parquet({literal(directory/'calendar.parquet')}) c
                    LEFT JOIN read_parquet({literal(directory/'observations.parquet')}) o
                      ON o.store_id=c.store_id AND o.sku_id=c.sku_id AND o.date BETWEEN c.date+1 AND c.date+7
                    GROUP BY ALL
                )
                SELECT {literal(source['build_id'])} AS build_id,{literal(source['dataset_hash'])} AS dataset_hash,
                       'oos_next7.complete_window.v1' AS label_version,date AS scoring_date,
                       store_id,sku_id,channel,segment_tier,label_window_end,observed_future_days,
                       observed_future_oos_days,observed_future_oos_days>0 AS confirmed_observed_oos,
                       outcome_known_at,DATE {literal(as_of)} AS evaluation_as_of,
                       label_window_end<=DATE {literal(as_of)} AS is_mature,
                       CASE WHEN observed_future_days=7 AND label_window_end<=DATE {literal(as_of)}
                            THEN (observed_future_oos_days>0)::INTEGER END AS target_oos_7d,
                       CASE WHEN label_window_end>DATE {literal(as_of)} THEN 'not_mature'
                            WHEN observed_future_days=7 THEN 'complete_window'
                            WHEN observed_future_oos_days>0 THEN 'partial_window_confirmed_positive'
                            ELSE 'partial_window_unknown' END AS label_source
                FROM outcomes""")
            con.execute("""CREATE TABLE evaluation.temporal_folds (
                fold_id VARCHAR,train_last DATE,validation_start DATE,validation_end DATE,outcome_end DATE)""")
            con.executemany("INSERT INTO evaluation.temporal_folds VALUES (?,?,?,?,?)",folds)
            con.execute("ALTER TABLE evaluation.temporal_folds ADD COLUMN outcomes_available BOOLEAN")
            con.execute("UPDATE evaluation.temporal_folds SET outcomes_available=outcome_end<=?",[as_of])
            train_eligibility = ""
            if plan is not None and not rolling_test:
                train_eligibility = """AND l.outcome_known_at IS NOT NULL AND
                    (l.scoring_date>f.train_last OR
                     (l.label_window_end<f.validation_start
                      AND l.outcome_known_at<cast(f.validation_start AS TIMESTAMP)))"""
            con.execute(f"""CREATE VIEW evaluation.fold_samples AS
                SELECT f.fold_id,CASE WHEN f.fold_id IN ('june_test','rolling_test') THEN 'test'
                       WHEN l.scoring_date<=f.train_last THEN 'train' ELSE 'validation' END AS partition,
                       l.*,l.target_oos_7d IS NOT NULL AND l.label_window_end<=f.outcome_end
                         AND (l.outcome_known_at IS NULL OR l.outcome_known_at<cast(f.outcome_end AS TIMESTAMP)+INTERVAL 1 DAY)
                         {train_eligibility} AS eligible
                FROM evaluation.labels_oos_next7 l JOIN evaluation.temporal_folds f
                  ON l.scoring_date<=f.train_last OR l.scoring_date BETWEEN f.validation_start AND f.validation_end""")
            con.execute("""CREATE TABLE evaluation.label_coverage AS
                SELECT channel,segment_tier,label_source,count(*) AS sample_count,
                       count(target_oos_7d) AS complete_label_count FROM evaluation.labels_oos_next7 GROUP BY ALL""")
            q.query("label_grain","labels_oos_next7","SELECT scoring_date,store_id,sku_id FROM evaluation.labels_oos_next7 GROUP BY 1,2,3 HAVING count(*)<>1")
            q.query("complete_truth_only","labels_oos_next7","""SELECT * FROM evaluation.labels_oos_next7
                WHERE (target_oos_7d IS NOT NULL AND (observed_future_days<>7 OR NOT is_mature))
                   OR (observed_future_days=7 AND is_mature AND target_oos_7d IS NULL)
                   OR observed_future_days>7""")
            q.query("purge_boundary","temporal_folds","SELECT * FROM evaluation.temporal_folds WHERE train_last+7>=validation_start OR validation_end+7>outcome_end")
            if rolling_test:
                test = plan.to_dict()["test"]
                q.query("test_only_partition", "fold_samples", "SELECT * FROM evaluation.fold_samples WHERE partition<>'test'")
                q.query("test_calendar_window", "labels_oos_next7", f"""SELECT scoring_date FROM evaluation.labels_oos_next7
                    WHERE scoring_date<DATE {literal(test['reserved_start'])} OR scoring_date>DATE {literal(test['reserved_end'])}""")
                q.query("test_pending_null", "labels_oos_next7", f"""SELECT scoring_date FROM evaluation.labels_oos_next7
                    WHERE scoring_date>DATE {literal(test['calendar_eligible_end'])} AND target_oos_7d IS NOT NULL""")
                q.query("test_eligible_bounds", "fold_samples", f"""SELECT scoring_date FROM evaluation.fold_samples WHERE eligible
                    AND (scoring_date<DATE {literal(test['calendar_eligible_start'])} OR scoring_date>DATE {literal(test['calendar_eligible_end'])}
                         OR outcome_known_at IS NULL OR outcome_known_at>=DATE {literal(test['outcome_end'])}+INTERVAL 1 DAY)""")
            elif plan is not None:
                q.query("rolling_training_known_at", "fold_samples", """
                    SELECT s.fold_id,s.scoring_date FROM evaluation.fold_samples s
                    JOIN evaluation.temporal_folds f USING(fold_id)
                    WHERE s.eligible AND s.partition='train' AND
                      (s.label_window_end>=f.validation_start OR s.outcome_known_at IS NULL
                       OR s.outcome_known_at>=cast(f.validation_start AS TIMESTAMP))
                """)
            q.record("source_immutable","labels_oos_next7",int(digest(database)!=source["database_sha256"]))
            q.require_pass()
            record.update(database="labels.duckdb",rows=con.execute("SELECT count(*) FROM evaluation.labels_oos_next7").fetchone()[0],
                          coverage=con.execute("SELECT label_source,count(*) FROM evaluation.labels_oos_next7 GROUP BY 1").fetchall())
        finally:
            atomic_json(directory/"quality.json",q.report())
            con.close()
    return record
