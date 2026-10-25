"""Metrics, thresholds and the generic evaluation report (module2.evaluation_report.v2).

Undefined metrics (single class, zero alerts, no rows) are reported as None and
can never pass a numeric gate. Thresholds are chosen on calibrated development
scores only and are then frozen; evaluation never re-selects them.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration, config
from .contracts import Module2Error, read_json, utc_now
from .training import choose_threshold, segment_keys, supported

EVALUATION_KINDS = ("development", "iterative_validation", "holdout_confirmation",
                    "independent_test", "prospective_monitoring")


def threshold_policy(y, probability, segments):
    """Global + per channel x tier thresholds; unsupported segments fall back."""
    y, probability = np.asarray(y, dtype=int), np.asarray(probability, dtype=float)
    segments = pd.Series(segments).reset_index(drop=True)
    global_threshold = float(choose_threshold(y, probability))
    chosen, support = {}, []
    for key in sorted(set(segments)):
        mask = segments.eq(key).to_numpy()
        ok = supported(y[mask], config.SEGMENT_MIN_ROWS)
        support.append({"segment": key, "rows": int(mask.sum()), "positives": int(y[mask].sum()),
                        "own_threshold": bool(ok)})
        if ok:
            chosen[key] = float(choose_threshold(y[mask], probability[mask]))
    for value in [global_threshold, *chosen.values()]:
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise Module2Error("Thresholds must be finite probabilities")
    return {"global": global_threshold, "segments": chosen, "support": support,
            "selected_on": "rolling_dev_1", "recall_floor": config.SELECTION_RECALL_FLOOR,
            "minimum_segment_rows": config.SEGMENT_MIN_ROWS,
            "minimum_each_class": config.SEGMENT_MIN_EACH_CLASS}


def check_thresholds(policy):
    if (not isinstance(policy, dict) or not isinstance(policy.get("segments"), dict)
            or not _probability(policy.get("global"))
            or not all(isinstance(k, str) and _probability(v) for k, v in policy["segments"].items())):
        raise Module2Error("Threshold policy must contain finite probabilities")
    return policy


def _probability(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and np.isfinite(value) and 0 <= value <= 1)


def route_thresholds(policy, segments):
    check_thresholds(policy)
    keys = list(segments)
    values = np.asarray([policy["segments"].get(k, policy["global"]) for k in keys], dtype=float)
    routes = np.asarray(["segment" if k in policy["segments"] else "global_fallback" for k in keys], dtype=object)
    return values, routes


def scope_metrics(y, probability, thresholds):
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = np.asarray(y, dtype=int)
    p = np.asarray(probability, dtype=float)
    alert = p >= np.asarray(thresholds, dtype=float)
    tp = int(np.sum((y == 1) & alert)); fp = int(np.sum((y == 0) & alert))
    fn = int(np.sum((y == 1) & ~alert)); tn = int(np.sum((y == 0) & ~alert))
    two = len(np.unique(y)) == 2
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    auc = float(roc_auc_score(y, p)) if two else None
    ap = float(average_precision_score(y, p)) if two else None
    undefined = [name for name, value in (("precision", precision), ("recall", recall),
                                          ("roc_auc", auc)) if value is None]
    meets = (not undefined and precision >= config.TARGETS["precision"]
             and recall >= config.TARGETS["recall"] and auc >= config.TARGETS["roc_auc"])
    return {"rows": int(len(y)), "positives": int(y.sum()),
            "base_rate": float(y.mean()) if len(y) else None,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "alerts": int(alert.sum()),
            "precision": precision, "recall": recall, "roc_auc": auc, "average_precision": ap,
            "brier": calibration.brier(y, p), "ece": calibration.ece(y, p),
            "undefined_metrics": undefined, "meets_numeric_targets": bool(meets)}


def scopes(frame):
    """(group_kind, group_id, mask) for overall, channel x tier and current state."""
    segments = segment_keys(frame).reset_index(drop=True)
    state = current_state(frame["oos_observed"]).reset_index(drop=True)
    out = [("all", "all", np.ones(len(frame), dtype=bool))]
    out += [("segment", key, segments.eq(key).to_numpy()) for key in sorted(set(segments))]
    out += [("current_status", name, state.eq(name).to_numpy()) for name in ("not_oos", "oos", "unknown")]
    return out


def current_state(series):
    """not_oos / oos / unknown from a bool, object or nullable-boolean column."""
    values = series.astype("boolean")
    return pd.Series(np.where(values.isna(), "unknown", np.where(values.fillna(False), "oos", "not_oos")),
                     index=series.index)


def evaluate_groups(frame, probability, thresholds):
    y = frame["target_oos_7d"].to_numpy(int)
    results = []
    for kind, key, mask in scopes(frame):
        metrics = scope_metrics(y[mask], np.asarray(probability)[mask], np.asarray(thresholds)[mask])
        results.append({"group_kind": kind, "group_id": key, **metrics})
    return results


def min_supported_segment_recall(results):
    values = [r["recall"] for r in results if r["group_kind"] == "segment"
              and r["rows"] >= config.SEGMENT_MIN_ROWS and r["positives"] >= config.SEGMENT_MIN_EACH_CLASS
              and r["rows"] - r["positives"] >= config.SEGMENT_MIN_EACH_CLASS and r["recall"] is not None]
    return min(values) if values else None


def reconcile(results, eligible_rows):
    """Counts must add up; contradictory reports are refused, never repaired."""
    overall = [r for r in results if r["group_kind"] == "all"]
    if len(overall) != 1:
        raise Module2Error("Exactly one overall result is required")
    o = overall[0]
    if o["tp"] + o["fp"] + o["fn"] + o["tn"] != o["rows"] or o["rows"] != eligible_rows:
        raise Module2Error("Overall confusion counts do not reconcile")
    for kind in ("segment", "current_status"):
        part = [r for r in results if r["group_kind"] == kind]
        if part and sum(r["rows"] for r in part) != o["rows"]:
            raise Module2Error(f"{kind} rows do not reconcile with the overall cohort")
    return True


def build_report(*, kind, status, identity, frame, probability, thresholds, routes, candidate_rows,
                 window, limitations, execution, extra=None):
    if kind not in EVALUATION_KINDS:
        raise Module2Error("Unknown evaluation kind")
    results = evaluate_groups(frame, probability, thresholds)
    reconcile(results, len(frame))
    y = frame["target_oos_7d"].to_numpy(int)
    report = {
        "version": config.VERSIONS["evaluation"], "kind": kind, "status": status,
        **identity, "cohort": config.COHORT, "window": window,
        "candidate_rows": int(candidate_rows), "eligible_rows": int(len(frame)),
        "label_coverage": (len(frame) / candidate_rows) if candidate_rows else None,
        "targets": config.TARGETS, "results": results,
        "numeric_gate_passed": results[0]["meets_numeric_targets"],
        "threshold_routes": {"segment": int(np.sum(routes == "segment")),
                             "global_fallback": int(np.sum(routes == "global_fallback"))},
        "calibration_summary": {"method": "sigmoid", "brier": results[0]["brier"],
                                "ece": results[0]["ece"],
                                "reliability": calibration.reliability(y, probability)},
        "count_reconciliation": "passed", "limitations": list(limitations),
        "execution": execution, "evaluated_at": utc_now(),
    }
    if extra:
        report.update(extra)
    return report


def check_report(report, *, kind=None):
    """Structural check used before any report is trusted downstream."""
    if report.get("version") != config.VERSIONS["evaluation"] or report.get("kind") not in EVALUATION_KINDS:
        raise Module2Error("Not a module2.evaluation_report.v2")
    if kind and report["kind"] != kind:
        raise Module2Error(f"Expected a {kind} report")
    reconcile(report["results"], report["eligible_rows"])
    overall = report["results"][0]
    for name in ("precision", "recall", "roc_auc"):
        value = overall.get(name)
        if value is not None and not _probability(value):
            raise Module2Error("Report metric outside [0,1]")
    expected = bool(overall["meets_numeric_targets"])
    if report.get("numeric_gate_passed") is not expected:
        raise Module2Error("Report gate flag contradicts its metrics")
    return report


def json_list(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


# ----------------------------------------------------------------------------
# Lifecycle: one validation of the frozen bundle, then a conditional holdout.
# ----------------------------------------------------------------------------

def _identity(bundle, plan):
    p = plan.to_dict()
    return {"bundle_manifest_sha256": bundle.manifest_sha256, "model_version": bundle.manifest["model_version"],
            "model_sha256": bundle.manifest["components"]["model"]["sha256"],
            "candidate_id": bundle.manifest["candidate_id"], "algorithm": bundle.manifest["algorithm"],
            "plan_sha256": p["plan_sha256"], "dataset_hash": p["source"]["dataset_hash"],
            "source_build_id": p["source"]["source_build_id"], "config_sha256": bundle.manifest["config_sha256"]}


def _write_predictions(folder, frame, out):
    predictions = frame[["build_id", "scoring_date", "store_id", "sku_id", "channel", "segment_tier",
                         "oos_observed", "target_oos_7d"]].copy()
    predictions["oos_observed"] = predictions["oos_observed"].astype("boolean")
    for key in ("raw", "probability", "threshold", "route", "decision"):
        predictions[key] = out[key]
    path = Path(folder) / "predictions.parquet"
    predictions.to_parquet(path, index=False)
    return path


def validate(run_dir, workflow):
    """Evaluate the frozen bundle once on the plan's outer validation window."""
    from . import bundles, inputs
    from .contracts import sha256_file, write_json
    run_dir = Path(run_dir)
    bundle = bundles.load_bundle(run_dir / "bundle")
    if bundle.eligibility().get("status") != "Pending_validation":
        raise Module2Error("Validation runs once, only for a freshly frozen bundle")
    folder = run_dir / "evaluation" / "validation"
    folder.mkdir(parents=True, exist_ok=False)
    access = {"event": "Outer_validation_truth_access", "at": utc_now(), "run_id": bundle.manifest["run_id"],
              "bundle_manifest_sha256": bundle.manifest_sha256}
    write_json(folder / "access.json", access, exclusive=True)
    manifest = inputs.export_outer(workflow, run_dir / "inputs" / "outer-validation", "validation")
    frame = inputs.read_export(run_dir / "inputs" / "outer-validation", "validation", manifest)
    out = bundle.predict(frame)
    predictions = _write_predictions(folder, frame, out)
    limitations = [
        "iterative_validation: this May window was evaluated in earlier experiments; not an untouched holdout",
        "Metrics cover rows with seven observed future days only (see label_coverage)",
        "Calibrator/thresholds transferred from rolling_dev_1; reliability shown, not guaranteed",
    ]
    report = build_report(
        kind="iterative_validation", status="Evaluated", identity=_identity(bundle, workflow["plan"]),
        frame=frame, probability=out["probability"], thresholds=out["threshold"], routes=out["route"],
        candidate_rows=manifest["candidate_rows"],
        window={"scoring_start": manifest["scoring_start"], "scoring_end": manifest["scoring_end"],
                "outcome_end": manifest["outcome_end"]},
        limitations=limitations,
        execution={"access_at": access["at"], "access_marker_sha256": sha256_file(folder / "access.json"),
                   "inputs_manifest_sha256": sha256_file(run_dir / "inputs" / "outer-validation" / "manifest.json"),
                   "predictions_sha256": sha256_file(predictions)},
        extra={"cohort": config.COHORT, "test_access": "Locked", "publication": "Not_authorized"})
    check_report(report, kind="iterative_validation")
    write_json(folder / "report.json", report, exclusive=True)
    bundles.update_eligibility(run_dir / "bundle", "Validation_passed" if report["numeric_gate_passed"]
                               else "Validation_failed", validation_report_sha256=sha256_file(folder / "report.json"))
    return report


def write_release(run_dir, workflow, policy_path, ledger_header):
    """Program resolution of the preauthorized policy; not a human signature."""
    from .contracts import sha256_file, write_json
    run_dir = Path(run_dir)
    folder = run_dir / "holdout"
    folder.mkdir(parents=True, exist_ok=False)
    plan = workflow["plan"].to_dict()

    def ref(path):
        path = Path(path).resolve()
        return {"path": str(path.relative_to(folder.resolve(), walk_up=True)), "sha256": sha256_file(path)}

    bundle = run_dir / "bundle"
    evidence = {
        "bundle_manifest": ref(bundle / "bundle.json"),
        "model": ref(bundle / read_json(bundle / "bundle.json")["components"]["model"]["path"]),
        "model_manifest": ref(bundle / "model" / "model.json"),
        "preprocessor": ref(bundle / "preprocessor.json"), "calibration": ref(bundle / "calibration.json"),
        "thresholds": ref(bundle / "threshold-policy.json"),
        "validation_report": ref(run_dir / "evaluation" / "validation" / "report.json"),
        "validation_predictions": ref(run_dir / "evaluation" / "validation" / "predictions.parquet"),
        "validation_access": ref(run_dir / "evaluation" / "validation" / "access.json"),
        "benchmark_report": ref(run_dir / "benchmark" / "report.json"),
        "training_inputs_manifest": ref(run_dir / "inputs" / "outer-train" / "manifest.json"),
        "validation_inputs_manifest": ref(run_dir / "inputs" / "outer-validation" / "manifest.json"),
    }
    release = {
        "release_version": "oos_test_release.v2", "status": "Approved", "approval_mode": "preauthorized_policy",
        "synthetic": bool(read_json(policy_path).get("synthetic")),
        "evidence_profile": "module2.evaluation_report.v2", "exposure_mode": "holdout_confirmation",
        "temporal_plan": plan, "plan_sha256": plan["plan_sha256"],
        "dataset_hash": plan["source"]["dataset_hash"], "source_build_id": plan["source"]["source_build_id"],
        "source_database_sha256": plan["source"]["source_database_sha256"], "test_window": plan["test"],
        "feature_version": config.FEATURE_VERSION, "label_version": config.LABEL_VERSION,
        "label_policy": "complete_window", "cohort": config.COHORT, "threshold_policy": config.THRESHOLD_POLICY,
        "exposure_ledger_id": ledger_header["ledger_id"],
        "policy": {"path": str(Path(policy_path).resolve()), "sha256": sha256_file(policy_path)},
        "evidence": evidence,
        "resolution": {"kind": "program_resolution_not_human_signature", "resolved_at": utc_now(),
                       "resolved_by": "modules.module_2 deliver (engineer-run program)",
                       "run_id": read_json(bundle / "bundle.json")["run_id"]},
    }
    write_json(folder / "release.json", release, exclusive=True)
    return folder / "release.json"


def run_holdout(run_dir, workflow, policy_path):
    """One holdout_confirmation of the frozen winner through the shared gate/ledger."""
    import duckdb

    from data_platform.labels import build_labels
    from data_platform.test_release import authorize_rolling_test_release

    from . import bundles, governance, inputs
    from .contracts import sha256_file, write_json
    run_dir = Path(run_dir)
    benchmark = read_json(run_dir / "benchmark" / "report.json", "benchmark report")
    validation = check_report(read_json(run_dir / "evaluation" / "validation" / "report.json"), kind="iterative_validation")
    bundle = bundles.load_bundle(run_dir / "bundle")
    if benchmark.get("status") != "Complete":
        raise Module2Error("Benchmark_incomplete blocks the holdout")
    if (run_dir / "holdout").exists() or bundle.eligibility().get("status") in ("Eligible_for_review", "Holdout_failed"):
        raise Module2Error("The single primary holdout evaluation for this bundle was already used")
    if validation["numeric_gate_passed"] is not True or bundle.eligibility().get("status") != "Validation_passed":
        raise Module2Error("Validation did not pass; holdout is not run")
    policy = governance.check_policy(policy_path, workflow, run_dir)
    header = governance.ensure_ledger(policy, policy_path)
    release = write_release(run_dir, workflow, policy_path, header)
    plan = workflow["plan"]
    authorize_rolling_test_release(release, plan.to_dict())          # read-only pre-check
    source_dir = Path(workflow["components"]["base"]["database"]).parent
    labels = build_labels(source_dir, plan.source_as_of, run_dir / "holdout" / "labels", purpose="test",
                          temporal_plan=plan.to_dict(), test_release=release,
                          test_exposure_ledger=policy["execution_scope"]["ledger"]["path"], threads=config.THREADS)
    governance.record_ledger_tip(policy)
    database = Path(labels["directory"]) / "labels.duckdb"
    with duckdb.connect(str(database), read_only=True) as con:
        candidates = con.execute("SELECT count(*) FROM evaluation.fold_samples WHERE fold_id='rolling_test'").fetchone()[0]
        truth = con.execute("""SELECT build_id,scoring_date,store_id,sku_id,target_oos_7d FROM evaluation.fold_samples
            WHERE fold_id='rolling_test' AND partition='test' AND eligible""").df()
        pending = con.execute(f"""SELECT count(*) FROM evaluation.labels_oos_next7
            WHERE scoring_date>DATE '{plan.to_dict()['test']['calendar_eligible_end']}'""").fetchone()[0]
    test = plan.to_dict()["test"]
    _, features, meta = inputs.scoring_features(workflow, test["calendar_eligible_start"], test["calendar_eligible_end"],
                                                purpose_label="holdout_confirmation features")
    frame = truth.merge(features, on=inputs.KEYS, how="left", validate="one_to_one", indicator=True)
    if not frame["_merge"].eq("both").all():
        raise Module2Error("Eligible holdout labels lack matching feature rows")
    frame = frame.drop(columns="_merge").sort_values(inputs.KEYS[1:]).reset_index(drop=True)
    inputs.check_frame(frame, plan, test["calendar_eligible_start"], test["calendar_eligible_end"])
    out = bundle.predict(frame)
    folder = run_dir / "evaluation" / "holdout"
    folder.mkdir(parents=True, exist_ok=False)
    predictions = _write_predictions(folder, frame, out)
    report = build_report(
        kind="holdout_confirmation", status="Evaluated", identity=_identity(bundle, plan), frame=frame,
        probability=out["probability"], thresholds=out["threshold"], routes=out["route"], candidate_rows=candidates,
        window={"scoring_start": test["calendar_eligible_start"], "scoring_end": test["calendar_eligible_end"],
                "outcome_end": test["outcome_end"], "pending_start": test["pending_start"], "pending_end": test["pending_end"]},
        limitations=[
            "holdout_confirmation: the June window had earlier teammate/notebook exploration; not an untouched independent test",
            "Not used for selection, calibration or thresholds; evaluated once for this frozen bundle",
            "Metrics cover rows with seven observed future days only (see label_coverage); last seven days pending",
        ],
        execution={"release_sha256": sha256_file(release), "label_artifact_id": labels["artifact_id"],
                   "exposure_receipt": labels["test_exposure"], "pending_label_rows": int(pending),
                   "features": meta, "predictions_sha256": sha256_file(predictions)},
        extra={"cohort": config.COHORT, "publication": "Not_authorized"})
    check_report(report, kind="holdout_confirmation")
    write_json(folder / "report.json", report, exclusive=True)
    passed = report["numeric_gate_passed"] and validation["numeric_gate_passed"] and benchmark["status"] == "Complete"
    bundles.update_eligibility(run_dir / "bundle", "Eligible_for_review" if passed else "Holdout_failed",
                               holdout_report_sha256=sha256_file(folder / "report.json"))
    return report
