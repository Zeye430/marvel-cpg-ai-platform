"""oos_test_release.v2 gate: read-only evidence, two explicit approval modes."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import module2_release_fixtures as fx
from data_platform.temporal_plan import TemporalPlan
from data_platform.test_release import authorize_rolling_test_release, authorize_test_release

ROOT = Path(__file__).resolve().parents[1]
R2 = ROOT / "outputs/v3/module2/validation/pooled-segment-20261021-r2"
WORKFLOW = ROOT / "outputs/v3/platform/rolling-verification-20261020/runs/20261020T230945-c00941cadd14/run.json"


def plan(d=date(2026, 6, 30)):
    return TemporalPlan(d, date(2025, 1, 1), "a" * 64, "synthetic-build", "b" * 64)


class GateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = plan()

    def build(self, name="ok", **kwargs):
        return fx.build(self.root / name, self.plan, "ledger-x", **kwargs)

    def refused(self, mutate, name, message=None, **kwargs):
        release = self.build(name, mutate=mutate, **kwargs)
        with self.assertRaises(ValueError) as caught:
            authorize_rolling_test_release(release, self.plan.to_dict())
        if message:
            self.assertIn(message, str(caught.exception))

    def test_preauthorized_policy_happy_path_and_program_resolution(self):
        auth = authorize_rolling_test_release(self.build(), self.plan.to_dict())
        self.assertEqual(auth["approval_mode"], "preauthorized_policy")
        self.assertEqual(auth["max_primary_evaluations"], 1)
        self.assertEqual(auth["confirmation_window"], self.plan.to_dict()["test"])
        self.assertEqual(auth["approval"]["approver"], "SYNTHETIC TEST APPROVER")
        self.assertIn("resolved_by", auth["approval"])                 # program, not a human signature
        self.assertEqual(auth["model_identity"]["candidate_id"], "xgboost-000")

    def test_manual_mode_happy_path(self):
        auth = authorize_rolling_test_release(self.build(mode="post_freeze_manual"), self.plan.to_dict())
        self.assertEqual(auth["approval"]["approved_by"], "SYNTHETIC TEST APPROVER")

    def test_every_evidence_role_tamper_is_refused(self):
        roles = ("bundle_manifest", "model", "model_manifest", "preprocessor", "calibration", "thresholds",
                 "validation_report", "validation_predictions", "validation_access", "benchmark_report",
                 "training_inputs_manifest", "validation_inputs_manifest")
        for role in roles:
            with self.subTest(role=role):
                release = self.build(f"tamper-{role}")
                record = json.loads(release.read_text())
                target = release.parent / record["evidence"][role]["path"]
                target.write_bytes(target.read_bytes() + b" ")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    authorize_rolling_test_release(release, self.plan.to_dict())

    def test_semantic_mismatches_with_recomputed_hashes_are_refused(self):
        cases = {
            "wrong-plan-in-bundle": lambda n, r: n == "bundle_manifest" and r.update(temporal_plan=plan(date(2026, 6, 29)).to_dict()),
            "incomplete-benchmark": lambda n, r: n == "benchmark_report" and r.update(status="Benchmark_incomplete"),
            "other-winner": lambda n, r: n == "benchmark_report" and r["selection"].update(winner="lightgbm-001"),
            "report-other-bundle": lambda n, r: n == "validation_report" and r.update(bundle_manifest_sha256="d" * 64),
            "report-kind": lambda n, r: n == "validation_report" and r.update(kind="holdout_confirmation"),
            "access-other-bundle": lambda n, r: n == "validation_access" and r.update(bundle_manifest_sha256="d" * 64),
            "train-wrong-cap": lambda n, r: n == "training_inputs_manifest" and r.update(scoring_end="2026-04-25"),
            "valid-wrong-window": lambda n, r: n == "validation_inputs_manifest" and r.update(scoring_end="2026-05-31"),
            "threshold-bool": lambda n, r: n == "thresholds" and r.update({"global": True}),
            "threshold-nan": lambda n, r: n == "thresholds" and r.update({"global": float("nan")}),
            "release-window": lambda n, r: n == "release" and r.update(test_window={**r["test_window"], "reserved_start": "2026-05-31"}),
            "release-source": lambda n, r: n == "release" and r.update(dataset_hash="e" * 64),
            "v1-version": lambda n, r: n == "release" and r.update(release_version="oos_test_release.v1"),
            "pending": lambda n, r: n == "release" and r.update(status="Pending_owner_approval"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.refused(mutate, name)

    def test_metric_and_count_contradictions_are_refused(self):
        def overall(change):
            return lambda n, r: n == "validation_report" and r["results"][0].update(change)
        cases = {
            "bool-precision": overall({"precision": True}), "nan-recall": overall({"recall": float("nan")}),
            "missing-auc": overall({"roc_auc": None}), "auc-above-one": overall({"roc_auc": 1.2}),
            "below-target": overall({"recall": 0.69, "tp": 69, "fn": 31, "fp": 31, "tn": 869, "alerts": 100}),
            "counts-do-not-sum": overall({"tn": 879}), "precision-contradicts": overall({"precision": 0.85}),
            "float-count": overall({"tp": 80.0}),
            "duplicate-overall": lambda n, r: n == "validation_report" and r["results"].append(dict(r["results"][0])),
            "false-flag": lambda n, r: n == "validation_report" and r.update(numeric_gate_passed=False),
            "coverage-mismatch": lambda n, r: n == "validation_report" and r.update(label_coverage=0.6),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.refused(mutate, name)

    def test_chronology_and_authority_rules(self):
        t = fx.times()
        shift = lambda key, minutes: (datetime.fromisoformat(t[key]) + timedelta(minutes=minutes)).isoformat()
        self.refused(None, "policy-after-fit", t=dict(t, approved=shift("fit", 1)))
        self.refused(lambda n, r: n == "validation_access" and r.update(at=shift("frozen", -1)), "access-before-freeze")
        self.refused(lambda n, r: n == "validation_report" and r.update(evaluated_at=shift("access", -1)), "eval-before-access")
        self.refused(lambda n, r: n == "release" and r["resolution"].update(resolved_at=shift("evaluated", -1)),
                     "resolution-before-evaluation")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.refused(lambda n, r: n == "release" and r["resolution"].update(resolved_at=future), "future-resolution")
        self.refused(lambda n, r: n == "release" and r["resolution"].update(kind="owner_signature"), "resolution-as-signature")
        self.refused(lambda n, r: n == "policy" and r["human_authority"].update(approver=" "), "blank-approver")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(max_primary_holdout_evaluations=2), "limit-two")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(max_primary_holdout_evaluations=True), "limit-bool")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(confirmation_windows=[]), "no-window")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(exposure_modes=["independent_test"]), "mode-not-allowed")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(allowed_output_roots=["/elsewhere"]), "outside-root")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(config_sha256="f" * 64), "other-rules")
        self.refused(lambda n, r: n == "policy" and r["execution_scope"].update(risk_acknowledgements=[]), "no-risks")
        self.refused(lambda n, r: n == "validation_report" and r.update(evaluated_at=r["evaluated_at"].replace("+00:00", "")),
                     "naive-time", "timezone")

    def test_benchmark_evidence_is_rederived_not_trusted(self):
        def bench(change):
            return lambda n, r: n == "benchmark_report" and change(r)

        def trials(predicate):
            return [t for t in self.current["trials"] if predicate(t)]

        def capture(change):
            def apply(r):
                self.current = r
                change(r)
            return bench(apply)
        cases = {
            "zero-trials": capture(lambda r: r.update(trials=[])),
            "two-complete-rf": capture(lambda r: r.update(trials=[t for t in r["trials"] if t["trial_id"] != "random_forest-002"])),
            "failed-not-complete": capture(lambda r: [t.update(status="Timeout") for t in r["trials"] if t["trial_id"] == "lightgbm-002"]),
            "missing-dev2": capture(lambda r: [t.pop("dev2") for t in r["trials"] if t["trial_id"] == "lightgbm-001"]),
            "contradictory-trial-counts": capture(lambda r: r["trials"][0]["dev2"]["results"][0].update(tn=1)),
            "wrong-winner": capture(lambda r: r["selection"].update(winner="lightgbm-000")),
            "declared-incomplete-list": capture(lambda r: r["selection"].update(incomplete_algorithms=["random_forest"])),
            "trial-over-time": capture(lambda r: r["trials"][1].update(wall_seconds=r["budget"]["trial_seconds"] + 1)),
            "study-over-time": capture(lambda r: r["study_progress"]["xgboost"].update(elapsed_seconds=10**6)),
            "ml-over-budget": capture(lambda r: r.update(ml_seconds_used=10**7)),
            "budget-changed": capture(lambda r: r["budget"].update(max_trials=50)),
            "too-many-trials": capture(lambda r: r["trials"].extend(
                fx.trial("lightgbm", 100 + i, fx.PARAMS["lightgbm"], fx.overall(1, 9, 9, 81)) for i in range(20))),
            "param-outside-space": capture(lambda r: r["trials"][0]["params"].update(num_leaves=999)),
            "param-extra-key": capture(lambda r: r["trials"][0]["params"].update(max_bin=63)),
            "bool-param": capture(lambda r: r["trials"][0]["params"].update(n_estimators=True)),
            "rf-not-fixed-start": capture(lambda r: [t["params"].update(max_depth=16) for t in r["trials"] if t["trial_id"] == "random_forest-000"]),
            "baseline-off-grid": capture(lambda r: [t["params"].update(C=5.0) for t in r["trials"] if t["trial_id"] == "logistic_regression-000"]),
            "unknown-algorithm": capture(lambda r: r["trials"][0].update(algorithm="lstm")),
            "synthetic-profile-real-release": capture(lambda r: r.update(profile="synthetic")),
            "run-id-mismatch": capture(lambda r: r.update(run_id="other-run")),
            "invalid-calibrator": capture(lambda r: [t["calibrator"].update(coef=-1.0) for t in r["trials"] if t["trial_id"] == "xgboost-001"]),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.refused(mutate, name)

    def test_bundle_rules_code_and_selected_components_are_bound(self):
        cases = {
            "rules-not-matching-sha": lambda n, r: n == "bundle_manifest" and r["rules"]["targets"].update(precision=0.5),
            "rules-rehashed-but-policy-differs": lambda n, r: n == "bundle_manifest" and (
                r["rules"]["targets"].update(precision=0.5), r.update(config_sha256=fx.config_sha(r["rules"]))),
            "arbitrary-code-hash": lambda n, r: n == "bundle_manifest" and r.update(code_hashes={"benchmark.py": "0" * 64}),
            "no-code-identity": lambda n, r: n == "bundle_manifest" and r.pop("code_hashes"),
            "policy-without-code-identity": lambda n, r: n == "policy" and r["execution_scope"].pop("code_identity"),
            "bundle-params-not-winner": lambda n, r: n == "bundle_manifest" and r["params"].update(max_depth=5),
            "bundle-other-candidate": lambda n, r: n == "bundle_manifest" and r.update(candidate_id="xgboost-001"),
            "calibrator-not-selected": lambda n, r: n == "calibration" and r.update(coef=2.0),
            "thresholds-not-selected": lambda n, r: n == "thresholds" and r.update({"global": 0.6}),
            "synthetic-flag-mismatch": lambda n, r: n == "release" and r.update(synthetic=True),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.refused(mutate, name)
        self.refused(lambda n, r: n == "release" and r.update(approved_code_identity={"benchmark.py": "0" * 64}),
                     "manual-wrong-code", "code identity", mode="post_freeze_manual")
        self.refused(lambda n, r: n == "release" and r.pop("approved_code_identity"),
                     "manual-no-code", "code identity", mode="post_freeze_manual")

    def test_synthetic_profile_only_with_synthetic_policy_and_shrunk_budget(self):
        def synthetic(n, r):
            if n == "benchmark_report":
                r.update(profile="synthetic", budget={**r["budget"], "max_trials": 4, "study_seconds": 240,
                                                      "trial_seconds": 120, "total_ml_seconds": 1800, "n_startup_trials": 2})
            if n == "policy":
                r["synthetic"] = True
            if n == "release":
                r["synthetic"] = True
        auth = authorize_rolling_test_release(self.build("synthetic", mutate=synthetic), self.plan.to_dict())
        self.assertEqual(auth["model_identity"]["candidate_id"], "xgboost-000")

        def weaker(n, r):
            synthetic(n, r)
            if n == "benchmark_report":
                r["budget"]["min_complete_trials"] = 1
        self.refused(weaker, "synthetic-weaker-minimum")

    def test_modes_never_fall_back_to_each_other(self):
        self.refused(lambda n, r: n == "release" and r.update(approved_by="Someone", approved_at=fx.times()["manual"]),
                     "policy-plus-signature", "impersonate")
        self.refused(lambda n, r: n == "release" and r.update(policy={"path": "x", "sha256": "0" * 64}),
                     "manual-plus-policy", "must not carry", mode="post_freeze_manual")
        t = fx.times()
        self.refused(lambda n, r: n == "release" and r.update(approved_at=t["evaluated"]),
                     "manual-not-after-evaluation", mode="post_freeze_manual", t=t)
        self.refused(lambda n, r: n == "release" and r["risk_acknowledgement"].update(subgroup_limits_reviewed=False),
                     "no-ack", mode="post_freeze_manual")

    def test_rolling_dates_not_perpetual_june(self):
        self.plan = plan(date(2026, 8, 31))
        auth = authorize_rolling_test_release(self.build("aug"), self.plan.to_dict())
        self.assertEqual(auth["test_window"]["reserved_start"], "2026-08-02")
        with self.assertRaises(ValueError):
            authorize_rolling_test_release(self.root / "aug" / "release.json", plan().to_dict())

    def test_v1_gate_rejects_v2_and_v2_rejects_renamed_v1(self):
        release = self.build("cross")
        with self.assertRaises(ValueError):
            authorize_test_release(release, "a" * 64)
        self.refused(lambda n, r: n == "release" and r.update(evidence_profile="may_validation"), "renamed-profile")


@unittest.skipUnless(R2.exists() and WORKFLOW.exists(), "Requires the local frozen r2 evidence (read-only)")
class LegacyAdapterTests(unittest.TestCase):
    """Explicit adapter for the earlier r2 evidence; only hashes/reads JSON, never writes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = json.loads(WORKFLOW.read_text())["temporal_plan"]
        self.before = {p.name: fx.sha(p) for p in R2.rglob("*") if p.is_file()}

    def release(self, **changes):
        roles = {"policy": "policy/frozen-policy.json", "candidate_manifest": "candidate/manifest.json",
                 "model": "candidate/model.txt", "categories": "candidate/categories.json",
                 "training_inputs_manifest": "train-inputs/manifest.json",
                 "validation_inputs_manifest": "validation-inputs/manifest.json",
                 "validation_access": "access/outer-validation.json", "validation_report": "evaluation/report.json",
                 "validation_predictions": "evaluation/validation-predictions.parquet"}
        record = {"release_version": "oos_test_release.v2", "status": "Approved", "approval_mode": "post_freeze_manual",
                  "evidence_profile": "module2.outer_validation_report.v1", "exposure_mode": "holdout_confirmation",
                  "temporal_plan": self.plan, "plan_sha256": self.plan["plan_sha256"],
                  **{k: self.plan["source"][k] for k in ("dataset_hash", "source_build_id", "source_database_sha256")},
                  "test_window": self.plan["test"], "confirmation_window": self.plan["test"],
                  "feature_version": "oos_features.v1-complete", "label_version": "oos_next7.complete_window.v1",
                  "label_policy": "complete_window", "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
                  "threshold_policy": "pooled_segment_thresholds", "exposure_ledger_id": "ledger-synthetic",
                  "evidence": {k: {"path": str(R2 / v), "sha256": fx.sha(R2 / v)} for k, v in roles.items()},
                  "approved_by": "SYNTHETIC TEST APPROVER (adapter test only)",
                  "approved_at": datetime.now(timezone.utc).isoformat()}
        record["risk_acknowledgement"] = {"validation_report_sha256": record["evidence"]["validation_report"]["sha256"],
                                          "complete_label_coverage_reviewed": True, "subgroup_limits_reviewed": True,
                                          "calibration_reviewed": True, "note": "adapter test"}
        record.update(changes)
        return fx.write(self.root / "release.json", record)

    def test_real_r2_evidence_passes_adapter_read_only(self):
        auth = authorize_rolling_test_release(self.release(), self.plan)
        self.assertEqual(auth["evidence_profile"], "module2.outer_validation_report.v1")
        self.assertEqual(self.before, {p.name: fx.sha(p) for p in R2.rglob("*") if p.is_file()})

    def test_legacy_evidence_cannot_use_policy_mode(self):
        with self.assertRaises(ValueError):
            authorize_rolling_test_release(self.release(approval_mode="preauthorized_policy"), self.plan)


if __name__ == "__main__":
    unittest.main()
