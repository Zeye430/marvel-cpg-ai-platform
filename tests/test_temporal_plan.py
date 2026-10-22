"""Rolling calendar plans and verified metadata-only CLI; no client outcomes."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_platform.__main__ import main
from data_platform.temporal_plan import TemporalPlan, plan_evaluation


def make_plan(as_of=date(2026, 6, 30), **overrides):
    return TemporalPlan(**{
        "source_as_of": as_of, "history_start": date(2025, 1, 1),
        "dataset_hash": "a" * 64, "source_build_id": "synthetic-source",
        "source_database_sha256": "b" * 64, **overrides,
    })


def invoke(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(args)
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


class TemporalPlanTests(unittest.TestCase):
    def test_exact_june_and_august_windows(self):
        expected = {
            date(2026, 6, 30): ("2026-04-24", "2026-05-02", "2026-05-31", "2026-05-24", "2026-06-01", "2026-06-23", "2026-06-24"),
            date(2026, 8, 31): ("2026-06-25", "2026-07-03", "2026-08-01", "2026-07-25", "2026-08-02", "2026-08-24", "2026-08-25"),
        }
        for d, values in expected.items():
            with self.subTest(as_of=d):
                record = make_plan(d).to_dict()
                training, validation, test = (record[k] for k in ("training", "validation", "test"))
                self.assertEqual((training["scoring_end"], validation["reserved_start"],
                                  validation["reserved_end"], validation["calendar_eligible_end"],
                                  test["reserved_start"], test["calendar_eligible_end"],
                                  test["pending_start"]), values)
                self.assertEqual(record["latest_scoring"], {
                    "scoring_date": str(d), "prediction_start": str(d + timedelta(days=1)),
                    "prediction_end": str(d + timedelta(days=7)),
                })
                self.assertEqual(record["test_access"], "Locked")
                self.assertEqual(record["planning_status"], "Temporal_only")

    def test_calendar_boundaries_and_inclusive_counts(self):
        # All days across a leap year, plus midmonth and year rollover.
        for offset in range(367):
            d = date(2024, 1, 1) + timedelta(days=offset)
            r = make_plan(d, history_start=date(2020, 1, 1)).to_dict()
            v, t, train = (r[k] for k in ("validation", "test", "training"))
            parse = date.fromisoformat
            self.assertEqual((parse(t["reserved_end"]) - parse(t["reserved_start"])).days + 1, 30)
            self.assertEqual((parse(v["reserved_end"]) - parse(v["reserved_start"])).days + 1, 30)
            self.assertEqual(parse(v["reserved_end"]) + timedelta(days=1), parse(t["reserved_start"]))
            self.assertLess(parse(train["scoring_end"]) + timedelta(days=7), parse(v["reserved_start"]))
            self.assertLess(parse(v["calendar_eligible_end"]) + timedelta(days=7), parse(t["reserved_start"]))
            self.assertEqual(parse(t["calendar_eligible_end"]) + timedelta(days=7), d)
            for part in (v, t):
                self.assertEqual((parse(part["calendar_eligible_end"]) - parse(part["calendar_eligible_start"])).days + 1, 23)

    def test_invalid_date_types_ranges_and_insufficient_history(self):
        for field in ("source_as_of", "history_start"):
            for value in ("2026-06-30", datetime(2026, 6, 30), None, True):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_plan(**{field: value})
        for d in (date.min, date(1, 2, 1), date.max):
            with self.subTest(as_of=d), self.assertRaises(ValueError):
                make_plan(d, history_start=date.min)
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            make_plan(history_start=date(2026, 4, 25))
        self.assertEqual(make_plan(history_start=date(2026, 4, 24)).to_dict()["training"]["scoring_start"], "2026-04-24")

    def test_invalid_source_identity(self):
        for field in ("dataset_hash", "source_database_sha256"):
            for value in (None, True, "", "a" * 63, "A" * 64, "z" * 64):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    make_plan(**{field: value})
        for value in (None, True, "", " id", "id ", "id\nother"):
            with self.subTest(build_id=value), self.assertRaises(ValueError):
                make_plan(source_build_id=value)

    def test_roundtrip_identity_frozen_inputs_and_detached_results(self):
        plan = make_plan()
        record = plan.to_dict()
        self.assertEqual(TemporalPlan.from_dict(json.loads(json.dumps(record))), plan)
        self.assertEqual(plan.to_dict(), make_plan().to_dict())
        payload = {k: v for k, v in record.items() if k not in {"plan_id", "plan_sha256"}}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        self.assertEqual(record["plan_sha256"], expected)
        self.assertEqual(record["plan_id"], "temporal-" + expected[:16])
        with self.assertRaises(FrozenInstanceError):
            plan.source_as_of = date(2026, 8, 31)
        record["test"]["pending_start"] = "corrupted return value"
        self.assertEqual(plan.to_dict()["test"]["pending_start"], "2026-06-24")
        for changed in (replace(plan, source_build_id="another"), replace(plan, dataset_hash="c" * 64),
                        replace(plan, source_database_sha256="c" * 64), replace(plan, source_as_of=date(2026, 7, 1)),
                        replace(plan, history_start=date(2025, 1, 2))):
            self.assertNotEqual(plan.to_dict()["plan_sha256"], changed.to_dict()["plan_sha256"])

    def test_tampered_unknown_missing_and_noncanonical_records_rejected(self):
        baseline = make_plan().to_dict()
        mutations = []
        for key, value in (("window_days", 30.0), ("horizon_days", True),
                           ("test_access", "Approved"), ("plan_sha256", "c" * 64),
                           ("plan_id", "other"), ("extra", "not supported")):
            r = deepcopy(baseline); r[key] = value; mutations.append(r)
        for key in baseline:
            r = deepcopy(baseline); del r[key]; mutations.append(r)
        r = deepcopy(baseline)
        r["source"]["source_as_of"] = "20260630"
        mutations.append(r)
        r = deepcopy(baseline)
        r["test"]["reserved_start"] = "2026-06-02"
        # Recompute the forged digest: validation must check semantics too.
        payload = {k: v for k, v in r.items() if k not in {"plan_id", "plan_sha256"}}
        r["plan_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        r["plan_id"] = "temporal-" + r["plan_sha256"][:16]
        mutations.extend([r, None, [], "not a record", {"source": None}])
        for r in mutations:
            with self.subTest(record=r), self.assertRaises(ValueError):
                TemporalPlan.from_dict(r)

    def test_reserved_overlap_does_not_imply_test_release(self):
        plan = make_plan()
        for days, expected in ((0, 30), (1, 29), (29, 1), (30, 0), (31, 0)):
            shifted = replace(plan, source_as_of=plan.source_as_of + timedelta(days=days))
            self.assertEqual(plan.test_overlap_days(shifted), expected)
            self.assertEqual(shifted.test_overlap_days(plan), expected)
            self.assertEqual(shifted.to_dict()["test_access"], "Locked")
        with self.assertRaises(ValueError):
            plan.test_overlap_days(plan.to_dict())


class TemporalPlanSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Opaque fixture bytes prove the metadata-only reader never queries a DB.
        (self.root / "build.duckdb").write_bytes(b"synthetic opaque source fixture, not client data")
        self.checks = [{"severity": "error", "passed": True, "failed_count": 0}]
        (self.root / "quality.json").write_text(json.dumps(self.checks))
        self.record = {
            "status": "Accepted", "layer": "silver", "build_id": "synthetic-source",
            "database": "build.duckdb", "dataset_hash": "a" * 64,
            "database_sha256": self.digest(self.root / "build.duckdb"),
            "quality_sha256": self.digest(self.root / "quality.json"),
            "configuration": {"as_of": "2026-06-30", "calendar_start": "2025-01-01"},
        }
        self.write_record()

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_record(self, record=None):
        (self.root / "run.json").write_text(json.dumps(self.record if record is None else record))

    def fingerprints(self):
        return {p.name: self.digest(p) for p in self.root.iterdir()}

    def test_verified_metadata_only_read_is_immutable(self):
        before = self.fingerprints()
        with patch("duckdb.connect", side_effect=AssertionError("must not open a DB")):
            r = plan_evaluation(self.root)
        self.assertEqual(r["source"]["source_database_sha256"], self.record["database_sha256"])
        self.assertEqual(r["test"]["reserved_start"], "2026-06-01")
        self.assertEqual(r["test_access"], "Locked")
        self.assertEqual(self.fingerprints(), before)

    def test_bad_status_source_type_and_metadata_rejected_without_writes(self):
        mutations = []
        for key, value in (("status", "Failed"), ("layer", "features"), ("kind", "serving"),
                           ("build_id", None), ("dataset_hash", "bad")):
            r = deepcopy(self.record); r[key] = value; mutations.append(r)
        for configuration in (None, {}, {"as_of": "2026-06-30"},
                              {"as_of": "2026-06-30", "calendar_start": "bad"},
                              {"as_of": "2026-06-30", "calendar_start": "2026-06-01"}):
            r = deepcopy(self.record); r["configuration"] = configuration; mutations.append(r)
        r = deepcopy(self.record); r["as_of"] = "2026-05-31"; mutations.append(r)
        for r in mutations:
            self.write_record(r)
            before = self.fingerprints()
            with self.subTest(record=r), self.assertRaises((ValueError, KeyError, TypeError)):
                plan_evaluation(self.root)
            self.assertEqual(self.fingerprints(), before)

    def test_integrity_and_required_quality_checks_not_bypassed(self):
        (self.root / "build.duckdb").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            plan_evaluation(self.root)
        self.record["database_sha256"] = self.digest(self.root / "build.duckdb")
        for checks in ([], [{"severity": "error", "passed": False, "failed_count": 1}],
                       [{"severity": "warning", "passed": True, "failed_count": 0}]):
            (self.root / "quality.json").write_text(json.dumps(checks))
            self.record["quality_sha256"] = self.digest(self.root / "quality.json")
            self.write_record()
            with self.subTest(checks=checks), self.assertRaisesRegex(ValueError, "quality"):
                plan_evaluation(self.root)

    def test_missing_files_fail_and_future_real_source_cap_is_not_bypassed(self):
        self.record["configuration"]["as_of"] = "2026-08-31"
        self.write_record()
        with self.assertRaisesRegex(ValueError, "available business"):
            plan_evaluation(self.root)
        (self.root / "quality.json").unlink()
        with self.assertRaises(FileNotFoundError):
            plan_evaluation(self.root)

    def test_cli_real_dispatch_has_no_database_or_file_writes(self):
        before = self.fingerprints()
        with patch("duckdb.connect", side_effect=AssertionError("must not open a DB")):
            code, out, err = invoke(["plan-evaluation", "--source-build-dir", str(self.root)])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(TemporalPlan.from_dict(json.loads(out)).source_as_of, date(2026, 6, 30))
        self.assertEqual(self.fingerprints(), before)

    def test_cli_help_arguments_and_failure_exits(self):
        self.assertEqual(invoke(["plan-evaluation", "--help"])[0], 0)
        self.assertEqual(invoke(["plan-evaluation"])[0], 2)
        self.assertEqual(invoke(["plan-evaluation", "--source-build-dir", str(self.root), "--as-of", "2026-08-31"])[0], 2)
        self.assertEqual(invoke(["plan-evaluation", "--source-build-dir", str(self.root / "missing")])[0], 1)
        with patch("data_platform.temporal_plan.plan_evaluation", side_effect=ValueError("fixture failure")):
            code, out, err = invoke(["plan-evaluation", "--source-build-dir", "source"])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("fixture failure", err)


if __name__ == "__main__":
    unittest.main()
