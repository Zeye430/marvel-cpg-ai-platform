"""Small shared helpers: canonical JSON, hashes, atomic writes, errors, guards."""

import hashlib
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config


class Module2Error(ValueError):
    """Invalid request, artifact, identity or policy; never a silent fallback."""


class Unavailable(RuntimeError):
    """A valid request with no releasable data; unavailable is not zero risk."""


class ResourceExhausted(RuntimeError):
    """A disk/memory/time guard stopped this run's own work."""


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def json_sha256(value):
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def sha256_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    """Timezone-aware ISO timestamp or Module2Error."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise Module2Error("Expected an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise Module2Error("Timestamps must be timezone-aware")
    return parsed


def clean_json(value):
    """Convert numpy/pandas scalars and NaN to strict JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_json(path, value, *, exclusive=False):
    """Atomic JSON write; ``exclusive`` refuses to replace an existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean_json(value), indent=2, sort_keys=True, allow_nan=False) + "\n"
    if exclusive:
        with path.open("x") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_json(path, what="record"):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Module2Error(f"Required {what} is missing or invalid: {Path(path).name}") from error


def config_sha256():
    return json_sha256(config.frozen_rules())


def code_hashes():
    """Byte identities of the Module 2 delivery code (excluding legacy scripts)."""
    package = Path(__file__).resolve().parent
    names = sorted(p.name for p in package.glob("*.py"))
    return {name: sha256_file(package / name) for name in names}


def directory_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def check_resources(run_dir, *, limits=None, stage=""):
    """Fail safely before an expensive step instead of filling the disk."""
    limits = limits or config.RESOURCE_LIMITS
    run_dir = Path(run_dir)
    free = shutil.disk_usage(run_dir if run_dir.exists() else run_dir.parent).free
    used = directory_bytes(run_dir) if run_dir.exists() else 0
    if free < limits["free_space_reserve_bytes"]:
        raise ResourceExhausted(f"Free disk below reserve before {stage or 'step'}")
    if used > limits["run_payload_bytes"]:
        raise ResourceExhausted(f"Run payload budget exceeded before {stage or 'step'}")
    return {"free_bytes": free, "run_bytes": used}
