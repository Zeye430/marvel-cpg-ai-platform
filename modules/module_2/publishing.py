"""Aggregated model-health packages and the call into the shared output publisher.

Health rows are projected from the actual benchmark and evaluation reports
(every trial and fold, not just the winner). They carry counts and metrics
only: no row-level targets, file paths or model bytes.
"""

import json
import shutil
from datetime import date
from pathlib import Path

from . import config
from .contracts import Module2Error, read_json, sha256_file, utc_now, write_json

from data_platform.model_outputs import HEALTH_COLUMNS, derive_model_health  # noqa: E402  single implementation


def model_outputs_status(eligibility, synthetic):
    if eligibility == "Eligible_for_review":
        return "Available"
    if synthetic and eligibility in ("Validation_passed", "Holdout_failed"):
        return "Available"   # explicitly labelled synthetic demonstration; failure stays visible
    return {"Validation_failed": "Unavailable_validation_failed", "Holdout_failed": "Unavailable_holdout_failed",
            "Validation_passed": "Unavailable_holdout_not_run"}.get(eligibility, "Unavailable_model_not_released")


def evaluation_plan(run_dir, fallback):
    """Plan under which this run's model was developed and evaluated (not a later scoring source)."""
    from data_platform.temporal_plan import TemporalPlan
    config_path = Path(run_dir) / "config.json"
    recorded = read_json(config_path).get("plan") if config_path.exists() else None
    return TemporalPlan.from_dict(recorded) if recorded else fallback


def build_health_package(run_dir, plan, output_dir, *, synthetic):
    """Package the evaluation evidence; rows are derived from it by the shared publisher's function."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise Module2Error("Health packages are immutable")
    run_dir = Path(run_dir)
    plan = evaluation_plan(run_dir, plan)
    eligibility_path = run_dir / "bundle" / "eligibility.json"
    eligibility = read_json(eligibility_path)["status"] if eligibility_path.exists() else "No_bundle"
    benchmark = read_json(run_dir / "benchmark" / "report.json")
    bundle_path = run_dir / "bundle" / "bundle.json"
    bundle = read_json(bundle_path) if bundle_path.exists() else None
    output_dir.mkdir(parents=True)
    evidence, files = {}, {}
    copies = (("bundle_manifest", bundle_path, "bundle.json"),
              ("benchmark_report", run_dir / "benchmark" / "report.json", "benchmark-report.json"),
              ("validation_report", run_dir / "evaluation" / "validation" / "report.json", "validation-report.json"),
              ("holdout_report", run_dir / "evaluation" / "holdout" / "report.json", "holdout-report.json"))
    for role, source, name in copies:
        if source.exists():
            shutil.copyfile(source, output_dir / name)       # byte-identical evidence copies
            files[name] = sha256_file(output_dir / name)
            evidence[role] = {"file": name, "sha256": files[name]}
    p = plan.to_dict()
    manifest = {"version": "module2.health_package.v1", "status": "Accepted", "synthetic": bool(synthetic),
                "eligibility_status": eligibility, "benchmark_status": benchmark["status"],
                "model_outputs_status": model_outputs_status(eligibility, synthetic),
                "bundle": None if bundle is None else {
                    "manifest_sha256": sha256_file(bundle_path), "model_version": bundle["model_version"],
                    "candidate_id": bundle["candidate_id"], "algorithm": bundle["algorithm"]},
                "evaluation_source": ({k: bundle["source"][k] for k in ("dataset_hash", "source_build_id", "plan_sha256")}
                                      if bundle else {"dataset_hash": p["source"]["dataset_hash"],
                                                      "source_build_id": p["source"]["source_build_id"],
                                                      "plan_sha256": p["plan_sha256"]}),
                "evaluation_plan": p, "evidence": evidence, "files": files, "created_at": utc_now()}
    frame = derive_model_health(output_dir, manifest)
    frame.to_parquet(output_dir / "model_health.parquet", index=False)
    manifest["files"]["model_health.parquet"] = sha256_file(output_dir / "model_health.parquet")
    manifest["rows"] = int(len(frame))
    write_json(output_dir / "manifest.json", manifest, exclusive=True)
    return manifest


def publish_candidate(base_serving_dir, scoring_dir, ranking_dir, health_dir, output_root, *, monitoring_dir=None,
                      access_policy=None, synthetic=False, publish=True):
    from data_platform.model_outputs import build_serving_with_model_outputs
    return build_serving_with_model_outputs(
        base_serving_dir, scoring_dir, ranking_dir, output_root, health_package_dir=health_dir,
        monitoring_package_dir=monitoring_dir, access_policy=access_policy, publish=publish,
        publication_kind="Synthetic_demo" if synthetic else "Local_review_candidate")
