"""Governed exports of prepared shared data into a run directory.

Reuses the shared verifier and the existing reader helpers; never rebuilds
ETL, never selects the newest directory and never opens test truth here.
Outer validation rows are exported only through ``export_outer`` after the
caller has frozen a bundle and recorded access.
"""

from pathlib import Path

import pandas as pd

from data_platform.temporal_plan import TemporalPlan

from . import config
from . import data_adapter as adapter
from .contracts import Module2Error, read_json, sha256_file, utc_now, write_json

KEYS = ["build_id", "scoring_date", "store_id", "sku_id"]
INNER_FOLDS = ("rolling_dev_1", "rolling_dev_2")


def load_workflow(path):
    path = Path(path)
    record = read_json(path, "shared workflow run")
    if record.get("status") != "Accepted" or record.get("evaluation_profile") != "trailing_30d":
        raise Module2Error("An explicit accepted rolling (trailing_30d) workflow run is required")
    plan = TemporalPlan.from_dict(record.get("temporal_plan"))
    if record.get("dataset_hash") != plan.dataset_hash:
        raise Module2Error("Workflow dataset differs from its temporal plan")
    return {"path": str(path.resolve()), "sha256": sha256_file(path), "record": record, "plan": plan,
            "components": record["components"]}


def _write(frame, folder, name, manifest):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.parquet"
    if target.exists():
        raise Module2Error("Run inputs are immutable; use a new run directory")
    frame.to_parquet(target, index=False)
    manifest[name] = {"file": target.name, "sha256": sha256_file(target), "rows": int(len(frame))}
    return target


def export_development(workflow, folder):
    """Both inner folds, train and held-out, through the existing reader."""
    folder = Path(folder)
    plan_sha = workflow["plan"].to_dict()["plan_sha256"]
    manifest = {"version": "module2.development_inputs.v1", "status": "Running", "plan_sha256": plan_sha,
                "workflow_sha256": workflow["sha256"], "features": list(config.FEATURES),
                "outer_validation": "Locked", "test": "Locked", "created_at": utc_now(), "files": {}}
    write_json(folder / "manifest.json", manifest)
    try:
        for fold in INNER_FOLDS:
            for partition in ("train", "validation"):
                batch = adapter.load_development_partition(
                    workflow["components"]["features"]["directory"], workflow["components"]["labels"]["directory"],
                    fold_id=fold, partition=partition, predictors=list(config.FEATURES))
                if batch.metadata["plan_sha256"] != plan_sha:
                    raise Module2Error("Reader plan differs from the pinned workflow")
                frame = pd.concat([batch.row_keys, batch.X, batch.y.rename("target_oos_7d")], axis=1)
                _write(frame, folder, f"{fold}-{partition}", manifest["files"])
                manifest["files"][f"{fold}-{partition}"]["metadata"] = batch.metadata
                write_json(folder / "manifest.json", manifest)
        manifest["status"] = "Accepted"
    except BaseException as error:
        manifest.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        write_json(folder / "manifest.json", manifest)
    return manifest


def read_export(folder, name, manifest=None):
    folder = Path(folder)
    manifest = manifest or read_json(folder / "manifest.json", "input manifest")
    entry = manifest.get("files", {}).get(name)
    if manifest.get("status") != "Accepted" or not entry:
        raise Module2Error(f"Accepted input {name} is unavailable")
    path = folder / entry["file"]
    if sha256_file(path) != entry["sha256"]:
        raise Module2Error(f"Input {name} changed after export")
    frame = pd.read_parquet(path)
    if len(frame) != entry["rows"]:
        raise Module2Error(f"Input {name} row count changed")
    return frame


def _development_sources(workflow):
    plan = workflow["plan"]
    features, feature_db, cutoff, actual = adapter._feature_artifact(
        workflow["components"]["features"]["directory"], "development")
    labels, label_db, label_cutoff = adapter._artifact(workflow["components"]["labels"]["directory"], "labels")
    if (actual != plan or TemporalPlan.from_dict(labels.get("temporal_plan")) != plan
            or cutoff != label_cutoff or labels.get("purpose") != "development"
            or any(features[k] != labels.get(k) for k in ("dataset_hash", "source_build_id", "source_database_sha256"))):
        raise Module2Error("Outer partitions require matching planned development inputs")
    return features, feature_db, labels, label_db


def check_frame(frame, plan, start, end):
    if (frame.empty or frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any()
            or not frame["build_id"].eq(plan.source_build_id).all()
            or not pd.to_datetime(frame["scoring_date"]).between(pd.Timestamp(start), pd.Timestamp(end)).all()
            or not frame["target_oos_7d"].isin([0, 1]).all()):
        raise Module2Error("Rows violate the source/fold/binary-label contract")


def export_outer(workflow, folder, partition):
    """Outer fold rows from the shared fold manifest; caller enforces chronology."""
    if partition not in ("train", "validation"):
        raise Module2Error("Only the outer training and validation partitions exist here")
    plan = workflow["plan"]
    features, feature_db, labels, label_db = _development_sources(workflow)
    bounds = next(row for row in plan.development_folds() if row[0] == "rolling_validation")
    fold = adapter._query(label_db, """SELECT cast(train_last AS VARCHAR),cast(validation_start AS VARCHAR),
        cast(validation_end AS VARCHAR),cast(outcome_end AS VARCHAR),outcomes_available
        FROM evaluation.temporal_folds WHERE fold_id='rolling_validation'""")
    if len(fold) != 1 or tuple(fold.iloc[0]) != (*bounds[1:], True):
        raise Module2Error("Prepared outer fold differs from the plan")
    candidates = int(adapter._query(label_db, """SELECT count(*) FROM evaluation.fold_samples
        WHERE fold_id='rolling_validation' AND partition=?""", [partition]).iloc[0, 0])
    truth = adapter._query(label_db, "SELECT " + ",".join(KEYS + ["dataset_hash", "target_oos_7d"])
                           + " FROM evaluation.fold_samples WHERE fold_id='rolling_validation' AND partition=? AND eligible",
                           [partition])
    adapter._check_keys(truth, labels)
    start, end = (plan.history_start.isoformat(), bounds[1]) if partition == "train" else bounds[2:4]
    frame = adapter._features(feature_db, features, list(config.FEATURES), start, end)
    data = truth[KEYS + ["target_oos_7d"]].merge(frame, on=KEYS, how="left", validate="one_to_one", indicator=True)
    if not data["_merge"].eq("both").all():
        raise Module2Error("Eligible outer labels lack matching feature rows")
    data = data[KEYS + list(config.FEATURES) + ["target_oos_7d"]].sort_values(KEYS[1:]).reset_index(drop=True)
    check_frame(data, plan, start, end)
    folder = Path(folder)
    manifest = {"version": "module2.outer_inputs.v1", "status": "Accepted", "partition": partition,
                "plan_sha256": plan.to_dict()["plan_sha256"], "workflow_sha256": workflow["sha256"],
                "feature_artifact_id": features["artifact_id"], "label_artifact_id": labels["artifact_id"],
                "candidate_rows": candidates, "scoring_start": start, "scoring_end": end,
                "outcome_end": plan.to_dict()["training"]["outcome_end"] if partition == "train" else bounds[4],
                "test": "Locked", "created_at": utc_now(), "files": {}}
    _write(data, folder, partition, manifest["files"])
    write_json(folder / "manifest.json", manifest, exclusive=True)
    return manifest


def scoring_features(workflow, start, end, *, purpose_label):
    """Feature rows from the plan-bound scoring artifact for explicit dates."""
    plan = workflow["plan"]
    record, database, cutoff, actual = adapter._feature_artifact(
        workflow["components"]["scoring_features"]["directory"], "scoring")
    if actual != plan or cutoff != plan.source_as_of:
        raise Module2Error("Scoring artifact differs from the pinned plan/source")
    columns = list(dict.fromkeys(list(config.FEATURES) + list(adapter.CONTEXT)))
    frame = adapter._features(database, record, columns, start, end)
    frame = frame.sort_values(KEYS[1:]).reset_index(drop=True)
    return record, frame, {"purpose": purpose_label, "feature_artifact_id": record["artifact_id"],
                           "feature_version": record["feature_version"], "start": str(start), "end": str(end)}
