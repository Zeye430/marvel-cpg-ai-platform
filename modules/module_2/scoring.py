"""Label-free latest-day scoring with a frozen, verified bundle.

Reads only the explicit purpose=scoring feature artifact for its latest date D
through the existing reader (no label artifact, no future reporting, no raw
CSV) and never fits anything. Every confirmed store-SKU of D is scored,
including currently out-of-stock and unknown-input pairs.
"""

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import bundles, config
from . import data_adapter as adapter
from .contracts import Module2Error, sha256_file, utc_now, write_json

PREDICTION_COLUMNS = ["build_id", "dataset_hash", "scoring_run_id", "scoring_date", "store_id", "sku_id",
                      "probability", "threshold", "is_predicted_oos", "drivers_json", "model_version",
                      "feature_version", "input_status"]
EVIDENCE = (("inventory_observed", "Current reported inventory (units)"),
            ("oos_observed", "Currently reported out of stock"),
            ("days_since_inventory_report", "Days since last inventory report"),
            ("in_stock_velocity_28d", "Prior 28-day in-stock sales velocity (units/day)"),
            ("receipt_units_7d", "Delivered units, last 7 days"),
            ("days_since_receipt", "Days since last delivery receipt"),
            ("scheduled_units_next7d", "Units on known schedule for the next 7 days"),
            ("days_until_next_scheduled_receipt", "Days until next known scheduled receipt"),
            ("pending_order_count", "Open orders known at end of day"),
            ("is_msl", "Must-stock-list SKU"),
            ("days_cover_observed", "Observed days cover (inventory / prior velocity), not a forecast date"))


def _value(value):
    if value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def input_status(frame):
    inventory = frame["inventory_observed"].notna() & frame["oos_observed"].notna()
    history = frame["days_since_inventory_report"].notna()
    return np.where(inventory, "Observed_context", np.where(history, "Missing_context", "Unknown_context"))


def drivers(row, scoring_date, out, i):
    start = scoring_date + timedelta(days=1)
    evidence = []
    for feature, label in EVIDENCE:
        value = _value(row[feature])
        evidence.append({"feature": feature, "label": label, "value": value,
                         "data_status": "Unknown" if value is None else "Observed",
                         "as_of": scoring_date.isoformat()})
    return json.dumps({
        "schema_version": config.VERSIONS["drivers"], "prediction_start": start.isoformat(),
        "prediction_end": (scoring_date + timedelta(days=config.HORIZON_DAYS)).isoformat(),
        "segment_key": out["segment"][i], "threshold_route": out["route"][i], "calibration_method": "sigmoid",
        "explanation_type": "rule_based_context", "evidence": evidence,
        "limitations": ["Context shown beside the model score; not SHAP or causal attribution",
                        "Probability is a calibrated estimate of any OOS day in the next 7 days, not a stockout date",
                        "Missing inputs are Unknown, never zero"]}, sort_keys=True, ensure_ascii=True)


def score(workflow, bundle_dir, output_dir, *, scoring_run_id=None, synthetic=False):
    """Score D for every confirmed pair; writes an immutable scoring package."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise Module2Error("Scoring packages are immutable; use a new directory")
    bundle = bundles.load_bundle(bundle_dir)
    scoring_dir = workflow["components"]["scoring_features"]["directory"]
    batch = adapter.load_scoring_inputs(scoring_dir, predictors=list(config.FEATURES))
    record, database, cutoff, plan = adapter._feature_artifact(scoring_dir, "scoring")
    if record["feature_version"] != bundle.manifest["feature_version"]:
        raise Module2Error("Scoring features are incompatible with the frozen bundle")
    frame = pd.concat([batch.row_keys, batch.X, batch.context.drop(
        columns=[c for c in batch.context.columns if c in batch.X.columns])], axis=1)
    expected = int(adapter._query(database, "SELECT count(*) FROM gold.features_oos_daily WHERE scoring_date=?",
                                  [cutoff]).iloc[0, 0])
    if len(frame) != expected or frame.duplicated(["store_id", "sku_id"]).any():
        raise Module2Error("Scoring must cover every confirmed pair of D exactly once")
    out = bundle.predict(frame)
    run_id = scoring_run_id or f"score-{cutoff.isoformat()}-{uuid.uuid4().hex[:10]}"
    scoring_date = cutoff if isinstance(cutoff, date) else date.fromisoformat(str(cutoff))
    status = input_status(frame)
    predictions = pd.DataFrame({
        "build_id": frame["build_id"].astype(str), "dataset_hash": record["dataset_hash"],
        "scoring_run_id": run_id, "scoring_date": pd.to_datetime(frame["scoring_date"]).dt.date,
        "store_id": frame["store_id"].astype(str), "sku_id": frame["sku_id"].astype(str),
        "probability": out["probability"].astype(float), "threshold": out["threshold"].astype(float),
        "is_predicted_oos": out["decision"].astype(bool),
        "drivers_json": [drivers(row, scoring_date, out, i) for i, row in enumerate(frame.to_dict("records"))],
        "model_version": bundle.manifest["model_version"], "feature_version": record["feature_version"],
        "input_status": status})
    if not predictions["probability"].between(0, 1).all():
        raise Module2Error("Probabilities must lie in [0,1]")
    detail = frame[["store_id", "sku_id", "channel", "segment_tier", "region", "category", "is_msl", "oos_observed",
                    "inventory_observed", "in_stock_velocity_28d", "days_cover_observed",
                    "days_since_inventory_report"]].copy()
    for name in ("is_msl", "oos_observed"):
        detail[name] = detail[name].astype("boolean")
    detail["raw_score"] = out["raw"]
    detail["calibrated_probability"] = out["probability"]
    detail["threshold_route"] = out["route"]
    detail["segment_key"] = out["segment"]
    output_dir.mkdir(parents=True)
    predictions.to_parquet(output_dir / "predictions.parquet", index=False)
    detail.to_parquet(output_dir / "scoring-detail.parquet", index=False)
    eligibility = bundle.eligibility()
    manifest = {
        "version": config.VERSIONS["scoring"], "status": "Accepted", "scoring_run_id": run_id,
        "scoring_date": scoring_date.isoformat(),
        "prediction_window": [(scoring_date + timedelta(days=1)).isoformat(),
                              (scoring_date + timedelta(days=config.HORIZON_DAYS)).isoformat()],
        "source": {"dataset_hash": record["dataset_hash"], "source_build_id": record["source_build_id"],
                   "feature_artifact_id": record["artifact_id"], "feature_database_sha256": record["database_sha256"],
                   "feature_version": record["feature_version"], "plan_sha256": plan.to_dict()["plan_sha256"]},
        "bundle": {"manifest_sha256": bundle.manifest_sha256, "model_version": bundle.manifest["model_version"],
                   "candidate_id": bundle.manifest.get("candidate_id"), "algorithm": bundle.manifest["algorithm"],
                   "eligibility_status": eligibility.get("status"), "training_source": bundle.manifest["source"]},
        "synthetic": bool(synthetic),
        "rows": int(len(predictions)), "expected_rows": expected,
        "alerts": int(predictions["is_predicted_oos"].sum()),
        "input_status_counts": {k: int(v) for k, v in pd.Series(status).value_counts().items()},
        "labels_opened": False, "models_fitted": False, "calibration_method": "sigmoid",
        "files": {name: sha256_file(output_dir / name) for name in ("predictions.parquet", "scoring-detail.parquet")},
        "created_at": utc_now(),
    }
    write_json(output_dir / "manifest.json", manifest, exclusive=True)
    return manifest


def read_package(directory):
    from .contracts import read_json
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json", "scoring manifest")
    if manifest.get("version") != config.VERSIONS["scoring"] or manifest.get("status") != "Accepted":
        raise Module2Error("Not an accepted scoring package")
    for name, sha in manifest["files"].items():
        if sha256_file(directory / name) != sha:
            raise Module2Error(f"Scoring package file changed: {name}")
    return manifest, pd.read_parquet(directory / "predictions.parquet"), pd.read_parquet(directory / "scoring-detail.parquet")
