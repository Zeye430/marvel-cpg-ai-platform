"""CLI parsing, dispatch and a synthetic subprocess export; no client data."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from data_platform.__main__ import main
from data_platform.ingestion import digest
from data_platform.pipeline import BuildConfig, ROOT
import test_feature_handoff as handoff_fixtures


class FeatureHandoffCliTests(unittest.TestCase):
    def invoke(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = main(arguments)
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue()

    def fixture(self):
        helper = handoff_fixtures.FeatureHandoffTests()
        self.addCleanup(helper.doCleanups)
        helper.setUp()
        return helper

    def test_required_arguments_and_invalid_dates_do_not_dispatch(self):
        cases = [
            [],
            ["--source-build-dir", "source"],
            ["--as-of", "2025-02-01"],
            ["--source-build-dir", "source", "--as-of", "2025-13-01"],
            ["--source-build-dir", "source", "--as-of", "yesterday"],
        ]
        with patch("data_platform.feature_handoff.export_feature_input") as export:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    status, stdout, stderr = self.invoke(["export-feature-input", *arguments])
                    self.assertEqual(status, 2)
                    self.assertEqual(stdout, "")
                    self.assertIn("error:", stderr)
            export.assert_not_called()

    def test_default_output_and_typed_dispatch_return_json(self):
        result = {"status": "Accepted", "rows": 32, "application_ready": False}
        with patch("data_platform.feature_handoff.export_feature_input", return_value=result) as export:
            status, stdout, stderr = self.invoke([
                "export-feature-input", "--source-build-dir", "source", "--as-of", "2025-02-01",
            ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout), result)
        self.assertEqual(stderr, "")
        export.assert_called_once_with(
            source_build_dir=Path("source"),
            output_root=BuildConfig().output_root / "feature-inputs",
            as_of=date(2025, 2, 1),
        )

    def test_help_does_not_export_and_existing_commands_keep_help(self):
        with patch("data_platform.feature_handoff.export_feature_input") as export:
            status, stdout, stderr = self.invoke(["export-feature-input", "--help"])
            self.assertEqual(status, 0)
            self.assertIn("--source-build-dir", stdout)
            self.assertIn("--as-of", stdout)
            self.assertIn("2026-05-31", stdout)
            self.assertEqual(stderr, "")
            for command in ("build", "audit-data", "evaluate-missing"):
                self.assertEqual(self.invoke([command, "--help"])[0], 0)
            export.assert_not_called()

    def test_module_subprocess_exports_only_synthetic_projection(self):
        fixture = self.fixture()
        before = digest(fixture.source / "build.duckdb")
        output_root = fixture.root / "cli-projections"
        completed = subprocess.run([
            sys.executable, "-m", "data_platform", "export-feature-input",
            "--source-build-dir", str(fixture.source), "--output-root", str(output_root),
            "--as-of", "2025-02-01",
        ], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "Accepted")
        self.assertEqual(result["rows"], 32)
        self.assertFalse(result["application_ready"])
        self.assertEqual(result["as_of"], "2025-02-01")
        directory = Path(result["directory"])
        self.assertEqual(directory.parent, output_root.resolve())
        self.assertEqual(digest(directory / result["database"]), result["database_sha256"])
        self.assertEqual(digest(fixture.source / "build.duckdb"), before)

    def test_cutoff_errors_return_one_without_creating_output(self):
        fixture = self.fixture()
        output_root = fixture.root / "rejected"
        for cutoff in ("2026-06-01", "2025-02-11"):
            with self.subTest(cutoff=cutoff):
                status, stdout, stderr = self.invoke([
                    "export-feature-input", "--source-build-dir", str(fixture.source),
                    "--output-root", str(output_root), "--as-of", cutoff,
                ])
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("accepted publications unchanged", stderr)
                self.assertFalse(output_root.exists())

    def test_integrity_failure_returns_one_and_leaves_source_unchanged(self):
        fixture = self.fixture()
        before = digest(fixture.source / "build.duckdb")
        (fixture.source / "quality.json").write_text("[]")
        status, stdout, stderr = self.invoke([
            "export-feature-input", "--source-build-dir", str(fixture.source),
            "--output-root", str(fixture.root / "rejected"), "--as-of", "2025-02-01",
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("quality hash mismatch", stderr)
        self.assertEqual(digest(fixture.source / "build.duckdb"), before)

    def test_missing_source_returns_one_with_no_traceback(self):
        fixture = self.fixture()
        status, stdout, stderr = self.invoke([
            "export-feature-input", "--source-build-dir", str(fixture.root / "missing"),
            "--as-of", "2025-02-01",
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("FileNotFoundError", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_interruption_returns_130(self):
        with patch("data_platform.feature_handoff.export_feature_input", side_effect=KeyboardInterrupt):
            status, stdout, stderr = self.invoke([
                "export-feature-input", "--source-build-dir", "source", "--as-of", "2025-02-01",
            ])
        self.assertEqual(status, 130)
        self.assertEqual(stdout, "")
        self.assertIn("Operation interrupted", stderr)


if __name__ == "__main__":
    unittest.main()
