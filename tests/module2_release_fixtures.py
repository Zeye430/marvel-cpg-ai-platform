"""Coherent SYNTHETIC oos_test_release.v2 evidence for gate/ledger/label tests.

Every file is built in dependency order so a mutation can change one record
while all downstream hashes are legitimately recomputed; this tests semantic
validation, not just integrity failures. Nothing here is a real approval.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True))
    return path


CODE = {"benchmark.py": "1" * 64, "bundles.py": "2" * 64, "evaluation.py": "3" * 64}
PARAMS = {"lightgbm": {"n_estimators": 150, "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 20,
                       "colsample_bytree": 0.8, "subsample": 0.8, "reg_lambda": 1.0},
          "xgboost": {"n_estimators": 150, "learning_rate": 0.05, "max_depth": 4, "min_child_weight": 2.0,
                      "subsample": 0.8, "colsample_bytree": 0.8, "reg_lambda": 1.0}}
CALIBRATOR = {"version": "module2.calibration.v1", "method": "sigmoid", "input": "logit_raw_probability",
              "clip": 1e-6, "coef": 1.0, "intercept": 0.0, "fit_rows": 1000, "fit_positives": 100}
THRESHOLDS = {"global": 0.5, "segments": {'["Modern Trade","Tier 1"]': 0.4}}


def rules():
    from modules.module_2 import config
    return json.loads(json.dumps(config.frozen_rules()))


def config_sha(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def overall(tp, fp, fn, tn, auc=0.9, ap=0.7):
    rows = tp + fp + fn + tn
    return {"group_kind": "all", "group_id": "all", "rows": rows, "positives": tp + fn, "tp": tp, "fp": fp, "fn": fn,
            "tn": tn, "alerts": tp + fp, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "roc_auc": auc, "average_precision": ap,
            "meets_numeric_targets": True}


def trial(algorithm, number, params, dev2, status="Complete"):
    record = {"algorithm": algorithm, "trial_id": f"{algorithm}-{number:03d}", "params": json.loads(json.dumps(params)), "status": status,
              "fit_seconds": {"rolling_dev_1": 1.0, "rolling_dev_2": 1.0}, "total_fit_seconds": 2.0,
              "wall_seconds": 5.0}
    if status == "Complete":
        record.update(dev1={"results": [overall(80, 20, 20, 880)]}, dev2={"results": [dev2]},
                      calibrator=dict(CALIBRATOR), thresholds=dict(THRESHOLDS))
    return record


def benchmark(r, winner_dev2=None):
    """Coherent v3 benchmark: 3 complete trials per main family + the baseline grid; xgboost-000 wins."""
    trials = []
    for n in range(3):
        trials.append(trial("lightgbm", n, PARAMS["lightgbm"], overall(76, 24, 24, 876)))
        trials.append(trial("xgboost", n, PARAMS["xgboost"],
                            winner_dev2 or overall(80, 20, 20, 880) if n == 0 else overall(75, 25, 25, 875)))
        trials.append(trial("random_forest", n, dict(r["rf_starting_trials"][n]), overall(74, 26, 26, 874)))
        trials.append(trial("logistic_regression", n, dict(r["baseline_grid"][n]), overall(70, 30, 30, 870)))
    return {"version": "module2.benchmark_report.v1", "run_id": "synthetic-run", "profile": "v3",
            "budget": dict(r["budget_v3"]), "kind": "development", "status": "Complete", "trials": trials,
            "study_progress": {a: {"elapsed_seconds": 100.0, "status": "Trial_limit_reached"}
                               for a in ("lightgbm", "xgboost", "random_forest")},
            "ml_seconds_used": 1000.0,
            "selection": {"benchmark_status": "Complete", "selection_status": "Selected", "winner": "xgboost-000",
                          "incomplete_algorithms": []}}


def times(base=None):
    now = base or datetime.now(timezone.utc)
    return {k: (now - timedelta(minutes=m)).isoformat() for k, m in
            (("approved", 300), ("fit", 240), ("frozen", 180), ("access", 120), ("evaluated", 60),
             ("resolved", 30), ("manual", 20))}


def build(root, plan, ledger_id, *, mode="preauthorized_policy", exposure="holdout_confirmation", mutate=None, t=None):
    """Write evidence + release under ``root``; ``mutate(name, record)`` may edit any record."""
    root = Path(root)
    t = t or times()
    mutate = mutate or (lambda name, record: None)
    p = plan.to_dict() if hasattr(plan, "to_dict") else plan
    from data_platform.temporal_plan import TemporalPlan
    folds = {r[0]: r for r in TemporalPlan.from_dict(p).development_folds()}
    fold = folds["rolling_validation"]
    e = root / "evidence"

    def record(name, value, filename=None):
        mutate(name, value)
        return write(e / (filename or f"{name}.json"), value)

    (e / "model").mkdir(parents=True, exist_ok=True)
    (e / "model" / "model.ubj").write_bytes(b"SYNTHETIC MODEL BYTES")
    model_json = record("model_manifest", {"algorithm": "xgboost", "file": "model.ubj"}, "model/model.json")
    pre = record("preprocessor", {"version": "module2.preprocessor.v1", "synthetic": True})
    cal = record("calibration", dict(CALIBRATOR))
    thr = record("thresholds", json.loads(json.dumps(THRESHOLDS)))
    r = rules()
    train = record("training_inputs_manifest", {"partition": "train", "status": "Accepted", "test": "Locked",
                                                "scoring_end": fold[1], "outcome_end": p["training"]["outcome_end"]})
    valid = record("validation_inputs_manifest", {"partition": "validation", "status": "Accepted", "test": "Locked",
                                                  "scoring_start": fold[2], "scoring_end": fold[3], "outcome_end": fold[4]})
    benchmark = record("benchmark_report", globals()["benchmark"](r))
    source = {k: p["source"][k] for k in ("dataset_hash", "source_build_id", "source_database_sha256")}
    bundle = record("bundle_manifest", {
        "version": "module2.model_bundle.v1", "status": "Frozen_for_validation", "temporal_plan": p,
        "feature_version": "oos_features.v1-complete", "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
        "threshold_policy": "pooled_segment_thresholds", "label_version": "oos_next7.complete_window.v1",
        "source": source, "candidate_id": "xgboost-000", "model_version": "m2-xgboost-synthetic", "algorithm": "xgboost",
        "run_id": "synthetic-run", "params": dict(PARAMS["xgboost"]), "rules": rules(), "config_sha256": config_sha(r),
        "code_hashes": dict(CODE), "fit_started_at": t["fit"], "frozen_at": t["frozen"],
        "components": {"model": {"sha256": sha(e / "model" / "model.ubj")}, "model_manifest": {"sha256": sha(model_json)},
                       "preprocessor": {"sha256": sha(pre)}, "calibration": {"sha256": sha(cal)},
                       "thresholds": {"sha256": sha(thr)}},
        "training": {"inputs_manifest_sha256": sha(train)}, "selection": {"benchmark_report_sha256": sha(benchmark)}},
        "bundle.json")
    predictions = e / "predictions.parquet"
    predictions.write_bytes(b"SYNTHETIC PREDICTION BYTES")
    access = record("validation_access", {"event": "Outer_validation_truth_access", "at": t["access"],
                                          "bundle_manifest_sha256": sha(bundle)})
    report = record("validation_report", {
        "version": "module2.evaluation_report.v2", "kind": "iterative_validation", "status": "Evaluated",
        "bundle_manifest_sha256": sha(bundle), "model_sha256": sha(e / "model" / "model.ubj"),
        "plan_sha256": p["plan_sha256"], "dataset_hash": p["source"]["dataset_hash"],
        "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
        "execution": {"access_marker_sha256": sha(access), "inputs_manifest_sha256": sha(valid),
                      "predictions_sha256": sha(predictions)},
        "results": [{"group_kind": "all", "group_id": "all", "rows": 1000, "positives": 100, "tp": 80, "fp": 20,
                     "fn": 20, "tn": 880, "alerts": 100, "precision": 0.8, "recall": 0.8, "roc_auc": 0.9,
                     "meets_numeric_targets": True}],
        "eligible_rows": 1000, "candidate_rows": 2000, "label_coverage": 0.5, "numeric_gate_passed": True,
        "evaluated_at": t["evaluated"]})
    policy = record("policy", {
        "version": "owner_execution_policy.v1",
        "human_authority": {"approver": "SYNTHETIC TEST APPROVER", "approved_at": t["approved"],
                            "message": "synthetic", "message_reference": "fixture"},
        "execution_scope": {"dataset_hash": p["source"]["dataset_hash"], "plan_sha256": p["plan_sha256"],
                            "source_build_id": p["source"]["source_build_id"], "feature_version": "oos_features.v1-complete",
                            "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
                            "label_version": "oos_next7.complete_window.v1", "config_sha256": config_sha(r),
                            "rules": rules(), "code_identity": dict(CODE),
                            "max_primary_holdout_evaluations": 1,
                            "exposure_modes": ["holdout_confirmation", "independent_test"],
                            "confirmation_windows": [p["test"]], "allowed_output_roots": [str(root.resolve())],
                            "risk_acknowledgements": ["synthetic fixture"]}})

    def ref(path):
        return {"path": str(Path(path).relative_to(root)), "sha256": sha(path)}
    release = {
        "release_version": "oos_test_release.v2", "status": "Approved", "approval_mode": mode,
        "evidence_profile": "module2.evaluation_report.v2", "exposure_mode": exposure,
        "temporal_plan": p, "plan_sha256": p["plan_sha256"], **source, "test_window": p["test"],
        "feature_version": "oos_features.v1-complete", "label_version": "oos_next7.complete_window.v1",
        "label_policy": "complete_window", "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
        "threshold_policy": "pooled_segment_thresholds", "exposure_ledger_id": ledger_id,
        "evidence": {"bundle_manifest": ref(bundle), "model": ref(e / "model" / "model.ubj"),
                     "model_manifest": ref(model_json), "preprocessor": ref(pre), "calibration": ref(cal),
                     "thresholds": ref(thr), "validation_report": ref(report), "validation_predictions": ref(predictions),
                     "validation_access": ref(access), "benchmark_report": ref(benchmark),
                     "training_inputs_manifest": ref(train), "validation_inputs_manifest": ref(valid)}}
    if mode == "preauthorized_policy":
        release["policy"] = ref(policy)
        release["resolution"] = {"kind": "program_resolution_not_human_signature", "resolved_at": t["resolved"],
                                 "resolved_by": "synthetic test program", "run_id": "synthetic"}
    else:
        release.update(approved_by="SYNTHETIC TEST APPROVER", approved_at=t["manual"], approved_code_identity=dict(CODE),
                       risk_acknowledgement={"validation_report_sha256": sha(report), "complete_label_coverage_reviewed": True,
                                             "subgroup_limits_reviewed": True, "calibration_reviewed": True,
                                             "note": "synthetic fixture"})
        if exposure == "holdout_confirmation":
            release["confirmation_window"] = p["test"]
    mutate("release", release)
    return write(root / "release.json", release)
