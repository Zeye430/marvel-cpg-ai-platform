"""Model-agnostic bundle (module2.model_bundle.v1): final fit, freeze, verified load.

Only the development-selected winner is refit, on the plan's outer training
partition, with the winner's dev1 calibrator and thresholds carried unchanged.
The frozen manifest never changes; later evaluation status lives beside it in
``eligibility.json``. Loading verifies every component hash and runtime versions.
"""

import gc
import platform
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration, config, evaluation, inputs, models
from .contracts import (Module2Error, check_resources, code_hashes, config_sha256, read_json, sha256_file,
                        utc_now, write_json)
from .preprocessing import Preprocessor, _numeric
from .training import segment_keys

COMPONENTS = ("model", "model_manifest", "preprocessor", "calibration", "thresholds",
              "drift_reference", "feature_importance")


def drift_reference(frame, pre):
    """Training distribution for later input-drift checks (no labels)."""
    numeric = {}
    for name in config.NUMERIC:
        values = _numeric(frame[name], name)
        observed = values[~np.isnan(values)]
        if observed.size:
            edges = np.unique(np.quantile(observed, np.linspace(0.1, 0.9, 9)))
            counts = np.bincount(np.searchsorted(edges, observed, side="right"), minlength=len(edges) + 1)
            share = (counts / len(values)).tolist()
        else:
            edges, share = np.array([]), [0.0]
        numeric[name] = {"edges": edges.tolist(), "bin_share": share,
                         "missing_share": float(np.isnan(values).mean())}
    categorical = {}
    for name in config.CATEGORICAL:
        values = frame[name].astype("string")
        shares = values.value_counts(normalize=False, dropna=True) / len(values)
        categorical[name] = {"levels": {str(k): float(v) for k, v in shares.items()},
                             "missing_share": float(values.isna().mean())}
    segments = segment_keys(frame).value_counts(normalize=True)
    return {"version": "module2.drift_reference.v1", "rows": int(len(frame)), "numeric": numeric,
            "categorical": categorical, "segment_mix": {k: float(v) for k, v in segments.items()},
            "preprocessor_sha256": pre.sha256}


def feature_importance(adapter, pre):
    """Algorithm-specific importance mapped back to the 47 raw predictors."""
    columns = pre.columns
    if adapter.algorithm == "lightgbm":
        values, metric = adapter.model.feature_importance(importance_type="gain"), "lightgbm_total_gain"
    elif adapter.algorithm == "xgboost":
        score = adapter.model.get_score(importance_type="total_gain")
        values = [score.get(f"f{i}", 0.0) for i in range(len(columns))]
        metric = "xgboost_total_gain"
    elif adapter.algorithm == "random_forest":
        values, metric = adapter.model.feature_importances_, "random_forest_mean_impurity_decrease"
    else:
        values = np.abs(adapter.model.named_steps["logistic"].coef_[0])
        metric = "logistic_abs_standardized_coefficient"
    totals = {name: 0.0 for name in config.FEATURES}
    for column, value in zip(columns, values):
        totals[pre.raw_feature(column)] += float(value)
    total = sum(totals.values())
    rows = [{"feature": k, "value": v, "share": (v / total) if total else None}
            for k, v in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"version": "module2.feature_importance.v1", "metric": metric, "algorithm": adapter.algorithm,
            "note": "Algorithm-specific training importance; not causal and not selected on test data",
            "features": rows}


def freeze_winner(run_dir, workflow, *, benchmark_report=None):
    """Final fit of the selected winner and a frozen bundle. Idempotence is refused."""
    run_dir = Path(run_dir)
    report_path = run_dir / "benchmark" / "report.json"
    report = benchmark_report or read_json(report_path, "benchmark report")
    selection = report["selection"]
    if report.get("status") != "Complete" or selection.get("selection_status") != "Selected":
        raise Module2Error("No complete benchmark winner; a bundle is not frozen (no fake winner)")
    trial = next(t for t in report["trials"] if t["trial_id"] == selection["winner"])
    bundle_dir = run_dir / "bundle"
    if bundle_dir.exists():
        raise Module2Error("A bundle already exists for this run; frozen bundles are not refit")
    check_resources(run_dir, stage="final fit")
    outer = run_dir / "inputs" / "outer-train"
    manifest = inputs.export_outer(workflow, outer, "train")
    train = inputs.read_export(outer, "train", manifest)
    plan = workflow["plan"]
    fit_started = utc_now()
    began = time.monotonic()
    pre = Preprocessor.fit(train)
    X = pre.transform(train)
    y = train["target_oos_7d"].to_numpy(int)
    adapter = models.fit_candidate(trial["algorithm"], trial["params"], X, y)
    fit_seconds = time.monotonic() - began
    fit_finished = utc_now()
    staging = run_dir / "bundle.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    model_meta = adapter.save(staging / "model")
    write_json(staging / "preprocessor.json", pre.to_dict())
    calibrator = calibration.check(trial["calibrator"])
    thresholds = evaluation.check_thresholds({k: trial["thresholds"][k] for k in ("global", "segments")})
    write_json(staging / "calibration.json", calibrator)
    write_json(staging / "threshold-policy.json", {**trial["thresholds"], **thresholds})
    write_json(staging / "drift-reference.json", drift_reference(train, pre))
    write_json(staging / "feature-importance.json", feature_importance(adapter, pre))
    sample = X[: min(len(X), 20000)]
    expected = adapter.predict_raw_probability(sample)
    del X
    gc.collect()
    files = {"model": f"model/{model_meta['file']}", "model_manifest": "model/model.json",
             "preprocessor": "preprocessor.json", "calibration": "calibration.json",
             "thresholds": "threshold-policy.json", "drift_reference": "drift-reference.json",
             "feature_importance": "feature-importance.json"}
    components = {role: {"path": path, "sha256": sha256_file(staging / path), "bytes": (staging / path).stat().st_size}
                  for role, path in files.items()}
    size = sum(c["bytes"] for c in components.values())
    if size > config.RESOURCE_LIMITS["bundle_bytes"]:
        raise Module2Error(f"Final bundle {size} bytes exceeds the 512 MiB limit; not frozen")
    p = plan.to_dict()
    bundle = {
        "version": config.VERSIONS["bundle"], "status": "Frozen_for_validation",
        "model_version": f"m2-{trial['algorithm']}-{report['run_id']}", "run_id": report["run_id"],
        "candidate_id": trial["trial_id"], "algorithm": trial["algorithm"], "params": trial["params"],
        "fixed_parameters": config.FIXED_PARAMETERS[trial["algorithm"]], "seed": config.SEED, "threads": config.THREADS,
        "source": {"dataset_hash": plan.dataset_hash, "source_build_id": plan.source_build_id,
                   "source_database_sha256": plan.source_database_sha256, "plan_sha256": p["plan_sha256"],
                   "plan_id": p["plan_id"], "workflow_sha256": workflow["sha256"]},
        "temporal_plan": p,
        "training": {"partition": "rolling_validation/train", "scoring_start": manifest["scoring_start"],
                     "scoring_end": manifest["scoring_end"], "outcome_end": manifest["outcome_end"],
                     "known_before": next(r for r in plan.development_folds() if r[0] == "rolling_validation")[2],
                     "rows": int(len(train)), "positives": int(y.sum()),
                     "inputs_manifest_sha256": sha256_file(outer / "manifest.json")},
        "feature_version": config.FEATURE_VERSION, "features": list(config.FEATURES),
        "transformed_columns": len(pre.columns), "cohort": config.COHORT, "label_version": config.LABEL_VERSION,
        "threshold_policy": config.THRESHOLD_POLICY, "calibration_method": "sigmoid",
        "calibration_source": "rolling_dev_1 held-out scores of the selected trial (transferred, not refit)",
        "config_sha256": config_sha256(), "rules": config.frozen_rules(), "code_hashes": code_hashes(),
        "dependencies": {**models.library_versions(), "python": platform.python_version(), "pandas": pd.__version__},
        "components": components, "bundle_bytes": size,
        "selection": {"benchmark_report_sha256": sha256_file(report_path), "trial_id": trial["trial_id"],
                      "benchmark_status": report["status"], "profile": report["profile"],
                      "dev2_overall": trial["dev2"]["results"][0]},
        "fit_started_at": fit_started, "fit_finished_at": fit_finished, "fit_seconds": fit_seconds,
        "limitations": [
            "Calibrator and thresholds are transferred from the rolling_dev_1 model of the selected trial",
            "Training cohort is conditional on complete seven-day observed labels",
            "Scores are probability estimates after sigmoid calibration, not guaranteed reliable probabilities",
        ],
    }
    bundle["frozen_at"] = utc_now()
    write_json(staging / "bundle.json", bundle, exclusive=True)
    staging.rename(bundle_dir)
    loaded = load_bundle(bundle_dir)
    if np.max(np.abs(loaded.adapter.predict_raw_probability(sample) - expected)) > 1e-10:
        raise Module2Error("Reloaded bundle predictions differ from the fitted model")
    write_json(bundle_dir / "eligibility.json", {"status": "Pending_validation", "history": [
        {"at": utc_now(), "status": "Frozen_for_validation"}]})
    return bundle


class Bundle:
    def __init__(self, directory, manifest, pre, adapter, calibrator, thresholds):
        self.directory, self.manifest = Path(directory), manifest
        self.pre, self.adapter, self.calibrator, self.thresholds = pre, adapter, calibrator, thresholds

    @property
    def manifest_sha256(self):
        return sha256_file(self.directory / "bundle.json")

    def predict(self, frame):
        X = self.pre.transform(frame)
        raw = self.adapter.predict_raw_probability(X)
        probability = calibration.apply(self.calibrator, raw)
        segments = segment_keys(frame)
        thresholds, routes = evaluation.route_thresholds(self.thresholds, segments)
        return {"raw": raw, "probability": probability, "threshold": thresholds, "route": routes,
                "segment": segments.to_numpy(), "decision": probability >= thresholds}

    def eligibility(self):
        path = self.directory / "eligibility.json"
        return read_json(path, "bundle eligibility") if path.exists() else {"status": "Unknown"}


def load_bundle(directory, *, require_current_rules=True):
    """Verify manifest, every component hash and runtime versions before use."""
    directory = Path(directory)
    manifest = read_json(directory / "bundle.json", "bundle manifest")
    if manifest.get("version") != config.VERSIONS["bundle"] or manifest.get("status") != "Frozen_for_validation":
        raise Module2Error("Not a frozen module2.model_bundle.v1")
    if manifest.get("features") != list(config.FEATURES) or manifest.get("feature_version") != config.FEATURE_VERSION:
        raise Module2Error("Bundle feature semantics differ from this code")
    if require_current_rules and manifest.get("config_sha256") != config_sha256():
        raise Module2Error("Bundle was frozen under different delivery rules")
    for role in COMPONENTS:
        entry = manifest["components"].get(role)
        if not entry or Path(entry["path"]).is_absolute() or ".." in Path(entry["path"]).parts:
            raise Module2Error(f"Unsafe or missing bundle component: {role}")
        if sha256_file(directory / entry["path"]) != entry["sha256"]:
            raise Module2Error(f"Bundle component changed: {role}")
    pre = Preprocessor(read_json(directory / manifest["components"]["preprocessor"]["path"]))
    adapter = models.load_candidate(directory / "model", expected=read_json(directory / "model" / "model.json"))
    calibrator = calibration.check(read_json(directory / manifest["components"]["calibration"]["path"]))
    thresholds = evaluation.check_thresholds(read_json(directory / manifest["components"]["thresholds"]["path"]))
    return Bundle(directory, manifest, pre, adapter, calibrator, thresholds)


def update_eligibility(bundle_dir, status, **details):
    path = Path(bundle_dir) / "eligibility.json"
    record = read_json(path, "bundle eligibility")
    record["status"] = status
    record.update(details)
    record["history"].append({"at": utc_now(), "status": status, **{k: v for k, v in details.items()
                                                                     if isinstance(v, (str, int, float, bool))}})
    write_json(path, record)
    return record
