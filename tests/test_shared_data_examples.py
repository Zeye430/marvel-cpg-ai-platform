"""The teammate's documented reader runs on scoped, isolated synthetic data."""

import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import duckdb

from data_platform.access import DataUnavailable
from data_platform.ingestion import digest
from data_platform.workflow import build_all
from data_platform.serving import VIEWS
from examples.read_shared_data import main, open_reader
import test_data_platform as source_fixtures


class ReaderExampleTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_help_does_not_open_database(self):
        with patch("examples.read_shared_data.open_reader") as opened:
            self.assertEqual(self.invoke(["--help"])[0], 0)
            opened.assert_not_called()

    def test_invalid_arguments_do_not_open_database(self):
        for args in ([], ["--schema-only", "--view", "gold.raw"],
                     ["--schema-only", "--limit", "10001"],
                     ["--schema-only", "--start", "2026-05-31", "--end", "2026-05-01"],
                     ["--schema-only", "--artifact-dir", "a", "--publication", "b"]):
            with self.subTest(args=args), patch("examples.read_shared_data.open_reader") as opened:
                self.assertEqual(self.invoke(args)[0], 2)
                opened.assert_not_called()

    def test_permission_and_unavailable_are_not_successful_zero_results(self):
        for error, expected in ((PermissionError("No scope"), 1),
                                (DataUnavailable("Model unreleased"), 3),
                                (duckdb.Error("Invalid database schema"), 1)):
            with self.subTest(error=error), patch("examples.read_shared_data.open_reader", side_effect=error):
                code, out, err = self.invoke(["--principal-id", "local-test"])
                self.assertEqual(code, expected)
                self.assertEqual(out, "")
                self.assertTrue(err)

    def test_both_input_sources_are_rejected(self):
        with self.assertRaises(ValueError):
            open_reader(publication="a", artifact_dir="b")

    def test_module_reads_schema_portability_and_source_immutability(self):
        fixture = source_fixtures.PlatformTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        root = fixture.root / "example-delivery"
        policy = fixture.root / "example-scope.json"
        policy.write_text(json.dumps({"version": "synthetic-example-v1", "assignments": [
            {"principal_id": "example-sales", "store_id": "S1", "role": "sales"}]}))
        record = build_all(fixture.config.as_of, root, source_dir=fixture.config.source_dir,
                           snapshot_root=fixture.config.snapshot_root, access_policy=policy,
                           threads=1, memory_limit="256MB", publish=True)
        publication = root / "serving/published/serving.json"
        serving = root / "serving/builds" / record["components"]["serving"]["artifact_id"]
        before = digest(serving / "business.duckdb")
        code, out, _ = self.invoke(["--publication", str(publication), "--schema-only"])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["schema"]), len(VIEWS))
        for view in ("negotiation_context", "store_day", "metric_definitions"):
            with self.subTest(view=view):
                code, out, _ = self.invoke(["--publication", str(publication), "--principal-id", "example-sales", "--view", view, "--limit", "5"])
                self.assertEqual(code, 0)
                self.assertGreater(json.loads(out)["row_count"], 0)
        self.assertEqual(self.invoke(["--publication", str(publication), "--principal-id", "unknown"])[0], 1)
        candidate_args=["--publication",str(publication),"--principal-id","example-sales","--view","estimated_store_day"]
        self.assertEqual(self.invoke(candidate_args)[0],3)
        code,out,_=self.invoke([*candidate_args,"--allow-candidate-estimates"])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(out)["metric_view"],"operational_estimate_candidate")
        self.assertEqual(json.loads(out)["estimate_release_status"],"Candidate_not_approved")
        self.assertEqual(self.invoke(["--publication", str(publication), "--principal-id", "example-sales", "--view", "oos_predictions"])[0], 3)
        copied = fixture.root / "copied-serving"
        shutil.copytree(serving, copied)
        result = subprocess.run([sys.executable, "-m", "examples.read_shared_data", "--artifact-dir", str(copied),
                                 "--view", "negotiation_context", "--principal-id", "example-sales"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["rows"][0]["policy_status"], "Requires review")
        self.assertEqual(digest(serving / "business.duckdb"), before)
        changed = json.loads(publication.read_text())
        changed["dataset_hash"] = "not-the-published-dataset"
        publication.write_text(json.dumps(changed))
        self.assertEqual(self.invoke(["--publication", str(publication), "--schema-only"])[0], 1)
        changed["directory"] = str(fixture.root / "not-on-this-machine")
        publication.write_text(json.dumps(changed))
        self.assertIn("rebuild locally or use --artifact-dir", self.invoke(["--publication", str(publication), "--schema-only"])[2])


if __name__ == "__main__":
    unittest.main()
