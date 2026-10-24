"""Verify explicit local approval/evidence before materializing June test truth.

This is an auditable engineering gate, not a signature/authentication service.
The Module 2 owner supplies genuine frozen model and May validation evidence.
"""

import json
import math
from datetime import datetime
from pathlib import Path

from .ingestion import digest


def authorize_test_release(release_path, dataset_hash):
    if release_path is None:
        raise ValueError("June test requires an approved frozen-model test release")
    location = Path(release_path).resolve()
    release = json.loads(location.read_text())
    if release.get("release_version") != "oos_test_release.v1" or release.get("status") != "Approved":
        raise ValueError("An approved oos_test_release.v1 record is required")
    for key in ("approved_by", "model_version", "feature_version", "threshold_policy_version"):
        if not isinstance(release.get(key), str) or not release[key].strip():
            raise ValueError("Missing test-release identity: " + key)
    if release.get("dataset_hash") != dataset_hash:
        raise ValueError("Test release dataset mismatch")
    frozen = datetime.fromisoformat(release["model_frozen_at"])
    approved = datetime.fromisoformat(release["approved_at"])
    if frozen.tzinfo is None or approved.tzinfo is None or frozen > approved:
        raise ValueError("Model must be frozen before explicit timezone-aware approval")
    paths = {}
    for name in ("model", "validation_report"):
        target = Path(release[name + "_file"])
        target = target if target.is_absolute() else location.parent / target
        if digest(target) != release.get(name + "_sha256"):
            raise ValueError("Test-release evidence hash mismatch: " + name)
        paths[name] = target
    report = json.loads(paths["validation_report"].read_text())
    expected = {"status": "Passed", "fold_id": "may_validation", "outcome_end": "2026-05-31",
                "label_policy": "complete_window", "dataset_hash": dataset_hash,
                **{key: release[key] for key in ("model_version", "feature_version", "threshold_policy_version", "model_sha256")}}
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("May validation evidence does not match the frozen release")
    for metric, minimum in (("precision", .75), ("recall", .70), ("roc_auc", .80)):
        value = report.get("metrics", {}).get(metric)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= 1:
            raise ValueError("May validation target not met: " + metric)
    return {"release_sha256": digest(location), "model_sha256": release["model_sha256"],
            "validation_report_sha256": release["validation_report_sha256"],
            "model_version": release["model_version"], "feature_version": release["feature_version"],
            "threshold_policy_version": release["threshold_policy_version"],
            "approved_by": release["approved_by"], "approved_at": release["approved_at"]}


# ---------------------------------------------------------------------------
# oos_test_release.v2: rolling plan-bound release, read-only evidence checks.
# Pure JSON/file-hash validation: no model loading, prediction, SQL or Module 2
# imports. Two explicit approval modes that never fall back to each other.
# ---------------------------------------------------------------------------

import hashlib as _hashlib
from datetime import timezone as _timezone

RELEASE_V2 = "oos_test_release.v2"
APPROVAL_MODES = ("post_freeze_manual", "preauthorized_policy")
EVIDENCE_PROFILES = ("module2.evaluation_report.v2", "module2.outer_validation_report.v1")
TARGETS_V2 = (("precision", .75), ("recall", .70), ("roc_auc", .80))
_SHA = set("0123456789abcdef")


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _is_sha(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _when(value, name):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid timestamp: {name}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {name}")
    return parsed


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _load(location, entry, role):
    if not isinstance(entry, dict) or not _is_sha(entry.get("sha256")) or not _nonblank(entry.get("path")):
        raise ValueError(f"Evidence role needs path and SHA-256: {role}")
    target = Path(entry["path"])
    target = target if target.is_absolute() else location.parent / target
    if not target.is_file() or digest(target) != entry["sha256"]:
        raise ValueError(f"Test-release evidence hash mismatch: {role}")
    return target


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"Evidence is not readable JSON: {Path(path).name}") from error


def _check_counts(row, eligible_rows):
    names = {"rows": "rows", "positives": "positives", "tp": "tp", "fp": "fp", "fn": "fn", "tn": "tn",
             "alerts": "alerts"}
    values = {}
    for key in names:
        value = row.get(key, row.get(key.upper()))
        if type(value) is not int or value < 0:
            raise ValueError("Confusion counts must be nonnegative integers")
        values[key] = value
    if (values["tp"] + values["fp"] + values["fn"] + values["tn"] != values["rows"]
            or values["tp"] + values["fn"] != values["positives"] or values["tp"] + values["fp"] != values["alerts"]
            or values["rows"] != eligible_rows or not values["tp"] + values["fp"] or not values["positives"]):
        raise ValueError("Validation counts do not reconcile")
    for metric, minimum in TARGETS_V2:
        value = row.get(metric)
        if not _number(value) or not minimum <= value <= 1:
            raise ValueError("Validation target not met or undefined: " + metric)
    if (abs(row["precision"] - values["tp"] / (values["tp"] + values["fp"])) > 1e-9
            or abs(row["recall"] - values["tp"] / values["positives"]) > 1e-9):
        raise ValueError("Validation precision/recall contradict counts")
    return values


def _common(release, plan):
    expected = plan.to_dict()
    if release.get("release_version") != RELEASE_V2 or release.get("status") != "Approved":
        raise ValueError("An approved oos_test_release.v2 record is required")
    if release.get("approval_mode") not in APPROVAL_MODES or release.get("evidence_profile") not in EVIDENCE_PROFILES:
        raise ValueError("Explicit supported approval_mode and evidence_profile are required")
    if release.get("temporal_plan") != expected or release.get("plan_sha256") != expected["plan_sha256"]:
        raise ValueError("Release plan differs from the canonical temporal plan")
    for key in ("dataset_hash", "source_build_id", "source_database_sha256"):
        if release.get(key) != expected["source"][key]:
            raise ValueError("Release source identity differs from the plan: " + key)
    if release.get("test_window") != expected["test"]:
        raise ValueError("Release test window must equal the plan's reserved test window")
    if (release.get("label_version") != "oos_next7.complete_window.v1" or release.get("label_policy") != "complete_window"
            or release.get("feature_version") != "oos_features.v1-complete"):
        raise ValueError("Unsupported label/feature policy in release")
    if release.get("exposure_mode") not in ("independent_test", "holdout_confirmation"):
        raise ValueError("Explicit exposure_mode required")
    if not _nonblank(release.get("exposure_ledger_id")):
        raise ValueError("Release must name the initialized exposure ledger")


_MAIN = ("lightgbm", "xgboost", "random_forest")
_BASELINE = "logistic_regression"
_TRIAL_STATUSES = ("Complete", "Failed", "Timeout", "Calibration_invalid", "Convergence_failed", "Resource_exhausted")


def _close(a, b, tolerance=1e-9):
    return _number(a) and _number(b) and abs(a - b) <= tolerance


def _reconcile_overall(row, what):
    """Counts/metrics of one result row must be internally consistent (no target applied)."""
    values = {}
    for key in ("rows", "positives", "tp", "fp", "fn", "tn", "alerts"):
        value = row.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"{what}: counts must be nonnegative integers")
        values[key] = value
    if (values["tp"] + values["fp"] + values["fn"] + values["tn"] != values["rows"]
            or values["tp"] + values["fn"] != values["positives"] or values["tp"] + values["fp"] != values["alerts"]):
        raise ValueError(f"{what}: confusion counts do not reconcile")
    for metric, numerator, denominator in (("precision", values["tp"], values["tp"] + values["fp"]),
                                           ("recall", values["tp"], values["positives"])):
        value = row.get(metric)
        if denominator == 0:
            if value is not None:
                raise ValueError(f"{what}: undefined {metric} must be null")
        elif not _close(value, numerator / denominator) or type(value) is bool:
            raise ValueError(f"{what}: {metric} contradicts counts")
    for metric in ("roc_auc", "average_precision"):
        value = row.get(metric)
        if value is not None and (type(value) is bool or not _number(value) or not 0 <= value <= 1):
            raise ValueError(f"{what}: {metric} invalid")
    return values


def _in_space(value, kind, bounds):
    if type(value) is bool:
        return False
    if kind == "categorical":
        return any(type(value) in (int, float) and type(v) in (int, float) and value == v for v in bounds)
    if kind in ("float", "log_float"):
        return _number(value) and bounds[0] <= value <= bounds[1]
    if kind == "int":
        return type(value) is int and bounds[0] <= value <= bounds[1]
    return False


def _min_supported_recall(results, rules):
    values = [r.get("recall") for r in results if r.get("group_kind") == "segment"
              and r.get("rows", 0) >= rules["segment_min_rows"] and r.get("positives", 0) >= rules["segment_min_each_class"]
              and r.get("rows", 0) - r.get("positives", 0) >= rules["segment_min_each_class"] and r.get("recall") is not None]
    return min(values) if values else None


def _check_benchmark(benchmark, bundle, rules, *, synthetic_profile_allowed):
    """Independently re-derive completeness, budgets and the selected winner from trial records."""
    if benchmark.get("version") != "module2.benchmark_report.v1" or benchmark.get("run_id") != bundle.get("run_id"):
        raise ValueError("Benchmark report identity differs from the bundle run")
    approved = rules.get("budget_v3")
    budget = benchmark.get("budget")
    if not isinstance(approved, dict) or not isinstance(budget, dict) or set(budget) != set(approved):
        raise ValueError("Benchmark budget missing or malformed")
    if benchmark.get("profile") == "v3":
        if budget != approved:
            raise ValueError("Benchmark budget differs from the approved v3 budget")
    elif benchmark.get("profile") == "synthetic" and synthetic_profile_allowed:
        if any(budget[k] > approved[k] for k in approved if k != "min_complete_trials") \
                or budget["min_complete_trials"] != approved["min_complete_trials"]:
            raise ValueError("Synthetic budget may only shrink limits and must keep the completeness minimum")
    else:
        raise ValueError("Unsupported benchmark profile for this release")
    trials = benchmark.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("Benchmark has no trial evidence")
    ids = [t.get("trial_id") for t in trials]
    if len(set(ids)) != len(ids) or not all(_nonblank(i) for i in ids):
        raise ValueError("Benchmark trial identities are missing or duplicated")
    spaces, grid = rules["search_spaces"], rules["baseline_grid"]
    complete = {a: [] for a in rules["algorithms"]}
    for trial in trials:
        algorithm, params, status = trial.get("algorithm"), trial.get("params"), trial.get("status")
        if algorithm not in rules["algorithms"] or status not in _TRIAL_STATUSES or not isinstance(params, dict):
            raise ValueError("Benchmark trial algorithm/status/parameters malformed")
        if algorithm == _BASELINE:
            if params not in grid:
                raise ValueError("Baseline parameters outside the approved grid")
        else:
            space = spaces[algorithm]
            if set(params) != set(space) or not all(_in_space(params[k], *space[k]) for k in space):
                raise ValueError("Trial parameters outside the approved search space")
        if status != "Complete":
            continue
        for fold in ("dev1", "dev2"):
            part = trial.get(fold) or {}
            results = part.get("results")
            overall = [r for r in results or [] if r.get("group_kind") == "all"]
            if len(overall) != 1 or results[0] is not overall[0]:
                raise ValueError("Complete trials need exactly one leading overall result per fold")
            _reconcile_overall(overall[0], f"{trial['trial_id']} {fold}")
        seconds = trial.get("fit_seconds") or {}
        if (set(seconds) != {"rolling_dev_1", "rolling_dev_2"} or not all(_number(v) and v >= 0 for v in seconds.values())
                or not _close(trial.get("total_fit_seconds"), sum(seconds.values()), 1e-6)):
            raise ValueError("Complete trials need both fold fit times")
        if not _number(trial.get("wall_seconds")) or trial["wall_seconds"] > budget["trial_seconds"]:
            raise ValueError("Complete trial exceeds the per-trial budget")
        calibrator, thresholds = trial.get("calibrator") or {}, trial.get("thresholds") or {}
        if (calibrator.get("method") != "sigmoid" or not _number(calibrator.get("coef")) or calibrator["coef"] <= 0
                or not _number(calibrator.get("intercept"))):
            raise ValueError("Complete trial calibrator invalid")
        values = [thresholds.get("global"), *(thresholds.get("segments") or {}).values()]
        if not all(_number(v) and type(v) is not bool and 0 <= v <= 1 for v in values):
            raise ValueError("Complete trial thresholds invalid")
        complete[algorithm].append(trial)
    for algorithm in _MAIN:
        mine = [t for t in trials if t["algorithm"] == algorithm]
        if len(mine) > budget["max_trials"] or len(complete[algorithm]) < budget["min_complete_trials"]:
            raise ValueError(f"Comparison incomplete or over budget for {algorithm}")
        progress = (benchmark.get("study_progress") or {}).get(algorithm) or {}
        if not _number(progress.get("elapsed_seconds")) or progress["elapsed_seconds"] > budget["study_seconds"] + budget["trial_seconds"]:
            raise ValueError(f"Study time evidence missing or over budget for {algorithm}")
    if len([t for t in trials if t["algorithm"] == _BASELINE]) > len(grid):
        raise ValueError("Too many baseline trials")
    forest = sorted((t for t in trials if t["algorithm"] == "random_forest"), key=lambda t: t["trial_id"])
    starts = rules["rf_starting_trials"]
    if [t["params"] for t in forest[:len(starts)]] != starts[:len(forest)]:
        raise ValueError("RandomForest did not start with the approved fixed trials")
    if not _number(benchmark.get("ml_seconds_used")) or benchmark["ml_seconds_used"] > budget["total_ml_seconds"]:
        raise ValueError("Benchmark ML time evidence missing or over budget")
    targets = rules["targets"]
    feasible = []
    for trial in (t for group in complete.values() for t in group):
        o = trial["dev2"]["results"][0]
        if (o.get("precision") is not None and o.get("recall") is not None and o.get("roc_auc") is not None
                and o["recall"] >= targets["recall"] and o["roc_auc"] >= targets["roc_auc"]):
            segment = _min_supported_recall(trial["dev2"]["results"], rules)
            feasible.append(((-o["precision"], -(segment if segment is not None else -1.0),
                              -(o.get("average_precision") or 0.0), trial["total_fit_seconds"],
                              trial["algorithm"], trial["trial_id"]), trial))
    if not feasible:
        raise ValueError("No feasible development candidate exists")
    winner = min(feasible, key=lambda pair: pair[0])[1]
    selection = benchmark.get("selection") or {}
    if (benchmark.get("status") != "Complete" or selection.get("benchmark_status") != "Complete"
            or selection.get("selection_status") != "Selected" or selection.get("winner") != winner["trial_id"]
            or selection.get("incomplete_algorithms")):
        raise ValueError("Declared benchmark selection differs from the recomputed winner")
    if (bundle.get("candidate_id") != winner["trial_id"] or bundle.get("algorithm") != winner["algorithm"]
            or bundle.get("params") != winner["params"]):
        raise ValueError("Bundle is not the recomputed development winner")
    return winner


def _v2_evidence(location, release, plan):
    expected = plan.to_dict()
    roles = ("bundle_manifest", "model", "model_manifest", "preprocessor", "calibration", "thresholds",
             "validation_report", "validation_predictions", "validation_access", "benchmark_report",
             "training_inputs_manifest", "validation_inputs_manifest")
    evidence = release.get("evidence") or {}
    if sorted(evidence) != sorted(roles):
        raise ValueError("Evidence must contain exactly the supported v2 roles")
    paths = {role: _load(location, evidence[role], role) for role in roles}
    sha = {role: evidence[role]["sha256"] for role in roles}
    bundle = _json(paths["bundle_manifest"])
    if (bundle.get("version") != "module2.model_bundle.v1" or bundle.get("status") != "Frozen_for_validation"
            or bundle.get("temporal_plan") != expected or bundle.get("feature_version") != release["feature_version"]
            or bundle.get("cohort") != release.get("cohort") or bundle.get("threshold_policy") != release.get("threshold_policy")
            or bundle.get("label_version") != release["label_version"]):
        raise ValueError("Bundle manifest identity differs from the release")
    for key in ("dataset_hash", "source_build_id", "source_database_sha256"):
        if bundle.get("source", {}).get(key) != expected["source"][key]:
            raise ValueError("Bundle source differs from the plan")
    for role in ("model", "model_manifest", "preprocessor", "calibration", "thresholds"):
        if bundle.get("components", {}).get(role, {}).get("sha256") != sha[role]:
            raise ValueError("Bundle component differs from release evidence: " + role)
    thresholds = _json(paths["thresholds"])
    values = [thresholds.get("global"), *(thresholds.get("segments") or {}).values()]
    if not values or not all(_number(v) and type(v) is not bool and 0 <= v <= 1 for v in values):
        raise ValueError("Frozen thresholds must be finite probabilities")
    if bundle.get("training", {}).get("inputs_manifest_sha256") != sha["training_inputs_manifest"]:
        raise ValueError("Bundle training inputs differ from evidence")
    training = _json(paths["training_inputs_manifest"])
    fold = next(row for row in plan.development_folds() if row[0] == "rolling_validation")
    if (training.get("partition") != "train" or training.get("status") != "Accepted" or training.get("test") != "Locked"
            or training.get("scoring_end") != fold[1] or training.get("outcome_end") != expected["training"]["outcome_end"]):
        raise ValueError("Training inputs are not the plan's outer training partition")
    rules = bundle.get("rules")
    if not isinstance(rules, dict) or _hashlib.sha256(_canon(rules).encode()).hexdigest() != bundle.get("config_sha256"):
        raise ValueError("Bundle rules are missing or do not match its config_sha256")
    if bundle.get("selection", {}).get("benchmark_report_sha256") != sha["benchmark_report"]:
        raise ValueError("Bundle does not reference this benchmark report")
    winner = _check_benchmark(_json(paths["benchmark_report"]), bundle, rules,
                              synthetic_profile_allowed=bool(release.get("synthetic")))
    calibration = _json(paths["calibration"])
    if {k: calibration.get(k) for k in ("method", "coef", "intercept")} != \
            {k: winner["calibrator"].get(k) for k in ("method", "coef", "intercept")}:
        raise ValueError("Bundle calibrator differs from the selected trial")
    if thresholds.get("global") != winner["thresholds"].get("global") \
            or (thresholds.get("segments") or {}) != (winner["thresholds"].get("segments") or {}):
        raise ValueError("Bundle thresholds differ from the selected trial")
    code = bundle.get("code_hashes")
    if not isinstance(code, dict) or not code or not all(_nonblank(k) and _is_sha(v) for k, v in code.items()):
        raise ValueError("Bundle code identity missing or malformed")
    report = _json(paths["validation_report"])
    access = _json(paths["validation_access"])
    valid_inputs = _json(paths["validation_inputs_manifest"])
    if (report.get("version") != "module2.evaluation_report.v2" or report.get("kind") != "iterative_validation"
            or report.get("status") != "Evaluated" or report.get("bundle_manifest_sha256") != sha["bundle_manifest"]
            or report.get("model_sha256") != sha["model"] or report.get("plan_sha256") != expected["plan_sha256"]
            or report.get("dataset_hash") != expected["source"]["dataset_hash"] or report.get("cohort") != release.get("cohort")
            or report.get("execution", {}).get("access_marker_sha256") != sha["validation_access"]
            or report.get("execution", {}).get("inputs_manifest_sha256") != sha["validation_inputs_manifest"]
            or report.get("execution", {}).get("predictions_sha256") != sha["validation_predictions"]
            or access.get("bundle_manifest_sha256") != sha["bundle_manifest"]
            or valid_inputs.get("partition") != "validation" or valid_inputs.get("status") != "Accepted"
            or [valid_inputs.get("scoring_start"), valid_inputs.get("scoring_end"), valid_inputs.get("outcome_end")]
            != list(fold[2:5])):
        raise ValueError("Validation evidence does not belong to this frozen bundle and plan")
    results = report.get("results")
    overall = [r for r in results or [] if r.get("group_kind") == "all"]
    if len(overall) != 1 or results[0] is not overall[0]:
        raise ValueError("Exactly one leading overall validation result is required")
    eligible, candidates = report.get("eligible_rows"), report.get("candidate_rows")
    if type(eligible) is not int or type(candidates) is not int or not 0 < eligible <= candidates:
        raise ValueError("Validation eligible/candidate counts are invalid")
    if not _number(report.get("label_coverage")) or abs(report["label_coverage"] - eligible / candidates) > 1e-12:
        raise ValueError("Validation label coverage contradicts counts")
    _check_counts(overall[0], eligible)
    if report.get("numeric_gate_passed") is not True or overall[0].get("meets_numeric_targets") is not True:
        raise ValueError("Validation numeric gate did not pass")
    times = {"fit_started_at": _when(bundle.get("fit_started_at"), "fit_started_at"),
             "frozen_at": _when(bundle.get("frozen_at"), "frozen_at"),
             "access_at": _when(access.get("at"), "validation access"),
             "evaluated_at": _when(report.get("evaluated_at"), "evaluated_at")}
    if not times["fit_started_at"] < times["frozen_at"] < times["access_at"] < times["evaluated_at"]:
        raise ValueError("Fit/freeze/validation-access/evaluation chronology is invalid")
    identity = {"model_version": bundle.get("model_version"), "candidate_id": bundle.get("candidate_id"),
                "algorithm": bundle.get("algorithm"), "model_sha256": sha["model"],
                "bundle_manifest_sha256": sha["bundle_manifest"], "config_sha256": bundle.get("config_sha256"),
                "code_identity_sha256": _hashlib.sha256(_canon(code).encode()).hexdigest()}
    return sha, times, identity, bundle, paths


def _legacy_evidence(location, release, plan):
    """Explicit adapter for the earlier frozen r2 evidence; manual approval only."""
    expected = plan.to_dict()
    roles = ("policy", "candidate_manifest", "model", "categories", "training_inputs_manifest",
             "validation_inputs_manifest", "validation_access", "validation_report", "validation_predictions")
    evidence = release.get("evidence") or {}
    if sorted(evidence) != sorted(roles):
        raise ValueError("Evidence must contain exactly the supported legacy roles")
    paths = {role: _load(location, evidence[role], role) for role in roles}
    sha = {role: evidence[role]["sha256"] for role in roles}
    policy, candidate = _json(paths["policy"]), _json(paths["candidate_manifest"])
    training, valid_inputs = _json(paths["training_inputs_manifest"]), _json(paths["validation_inputs_manifest"])
    access, report = _json(paths["validation_access"]), _json(paths["validation_report"])
    fold = next(row for row in plan.development_folds() if row[0] == "rolling_validation")
    if (policy.get("version") != "module2.outer_validation_policy.v1" or policy.get("status") != "Frozen_for_validation"
            or policy.get("policy") != release.get("threshold_policy") or policy.get("cohort") != release.get("cohort")
            or policy.get("test_access") != "Locked" or policy.get("temporal_plan") != expected):
        raise ValueError("Legacy frozen policy does not match the release")
    thresholds = policy.get("thresholds") or {}
    values = [thresholds.get("global"), *(thresholds.get("segments") or {}).values()]
    if not all(_number(v) and type(v) is not bool and 0 <= v <= 1 for v in values):
        raise ValueError("Legacy thresholds must be finite probabilities")
    if (candidate.get("version") != "module2.outer_candidate.v1" or candidate.get("status") != "Frozen_for_validation"
            or candidate.get("policy_sha256") != sha["policy"] or candidate.get("model_sha256") != sha["model"]
            or candidate.get("categories_sha256") != sha["categories"]
            or candidate.get("training_manifest_sha256") != sha["training_inputs_manifest"]
            or candidate.get("features") != policy.get("features")):
        raise ValueError("Legacy candidate does not bind the policy/model/categories/training inputs")
    if (training.get("version") != "module2.outer_partition.v1" or training.get("partition") != "train"
            or training.get("status") != "Accepted" or training.get("policy_sha256") != sha["policy"]
            or training.get("test_access") != "Locked" or training.get("scoring_end") != fold[1]
            or training.get("outcome_end") != expected["training"]["outcome_end"]):
        raise ValueError("Legacy training inputs are not the plan's outer training partition")
    if (valid_inputs.get("partition") != "validation" or valid_inputs.get("status") != "Accepted"
            or valid_inputs.get("policy_sha256") != sha["policy"]
            or valid_inputs.get("candidate_manifest_sha256") != sha["candidate_manifest"]
            or access.get("policy_sha256") != sha["policy"] or access.get("candidate_manifest_sha256") != sha["candidate_manifest"]):
        raise ValueError("Legacy validation inputs/access refer to a different candidate")
    if (report.get("version") != "module2.outer_validation_report.v1" or report.get("status") != "Evaluated_not_promoted"
            or report.get("policy_sha256") != sha["policy"] or report.get("candidate_manifest_sha256") != sha["candidate_manifest"]
            or report.get("model_sha256") != sha["model"] or report.get("temporal_plan") != expected
            or report.get("validation_inputs_sha256") != sha["validation_inputs_manifest"]
            or report.get("predictions_sha256") != sha["validation_predictions"]
            or report.get("test_access") != "Locked" or report.get("publication") != "Not_authorized"
            or report.get("cohort") != release.get("cohort") or report.get("policy") != release.get("threshold_policy")):
        raise ValueError("Legacy validation report does not match the frozen evidence")
    overall = [r for r in report.get("results", []) if r.get("scope") == "all"]
    if len(overall) != 1:
        raise ValueError("Exactly one overall legacy result is required")
    eligible, candidates = report.get("eligible_rows"), report.get("candidate_rows")
    if type(eligible) is not int or type(candidates) is not int or not 0 < eligible <= candidates:
        raise ValueError("Legacy eligible/candidate counts are invalid")
    if not _number(report.get("label_coverage")) or abs(report["label_coverage"] - eligible / candidates) > 1e-12:
        raise ValueError("Legacy label coverage contradicts counts")
    _check_counts(overall[0], eligible)
    times = {"fit_started_at": _when(candidate.get("started_at"), "candidate started"),
             "frozen_at": _when(candidate.get("frozen_at"), "candidate frozen"),
             "access_at": _when(access.get("at"), "validation access"),
             "evaluated_at": _when(report.get("evaluated_at"), "evaluated_at")}
    if not (_when(policy.get("created_at"), "policy created") <= times["fit_started_at"] < times["frozen_at"]
            < times["access_at"] < times["evaluated_at"]):
        raise ValueError("Legacy freeze/access/evaluation chronology is invalid")
    identity = {"model_version": None, "candidate_id": None, "algorithm": "lightgbm", "model_sha256": sha["model"],
                "bundle_manifest_sha256": sha["candidate_manifest"], "config_sha256": sha["policy"]}
    return sha, times, identity, None, paths


def authorize_rolling_test_release(release_path, temporal_plan, *, now=None):
    """Read-only v2 release validation; returns a JSON-safe authorization."""
    from .temporal_plan import TemporalPlan
    location = Path(release_path).resolve()
    release = _json(location)
    plan = TemporalPlan.from_dict(temporal_plan)
    _common(release, plan)
    now = now or datetime.now(_timezone.utc)
    if release["evidence_profile"] == "module2.evaluation_report.v2":
        sha, times, identity, bundle, paths = _v2_evidence(location, release, plan)
    else:
        if release["approval_mode"] != "post_freeze_manual":
            raise ValueError("Legacy evidence requires post-freeze manual approval")
        sha, times, identity, bundle, paths = _legacy_evidence(location, release, plan)
    authorization = {
        "release_path": str(location), "release_sha256": digest(location), "release_version": RELEASE_V2,
        "approval_mode": release["approval_mode"], "evidence_profile": release["evidence_profile"],
        "plan_sha256": plan.to_dict()["plan_sha256"], "temporal_plan": plan.to_dict(),
        **{k: release[k] for k in ("dataset_hash", "source_build_id", "source_database_sha256", "test_window",
                                   "label_version", "feature_version", "exposure_mode", "exposure_ledger_id")},
        "cohort": release.get("cohort"), "threshold_policy": release.get("threshold_policy"),
        "evidence": sha, "evidence_sha256": _hashlib.sha256(_canon(sha).encode()).hexdigest(),
        "model_identity": identity, "chronology": {k: v.isoformat() for k, v in times.items()},
        "policy_sha256": None, "max_primary_evaluations": None, "confirmation_window": None,
    }
    if release["approval_mode"] == "post_freeze_manual":
        if any(key in release for key in ("policy", "resolution")):
            raise ValueError("Manual approval must not carry a preauthorized policy resolution")
        if not _nonblank(release.get("approved_by")):
            raise ValueError("Manual approval needs a nonblank human approver")
        approved = _when(release.get("approved_at"), "approved_at")
        if not times["evaluated_at"] < approved <= now:
            raise ValueError("Manual approval must follow validation and not be in the future")
        if release["evidence_profile"] == "module2.evaluation_report.v2" \
                and release.get("approved_code_identity") != bundle.get("code_hashes"):
            raise ValueError("Manual approval must name the frozen bundle's exact code identity")
        ack = release.get("risk_acknowledgement") or {}
        if (ack.get("validation_report_sha256") != sha["validation_report"]
                or ack.get("complete_label_coverage_reviewed") is not True or ack.get("subgroup_limits_reviewed") is not True
                or ack.get("calibration_reviewed") is not True or not _nonblank(ack.get("note"))):
            raise ValueError("Manual approval needs the explicit risk acknowledgement bound to this report")
        if release["exposure_mode"] == "holdout_confirmation":
            authorization["confirmation_window"] = release.get("confirmation_window")
        authorization["approval"] = {"approved_by": release["approved_by"], "approved_at": approved.isoformat()}
    else:
        if any(key in release for key in ("approved_by", "approved_at", "risk_acknowledgement")):
            raise ValueError("Policy resolution must not impersonate a post-freeze manual signature")
        if release["evidence_profile"] != "module2.evaluation_report.v2":
            raise ValueError("Preauthorized policy supports only the v2 evaluation evidence profile")
        policy_path = _load(location, release.get("policy"), "policy")
        policy = _json(policy_path)
        human = policy.get("human_authority") or {}
        scope = policy.get("execution_scope") or {}
        if (policy.get("version") != "owner_execution_policy.v1" or not _nonblank(human.get("approver"))
                or not _nonblank(human.get("message_reference")) or not _nonblank(human.get("message"))):
            raise ValueError("Execution policy lacks a genuine recorded human authority")
        approved = _when(human.get("approved_at"), "policy approved_at")
        expected = plan.to_dict()
        if (scope.get("dataset_hash") != expected["source"]["dataset_hash"] or scope.get("plan_sha256") != expected["plan_sha256"]
                or scope.get("source_build_id") != expected["source"]["source_build_id"]
                or scope.get("feature_version") != release["feature_version"] or scope.get("cohort") != release.get("cohort")
                or scope.get("label_version") != release["label_version"]
                or scope.get("config_sha256") != identity["config_sha256"]):
            raise ValueError("Execution policy scope differs from this release/bundle")
        limit = scope.get("max_primary_holdout_evaluations")
        if type(limit) is not int or limit != 1:
            raise ValueError("Execution policy must allow exactly one primary holdout evaluation")
        if release["exposure_mode"] not in (scope.get("exposure_modes") or []):
            raise ValueError("Exposure mode not authorized by the execution policy")
        if release["exposure_mode"] == "holdout_confirmation" and expected["test"] not in (scope.get("confirmation_windows") or []):
            raise ValueError("Confirmation window not authorized by the execution policy")
        roots = [Path(r).resolve() for r in scope.get("allowed_output_roots") or []]
        if not any(paths["bundle_manifest"].resolve().is_relative_to(r) for r in roots):
            raise ValueError("Bundle lies outside the policy's authorized output roots")
        if not scope.get("risk_acknowledgements"):
            raise ValueError("Execution policy must record the accepted risks")
        if scope.get("rules") != bundle.get("rules"):
            raise ValueError("Bundle rules differ from the policy's approved rules")
        if not isinstance(scope.get("code_identity"), dict) or scope["code_identity"] != bundle.get("code_hashes"):
            raise ValueError("Bundle code identity is not the code frozen by the execution policy")
        if bool(policy.get("synthetic")) != bool(release.get("synthetic")):
            raise ValueError("Synthetic and real evidence cannot share a release/policy")
        resolution = release.get("resolution") or {}
        resolved = _when(resolution.get("resolved_at"), "resolved_at")
        if not _nonblank(resolution.get("resolved_by")) or resolution.get("kind") != "program_resolution_not_human_signature":
            raise ValueError("Program resolution must identify itself and not claim a human signature")
        if not approved <= times["fit_started_at"] < times["frozen_at"] < times["access_at"] < times["evaluated_at"] < resolved <= now:
            raise ValueError("Policy approval/fit/freeze/access/evaluation/resolution chronology is invalid")
        authorization.update(policy_sha256=digest(policy_path), max_primary_evaluations=limit,
                             confirmation_window=expected["test"] if release["exposure_mode"] == "holdout_confirmation" else None,
                             approval={"approver": human["approver"], "approved_at": approved.isoformat(),
                                       "message_reference": human["message_reference"],
                                       "resolved_at": resolved.isoformat(), "resolved_by": resolution["resolved_by"]})
    return authorization
