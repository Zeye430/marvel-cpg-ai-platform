"""Strict rolling calendar bounds, not model approval or test-label access.

The pure plan validates declared provenance. The CLI wrapper binds it to an
explicit verified legacy source build; existing builders/gates are unchanged.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import re


PLAN_VERSION = "oos_temporal_plan.v1"
WINDOW_DAYS = 30
HORIZON_DAYS = 7
FOLD_POLICY_VERSION = "trailing30.outer_plus_two_history.v1"


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class TemporalPlan:
    source_as_of: date
    history_start: date
    dataset_hash: str
    source_build_id: str
    source_database_sha256: str

    def __post_init__(self):
        if type(self.source_as_of) is not date or type(self.history_start) is not date:
            raise ValueError("source_as_of and history_start must be calendar dates, not datetimes/strings")
        for name in ("dataset_hash", "source_database_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a lowercase SHA256 hex digest")
        value = self.source_build_id
        if not isinstance(value, str) or not value or value != value.strip() or not value.isprintable():
            raise ValueError("source_build_id must be a nonempty trimmed printable string")
        try:
            train_last = self.source_as_of - timedelta(days=2 * WINDOW_DAYS + HORIZON_DAYS)
            self.source_as_of + timedelta(days=HORIZON_DAYS)
        except OverflowError as error:
            raise ValueError("Source date cannot represent the full temporal plan") from error
        if self.history_start > train_last:
            raise ValueError("Insufficient calendar history: no training scoring date before the seven-day purge")

    def _payload(self):
        d = self.source_as_of
        test_start = d - timedelta(days=WINDOW_DAYS - 1)
        validation_start = d - timedelta(days=2 * WINDOW_DAYS - 1)
        validation_end = test_start - timedelta(days=1)
        train_end = validation_start - timedelta(days=HORIZON_DAYS + 1)
        validation_last = validation_end - timedelta(days=HORIZON_DAYS)
        test_last = d - timedelta(days=HORIZON_DAYS)
        return {
            "plan_version": PLAN_VERSION,
            "window_mode": "trailing_30d",
            "window_days": WINDOW_DAYS,
            "horizon_days": HORIZON_DAYS,
            "planning_status": "Temporal_only",
            "test_access": "Locked",
            "eligibility_policy": "complete_seven_observed_future_days_with_point_in_time_known_at",
            "source": {
                "dataset_hash": self.dataset_hash,
                "source_build_id": self.source_build_id,
                "source_database_sha256": self.source_database_sha256,
                "source_as_of": self.source_as_of.isoformat(),
                "history_start": self.history_start.isoformat(),
            },
            "training": {
                "scoring_start": self.history_start.isoformat(),
                "scoring_end": train_end.isoformat(),
                "outcome_end": (validation_start - timedelta(days=1)).isoformat(),
                "purged_start": (train_end + timedelta(days=1)).isoformat(),
                "purged_end": (validation_start - timedelta(days=1)).isoformat(),
            },
            "validation": {
                "reserved_start": validation_start.isoformat(),
                "reserved_end": validation_end.isoformat(),
                "calendar_eligible_start": validation_start.isoformat(),
                "calendar_eligible_end": validation_last.isoformat(),
                "outcome_end": validation_end.isoformat(),
                "purged_start": (validation_last + timedelta(days=1)).isoformat(),
                "purged_end": validation_end.isoformat(),
            },
            "test": {
                "reserved_start": test_start.isoformat(),
                "reserved_end": d.isoformat(),
                "calendar_eligible_start": test_start.isoformat(),
                "calendar_eligible_end": test_last.isoformat(),
                "outcome_end": d.isoformat(),
                "pending_start": (test_last + timedelta(days=1)).isoformat(),
                "pending_end": d.isoformat(),
            },
            "latest_scoring": {
                "scoring_date": d.isoformat(),
                "prediction_start": (d + timedelta(days=1)).isoformat(),
                "prediction_end": (d + timedelta(days=HORIZON_DAYS)).isoformat(),
            },
        }

    def to_dict(self):
        """Return detached canonical content; the digest is not authorization."""
        payload = self._payload()
        encoded = _canonical_json(payload).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return {**payload, "plan_id": "temporal-" + digest[:16], "plan_sha256": digest}

    @classmethod
    def from_dict(cls, record):
        """Reject noncanonical, altered or incomplete persisted plans."""
        try:
            source = record["source"]
            candidate = cls(
                source_as_of=date.fromisoformat(source["source_as_of"]),
                history_start=date.fromisoformat(source["history_start"]),
                dataset_hash=source["dataset_hash"],
                source_build_id=source["source_build_id"],
                source_database_sha256=source["source_database_sha256"],
            )
            if _canonical_json(candidate.to_dict()) != _canonical_json(record):
                raise ValueError("Temporal plan content or identity mismatch")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid or inconsistent temporal plan") from error
        return candidate

    def test_overlap_days(self, other):
        """Count reserved date overlap only; zero does not prove independence."""
        if not isinstance(other, TemporalPlan):
            raise ValueError("Overlap requires another TemporalPlan")
        start = max(self.source_as_of, other.source_as_of) - timedelta(days=WINDOW_DAYS - 1)
        end = min(self.source_as_of, other.source_as_of)
        return max(0, (end - start).days + 1)

    def development_folds(self):
        """Legacy-shaped tuples: up to two historical folds, then outer validation.

        Calendar support is not observed-label/model support. Omit an earlier
        fold if it has no possible training date; never shift the outer window.
        This derived policy does not change the v1 temporal-plan digest.
        """
        outer_start = self.source_as_of - timedelta(days=2 * WINDOW_DAYS - 1)
        folds = []
        for offset in (2, 1, 0):
            try:
                start = outer_start - timedelta(days=offset * WINDOW_DAYS)
                outcome_end = start + timedelta(days=WINDOW_DAYS - 1)
                train_last = start - timedelta(days=HORIZON_DAYS + 1)
            except OverflowError:
                if offset:
                    continue
                raise ValueError("Cannot represent the outer development fold")
            if train_last < self.history_start:
                continue
            fold_id = "rolling_validation" if offset == 0 else f"rolling_dev_{3-offset}"
            folds.append((fold_id, train_last.isoformat(), start.isoformat(),
                          (outcome_end - timedelta(days=HORIZON_DAYS)).isoformat(),
                          outcome_end.isoformat()))
        return tuple(folds)


def _source_temporal_plan(record, cutoff):
    """Convert already-verified base metadata; does not authenticate a caller."""
    configuration = record.get("configuration")
    if record.get("layer") != "silver" or record.get("kind") is not None or not isinstance(configuration, dict):
        raise ValueError("Temporal planning requires an accepted source/base build, not feature/label/serving artifacts")
    if configuration.get("as_of") != cutoff.isoformat():
        raise ValueError("Source configuration cutoff is inconsistent")
    try:
        return TemporalPlan(
            source_as_of=cutoff,
            history_start=date.fromisoformat(configuration["calendar_start"]),
            dataset_hash=record["dataset_hash"],
            source_build_id=record["build_id"],
            source_database_sha256=record["database_sha256"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Source metadata cannot define a valid temporal plan") from error


def bind_source_plan(plan_record, source_record, cutoff):
    """Bind canonical plan to the actual source metadata after verification."""
    plan = TemporalPlan.from_dict(plan_record)
    if plan.to_dict() != _source_temporal_plan(source_record, cutoff).to_dict():
        raise ValueError("Temporal plan differs from the verified source identity, cutoff or history")
    return plan


def plan_evaluation(source_build_dir):
    """Verify an explicit accepted source build and plan from metadata only.

    No database query, labels, output file or pointer writes. The current
    verifier's June source limit remains; future date arithmetic is not new
    source provisioning or a v2 feature/label-builder integration.
    """
    from .artifacts import verified_artifact

    record, _, cutoff = verified_artifact(source_build_dir)
    return _source_temporal_plan(record, cutoff).to_dict()
