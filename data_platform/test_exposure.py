"""Project-wide test-window exposure ledger: a local audit, not access control.

One append-only JSONL file with a hash chain. It must be explicitly
initialized (with an honest history note and known prior exposures) and is
never created, repaired, truncated or refunded automatically. A reservation is
appended and fsynced before any test-truth query; it records *possible* access,
not a successful evaluation. Deleting or copying the file defeats it, so the
owner keeps one ledger and its backups.
"""

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .pipeline import writer_lock

LEDGER_VERSION = "oos_test_exposure_ledger.v1"
SCOPE = "marvel_cpg.store_sku_oos"
EXPOSURE_MODES = ("independent_test", "holdout_confirmation")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _entry_hash(entry):
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(_canonical(body).encode("ascii")).hexdigest()


def _aware(value, name):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _interval(record, name="window"):
    try:
        start = date.fromisoformat(record["reserved_start"])
        end = date.fromisoformat(record["reserved_end"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{name} needs reserved_start/reserved_end dates") from error
    if end < start:
        raise ValueError(f"{name} ends before it starts")
    return start, end


def _overlap(a, b):
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return max(0, (end - start).days + 1)


def _append(path, entry):
    line = _canonical(entry) + "\n"
    with Path(path).open("a", encoding="ascii") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def initialize_test_exposure_ledger(ledger_path, *, initialized_by, initialized_at, history_note,
                                    prior_exposures, authorized_under=None):
    """Explicit, create-exclusive initialization; never replaces an existing ledger."""
    path = Path(ledger_path)
    for name, value in (("initialized_by", initialized_by), ("history_note", history_note)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Ledger {name} must be a nonblank description")
    _aware(initialized_at, "initialized_at")
    if not isinstance(prior_exposures, list):
        raise ValueError("prior_exposures must be an explicit list (empty only after real history review)")
    for item in prior_exposures:
        _interval(item, "prior exposure")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip() or not item.get("provenance"):
            raise ValueError("Each prior exposure needs a reason and provenance")
    header = {"version": LEDGER_VERSION, "scope": SCOPE, "kind": "Header", "sequence": 0,
              "ledger_id": "ledger-" + uuid.uuid4().hex, "previous_hash": None,
              "initialized_by": initialized_by, "initialized_at": initialized_at,
              "recorded_at": datetime.now(timezone.utc).isoformat(), "history_note": history_note,
              "prior_exposures": [dict(item, exposure_id=f"prior-{i}") for i, item in enumerate(prior_exposures)],
              "authorized_under": authorized_under}
    header["entry_hash"] = _entry_hash(header)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="ascii") as handle:
        handle.write(_canonical(header) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return header


def read_ledger(ledger_path):
    """Validate the whole chain; any malformed/partial/tampered line refuses."""
    path = Path(ledger_path)
    if not path.is_file():
        raise ValueError("Test exposure ledger is missing or uninitialized")
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("Ledger has a partial trailing record; manual reconciliation required")
    entries = []
    for number, line in enumerate(raw.decode("ascii").splitlines()):
        try:
            entry = json.loads(line)
        except ValueError as error:
            raise ValueError(f"Ledger line {number} is malformed") from error
        if entry.get("sequence") != number or entry.get("entry_hash") != _entry_hash(entry):
            raise ValueError("Ledger hash chain or sequence is invalid")
        if number and entry.get("previous_hash") != entries[-1]["entry_hash"]:
            raise ValueError("Ledger hash chain is broken")
        entries.append(entry)
    if not entries or entries[0].get("kind") != "Header" or entries[0].get("version") != LEDGER_VERSION:
        raise ValueError("Ledger header is missing or unsupported")
    return entries[0], entries[1:]


def _identity(authorization):
    keys = ("release_sha256", "plan_sha256", "dataset_hash", "source_build_id", "source_database_sha256",
            "label_version", "exposure_mode", "model_identity", "evidence_sha256", "policy_sha256")
    return {k: authorization.get(k) for k in keys}


def reserve_test_exposure(ledger_path, *, authorization, artifact_id, fold_policy_version, replay_reason=None,
                          revalidate=None, before_append=None):
    """Durably reserve the authorized test window before any truth SQL.

    ``revalidate`` re-runs the read-only release check under the lock.
    ``before_append`` is a test hook only. Returns the receipt.
    """
    path = Path(ledger_path)
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("Reservation needs the label artifact identity")
    with writer_lock(path.with_name(path.name + ".lock")):
        header, entries = read_ledger(path)
        if authorization.get("exposure_ledger_id") != header["ledger_id"]:
            raise ValueError("Release names a different exposure ledger")
        if revalidate is not None:
            authorization = revalidate()
        mode = authorization.get("exposure_mode")
        if mode not in EXPOSURE_MODES:
            raise ValueError("Unsupported exposure mode")
        window = _interval(authorization["test_window"], "test window")
        identity = _identity(authorization)
        priors = [p for p in header["prior_exposures"] if _overlap(window, _interval(p)) > 0]
        earlier = [e for e in entries if e.get("kind") in ("Reservation", "Replay")
                   and _overlap(window, _interval(e["window"])) > 0]
        replay_of = None
        if earlier or (priors and mode == "independent_test"):
            if replay_reason is not None:
                if not isinstance(replay_reason, str) or not replay_reason.strip():
                    raise ValueError("A replay needs a nonblank reason")
                same = [e for e in earlier if e["identity"] == identity and e["window"] == authorization["test_window"]]
                conflicting = [e for e in earlier if e not in same]
                if not same or conflicting:
                    raise ValueError("Replay requires identical release/evidence/source/window and no conflicting exposure")
                replay_of = same[0]["event_id"]
            elif earlier:
                raise ValueError("Test window overlaps an earlier reserved evaluation; a new primary evaluation is refused")
            else:
                raise ValueError("Independent test window overlaps recorded prior exposure")
        elif replay_reason is not None:
            raise ValueError("Nothing to replay for this window")
        if mode == "holdout_confirmation" and replay_of is None:
            allowed = authorization.get("confirmation_window")
            if allowed != authorization["test_window"]:
                raise ValueError("Holdout confirmation is limited to the explicitly authorized window")
        limit = authorization.get("max_primary_evaluations")
        if replay_of is None and authorization.get("policy_sha256") is not None:
            used = sum(e.get("kind") == "Reservation" and e["identity"].get("policy_sha256") == identity["policy_sha256"]
                       for e in entries)
            if not isinstance(limit, int) or isinstance(limit, bool) or used >= limit:
                raise ValueError("The execution policy's primary evaluation limit is already used")
        entry = {"version": LEDGER_VERSION, "kind": "Replay" if replay_of else "Reservation",
                 "sequence": len(entries) + 1, "previous_hash": (entries[-1] if entries else header)["entry_hash"],
                 "event_id": "event-" + uuid.uuid4().hex, "ledger_id": header["ledger_id"],
                 "at": datetime.now(timezone.utc).isoformat(), "artifact_id": artifact_id,
                 "window": authorization["test_window"], "exposure_mode": mode,
                 "evaluation_name": "holdout_confirmation (not an independent test)"
                 if mode == "holdout_confirmation" else "independent_test",
                 "fold_policy_version": fold_policy_version, "identity": identity,
                 "prior_exposure_refs": [p["exposure_id"] for p in priors],
                 "replay_of": replay_of, "replay_reason": replay_reason}
        entry["entry_hash"] = _entry_hash(entry)
        if before_append is not None:
            before_append()
        _append(path, entry)
        return {"ledger_path": str(path.resolve()), "ledger_id": header["ledger_id"],
                "sequence": entry["sequence"], "event_id": entry["event_id"], "entry_hash": entry["entry_hash"],
                "artifact_id": artifact_id, "kind": entry["kind"], "exposure_mode": mode,
                "prior_exposure_refs": entry["prior_exposure_refs"], "replay_of": replay_of}


def verify_test_exposure_receipt(receipt, authorization):
    """Read-only: the receipt's entry exists and matches; appends nothing."""
    header, entries = read_ledger(receipt["ledger_path"])
    if header["ledger_id"] != receipt.get("ledger_id"):
        raise ValueError("Receipt names a different ledger")
    match = [e for e in entries if e["sequence"] == receipt.get("sequence")]
    if len(match) != 1:
        raise ValueError("Receipt entry not found")
    entry = match[0]
    if (entry["entry_hash"] != receipt.get("entry_hash") or entry["event_id"] != receipt.get("event_id")
            or entry["artifact_id"] != receipt.get("artifact_id") or entry["identity"] != _identity(authorization)
            or entry["window"] != authorization.get("test_window")):
        raise ValueError("Receipt does not match the ledger entry or authorization")
    return entry
