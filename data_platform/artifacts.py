"""Local immutable artifact verification and terminal build records."""

import json
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .features import DEVELOPMENT_CUTOFF
from .time_policy import BUSINESS_CUTOFF, validate_feature_cutoff
from .ingestion import digest
from .pipeline import atomic_json, code_provenance, now, writer_lock


def verified_artifact(directory, *, kind=None):
    directory = Path(directory).resolve()
    record = json.loads((directory / "run.json").read_text())
    if record.get("status") != "Accepted" or (kind and record.get("kind") != kind):
        raise ValueError("An accepted artifact of the required kind is needed")
    database = directory / Path(record["database"]).name
    if digest(database) != record.get("database_sha256") or digest(directory/"quality.json") != record.get("quality_sha256"):
        raise ValueError("Artifact integrity mismatch")
    checks = json.loads((directory/"quality.json").read_text())
    required = [c for c in checks if c["severity"] == "error"]
    if not required or any(not c["passed"] or c["failed_count"] for c in required):
        raise ValueError("Artifact required quality checks did not pass")
    cutoff = date.fromisoformat(record.get("as_of") or record["configuration"]["as_of"])
    projection_has_plan = isinstance(record.get("projection"), dict) and "temporal_plan" in record["projection"]
    if projection_has_plan or any(key in record for key in ("temporal_plan", "fold_policy_version", "folds")):
        from .temporal_plan import TemporalPlan, FOLD_POLICY_VERSION
        plan = TemporalPlan.from_dict(record.get("temporal_plan"))
        expected = {
            "dataset_hash": plan.dataset_hash, "source_build_id": plan.source_build_id,
            "source_database_sha256": plan.source_database_sha256,
        }
        planned_test = record.get("kind") == "labels" and record.get("purpose") == "test"
        if planned_test:
            from .labels import TEST_FOLD_POLICY_VERSION, test_fold
            expected.update({
                "purpose": "test", "restricted": True, "as_of": plan.source_as_of.isoformat(),
                "fold_policy_version": TEST_FOLD_POLICY_VERSION, "folds": [list(test_fold(plan))],
                "label_version": "oos_next7.complete_window.v1",
            })
            if record.get("restricted") is not True:
                raise ValueError("Rolling test labels must remain restricted")
            _verify_planned_test(record, plan)
        elif record.get("kind") == "labels":
            expected.update({
                "purpose": "development", "restricted": True,
                "as_of": plan.to_dict()["validation"]["outcome_end"],
                "fold_policy_version": FOLD_POLICY_VERSION,
                "folds": [list(row) for row in plan.development_folds()],
                "label_version": "oos_next7.complete_window.v1",
            })
            if record.get("restricted") is not True:
                raise ValueError("Rolling labels must remain restricted")
        elif record.get("kind") == "features":
            validate_feature_cutoff(cutoff, record.get("purpose"), temporal_plan=plan.to_dict())
            expected["feature_version"] = "oos_features.v1-complete"
            expected["as_of"] = cutoff.isoformat()
            if any(key in record for key in ("fold_policy_version", "folds")):
                raise ValueError("Feature artifacts must not carry label-fold metadata")
            projection = record.get("projection")
            if not isinstance(projection, dict):
                raise ValueError("Rolling features require their source-bound projection metadata")
            projection_plan = TemporalPlan.from_dict(projection.get("temporal_plan"))
            projection_expected = {
                **{key: expected[key] for key in ("dataset_hash", "source_build_id", "source_database_sha256")},
                "purpose": record.get("purpose"), "as_of": cutoff.isoformat(),
                "status": "Accepted", "projection_version": "oos_complete_input.v3",
            }
            if projection_plan != plan or any(projection.get(k) != v for k, v in projection_expected.items()):
                raise ValueError("Rolling feature projection differs from the declared source/plan")
        else:
            raise ValueError("Unsupported artifact kind for a temporal plan")
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError("Rolling artifact metadata differs from its temporal plan")
        if not planned_test and (record.get("test_release") is not None or "test_exposure" in record):
            raise ValueError("Development/scoring plans must not carry test-release evidence")
    if cutoff > BUSINESS_CUTOFF:
        raise ValueError("Artifact exceeds the available business data end")
    if record.get("kind") == "features":
        validate_feature_cutoff(cutoff, record.get("purpose", "development"),
                                temporal_plan=record.get("temporal_plan"))
    if record.get("kind") == "labels" and cutoff > DEVELOPMENT_CUTOFF:
        if record.get("purpose") != "test" or not record.get("test_release"):
            raise ValueError("June test labels require an explicit frozen-model release")
    return record, database, cutoff


def _verify_planned_test(record, plan):
    """Re-check v2 evidence and the exposure receipt; never reserves or appends."""
    from .test_release import authorize_rolling_test_release
    from .test_exposure import verify_test_exposure_receipt
    stored = record.get("test_release")
    receipt = record.get("test_exposure")
    if not isinstance(stored, dict) or not isinstance(receipt, dict):
        raise ValueError("Planned test labels require their release authorization and exposure receipt")
    current = authorize_rolling_test_release(stored["release_path"], plan.to_dict())
    for key in ("release_sha256", "evidence_sha256", "plan_sha256", "test_window", "exposure_mode",
                "exposure_ledger_id", "model_identity"):
        if current.get(key) != stored.get(key):
            raise ValueError("Test release evidence changed after label materialization")
    if receipt.get("artifact_id") != record.get("artifact_id"):
        raise ValueError("Exposure receipt belongs to a different label artifact")
    verify_test_exposure_receipt(receipt, current)


def require_separate_root(output_root,*inputs):
    root=Path(output_root).resolve()
    for value in inputs:
        parent=Path(value).resolve()
        if root==parent or parent in root.parents:
            raise ValueError("Output artifacts must not be written inside an input artifact")


@contextmanager
def artifact_attempt(output_root, kind, metadata):
    root = Path(output_root).resolve()
    with writer_lock(root/".writer.lock"):
        identity = now().strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:12]
        directory = root/"builds"/identity
        directory.mkdir(parents=True)
        revision, tree = code_provenance()
        record = {**metadata, "artifact_id": identity, "kind": kind, "status": "Running",
                  "application_ready": False, "code_revision": revision, "code_tree_hash": tree,
                  "started_at": str(now()), "directory": str(directory)}
        atomic_json(directory/"run.json", record)
        try:
            yield directory, record
            database = directory/Path(record["database"]).name
            record.update(database_sha256=digest(database), quality_sha256=digest(directory/"quality.json"), status="Accepted")
        except (Exception, KeyboardInterrupt) as error:
            record.update(status="Interrupted" if isinstance(error, KeyboardInterrupt) else "Failed",
                          error=f"{type(error).__name__}: {error}")
            raise
        finally:
            record["finished_at"] = str(now())
            atomic_json(directory/"run.json", record)
