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
