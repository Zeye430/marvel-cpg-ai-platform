"""Offline imports, CLI side effects, governance refusals and the synthetic deliver chain."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAS_ML = all(importlib.util.find_spec(n) for n in ("lightgbm", "xgboost", "sklearn", "optuna", "pyarrow"))
SLOW = os.environ.get("MODULE2_SLOW") == "1"

BLOCKER = r"""
import importlib.abc, sys, socket
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('shiny', 'shinywidgets', 'plotly') or name in ('shared.data', 'shared.auth'):
            raise ImportError('blocked offline import: ' + name)
sys.meta_path.insert(0, Block())
def audit(event, args):
    if event == 'socket.connect':
        raise RuntimeError('network blocked')
sys.addaudithook(audit)
"""


def run(code, *args):
    return subprocess.run([sys.executable, "-c", BLOCKER + code, *args], cwd=ROOT, capture_output=True, text=True)


class OfflineBoundaryTests(unittest.TestCase):
    def test_offline_modules_import_without_ui_shared_data_or_network(self):
        modules = ["modules.module_2", "modules.module_2.config", "modules.module_2.data_adapter",
                   "modules.module_2.contracts", "modules.module_2.governance", "modules.module_2.service",
                   "data_platform.test_release", "data_platform.test_exposure", "data_platform.model_outputs"]
        if HAS_ML:
            modules += ["modules.module_2.benchmark", "modules.module_2.bundles", "modules.module_2.scoring",
                        "modules.module_2.ranking", "modules.module_2.monitoring", "modules.module_2.delivery"]
        result = run("import importlib\nfor m in " + repr(modules) + ": importlib.import_module(m)\nprint('ok')")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_cli_help_and_doctor_have_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            before = sorted(p.name for p in Path(temporary).iterdir())
            for args in (["--help"], ["deliver", "--help"], ["evaluate-holdout", "--help"]):
                result = subprocess.run([sys.executable, "-m", "modules.module_2", *args], cwd=temporary,
                                        env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            target = Path(temporary) / "not-created"
            doctor = subprocess.run([sys.executable, "-m", "modules.module_2", "doctor", "--output-root", str(target)],
                                    cwd=temporary, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
            self.assertIn('"checks"', doctor.stdout)
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), before)
            self.assertFalse(target.exists())


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class GovernanceRefusalTests(unittest.TestCase):
    def test_policy_requires_genuine_human_fields_and_v3_requires_authority(self):
        from modules.module_2 import delivery, governance
        from modules.module_2.contracts import Module2Error
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for missing in ("approver", "approved_at", "message", "message_reference"):
                human = {"approver": "x", "approved_at": "2026-10-21T00:00:00+00:00", "message": "m",
                         "message_reference": "r"}
                human[missing] = " "
                with self.subTest(missing=missing), self.assertRaises(Module2Error):
                    governance.create_policy(root / f"{missing}.json", human_authority=human, workflow=None,
                                             allowed_output_roots=[root], ledger_path=root / "l", known_history="h",
                                             prior_exposures=[])
            with self.assertRaises(Module2Error):
                delivery.deliver(profile="v3", output_root=root / "run")
            (root / "used").mkdir()
            (root / "used" / "something").write_text("x")
            with self.assertRaises(Module2Error):
                delivery.deliver(profile="synthetic", output_root=root / "used")


@unittest.skipUnless(HAS_ML and SLOW, "Slow synthetic end-to-end; set MODULE2_SLOW=1")
class SyntheticDeliverTests(unittest.TestCase):
    def test_full_chain_retention_tamper_and_idempotent_daily(self):
        from modules.module_2 import bundles, delivery, evaluation, inputs
        from modules.module_2.contracts import Module2Error, read_json
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "deliver"
            report = delivery.deliver(profile="synthetic", output_root=out)
            self.assertTrue(report["synthetic"])
            self.assertEqual(report["steps"]["benchmark"]["status"], "Complete")
            self.assertEqual(report["model_files"], [f for f in report["model_files"] if f.startswith("bundle/model/")])
            self.assertEqual(len(report["model_files"]), 1)                      # winner-only retention
            self.assertIn(report["serving_candidate"]["publication_kind"], ("Synthetic_demo",))
            config = read_json(out / "config.json")
            workflow = inputs.load_workflow(config["workflow_run"])
            if report["steps"]["holdout"].get("kind"):
                with self.assertRaises(Module2Error):
                    evaluation.run_holdout(out, workflow, config["authority_file"])
            self.assertEqual(delivery.daily(run_dir=out, workflow_run=config["workflow_run"])["status"],
                             "No_op_already_processed" if (out / "scoring" / "2026-06-30").exists() else "Processed")
            component = out / "bundle" / "calibration.json"
            component.write_text(component.read_text() + " ")
            with self.assertRaises(Module2Error):
                bundles.load_bundle(out / "bundle")


if __name__ == "__main__":
    unittest.main()
