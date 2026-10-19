"""Governed full feature orchestration, with labels and original source isolated."""

import shutil
from datetime import date
from pathlib import Path
import duckdb

from .artifacts import artifact_attempt, verified_artifact, require_separate_root
from .context_features import build_oos_context_features
from .feature_handoff import export_feature_input
from .features import DEVELOPMENT_CUTOFF, build_oos_state_features
from .ingestion import digest
from .pipeline import ROOT, atomic_json
from .quality import Quality
from .replenishment_features import build_oos_replenishment_features
from .time_policy import validate_feature_cutoff

FEATURE_VERSION = "oos_features.v1-complete"


def build_features(source_build_dir, as_of, output_root=None, *, assume_complete_snapshot=False,
                   memory_limit="1GB", threads=2, purpose="development"):
    validate_feature_cutoff(as_of, purpose)
    if threads<1:
        raise ValueError("threads must be positive")
    source, _, cutoff = verified_artifact(source_build_dir)
    if as_of>cutoff:
        raise ValueError("Features cannot exceed the source cutoff")
    root = Path(output_root or ROOT/"outputs/v3/platform/features")
    require_separate_root(root,source_build_dir)
    projection = export_feature_input(source_build_dir, root/"inputs",
                                      as_of, include_replenishment=True, include_context=True, purpose=purpose)
    with artifact_attempt(root, "features", {
        "as_of": str(as_of), "source_build_id": source["build_id"], "dataset_hash": source["dataset_hash"],
        "feature_version": FEATURE_VERSION, "projection": projection,
        "assume_complete_snapshot": assume_complete_snapshot, "memory_limit": memory_limit, "threads": threads,
        "purpose": purpose,
    }) as (directory, record):
        database = directory/"features.duckdb"
        input_db = Path(projection["directory"])/projection["database"]
        if digest(input_db)!=projection["database_sha256"]:
            raise ValueError("Projection changed before feature build")
        shutil.copyfile(input_db, database)
        con = duckdb.connect(str(database), config={"memory_limit":memory_limit,"threads":threads})
        con.execute("SET preserve_insertion_order=false")
        q = Quality(con, record["artifact_id"])
        try:
            record["state"] = build_oos_state_features(con,as_of,q,purpose=purpose)
            record["replenishment"] = build_oos_replenishment_features(con,as_of,q,assume_complete_snapshot=assume_complete_snapshot,purpose=purpose)
            record["context"] = build_oos_context_features(con,as_of,q,purpose=purpose)
            # Retain component versions but publish one complete, unique row.
            con.execute(f"""CREATE TEMP TABLE _combined AS
                SELECT d.* EXCLUDE(feature_version),'{FEATURE_VERSION}' AS feature_version,
                       d.feature_version AS state_feature_version,r.feature_version AS replenishment_feature_version,
                       c.feature_version AS context_feature_version,
                       r.* EXCLUDE(build_id,dataset_hash,data_version,scoring_date,store_id,sku_id,as_of_timestamp,feature_version,availability_assumption_id),
                       c.* EXCLUDE(build_id,dataset_hash,data_version,scoring_date,store_id,sku_id,as_of_timestamp,feature_version)
                FROM gold.features_oos_daily d
                JOIN gold.features_oos_replenishment r USING(build_id,scoring_date,store_id,sku_id)
                JOIN gold.features_oos_context c USING(build_id,scoring_date,store_id,sku_id)""")
            count = con.execute("SELECT count(*) FROM _combined").fetchone()[0]
            q.record("complete_row_parity","features_oos_daily",abs(count-record["state"]["rows"]))
            q.query("complete_grain","features_oos_daily","SELECT scoring_date,store_id,sku_id FROM _combined GROUP BY 1,2,3 HAVING count(*)<>1")
            q.require_pass()
            con.execute("DROP TABLE gold.features_oos_daily")
            con.execute("CREATE TABLE gold.features_oos_daily AS SELECT * FROM _combined")
            record.update(rows=count,database="features.duckdb")
            q.record("immutable_input","features_oos_daily",int(digest(input_db)!=projection["database_sha256"]))
            q.require_pass()
        finally:
            atomic_json(directory/"quality.json",q.report())
            con.close()
    return record
