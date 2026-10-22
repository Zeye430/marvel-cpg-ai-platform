"""Issued-prediction monitoring, input drift and challenger requests.

Monitors only predictions actually issued by scoring runs (never retrospective
evaluation predictions re-stamped as history). Outcomes come later from
reported facts with the same complete seven-observed-day rule; immature,
partial and unknown outcomes are counted separately. Thresholds are fixed
business heuristics, not significance tests. A trigger produces a challenger
request; it never replaces the champion automatically.
"""

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, evaluation
from .contracts import Module2Error, read_json, sha256_file, utc_now, write_json
from .preprocessing import _numeric

MONITOR_COLUMNS = ["dataset_hash", "source_build_id", "monitoring_run_id", "model_version", "observation_cutoff",
                   "window_start", "window_end", "issued_count", "eligible_count", "pending_count",
                   "unknown_outcome_count", "tp", "fp", "fn", "tn", "alert_count", "precision", "recall", "roc_auc",
                   "average_precision", "label_coverage", "brier_score", "ece", "performance_status", "drift_status",
                   "retrain_status", "drift_json", "reasons_json", "issued_runs_json", "report_sha256", "schema_version"]


# ---------------------------------------------------------------- issued history

def record_issued(scoring_dir, history_root, *, synthetic=False):
    """Append one scoring run to the issued history; identical re-runs are no-ops."""
    from .scoring import read_package
    manifest, predictions, _ = read_package(scoring_dir)
    history_root = Path(history_root)
    index_path = history_root / "issued-index.jsonl"
    entries = [json.loads(line) for line in index_path.read_text().splitlines()] if index_path.exists() else []
    package_sha = sha256_file(Path(scoring_dir) / "manifest.json")
    existing = [e for e in entries if e["scoring_run_id"] == manifest["scoring_run_id"]]
    if existing:
        if existing[0]["scoring_manifest_sha256"] != package_sha:
            raise Module2Error("A different package already used this scoring run id")
        return existing[0]
    if any(e["scoring_date"] == manifest["scoring_date"] and e["model_version"] == manifest["bundle"]["model_version"]
           and e["dataset_hash"] == manifest["source"]["dataset_hash"] for e in entries):
        raise Module2Error("This model already issued predictions for this source date; refusing duplicates")
    issued = predictions[["scoring_run_id", "scoring_date", "store_id", "sku_id", "probability", "threshold",
                          "is_predicted_oos", "model_version", "input_status"]].copy()
    target = history_root / "issued" / f"{manifest['scoring_run_id']}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    issued.to_parquet(target, index=False)
    entry = {"version": config.VERSIONS["issued"], "scoring_run_id": manifest["scoring_run_id"],
             "scoring_date": manifest["scoring_date"], "model_version": manifest["bundle"]["model_version"],
             "bundle_manifest_sha256": manifest["bundle"]["manifest_sha256"],
             "dataset_hash": manifest["source"]["dataset_hash"], "source_build_id": manifest["source"]["source_build_id"],
             "rows": int(len(issued)), "file": target.name, "sha256": sha256_file(target),
             "scoring_manifest_sha256": package_sha, "issued_at": manifest["created_at"], "synthetic": bool(synthetic)}
    with index_path.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def issued_history(history_root):
    history_root = Path(history_root)
    index_path = history_root / "issued-index.jsonl"
    if not index_path.exists():
        return [], pd.DataFrame()
    entries = [json.loads(line) for line in index_path.read_text().splitlines()]
    frames = []
    for e in entries:
        path = history_root / "issued" / e["file"]
        if sha256_file(path) != e["sha256"]:
            raise Module2Error("Issued history file changed")
        frames.append(pd.read_parquet(path))
    return entries, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------- outcomes

def outcomes(issued, daily, cutoff):
    """Complete seven-observed-day outcome per issued row from reported facts."""
    if issued.empty:
        return issued.assign(outcome_status=pd.Series(dtype=str), target=pd.Series(dtype="Int64"))
    daily = daily[["date", "store_id", "sku_id", "oos_observed"]].copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    issued = issued.copy()
    issued["scoring_date"] = pd.to_datetime(issued["scoring_date"]).dt.date
    future = issued[["scoring_run_id", "scoring_date", "store_id", "sku_id"]].merge(daily, on=["store_id", "sku_id"])
    future = future[(future["date"] > future["scoring_date"])
                    & (future["date"] <= future["scoring_date"] + timedelta(days=config.HORIZON_DAYS))
                    & (future["date"] <= cutoff) & future["oos_observed"].notna()]
    summary = future.groupby(["scoring_run_id", "store_id", "sku_id"]).agg(
        observed=("date", "nunique"), any_oos=("oos_observed", lambda s: bool(s.astype(bool).any()))).reset_index()
    out = issued.merge(summary, on=["scoring_run_id", "store_id", "sku_id"], how="left")
    out["observed"] = out["observed"].fillna(0).astype(int)
    out["any_oos"] = out["any_oos"].astype("boolean")
    mature = out["scoring_date"].map(lambda d: d + timedelta(days=config.HORIZON_DAYS) <= cutoff)
    out["outcome_status"] = np.where(~mature, "Pending",
                                     np.where(out["observed"] == config.HORIZON_DAYS, "Complete", "Unknown_partial"))
    out["target"] = pd.array(np.where(out["outcome_status"] == "Complete", out["any_oos"].fillna(False).astype(int), -1),
                             dtype="Int64")
    out.loc[out["outcome_status"] != "Complete", "target"] = pd.NA
    return out


# ---------------------------------------------------------------- drift

def psi(reference, current):
    eps = config.MONITORING["psi_smoothing"]
    r = np.asarray(reference, dtype=float) + eps
    c = np.asarray(current, dtype=float) + eps
    r, c = r / r.sum(), c / c.sum()
    return float(np.sum((c - r) * np.log(c / r)))


def drift(reference, frame):
    rows = int(len(frame))
    report = {"reference_rows": reference["rows"], "current_rows": rows, "numeric": {}, "categorical": {},
              "segment_mix": {}}
    reasons = []
    for name, ref in reference["numeric"].items():
        values = _numeric(frame[name], name)
        missing = float(np.isnan(values).mean()) if rows else 0.0
        observed = values[~np.isnan(values)]
        edges = np.asarray(ref["edges"])
        counts = np.bincount(np.searchsorted(edges, observed, side="right"), minlength=len(edges) + 1) if len(edges) \
            else np.asarray([len(observed)])
        current = list(counts / rows) + [missing] if rows else [0.0] * (len(edges) + 2)
        value = psi(list(ref["bin_share"]) + [ref["missing_share"]], current)
        increase = missing - ref["missing_share"]
        report["numeric"][name] = {"psi": value, "missing_rate": missing, "reference_missing_rate": ref["missing_share"],
                                   "missing_rate_increase": increase}
        if value >= config.MONITORING["psi_warning"]:
            reasons.append(f"PSI {value:.3f} on {name}")
        if increase >= config.MONITORING["missing_rate_increase"]:
            reasons.append(f"Missing rate +{increase:.1%} on {name}")
    for name, ref in reference["categorical"].items():
        values = frame[name].astype("string")
        present = values.dropna()
        unknown = float((~present.isin(list(ref["levels"]))).mean()) if len(present) else 0.0
        missing = float(values.isna().mean()) if rows else 0.0
        report["categorical"][name] = {"unknown_category_rate": unknown, "missing_rate": missing,
                                       "reference_missing_rate": ref["missing_share"]}
        if unknown >= config.MONITORING["unknown_category_rate"]:
            reasons.append(f"Unknown category rate {unknown:.1%} on {name}")
        if missing - ref["missing_share"] >= config.MONITORING["missing_rate_increase"]:
            reasons.append(f"Missing rate +{missing - ref['missing_share']:.1%} on {name}")
    from .training import segment_keys
    report["segment_mix"] = {"current": segment_keys(frame).value_counts(normalize=True).to_dict() if rows else {},
                             "reference": reference["segment_mix"]}
    worst = max((v["psi"] for v in report["numeric"].values()), default=0.0)
    if rows < config.MONITORING["drift_min_rows"] or reference["rows"] < config.MONITORING["drift_min_rows"]:
        status = "Insufficient_support"
        reasons = []
    elif worst >= config.MONITORING["psi_severe"]:
        status = "Severe"
    elif reasons:
        status = "Warning"
    else:
        status = "Stable"
    report["max_psi"] = worst
    return status, reasons, report


# ---------------------------------------------------------------- monitoring run

def monitor(*, history_root, daily, observation_cutoff, current_features, bundle, source, output_dir, synthetic=False):
    """Build a monitoring package for every model version present in issued history."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise Module2Error("Monitoring packages are immutable")
    entries, issued = issued_history(history_root)
    run_id = f"monitor-{observation_cutoff.isoformat()}-{uuid.uuid4().hex[:8]}"
    window_end = observation_cutoff - timedelta(days=config.HORIZON_DAYS)
    window_start = window_end - timedelta(days=config.MONITORING["window_days"] - 1)
    drift_status, drift_reasons, drift_report = drift(
        json.loads((bundle.directory / bundle.manifest["components"]["drift_reference"]["path"]).read_text()),
        current_features)
    versions = sorted({e["model_version"] for e in entries}) or [bundle.manifest["model_version"]]
    rows = []
    for version in versions:
        mine = issued[issued["model_version"] == version] if len(issued) else issued
        scored = outcomes(mine, daily, observation_cutoff)
        in_window = scored[(scored["scoring_date"] >= window_start) & (scored["scoring_date"] <= window_end)] \
            if len(scored) else scored
        complete = in_window[in_window["outcome_status"] == "Complete"] if len(in_window) else in_window
        pending = int((scored["outcome_status"] == "Pending").sum()) if len(scored) else 0
        unknown = int((in_window["outcome_status"] == "Unknown_partial").sum()) if len(in_window) else 0
        metrics = {k: None for k in ("tp", "fp", "fn", "tn", "alerts", "precision", "recall", "roc_auc",
                                     "average_precision", "brier", "ece")}
        reasons = list(drift_reasons) if version == bundle.manifest["model_version"] else []
        if len(complete) == 0:
            performance = "Pending_outcomes"
        else:
            metrics = evaluation.scope_metrics(complete["target"].astype(int), complete["probability"], complete["threshold"])
            enough = (metrics["rows"] >= config.MONITORING["min_rows"] and metrics["positives"] >= config.MONITORING["min_positives"]
                      and metrics["alerts"] >= config.MONITORING["min_alerts"])
            performance = "Evaluated" if enough else "Insufficient_support"
            if enough and not metrics["meets_numeric_targets"]:
                reasons.append("Matured issued-prediction performance below P>=.75/R>=.70/AUC>=.80 targets")
        triggered = bool(reasons) and (drift_status in ("Warning", "Severe") or performance == "Evaluated")
        rows.append({"dataset_hash": source["dataset_hash"], "source_build_id": source["source_build_id"],
                     "monitoring_run_id": run_id, "model_version": version, "observation_cutoff": observation_cutoff,
                     "window_start": window_start, "window_end": window_end,
                     "issued_count": int(len(mine)), "eligible_count": int(len(complete)), "pending_count": pending,
                     "unknown_outcome_count": unknown, "tp": metrics["tp"], "fp": metrics["fp"], "fn": metrics["fn"],
                     "tn": metrics["tn"], "alert_count": metrics["alerts"], "precision": metrics["precision"],
                     "recall": metrics["recall"], "roc_auc": metrics["roc_auc"],
                     "average_precision": metrics["average_precision"],
                     "label_coverage": (len(complete) / len(in_window)) if len(in_window) else None,
                     "brier_score": metrics["brier"], "ece": metrics["ece"], "performance_status": performance,
                     "drift_status": drift_status if version == bundle.manifest["model_version"] else "Not_assessed",
                     "retrain_status": "Challenger_requested" if triggered else "Not_requested",
                     "drift_json": json.dumps(drift_report if version == bundle.manifest["model_version"] else {},
                                              sort_keys=True, default=float),
                     "reasons_json": json.dumps(reasons),
                     "issued_runs_json": json.dumps(sorted(e["scoring_run_id"] for e in entries if e["model_version"] == version)),
                     "report_sha256": None, "schema_version": config.VERSIONS["monitoring"]})
    output_dir.mkdir(parents=True)
    report = {"version": "module2.monitoring_report.v1", "monitoring_run_id": run_id, "synthetic": bool(synthetic),
              "observation_cutoff": observation_cutoff.isoformat(), "rows": rows, "created_at": utc_now(),
              "note": "Monitoring of issued predictions; not the release holdout. Heuristic thresholds, not significance tests."}
    write_json(output_dir / "report.json", report)
    sha = sha256_file(output_dir / "report.json")
    frame = pd.DataFrame(rows).reindex(columns=MONITOR_COLUMNS)
    frame["report_sha256"] = sha
    for column in ("issued_count", "eligible_count", "pending_count", "unknown_outcome_count", "tp", "fp", "fn", "tn",
                   "alert_count"):
        frame[column] = frame[column].astype("Int64")
    for column in ("precision", "recall", "roc_auc", "average_precision", "label_coverage", "brier_score", "ece"):
        frame[column] = frame[column].astype("Float64")
    frame.to_parquet(output_dir / "model_monitoring.parquet", index=False)
    requests = [r for r in rows if r["retrain_status"] == "Challenger_requested"]
    if requests:
        write_json(output_dir / "challenger-request.json", {
            "status": "Challenger_requested", "monitoring_run_id": run_id, "created_at": utc_now(),
            "requests": [{"model_version": r["model_version"], "reasons": json.loads(r["reasons_json"]),
                          "window": [str(r["window_start"]), str(r["window_end"])], "eligible_count": r["eligible_count"],
                          "label_coverage": r["label_coverage"]} for r in requests],
            "action": "Run the same multi-algorithm benchmark under a newly approved scope; the champion is not replaced automatically"})
    files = {n: sha256_file(output_dir / n) for n in ("report.json", "model_monitoring.parquet")}
    write_json(output_dir / "manifest.json", {"version": "module2.monitoring_package.v1", "status": "Accepted",
                                              "monitoring_run_id": run_id, "synthetic": bool(synthetic),
                                              "source": {"dataset_hash": source["dataset_hash"],
                                                         "source_build_id": source["source_build_id"]},
                                              "observation_cutoff": observation_cutoff.isoformat(),
                                              "bundle_manifest_sha256": bundle.manifest_sha256,
                                              "model_version": bundle.manifest["model_version"],
                                              "challenger_requested": bool(requests), "files": files,
                                              "created_at": utc_now()}, exclusive=True)
    return report
