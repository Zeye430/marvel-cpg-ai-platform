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
    if cutoff > BUSINESS_CUTOFF:
        raise ValueError("Artifact exceeds the available business data end")
    if record.get("kind") == "features":
        validate_feature_cutoff(cutoff, record.get("purpose", "development"))
    if record.get("kind") == "labels" and cutoff > DEVELOPMENT_CUTOFF:
        if record.get("purpose") != "test" or not record.get("test_release"):
            raise ValueError("June test labels require an explicit frozen-model release")
    return record, database, cutoff


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
