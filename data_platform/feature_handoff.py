"""Export an isolated, allowlisted daily-state input; never publish an app DB."""

import json
import tempfile
import uuid
from datetime import date
from pathlib import Path

import duckdb

from .features import DEVELOPMENT_CUTOFF, INPUT_COLUMNS
from .ingestion import digest, literal
from .pipeline import PACKAGE, atomic_json, code_provenance
from .replenishment_features import ORDER_COLUMNS, RECEIPT_COLUMNS
from .context_features import CONTEXT_SOURCES
from .time_policy import BUSINESS_CUTOFF, validate_feature_cutoff
from .temporal_plan import bind_source_plan


def export_feature_input(source_build_dir, output_root, as_of, *, include_replenishment=False, include_context=False, purpose="development", temporal_plan=None):
    """Create an immutable projection directory from a validated local build.

    Only this exporter connects to the source database. Feature calculations
    subsequently connect only to projection.duckdb, with no source attachment.
    Failed pending directories are retained for diagnosis, never promoted.
    """
    validate_feature_cutoff(as_of, purpose, temporal_plan=temporal_plan)
    if type(include_replenishment) is not bool or type(include_context) is not bool:
        raise ValueError("Projection flags must be explicit booleans")
    source_dir = Path(source_build_dir).resolve()
    report = json.loads((source_dir / "run.json").read_text())
    if report.get("status") != "Accepted" or not report.get("daily_state"):
        raise ValueError("Source must be an accepted build containing daily state")
    cutoff = date.fromisoformat(report["configuration"]["as_of"])
    if as_of > cutoff or cutoff > BUSINESS_CUTOFF:
        raise ValueError("Requested/source cutoff exceeds the authorized source build")
    source_db, source_quality = source_dir / "build.duckdb", source_dir / "quality.json"
    if digest(source_db) != report.get("database_sha256"):
        raise ValueError("Source database hash mismatch")
    if digest(source_quality) != report.get("quality_sha256"):
        raise ValueError("Source quality hash mismatch")
    checks = json.loads(source_quality.read_text())
    required = [c for c in checks if c.get("severity") == "error"]
    if not required or any(c.get("passed") is not True or c.get("failed_count") != 0 for c in required):
        raise ValueError("Source must have passing required quality checks")
    plan_metadata = {}
    if temporal_plan is not None:
        plan = bind_source_plan(temporal_plan, report, cutoff)
        plan_metadata["temporal_plan"] = plan.to_dict()

    root = Path(output_root).resolve()
    if root == source_dir or source_dir in root.parents:
        raise ValueError("Projection output must not modify the source build directory")
    root.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=".pending-feature-", dir=root))
    projection_id = "feature-input-" + uuid.uuid4().hex
    final = root / projection_id
    revision, tree_hash = code_provenance()
    result = {
        "projection_id": projection_id, "status": "Running", "application_ready": False,
        "source_build_id": report["build_id"], "dataset_hash": report["dataset_hash"],
        "source_database_sha256": report["database_sha256"],
        "source_quality_sha256": report["quality_sha256"],
        "source_run_sha256": digest(source_dir / "run.json"),
        "source_build_dir": str(source_dir), "as_of": str(as_of),
        "columns": list(INPUT_COLUMNS), "projection_version": "oos_state_input.v1",
        "code_revision": revision, "code_tree_hash": tree_hash,
        "include_replenishment": include_replenishment,
        "include_context": include_context,
        "purpose": purpose,
        **plan_metadata,
    }
    try:
        projection = ", ".join(INPUT_COLUMNS)
        parquet = pending / "daily_state.parquet"
        projected_tables = {"daily_state": parquet}
        with duckdb.connect(str(source_db), read_only=True) as source:
            bad = source.execute("""
                SELECT count(*) FROM gold.mart_store_sku_daily
                WHERE date <= ? AND assortment_is_confirmed_asof AND
                    (build_id IS DISTINCT FROM ? OR dataset_hash IS DISTINCT FROM ?
                     OR data_version IS DISTINCT FROM 'v3')
            """, [as_of, report["build_id"], report["dataset_hash"]]).fetchone()[0]
            if bad:
                raise ValueError("Daily-state identity differs from its source build manifest")
            source.execute(f"""
                COPY (SELECT {projection} FROM gold.mart_store_sku_daily
                      WHERE date <= DATE {literal(as_of)} AND assortment_is_confirmed_asof)
                TO {literal(parquet)} (FORMAT PARQUET)
            """)
            if include_replenishment:
                result["projection_version"] = "oos_state_replenishment_input.v2"
                result["additional_columns"] = {}
                for name, relation, columns, event in (
                    ("order_schedule_events", "order_schedule_events", ORDER_COLUMNS, "order_date"),
                    ("receipt_events", "replenishment_events", RECEIPT_COLUMNS, "receipt_date"),
                ):
                    predicate = (f"{event}<=DATE {literal(as_of)} AND "
                                 f"source_known_at<DATE {literal(as_of)}+INTERVAL 1 DAY")
                    bad = source.execute(f"""
                        SELECT count(*) FROM silver.{relation} WHERE {predicate} AND
                          (build_id IS DISTINCT FROM ? OR dataset_hash IS DISTINCT FROM ?
                           OR data_version IS DISTINCT FROM 'v3')
                    """, [report["build_id"], report["dataset_hash"]]).fetchone()[0]
                    if bad:
                        raise ValueError("Replenishment identity differs from its source build manifest")
                    event_file = pending / (name + ".parquet")
                    source.execute(f"""
                        COPY (SELECT {', '.join(columns)} FROM silver.{relation} WHERE {predicate})
                        TO {literal(event_file)} (FORMAT PARQUET)
                    """)
                    projected_tables[name] = event_file
                    result["additional_columns"][name] = list(columns)
            if include_context:
                result["projection_version"] = "oos_complete_input.v3"
                result.setdefault("additional_columns", {})
                for name, relation, columns in CONTEXT_SOURCES:
                    predicate = f"date<=DATE {literal(as_of)} AND source_known_at<DATE {literal(as_of)}+INTERVAL 1 DAY"
                    bad = source.execute(f"""SELECT count(*) FROM silver.{relation} WHERE {predicate} AND
                        (build_id IS DISTINCT FROM ? OR dataset_hash IS DISTINCT FROM ? OR data_version IS DISTINCT FROM 'v3')""",
                        [report["build_id"], report["dataset_hash"]]).fetchone()[0]
                    if bad:
                        raise ValueError("Context identity differs from source build")
                    event_file = pending / (name+".parquet")
                    source.execute(f"COPY (SELECT {', '.join(columns)} FROM silver.{relation} WHERE {predicate}) TO {literal(event_file)} (FORMAT PARQUET)")
                    projected_tables[name] = event_file
                    result["additional_columns"][name] = list(columns)
        database = pending / "projection.duckdb"
        with duckdb.connect(str(database)) as target:
            target.execute((PACKAGE / "sql/meta.sql").read_text())
            target.execute("CREATE SCHEMA feature_input")
            for name, event_file in projected_tables.items():
                target.execute(f"CREATE TABLE feature_input.{name} AS SELECT * FROM read_parquet({literal(event_file)})")
            result["rows"] = target.execute("SELECT count(*) FROM feature_input.daily_state").fetchone()[0]
            if include_replenishment or include_context:
                result["replenishment_rows"] = {
                    name: target.execute(f"SELECT count(*) FROM feature_input.{name}").fetchone()[0]
                    for name in projected_tables if name != "daily_state"
                }
        # Accepted builds are immutable; refuse promotion if the parent changed
        # while the projection was being copied. Neither source nor accepted
        # publication pointers are ever written by this function.
        if (digest(source_db) != report["database_sha256"]
                or digest(source_quality) != report["quality_sha256"]
                or digest(source_dir / "run.json") != result["source_run_sha256"]):
            raise ValueError("Source changed during projection export")
        result.update(status="Accepted", database="projection.duckdb",
                      database_sha256=digest(database), parquet_sha256=digest(parquet))
        if include_replenishment or include_context:
            result["additional_parquet_sha256"] = {
                name: digest(path) for name, path in projected_tables.items() if name != "daily_state"
            }
        atomic_json(pending / "run.json", result)
        pending.rename(final)
        return {**result, "directory": str(final)}
    except (Exception, KeyboardInterrupt) as error:
        result.update(status="Interrupted" if isinstance(error, KeyboardInterrupt) else "Failed",
                      error=f"{type(error).__name__}: {error}")
        atomic_json(pending / "run.json", result)
        raise
