"""Frozen outer-validation stages, deliberately without test or promotion modes."""

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from data_platform.temporal_plan import TemporalPlan
from . import training as t


COHORT = "all_complete_observed_labels_current_missing_and_oos_retained"
POLICY = "pooled_segment_thresholds"
KEYS = ["build_id", "scoring_date", "store_id", "sku_id"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"Required stage record missing or invalid: {Path(path).name}") from error


def _code_hashes():
    return {name: t._hash(Path(__file__).with_name(name))
            for name in ("validation.py", "training.py", "data_adapter.py")}


def _valid_number(value, low=0, high=1):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and np.isfinite(value) and low <= value <= high)


def _check_thresholds(thresholds):
    if (not isinstance(thresholds, dict) or not _valid_number(thresholds.get("global"))
            or not isinstance(thresholds.get("segments"), dict)
            or not all(isinstance(k, str) and _valid_number(v) for k, v in thresholds["segments"].items())):
        raise ValueError("Frozen thresholds must be finite probabilities")


def _sources(workflow, plan):
    from . import data_adapter as a
    f, fdb, cutoff, actual = a._feature_artifact(workflow["components"]["features"]["directory"], "development")
    l, ldb, lcutoff = a._artifact(workflow["components"]["labels"]["directory"], "labels")
    if (actual != plan or TemporalPlan.from_dict(l.get("temporal_plan")) != plan
            or cutoff != lcutoff or l.get("purpose") != "development"
            or any(f[k] != l.get(k) for k in ("dataset_hash", "source_build_id", "source_database_sha256"))):
        raise ValueError("Outer validation requires matching planned development inputs")
    return f, fdb, l, ldb


def freeze_policy(workflow_run, development_dir, run_dir):
    """Freeze the chosen policy before querying any outer partition."""
    workflow_run, development_dir, run_dir = map(Path, (workflow_run, development_dir, run_dir))
    workflow = _read(workflow_run)
    plan = TemporalPlan.from_dict(workflow.get("temporal_plan"))
    inputs = _read(development_dir/"inputs/inputs.json")
    report = _read(development_dir/"results/report.json")
    threshold_path = development_dir/"results/thresholds.json"
    thresholds = _read(threshold_path)
    if (workflow.get("status") != "Accepted" or workflow.get("evaluation_profile") != "trailing_30d"
            or inputs.get("status") != "Accepted" or report.get("status") != "Completed_not_promoted"
            or report.get("cohort") != COHORT or inputs.get("cohort") != COHORT
            or inputs.get("features") != list(t.FEATURES) or report.get("features") != list(t.FEATURES)
            or report.get("parameters") != t.PARAMETERS or inputs.get("parameters") != t.PARAMETERS
            or report.get("test") != "Locked" or report.get("outer_validation") != "Locked"
            or inputs.get("workflow_sha256") != t._hash(workflow_run)
            or report.get("input_manifest_sha256") != t._hash(development_dir/"inputs/inputs.json")
            or TemporalPlan.from_dict(report.get("temporal_plan")) != plan
            or TemporalPlan.from_dict(inputs.get("temporal_plan")) != plan
            or thresholds.get("selected_on") != "rolling_dev_1"):
        raise ValueError("A compatible completed inner-development experiment is required")
    chosen = [r for r in report.get("results", []) if r.get("fold") == "rolling_dev_2"
              and r.get("policy") == POLICY and r.get("scope") == "all"]
    if len(chosen) != 1 or not all(_valid_number(chosen[0].get(k), minimum)
            for k, minimum in (("precision", .75), ("recall", .70), ("roc_auc", .80))):
        raise ValueError("The chosen development policy has not met the numeric prerequisites")
    _check_thresholds(thresholds.get("policies", {}).get("pooled"))
    for name, sha in report.get("model_files", {}).items():
        path = (development_dir/"results"/name).resolve()
        if not path.is_relative_to((development_dir/"results").resolve()) or t._hash(path) != sha:
            raise ValueError("Development model evidence changed")
    if not report.get("model_files"):
        raise ValueError("Development model evidence is missing")
    f, _, l, _ = _sources(workflow, plan)
    policy = {"version": "module2.outer_validation_policy.v1", "status": "Frozen_for_validation",
        "created_at": _now(), "policy": POLICY, "cohort": COHORT, "test_access": "Locked",
        "temporal_plan": plan.to_dict(), "features": list(t.FEATURES),
        "categorical": list(t.CATEGORICAL), "parameters": t.PARAMETERS,
        "thresholds": thresholds["policies"]["pooled"], "code_hashes": _code_hashes(),
        "dependencies": [
            {"path": str(p.resolve()), "sha256": t._hash(p)} for p in
            (workflow_run, development_dir/"inputs/inputs.json", development_dir/"results/report.json", threshold_path)],
        "workflow_run": str(workflow_run.resolve()),
        "source_artifacts": {"features": {"artifact_id": f["artifact_id"], "database_sha256": f["database_sha256"]},
                             "labels": {"artifact_id": l["artifact_id"], "database_sha256": l["database_sha256"]}}}
    run_dir.mkdir(parents=True, exist_ok=False)
    t._save(run_dir/"policy/frozen-policy.json", policy)
    t._event("policy_frozen", run_dir=str(run_dir), plan=plan.to_dict()["plan_id"])
    return policy


def _policy(run_dir):
    path = Path(run_dir)/"policy/frozen-policy.json"
    p = _read(path)
    TemporalPlan.from_dict(p.get("temporal_plan"))
    if (p.get("version") != "module2.outer_validation_policy.v1" or p.get("status") != "Frozen_for_validation"
            or p.get("policy") != POLICY or p.get("cohort") != COHORT or p.get("test_access") != "Locked"
            or p.get("features") != list(t.FEATURES) or p.get("categorical") != list(t.CATEGORICAL)
            or p.get("parameters") != t.PARAMETERS or p.get("code_hashes") != _code_hashes()):
        raise ValueError("Frozen policy/code/configuration changed")
    if not p.get("dependencies") or any(t._hash(r["path"]) != r["sha256"] for r in p["dependencies"]):
        raise ValueError("Frozen policy evidence changed")
    original = _read(p["dependencies"][-1]["path"])
    if p.get("thresholds") != original["policies"]["pooled"]:
        raise ValueError("Thresholds must remain frozen from development")
    _check_thresholds(p["thresholds"])
    return p, t._hash(path)


def _inputs(run_dir, partition, policy_hash):
    folder = Path(run_dir)/f"{partition}-inputs"
    record = _read(folder/"manifest.json")
    if (record.get("version") != "module2.outer_partition.v1" or record.get("test_access") != "Locked"
            or record.get("status") != "Accepted" or record.get("partition") != partition
            or record.get("policy_sha256") != policy_hash or record.get("file") != "data.parquet"
            or t._hash(folder/"data.parquet") != record.get("data_sha256")):
        raise ValueError("Partition input/policy integrity mismatch")
    return record, folder/"data.parquet"


def _candidate(run_dir, policy_hash):
    folder = Path(run_dir)/"candidate"
    c = _read(folder/"manifest.json")
    if (c.get("version") != "module2.outer_candidate.v1" or c.get("test_access") != "Locked"
            or c.get("status") != "Frozen_for_validation" or c.get("policy_sha256") != policy_hash
            or c.get("features") != list(t.FEATURES)
            or t._hash(folder/"model.txt") != c.get("model_sha256")
            or t._hash(folder/"categories.json") != c.get("categories_sha256")
            or t._hash(Path(run_dir)/"train-inputs/manifest.json") != c.get("training_manifest_sha256")):
        raise ValueError("A matching frozen candidate is required before validation access")
    return c, t._hash(folder/"manifest.json")


def _check_frame(frame, policy, partition):
    plan = TemporalPlan.from_dict(policy["temporal_plan"])
    bounds = next(row for row in plan.development_folds() if row[0] == "rolling_validation")
    start, end = (plan.history_start.isoformat(), bounds[1]) if partition == "train" else bounds[2:4]
    if (frame.empty or frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any()
            or not frame["build_id"].eq(plan.source_build_id).all()
            or not pd.to_datetime(frame["scoring_date"]).between(pd.Timestamp(start), pd.Timestamp(end)).all()
            or not frame["target_oos_7d"].isin([0, 1]).all()):
        raise ValueError("Rows violate the frozen source/fold/binary-label contract")


def export_partition(run_dir, partition):
    """Only this controlled entry point opens the prepared outer partitions."""
    if partition not in ("train", "validation"):
        raise ValueError("Only outer training/validation are supported; test is locked")
    run_dir = Path(run_dir)
    p, phash = _policy(run_dir)
    candidate_hash = None
    if partition == "validation":
        _, candidate_hash = _candidate(run_dir, phash)  # before any label connection
    folder = run_dir/f"{partition}-inputs"
    folder.mkdir(exist_ok=False)
    if partition == "validation":
        access = run_dir/"access/outer-validation.json"
        access.parent.mkdir(exist_ok=True)
        with access.open("x") as handle:
            json.dump({"event": "Outer_validation_truth_access", "at": _now(),
                       "policy_sha256": phash, "candidate_manifest_sha256": candidate_hash}, handle, indent=2)
    record = {"version": "module2.outer_partition.v1", "status": "Running", "partition": partition,
              "policy_sha256": phash, "candidate_manifest_sha256": candidate_hash, "test_access": "Locked"}
    try:
        import duckdb
        from . import data_adapter as a
        plan = TemporalPlan.from_dict(p["temporal_plan"])
        f, fdb, l, ldb = _sources(_read(p["workflow_run"]), plan)
        for name, source in (("features", f), ("labels", l)):
            if any(source[key] != value for key, value in p["source_artifacts"][name].items()):
                raise ValueError("Prepared source changed after policy freeze")
        bounds = next(row for row in plan.development_folds() if row[0] == "rolling_validation")
        fold = a._query(ldb, """SELECT cast(train_last AS VARCHAR),cast(validation_start AS VARCHAR),
            cast(validation_end AS VARCHAR),cast(outcome_end AS VARCHAR),outcomes_available
            FROM evaluation.temporal_folds WHERE fold_id='rolling_validation'""")
        if len(fold) != 1 or tuple(fold.iloc[0]) != (*bounds[1:], True):
            raise ValueError("Prepared outer fold differs from the frozen plan")
        counts = a._query(ldb, """SELECT count(*) FROM evaluation.fold_samples
            WHERE fold_id='rolling_validation' AND partition=?""", [partition])
        truth = a._query(ldb, "SELECT " + ",".join(KEYS+["dataset_hash", "target_oos_7d"])
            + " FROM evaluation.fold_samples WHERE fold_id='rolling_validation' AND partition=? AND eligible", [partition])
        a._check_keys(truth, l)
        start, end = (plan.history_start.isoformat(), bounds[1]) if partition == "train" else bounds[2:4]
        features = a._features(fdb, f, list(t.FEATURES), start, end)
        frame = truth[KEYS+["target_oos_7d"]].merge(features, on=KEYS, how="left", validate="one_to_one", indicator=True)
        if not frame["_merge"].eq("both").all():
            raise ValueError("Eligible outer labels lack matching features")
        frame = frame[KEYS+list(t.FEATURES)+["target_oos_7d"]].sort_values(KEYS[1:]).reset_index(drop=True)
        _check_frame(frame, p, partition)
        with duckdb.connect(config={"threads": 2, "memory_limit": "512MB"}) as con:
            con.register("batch", frame)
            path = str((folder/"data.parquet").resolve()).replace("'", "''")
            con.execute(f"COPY batch TO '{path}' (FORMAT PARQUET)")
        record.update(status="Accepted", file="data.parquet", data_sha256=t._hash(folder/"data.parquet"),
                      rows=len(frame), candidate_rows=int(counts.iloc[0, 0]),
                      scoring_start=start, scoring_end=end,
                      outcome_end=plan.to_dict()["training"]["outcome_end"] if partition == "train" else bounds[4])
        t._event("outer_partition_exported", partition=partition, rows=len(frame))
    except BaseException as error:
        record.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        t._save(folder/"manifest.json", record)
    return record


def fit_candidate(run_dir):
    """Fit training only; no validation loader, early stopping or tuning."""
    import lightgbm
    run_dir = Path(run_dir)
    p, phash = _policy(run_dir)
    inputs, data = _inputs(run_dir, "train", phash)
    frame = pd.read_parquet(data)
    _check_frame(frame, p, "train")
    if len(frame) != inputs["rows"] or len(frame.target_oos_7d.unique()) != 2:
        raise ValueError("Candidate requires complete training data with both classes")
    folder = run_dir/"candidate"
    folder.mkdir(exist_ok=False)
    record = {"version": "module2.outer_candidate.v1", "status": "Fitting", "policy_sha256": phash, "test_access": "Locked",
              "features": list(t.FEATURES), "training_manifest_sha256": t._hash(run_dir/"train-inputs/manifest.json"),
              "train_rows": len(frame), "started_at": _now()}
    try:
        X, _, levels = t.prepare_categories(frame, frame.iloc[:0])
        model = lightgbm.LGBMClassifier(**p["parameters"])
        t._event("fit_outer_candidate", train_rows=len(X), train_scoring_end=inputs["scoring_end"])
        model.fit(X, frame.target_oos_7d.to_numpy(int), categorical_feature=list(t.CATEGORICAL))
        model.booster_.save_model(str(folder/"model.txt"))
        t._save(folder/"categories.json", levels)
        record.update(status="Frozen_for_validation", frozen_at=_now(), model_sha256=t._hash(folder/"model.txt"),
                      categories_sha256=t._hash(folder/"categories.json"),
                      environment={"python": platform.python_version(), "lightgbm": lightgbm.__version__,
                                   "pandas": pd.__version__, "numpy": np.__version__})
    except BaseException as error:
        record.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        t._save(folder/"manifest.json", record)
    return record


def evaluate_candidate(run_dir):
    """Predict with the same frozen model/thresholds; never refit or select."""
    import lightgbm
    run_dir = Path(run_dir)
    p, phash = _policy(run_dir)
    candidate, chash = _candidate(run_dir, phash)
    inputs, data = _inputs(run_dir, "validation", phash)
    access = _read(run_dir/"access/outer-validation.json")
    if any(r.get("candidate_manifest_sha256") != chash for r in (inputs, access)) or access.get("policy_sha256") != phash:
        raise ValueError("Validation inputs/access refer to a different frozen candidate")
    frame = pd.read_parquet(data)
    _check_frame(frame, p, "validation")
    if len(frame) != inputs["rows"]:
        raise ValueError("Validation input row count changed")
    folder = run_dir/"evaluation"
    folder.mkdir(exist_ok=False)
    report = {"version": "module2.outer_validation_report.v1", "status": "Running", "results": [],
              "evaluation_type": "Retrospective_out_of_time_validation", "policy": POLICY, "cohort": COHORT,
              "policy_sha256": phash, "candidate_manifest_sha256": chash, "model_sha256": candidate["model_sha256"],
              "temporal_plan": p["temporal_plan"], "validation_inputs_sha256": t._hash(run_dir/"validation-inputs/manifest.json"),
              "candidate_rows": inputs["candidate_rows"], "eligible_rows": len(frame),
              "label_coverage": len(frame)/inputs["candidate_rows"], "test_access": "Locked",
              "test_authorization": "Not_granted", "publication": "Not_authorized", "evaluated_at": _now()}
    try:
        X = frame[list(t.FEATURES)].copy()
        levels = _read(run_dir/"candidate/categories.json")
        for name in t.CATEGORICAL:
            X[name] = pd.Categorical(X[name], categories=levels[name])
        for name in t.NUMERIC:
            X[name] = pd.to_numeric(X[name], errors="raise").astype(float)
            if np.isinf(X[name].to_numpy()).any():
                raise ValueError("Non-finite validation numeric predictor")
        model = lightgbm.Booster(model_file=str(run_dir/"candidate/model.txt"))
        score = model.predict(X, num_threads=2)
        segments = t.segment_keys(frame)
        thresholds = np.asarray([p["thresholds"]["segments"].get(k, p["thresholds"]["global"]) for k in segments])
        y = frame.target_oos_7d.to_numpy(int)
        scopes = [("all", np.ones(len(y), dtype=bool))]
        scopes += [("segment:"+k, segments.eq(k).to_numpy()) for k in sorted(set(segments))]
        scopes += [("current_not_oos", frame.oos_observed.eq(False).fillna(False).to_numpy(bool)),
                   ("current_oos", frame.oos_observed.eq(True).fillna(False).to_numpy(bool)),
                   ("current_unknown", frame.oos_observed.isna().to_numpy())]
        report["results"] = [{"scope": name, **t.evaluate(y[mask], score[mask], thresholds[mask])} for name, mask in scopes]
        report.update(status="Evaluated_not_promoted", numeric_gate_passed=report["results"][0]["meets_numeric_targets"],
                      unknown_segment_rows=int(sum(k not in p["thresholds"]["segments"] for k in segments)))
        predictions = frame[KEYS+["channel", "segment_tier", "oos_observed", "target_oos_7d"]].copy()
        predictions["score"] = score; predictions["threshold"] = thresholds
        predictions.to_parquet(folder/"validation-predictions.parquet", index=False)
        pd.DataFrame(report["results"]).to_csv(folder/"metrics.csv", index=False)
        report["predictions_sha256"] = t._hash(folder/"validation-predictions.parquet")
        t._event("outer_validation_evaluated", overall=report["results"][0], test_access="Locked")
    except BaseException as error:
        report.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        t._save(folder/"report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--workflow-run", required=True)
    freeze.add_argument("--development-dir", required=True)
    freeze.add_argument("--run-dir", required=True)
    for name in ("export-training", "fit", "export-validation", "evaluate"):
        commands.add_parser(name).add_argument("--run-dir", required=True)
    args = vars(parser.parse_args()); action = args.pop("action")
    if action == "freeze":
        freeze_policy(**args)
    elif action == "export-training":
        export_partition(args["run_dir"], "train")
    elif action == "export-validation":
        export_partition(args["run_dir"], "validation")
    elif action == "fit":
        fit_candidate(args["run_dir"])
    else:
        evaluate_candidate(args["run_dir"])


if __name__ == "__main__":
    main()
