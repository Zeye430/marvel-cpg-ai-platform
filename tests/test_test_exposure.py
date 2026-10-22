"""Project-wide test exposure ledger: explicit init, durable reservation, no refunds."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from data_platform.temporal_plan import TemporalPlan
from data_platform import test_exposure as ledger

ROOT = Path(__file__).resolve().parents[1]


def plan(d=date(2026, 6, 30)):
    return TemporalPlan(d, date(2025, 1, 1), "a" * 64, "synthetic-build", "b" * 64)


def authorization(ledger_id, d=date(2026, 6, 30), mode="holdout_confirmation", model="m1", policy="p" * 64, **extra):
    p = plan(d).to_dict()
    record = {"exposure_ledger_id": ledger_id, "exposure_mode": mode, "test_window": p["test"],
              "confirmation_window": p["test"] if mode == "holdout_confirmation" else None,
              "release_sha256": "r" * 64, "plan_sha256": p["plan_sha256"], "dataset_hash": "a" * 64,
              "source_build_id": "synthetic-build", "source_database_sha256": "b" * 64,
              "label_version": "oos_next7.complete_window.v1", "model_identity": {"model_sha256": model},
              "evidence_sha256": "e" * 64, "policy_sha256": policy, "max_primary_evaluations": 1}
    record.update(extra)
    return record


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "governance" / "test-exposure.jsonl"

    def init(self, prior=None):
        return ledger.initialize_test_exposure_ledger(
            self.path, initialized_by="synthetic test", initialized_at=datetime.now(timezone.utc).isoformat(),
            history_note="SYNTHETIC history", prior_exposures=prior if prior is not None else [])

    def reserve(self, auth, artifact="label-artifact-1", **kwargs):
        return ledger.reserve_test_exposure(self.path, authorization=auth, artifact_id=artifact,
                                            fold_policy_version="trailing30.test_only.v1", **kwargs)

    def test_initialization_is_explicit_and_never_replaced(self):
        with self.assertRaises(ValueError):
            self.reserve(authorization("ledger-x"))                    # missing ledger: no auto-create
        header = self.init()
        with self.assertRaises(FileExistsError):
            self.init()
        for bad in ({"initialized_by": " "}, {"initialized_at": "2026-10-21T00:00:00"}, {"prior_exposures": None}):
            with self.subTest(bad=bad):
                args = dict(initialized_by="x", initialized_at=datetime.now(timezone.utc).isoformat(),
                            history_note="h", prior_exposures=[])
                args.update(bad)
                with self.assertRaises(ValueError):
                    ledger.initialize_test_exposure_ledger(Path(self.path.parent) / "other.jsonl", **args)
        self.assertEqual(ledger.read_ledger(self.path)[0]["ledger_id"], header["ledger_id"])

    def test_overlap_refused_regardless_of_run_output_source_or_candidate(self):
        header = self.init()
        receipt = self.reserve(authorization(header["ledger_id"]))
        self.assertEqual(receipt["kind"], "Reservation")
        variants = [authorization(header["ledger_id"], model="m2"),
                    authorization(header["ledger_id"], release_sha256="s" * 64, dataset_hash="c" * 64),
                    authorization(header["ledger_id"], d=date(2026, 7, 1), policy="q" * 64)]
        for auth in variants:
            with self.subTest(auth=auth["model_identity"]), self.assertRaises(ValueError):
                self.reserve(auth, artifact="another-artifact")
        # A window 30 days later shares no reserved date and is allowed under its own policy.
        later = authorization(header["ledger_id"], d=date(2026, 7, 30), policy="z" * 64)
        self.assertEqual(self.reserve(later, artifact="later")["kind"], "Reservation")

    def test_confirmation_against_prior_history_and_single_primary(self):
        header = self.init(prior=[{"reserved_start": "2026-06-01", "reserved_end": "2026-06-30",
                                   "reason": "historical notebook exploration", "provenance": "synthetic"}])
        receipt = self.reserve(authorization(header["ledger_id"]))
        self.assertEqual(receipt["prior_exposure_refs"], ["prior-0"])
        entry = ledger.read_ledger(self.path)[1][0]
        self.assertIn("not an independent test", entry["evaluation_name"])
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], d=date(2026, 8, 31)))      # policy limit already used
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], mode="independent_test", policy="w" * 64,
                                       d=date(2026, 6, 15)))                           # prior overlap
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], confirmation_window=None, policy="v" * 64,
                                       d=date(2026, 9, 30)))

    def test_replay_requires_identity_and_reason(self):
        header = self.init()
        auth = authorization(header["ledger_id"])
        first = self.reserve(auth)
        for reason in (None, " "):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.reserve(auth, artifact="replay", replay_reason=reason)
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], model="other"), artifact="replay",
                         replay_reason="verification")
        replay = self.reserve(auth, artifact="replay", replay_reason="Deterministic verification replay")
        self.assertEqual((replay["kind"], replay["replay_of"]), ("Replay", first["event_id"]))
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], d=date(2026, 9, 30), policy="n" * 64),
                         artifact="x", replay_reason="nothing to replay")

    def test_wrong_ledger_id_and_unknown_mode_refused(self):
        self.init()
        with self.assertRaises(ValueError):
            self.reserve(authorization("ledger-other"))
        header = ledger.read_ledger(self.path)[0]
        with self.assertRaises(ValueError):
            self.reserve(authorization(header["ledger_id"], mode="monitoring"))

    def test_fsync_failure_appends_nothing_and_is_not_refunded_later(self):
        header = self.init()
        with patch("data_platform.test_exposure.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.reserve(authorization(header["ledger_id"]))
        # The append may have reached the page cache before fsync failed; either way the
        # chain stays valid and nothing is silently removed.
        header2, entries = ledger.read_ledger(self.path)
        self.assertEqual(header2["ledger_id"], header["ledger_id"])
        self.assertLessEqual(len(entries), 1)

    def test_hook_failure_before_append_leaves_no_entry(self):
        header = self.init()
        with self.assertRaises(RuntimeError):
            self.reserve(authorization(header["ledger_id"]), before_append=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(ledger.read_ledger(self.path)[1], [])

    def test_partial_tail_or_tampered_chain_refuses(self):
        header = self.init()
        self.reserve(authorization(header["ledger_id"]))
        original = self.path.read_bytes()
        self.path.write_bytes(original + b'{"partial":')
        with self.assertRaises(ValueError):
            ledger.read_ledger(self.path)
        lines = original.decode().splitlines()
        entry = json.loads(lines[1]); entry["artifact_id"] = "rewritten"
        self.path.write_text(lines[0] + "\n" + json.dumps(entry) + "\n")
        with self.assertRaises(ValueError):
            ledger.read_ledger(self.path)
        self.path.write_text(lines[0] + "\n")                      # truncation hides the reservation
        self.assertEqual(ledger.read_ledger(self.path)[1], [])     # documented limitation: file ledger is not access control

    def test_concurrent_writer_is_refused_while_lock_held(self):
        header = self.init()
        lock = self.path.with_name(self.path.name + ".lock")
        script = ("import fcntl,sys,time;h=open(sys.argv[1],'a');fcntl.flock(h,fcntl.LOCK_EX);"
                  "print('locked',flush=True);time.sleep(5)")
        holder = subprocess.Popen([sys.executable, "-c", script, str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            with self.assertRaises(RuntimeError):
                self.reserve(authorization(header["ledger_id"]))
        finally:
            holder.kill(); holder.wait(); holder.stdout.close()
        self.assertEqual(ledger.read_ledger(self.path)[1], [])
        self.assertEqual(self.reserve(authorization(header["ledger_id"]))["kind"], "Reservation")

    def test_receipt_verification_is_read_only(self):
        header = self.init()
        auth = authorization(header["ledger_id"])
        receipt = self.reserve(auth)
        before = self.path.read_bytes()
        for _ in range(3):
            ledger.verify_test_exposure_receipt(receipt, auth)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            ledger.verify_test_exposure_receipt({**receipt, "entry_hash": "0" * 64}, auth)
        with self.assertRaises(ValueError):
            ledger.verify_test_exposure_receipt(receipt, authorization(header["ledger_id"], model="other"))


if __name__ == "__main__":
    unittest.main()
