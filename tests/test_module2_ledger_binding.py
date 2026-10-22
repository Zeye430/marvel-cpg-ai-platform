"""Module 2 exposure-ledger setup: one-time binding, fail-closed recovery (synthetic only)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from data_platform import test_exposure as shared
from data_platform.temporal_plan import TemporalPlan
from modules.module_2 import governance
from modules.module_2.contracts import Module2Error

HAS_ML = all(importlib.util.find_spec(n) for n in ("lightgbm", "xgboost", "sklearn", "optuna", "pyarrow"))
PLAN = TemporalPlan(date(2026, 6, 30), date(2025, 1, 1), "a" * 64, "synthetic-build", "b" * 64).to_dict()


def authorization(ledger_id, policy_sha):
    return {"exposure_ledger_id": ledger_id, "exposure_mode": "holdout_confirmation", "test_window": PLAN["test"],
            "confirmation_window": PLAN["test"], "release_sha256": "r" * 64, "plan_sha256": PLAN["plan_sha256"],
            "dataset_hash": "a" * 64, "source_build_id": "synthetic-build", "source_database_sha256": "b" * 64,
            "label_version": "oos_next7.complete_window.v1", "model_identity": {"model_sha256": "m" * 64},
            "evidence_sha256": "e" * 64, "policy_sha256": policy_sha, "max_primary_evaluations": 1}


class LedgerBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "governance" / "test-exposure.jsonl"
        self.policy_path = self.root / "governance" / "policy.json"
        self.policy_path.parent.mkdir(parents=True)
        self.policy_path.write_text('{"synthetic": true}\n')
        self.policy = self.make_policy()

    def make_policy(self, permitted=True):
        return {"policy_id": "policy-synthetic", "execution_scope": {
            "ledger": {"path": str(self.ledger.resolve()), "initialization_permitted": permitted},
            "known_history": "SYNTHETIC: generated data, no history", "prior_exposures": []}}

    def reserve(self, header):
        policy_sha = governance.sha256_file(self.policy_path)
        return shared.reserve_test_exposure(self.ledger, authorization=authorization(header["ledger_id"], policy_sha),
                                            artifact_id="synthetic-labels", fold_policy_version="trailing30.test_only.v1")

    def assertRefuses(self, pattern):
        before = self.ledger.read_bytes() if self.ledger.is_file() else None
        with self.assertRaisesRegex(Module2Error, pattern):
            governance.ensure_ledger(self.policy, self.policy_path)
        self.assertEqual(self.ledger.read_bytes() if self.ledger.is_file() else None, before)   # nothing rebuilt

    def test_first_setup_writes_ledger_and_durable_receipt_then_reuses(self):
        header = governance.ensure_ledger(self.policy, self.policy_path)
        receipt = json.loads(governance.binding_path(self.ledger).read_text())
        self.assertEqual((receipt["ledger_id"], receipt["header_hash"], receipt["bound_by"]),
                         (header["ledger_id"], header["entry_hash"], "initialized"))
        self.assertEqual(governance.ensure_ledger(self.policy, self.policy_path), header)
        self.reserve(header)
        governance.record_ledger_tip(self.policy)
        self.assertEqual(governance.ensure_ledger(self.policy, self.policy_path), header)    # valid growth reused
        self.assertEqual(json.loads(governance.binding_path(self.ledger).read_text())["observed"]["sequence"], 1)

    def test_initialized_then_missing_refuses_and_is_never_recreated(self):
        governance.ensure_ledger(self.policy, self.policy_path)
        self.ledger.rename(self.root / "backup.jsonl")
        self.assertRefuses("never re-created")
        self.assertFalse(self.ledger.exists())

    def test_consumed_then_missing_refuses_and_restore_keeps_the_limit(self):
        # Regression: a consumed ledger cannot be recreated after file loss.
        header = governance.ensure_ledger(self.policy, self.policy_path)
        self.reserve(header)
        governance.record_ledger_tip(self.policy)
        backup = self.root / "backup.jsonl"
        shutil.move(self.ledger, backup)
        self.assertRefuses("never re-created")
        self.assertFalse(self.ledger.exists())
        shutil.move(backup, self.ledger)                                   # manual restore of the original
        self.assertEqual(governance.ensure_ledger(self.policy, self.policy_path), header)
        with self.assertRaisesRegex(ValueError, "earlier reserved evaluation"):
            self.reserve(header)

    def test_corrupt_replaced_or_truncated_ledgers_refuse(self):
        header = governance.ensure_ledger(self.policy, self.policy_path)
        self.reserve(header)
        governance.record_ledger_tip(self.policy)
        original = self.ledger.read_bytes()
        lines = original.splitlines(keepends=True)
        cases = {
            "partial trailing": original[:-1],
            "malformed": original + b"{not json\n",
            "empty": b"",
            "truncated": lines[0],                                         # reservation removed at a line boundary
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.ledger.write_bytes(content)
                self.assertRefuses("manual")
        self.ledger.unlink()
        shared.initialize_test_exposure_ledger(self.ledger, initialized_by="someone", initialized_at=header["initialized_at"],
                                               history_note="fresh", prior_exposures=[])
        self.assertRefuses("not the bound ledger")
        self.ledger.write_bytes(original)
        self.assertEqual(governance.ensure_ledger(self.policy, self.policy_path), header)

    def test_malformed_or_foreign_receipt_refuses(self):
        governance.ensure_ledger(self.policy, self.policy_path)
        receipt = governance.binding_path(self.ledger)
        good = receipt.read_text()
        for bad in ("{", json.dumps(dict(json.loads(good), ledger_path="/elsewhere/ledger.jsonl"))):
            with self.subTest(bad=bad[:20]):
                receipt.write_text(bad)
                with self.assertRaises(Module2Error):
                    governance.ensure_ledger(self.policy, self.policy_path)
        receipt.write_text(good)
        self.ledger.unlink()
        receipt.write_text("{")
        with self.assertRaises(Module2Error):
            governance.ensure_ledger(self.policy, self.policy_path)
        self.assertFalse(self.ledger.exists())

    def test_existing_ledger_without_receipt_is_adopted_then_protected(self):
        header = shared.initialize_test_exposure_ledger(
            self.ledger, initialized_by="earlier program", initialized_at="2026-10-21T00:00:00+00:00",
            history_note="SYNTHETIC", prior_exposures=[])
        self.reserve(header)
        self.assertEqual(governance.ensure_ledger(self.policy, self.policy_path), header)
        receipt = json.loads(governance.binding_path(self.ledger).read_text())
        self.assertEqual((receipt["bound_by"], receipt["observed"]["sequence"]), ("adopted_existing_ledger", 1))
        self.ledger.unlink()
        self.assertRefuses("never re-created")

    def test_initialization_not_permitted(self):
        self.policy = self.make_policy(permitted=False)
        self.assertRefuses("not permitted")
        self.assertFalse(governance.binding_path(self.ledger).exists())

    def test_parallel_first_setup_yields_one_identity(self):
        barrier = threading.Barrier(8)

        def attempt(_):
            barrier.wait()
            return governance.ensure_ledger(self.policy, self.policy_path)["ledger_id"]

        with ThreadPoolExecutor(8) as pool:
            identities = set(pool.map(attempt, range(8)))
        header, entries = shared.read_ledger(self.ledger)
        self.assertEqual(identities, {header["ledger_id"]})
        self.assertEqual(entries, [])
        self.assertEqual(json.loads(governance.binding_path(self.ledger).read_text())["ledger_id"], header["ledger_id"])


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class HoldoutRefusalTests(unittest.TestCase):
    """run_holdout must stop at the ledger check: no release file, no label/truth build."""

    def run_holdout(self, run_dir, policy, policy_path):
        from modules.module_2 import evaluation

        def fake_read(path, *args, **kwargs):
            return {"status": "Complete"} if "benchmark" in str(path) else {"numeric_gate_passed": True}

        bundle = MagicMock()
        bundle.eligibility.return_value = {"status": "Validation_passed"}
        with patch.object(evaluation, "read_json", side_effect=fake_read), \
                patch.object(evaluation, "check_report", side_effect=lambda report, kind: report), \
                patch("modules.module_2.bundles.load_bundle", return_value=bundle), \
                patch("modules.module_2.governance.check_policy", return_value=policy), \
                patch.object(evaluation, "write_release", side_effect=RuntimeError("release written")) as release, \
                patch("data_platform.labels.build_labels") as labels:
            try:
                evaluation.run_holdout(run_dir, {"plan": None}, policy_path)
            finally:
                self.release_calls, self.label_calls = release.call_count, labels.call_count

    def test_refusal_happens_before_release_or_truth_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "governance" / "test-exposure.jsonl"
            policy_path = root / "governance" / "policy.json"
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text("{}\n")
            policy = {"policy_id": "policy-synthetic", "execution_scope": {
                "ledger": {"path": str(ledger), "initialization_permitted": True},
                "known_history": "SYNTHETIC", "prior_exposures": []}}
            run_dir = root / "run"
            run_dir.mkdir()
            with self.assertRaisesRegex(RuntimeError, "release written"):     # first setup proceeds to the release
                self.run_holdout(run_dir, policy, policy_path)
            self.assertEqual(self.release_calls, 1)
            ledger.rename(root / "backup.jsonl")
            with self.assertRaisesRegex(Module2Error, "never re-created"):
                self.run_holdout(run_dir, policy, policy_path)
            self.assertEqual((self.release_calls, self.label_calls), (0, 0))
            self.assertFalse((run_dir / "holdout").exists())
            self.assertFalse(ledger.exists())


if __name__ == "__main__":
    unittest.main()
