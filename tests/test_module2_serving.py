"""Model-output publisher, scoped paged reads and the Module 2 service (synthetic)."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

HAS_ML = all(importlib.util.find_spec(n) for n in ("sklearn", "pyarrow"))
if HAS_ML:
    from data_platform.access import DataUnavailable, ServingReader
    from data_platform.ingestion import digest
    from data_platform.model_outputs import build_serving_with_model_outputs
    from data_platform.serving import rollback_serving
    from data_platform.workflow import build_all
    from modules.module_2 import inputs, publishing, ranking, scoring, synthetic
    from modules.module_2.contracts import sha256_file, write_json
    from modules.module_2.service import Module2Service, PermissionDenied, Principal, demo_scope, resolve_published
    from modules.module_2.training import segment_keys


EVIDENCE = {}      # filled from the test workflow's real plan in setUpClass


class StubBundle:
    """Deterministic stand-in for a frozen model; scoring code itself is real.

    Its identity is the SHA-256 of the fixture bundle manifest, like a real bundle."""

    eligibility_status = "Validation_passed"

    def __init__(self, directory):
        self.directory = Path(directory)
        bundle = EVIDENCE["default"]["bundle"]
        self.manifest = {"model_version": bundle["model_version"], "feature_version": "oos_features.v1-complete",
                         "algorithm": bundle["algorithm"], "candidate_id": bundle["candidate_id"],
                         "source": dict(bundle["source"])}
        self.manifest_sha256 = EVIDENCE["default"]["bundle_sha"]

    def predict(self, frame):
        cover = pd.to_numeric(frame["days_cover_observed"], errors="coerce").fillna(3.0).to_numpy(float)
        probability = 1 / (1 + np.exp(cover - 4))
        segments = segment_keys(frame)
        threshold = np.full(len(frame), 0.5)
        return {"raw": probability, "probability": probability, "threshold": threshold,
                "route": np.full(len(frame), "global_fallback", dtype=object), "segment": segments.to_numpy(),
                "decision": probability >= threshold}

    def eligibility(self):
        return {"status": self.eligibility_status}


def canonical(value):
    return json.dumps(value, indent=2, sort_keys=True).encode()


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def group(kind, gid, tp, fp, fn, tn, auc):
    rows = tp + fp + fn + tn
    return {"group_kind": kind, "group_id": gid, "rows": rows, "positives": tp + fn, "tp": tp, "fp": fp, "fn": fn,
            "tn": tn, "alerts": tp + fp, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "roc_auc": auc, "average_precision": 0.7,
            "brier": 0.05, "ece": 0.01, "meets_numeric_targets": bool(tp + fp and tp / (tp + fp) >= .75
                                                                       and tp / (tp + fn) >= .70 and auc >= .80)}


def make_evidence(plan, model_version="m2-stub-synthetic"):
    """Coherent bundle/benchmark identities for one model under the given historical plan."""
    p = plan
    source = {"dataset_hash": p["source"]["dataset_hash"], "source_build_id": p["source"]["source_build_id"],
              "plan_sha256": p["plan_sha256"]}
    results = [group("all", "all", 80, 20, 20, 880, 0.9), group("segment", '["Modern Trade","Tier 1"]', 30, 5, 10, 455, 0.92)]
    benchmark = {"version": "module2.benchmark_report.v1", "run_id": "stub-run", "plan_sha256": p["plan_sha256"],
                 "fold_candidates": {"rolling_dev_1": 2000, "rolling_dev_2": 2000}, "status": "Complete",
                 "selection": {"winner": "stub-000"},
                 "trials": [{"algorithm": "stub", "trial_id": "stub-000", "params": {"depth": 3}, "status": "Complete",
                             "fit_seconds": {"rolling_dev_1": 1.0, "rolling_dev_2": 1.5},
                             "dev1": {"results": results}, "dev2": {"results": results}},
                            {"algorithm": "stub", "trial_id": "stub-001", "params": {"depth": 9}, "status": "Timeout",
                             "fit_seconds": {}}]}
    benchmark_bytes = canonical(benchmark)
    bundle = {"model_version": model_version, "candidate_id": "stub-000", "algorithm": "stub", "params": {"depth": 3},
              "fit_seconds": 2.0, "source": source, "temporal_plan": p,
              "selection": {"benchmark_report_sha256": sha(benchmark_bytes)}}
    bundle_bytes = canonical(bundle)
    return {"plan": p, "source": source, "benchmark": benchmark, "benchmark_bytes": benchmark_bytes,
            "bundle": bundle, "bundle_bytes": bundle_bytes, "bundle_sha": sha(bundle_bytes)}


def report(kind, ev, *, passed=True, bundle_sha=None, source=None):
    fold = next(r for r in __import__("data_platform.temporal_plan", fromlist=["TemporalPlan"]).TemporalPlan
                .from_dict(ev["plan"]).development_folds() if r[0] == "rolling_validation")
    test = ev["plan"]["test"]
    window = ({"scoring_start": fold[2], "scoring_end": fold[3], "outcome_end": fold[4]} if kind == "iterative_validation"
              else {"scoring_start": test["calendar_eligible_start"], "scoring_end": test["calendar_eligible_end"],
                    "outcome_end": test["outcome_end"]})
    overall = group("all", "all", *((80, 20, 20, 880) if passed else (40, 60, 60, 840)), 0.9 if passed else 0.7)
    return {"version": "module2.evaluation_report.v2", "kind": kind, "status": "Evaluated",
            "bundle_manifest_sha256": bundle_sha or ev["bundle_sha"], **(source or ev["source"]),
            "model_version": ev["bundle"]["model_version"], "candidate_id": "stub-000", "algorithm": "stub",
            "window": window, "candidate_rows": 2000, "eligible_rows": 1000, "limitations": ["synthetic fixture"],
            "numeric_gate_passed": passed,
            "results": [overall, group("current_status", "not_oos", 50, 10, 15, 625, 0.88),
                        group("current_status", "oos", 30, 10, 5, 255, 0.9)]}


def health_package(directory, *, status="Available", eligibility="Validation_passed", synthetic=True,
                   reports=("validation",), passed=True, report_bundle=None, report_source=None, evidence=None,
                   declared_bundle=None, evaluation_source=None, mutate_rows=None):
    """Write a health package; by default fully consistent, rows derived by the shared function."""
    from data_platform.model_outputs import derive_model_health
    ev = evidence or EVIDENCE["default"]
    directory = Path(directory)
    directory.mkdir(parents=True)
    files, evidence_map = {}, {}

    def put(role, name, data):
        (directory / name).write_bytes(data)
        files[name] = sha256_file(directory / name)
        evidence_map[role] = {"file": name, "sha256": files[name]}
    put("bundle_manifest", "bundle.json", ev["bundle_bytes"])
    put("benchmark_report", "benchmark-report.json", ev["benchmark_bytes"])
    for role in reports:
        kind = "iterative_validation" if role == "validation" else "holdout_confirmation"
        put(f"{role}_report", f"{role}-report.json",
            canonical(report(kind, ev, passed=passed, bundle_sha=report_bundle, source=report_source)))
    manifest = {"version": "module2.health_package.v1", "status": "Accepted", "synthetic": synthetic,
                "eligibility_status": eligibility, "model_outputs_status": status,
                "bundle": declared_bundle or {"manifest_sha256": ev["bundle_sha"],
                                              **{k: ev["bundle"][k] for k in ("model_version", "candidate_id", "algorithm")}},
                "evaluation_source": dict(evaluation_source or ev["source"]), "evaluation_plan": ev["plan"],
                "evidence": evidence_map, "files": files}
    try:
        frame = derive_model_health(directory, manifest)
    except ValueError:
        frame = pd.DataFrame(columns=publishing.HEALTH_COLUMNS)     # deliberately inconsistent negative fixture
    if mutate_rows is not None:
        frame = mutate_rows(frame)
    frame.to_parquet(directory / "model_health.parquet", index=False)
    files["model_health.parquet"] = sha256_file(directory / "model_health.parquet")
    write_json(directory / "manifest.json", manifest)
    return directory


@unittest.skipUnless(HAS_ML, "Requires the module2 extra")
class ServingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls.temp.name)
        synthetic.generate(root / "raw", stores_per_segment=1, skus=4)
        write_json(root / "scope0.json", {"version": "none", "assignments": []})
        build_all(date(2026, 6, 30), root / "workflow", source_dir=root / "raw", snapshot_root=root / "snapshots",
                  access_policy=root / "scope0.json", rolling_evaluation=True, assume_complete_snapshot=True, threads=1,
                  memory_limit="512MB")
        cls.workflow = inputs.load_workflow(next((root / "workflow" / "runs").glob("*/run.json")))
        cls.base = cls.workflow["components"]["serving"]["directory"]
        EVIDENCE["default"] = make_evidence(cls.workflow["plan"].to_dict())
        EVIDENCE["other"] = make_evidence(cls.workflow["plan"].to_dict(), model_version="other-model")
        with patch.object(scoring.bundles, "load_bundle", lambda d: StubBundle(d)):
            scoring.score(cls.workflow, root / "bundle", root / "scoring", synthetic=True)
            eligible = StubBundle(root / "bundle")
            eligible.eligibility_status = "Eligible_for_review"
            with patch.object(scoring.bundles, "load_bundle", lambda d: eligible):
                scoring.score(cls.workflow, root / "bundle", root / "scoring-real-eligible")
            scoring.score(cls.workflow, root / "bundle", root / "scoring-real-validation-only")
        import duckdb
        from data_platform.artifacts import verified_artifact
        _, db, _ = verified_artifact(cls.base, kind="serving")
        with duckdb.connect(str(db), read_only=True) as con:
            products = con.execute("SELECT sku_id,list_price,is_msl FROM gold.dim_products").df()
            cls.stores = con.execute("SELECT store_id FROM gold.dim_stores").df()
        for name in ("", "-real-eligible", "-real-validation-only"):
            ranking.rank(root / f"scoring{name}", products, cls.stores, root / f"ranking{name}")
        health_package(root / "health")
        scope = demo_scope(cls.stores["store_id"].tolist())
        partial = sorted(cls.stores["store_id"])[:3]
        scope["assignments"] += [{"principal_id": "demo:partial-manager", "store_id": s, "role": "manager"} for s in partial]
        write_json(root / "scope.json", scope)
        cls.base_hash = digest(Path(cls.base) / "business.duckdb")
        cls.published = build_serving_with_model_outputs(
            cls.base, root / "scoring", root / "ranking", root / "candidate", health_package_dir=root / "health",
            access_policy=root / "scope.json", publish=True, publication_kind="Synthetic_demo")
        cls.reader = ServingReader(cls.published["directory"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    # ---------------------------------------------------------------- publisher
    def test_publication_adds_outputs_without_changing_facts_or_base(self):
        self.assertEqual(self.published["model_outputs_status"], "Available")
        self.assertEqual(digest(Path(self.base) / "business.duckdb"), self.base_hash)
        base = ServingReader(self.base)
        self.assertEqual(base.record["model_outputs_status"], "Unavailable_model_not_released")
        rows = self.reader.read_page("oos_predictions", "demo:manager", page_size=1000)["rows"]
        confirmed = [r for r in self.reader.read("store_sku_daily", "demo:manager", start=date(2026, 6, 30), limit=10000)]
        self.assertEqual(len(rows), len(confirmed))
        self.assertTrue(all(0 <= r["probability"] <= 1 for r in rows))
        self.assertEqual(self.published["model_outputs"]["publication_kind"], "Synthetic_demo")

    def refused(self, scoring_dir, ranking_dir, health_dir, kind, **kwargs):
        root = self.root / f"refused-{len(list(self.root.glob('refused-*')))}"
        with self.assertRaises(ValueError):
            build_serving_with_model_outputs(self.base, scoring_dir, ranking_dir, root, health_package_dir=health_dir,
                                             access_policy=self.root / "scope.json", publish=True,
                                             publication_kind=kind, **kwargs)
        self.assertFalse((root / "published" / "serving.json").exists())          # no pointer change

    def test_isolated_root_and_eligibility_rules(self):
        args = (self.base, self.root / "scoring", self.root / "ranking")
        with self.assertRaises(ValueError):
            build_serving_with_model_outputs(*args, Path(self.base).parent.parent, health_package_dir=self.root / "health")
        formal = self.root / "formal"
        (formal / "published").mkdir(parents=True)
        (formal / "published" / "serving.json").write_text("{}")
        with self.assertRaises(ValueError):
            build_serving_with_model_outputs(*args, formal, health_package_dir=self.root / "health")

    def test_unrelated_eligible_health_cannot_qualify_other_predictions(self):
        real = (self.root / "scoring-real-validation-only", self.root / "ranking-real-validation-only")
        eligible_health = health_package(self.root / "h-eligible-unrelated", eligibility="Eligible_for_review",
                                         synthetic=False, reports=("validation", "holdout"), evidence=EVIDENCE["other"])
        self.refused(*real, eligible_health, "Local_review_candidate")                  # mixed model
        same_bundle = health_package(self.root / "h-eligible-same", eligibility="Eligible_for_review", synthetic=False,
                                     reports=("validation", "holdout"))
        self.refused(*real, same_bundle, "Local_review_candidate")                      # scored bundle not eligible

    def test_mixed_source_failed_or_missing_qualification_and_synthetic_mixing(self):
        eligible = (self.root / "scoring-real-eligible", self.root / "ranking-real-eligible")
        cases = {
            "mixed-source": health_package(self.root / "h1", eligibility="Eligible_for_review", synthetic=False,
                                           reports=("validation", "holdout"),
                                           evaluation_source={**EVIDENCE["default"]["source"], "source_build_id": "x"}),
            "failed-report": health_package(self.root / "h2", eligibility="Eligible_for_review", synthetic=False,
                                            reports=("validation", "holdout"), passed=False),
            "missing-holdout": health_package(self.root / "h3", eligibility="Eligible_for_review", synthetic=False),
            "report-other-bundle": health_package(self.root / "h4", eligibility="Eligible_for_review", synthetic=False,
                                                  reports=("validation", "holdout"), report_bundle="b" * 64),
            "report-other-source": health_package(self.root / "h4b", eligibility="Eligible_for_review", synthetic=False,
                                                  reports=("validation", "holdout"),
                                                  report_source={**EVIDENCE["default"]["source"], "dataset_hash": "e" * 64}),
            "synthetic-health-real-kind": health_package(self.root / "h5", eligibility="Eligible_for_review",
                                                         synthetic=True, reports=("validation", "holdout")),
        }
        for name, health in cases.items():
            with self.subTest(case=name):
                self.refused(*eligible, health, "Local_review_candidate")
        with self.subTest(case="real-scoring-synthetic-kind"):
            self.refused(*eligible, health_package(self.root / "h6", synthetic=True), "Synthetic_demo")
        with self.subTest(case="real-health-synthetic-kind"):
            self.refused(self.root / "scoring", self.root / "ranking", health_package(self.root / "h7", synthetic=False),
                         "Synthetic_demo")
        with self.subTest(case="half-a-pair"):
            self.refused(self.root / "scoring", None, self.root / "health", "Synthetic_demo")

    def test_qualified_real_candidate_is_available(self):
        health = health_package(self.root / "h-qualified", eligibility="Eligible_for_review", synthetic=False,
                                reports=("validation", "holdout"))
        record = build_serving_with_model_outputs(
            self.base, self.root / "scoring-real-eligible", self.root / "ranking-real-eligible", self.root / "real-root",
            health_package_dir=health, access_policy=self.root / "scope.json", publish=True,
            publication_kind="Local_review_candidate")
        self.assertEqual(record["model_outputs_status"], "Available")
        self.assertEqual(len(record["model_outputs"]["qualifying_report_sha256"]), 2)

    def test_served_health_rows_must_equal_their_packaged_evidence(self):
        """R-01b: every served row is reconciled to its report/benchmark/source; never trusted as supplied."""
        eligible = (self.root / "scoring-real-eligible", self.root / "ranking-real-eligible")

        def first(column, value, where=None):
            def change(frame):
                frame = frame.copy()
                index = frame.index[0] if where is None else frame.index[where(frame)][0]
                frame.loc[index, column] = value
                return frame
            return change
        validation = lambda f: (f["evaluation_kind"] == "iterative_validation").to_numpy()
        development = lambda f: (f["evaluation_kind"] == "development").to_numpy()
        cases = {
            "wrong-source-row": first("source_build_id", "b", validation),
            "wrong-dataset-row": first("dataset_hash", "d", validation),
            "wrong-count": first("sample_count", 100, validation),
            "wrong-metric": first("precision", 0.99, validation),
            "wrong-window": first("window_start", date(2026, 5, 3), validation),
            "wrong-group": first("group_id", "not_a_group", validation),
            "wrong-dev-params": first("parameters_json", "{}", development),
            "dev-forced-to-selected-model": first("model_version", "m2-stub-synthetic", lambda f: (f["candidate_id"] == "stub-001").to_numpy()),
            "extra-row": lambda f: pd.concat([f, f.iloc[[0]]], ignore_index=True),
            "missing-row": lambda f: f.iloc[1:].reset_index(drop=True),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.refused(*eligible, health_package(self.root / f"rows-{name}", eligibility="Eligible_for_review",
                                                       synthetic=False, reports=("validation", "holdout"),
                                                       mutate_rows=mutate), "Local_review_candidate")
        unrelated_benchmark = dict(EVIDENCE["default"], benchmark_bytes=EVIDENCE["other"]["benchmark_bytes"] + b" ")
        with self.subTest(case="benchmark-not-selecting-bundle"):
            self.refused(*eligible, health_package(self.root / "rows-bench", eligibility="Eligible_for_review",
                                                   synthetic=False, reports=("validation", "holdout"),
                                                   evidence=unrelated_benchmark), "Local_review_candidate")

    def test_served_rows_are_the_derived_historical_evidence(self):
        health = health_package(self.root / "h-derived", eligibility="Eligible_for_review", synthetic=False,
                                reports=("validation", "holdout"))
        record = build_serving_with_model_outputs(
            self.base, self.root / "scoring-real-eligible", self.root / "ranking-real-eligible", self.root / "derived-root",
            health_package_dir=health, access_policy=self.root / "scope.json", publish=False,
            publication_kind="Local_review_candidate")
        rows = ServingReader(record["directory"]).read_page("model_health", "demo:manager", page_size=1000)["rows"]
        source = EVIDENCE["default"]["source"]
        self.assertTrue(all(r["dataset_hash"] == source["dataset_hash"] and r["source_build_id"] == source["source_build_id"]
                            for r in rows))
        development = [r for r in rows if r["evaluation_kind"] == "development"]
        self.assertEqual({r["model_version"] for r in development if r["candidate_id"] == "stub-001"},
                         {"development-candidate:stub-run:stub-001"})            # own identity, not the winner's
        self.assertEqual({r["evaluation_status"] for r in development if r["candidate_id"] == "stub-001"}, {"Timeout"})
        holdout = [r for r in rows if r["evaluation_kind"] == "holdout_confirmation" and r["group_kind"] == "all"]
        self.assertEqual((holdout[0]["sample_count"], holdout[0]["candidate_count"]), (1000, 2000))

    def test_monitoring_must_belong_to_this_source_date_and_model(self):
        directory = self.root / "monitoring-foreign"
        directory.mkdir()
        pd.DataFrame(columns=["x"]).to_parquet(directory / "model_monitoring.parquet")
        write_json(directory / "manifest.json", {
            "version": "module2.monitoring_package.v1", "status": "Accepted", "synthetic": True,
            "source": {"dataset_hash": "other", "source_build_id": "other"}, "observation_cutoff": "2026-06-29",
            "bundle_manifest_sha256": EVIDENCE["default"]["bundle_sha"],
            "files": {"model_monitoring.parquet": sha256_file(directory / "model_monitoring.parquet")}})
        self.refused(self.root / "scoring", self.root / "ranking", self.root / "health", "Synthetic_demo",
                     monitoring_package_dir=directory)

    def test_health_only_publication_is_unreleased_and_rollback_works(self):
        record = build_serving_with_model_outputs(
            self.base, None, None, self.root / "candidate", health_package_dir=health_package(
                self.root / "h-unavail", status="Unavailable_validation_failed", eligibility="Validation_failed",
                synthetic=False),
            access_policy=self.root / "scope.json", publish=True, publication_kind="Local_review_candidate")
        reader = ServingReader(record["directory"])
        with self.assertRaises(DataUnavailable):
            reader.read_page("oos_predictions", "demo:manager")
        self.assertEqual(len(reader.read_page("model_health", "demo:manager")["rows"]),
                         len(pd.read_parquet(self.root / "h-unavail" / "model_health.parquet")))
        back = rollback_serving(self.root / "candidate", self.published["publication"]["publication_id"])
        self.assertEqual(Path(resolve_published(self.root / "candidate")).name, Path(self.published["directory"]).name)
        self.assertTrue(back["action"].startswith("rollback:"))

    # ---------------------------------------------------------------- reader
    def test_paged_reads_are_complete_scoped_and_cursor_bound(self):
        seen, cursor = [], None
        while True:
            page = self.reader.read_page("oos_predictions", "demo:manager", page_size=7, after=cursor)
            seen += [(r["store_id"], r["sku_id"]) for r in page["rows"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), len(self.reader.read("oos_predictions", "demo:manager", limit=10000)))
        rep = self.reader.read_page("oos_predictions", "demo:rep-a", page_size=1000)["rows"]
        rep_stores = {r["store_id"] for r in self.reader.read_page("stores", "demo:rep-a")["rows"]}
        self.assertTrue(rep and {r["store_id"] for r in rep} <= rep_stores)
        first = self.reader.read_page("oos_predictions", "demo:manager", page_size=3)
        with self.assertRaises(ValueError):                                       # other principal
            self.reader.read_page("oos_predictions", "demo:rep-a", page_size=3, after=first["next_cursor"])
        with self.assertRaises(ValueError):                                       # other filters
            self.reader.read_page("oos_predictions", "demo:manager", page_size=3, after=first["next_cursor"], sku_id="SYN-P001")
        body, tag = first["next_cursor"].rsplit(".", 1)
        with self.assertRaises(ValueError):                                       # forged
            self.reader.read_page("oos_predictions", "demo:manager", page_size=3, after=body + "." + "0" * len(tag))
        other = ServingReader(self.published["directory"])
        with self.assertRaises(ValueError):                                       # other reader instance/secret
            other.read_page("oos_predictions", "demo:manager", page_size=3, after=first["next_cursor"])
        for bad in ({"page_size": 0}, {"page_size": 1001}, {"store_id": "S1", "name": "model_health"},
                    {"start": "2026-06-30"}, {"name": "sell_in_events"}):
            name = bad.pop("name", "oos_predictions")
            with self.subTest(bad=bad, name=name), self.assertRaises(ValueError):
                self.reader.read_page(name, "demo:manager", **bad)

    def test_manager_only_aggregates_on_both_read_paths(self):
        self.assertEqual(len(self.reader.read_page("model_health", "demo:manager")["rows"]),
                         len(pd.read_parquet(self.root / "health" / "model_health.parquet")))
        for principal in ("demo:rep-a", "demo:partial-manager", "unknown-principal"):
            for method in (lambda p: self.reader.read_page("model_health", p),
                           lambda p: self.reader.read("model_health", p),
                           lambda p: self.reader.read("model_monitoring", p)):
                with self.subTest(principal=principal), self.assertRaises(PermissionError):
                    method(principal)
        self.assertTrue(self.reader.principal_scope("demo:manager")["manager_full_scope"])
        self.assertFalse(self.reader.principal_scope("demo:partial-manager")["manager_full_scope"])

    # ---------------------------------------------------------------- service
    def test_service_scope_ranks_export_and_denial(self):
        service = Module2Service(self.published["directory"])
        with self.assertRaises(PermissionDenied):
            service.get_priorities(Principal("demo:rep-a", False))
        rep_a = service.get_priorities(Principal("demo:rep-a", True))
        rep_b = service.get_priorities(Principal("demo:rep-b", True))
        manager = service.get_priorities(Principal("demo:manager", True))
        a_ids = {r["store_id"] for r in rep_a.rows}
        self.assertTrue(a_ids and a_ids.isdisjoint({r["store_id"] for r in rep_b.rows}))
        self.assertEqual([r["display_rank"] for r in rep_a.rows], list(range(1, len(rep_a.rows) + 1)))
        self.assertTrue(all(r["global_rank"] is None for r in rep_a.rows))
        self.assertTrue(manager.totals["full_population_scope"])
        self.assertEqual([r["global_rank"] for r in manager.rows], [r["display_rank"] for r in manager.rows])
        partial = service.get_priorities(Principal("demo:partial-manager", True))
        self.assertFalse(partial.totals["full_population_scope"])
        self.assertEqual(service.get_model_health(Principal("demo:partial-manager", True)).status, "Permission_denied")
        csv_rows = service.export_visit_plan(Principal("demo:rep-a", True)).strip().splitlines()[1:]
        self.assertEqual({line.split(",")[1] for line in csv_rows}, a_ids)
        outside = next(s for s in self.stores["store_id"] if s not in a_ids)
        with self.assertRaises(PermissionDenied):
            service.get_store_detail(Principal("demo:rep-a", True), outside)
        detail = service.get_store_detail(Principal("demo:rep-a", True), sorted(a_ids)[0])
        self.assertTrue(detail.detail["skus"] and detail.detail["history"])
        with self.assertRaises(Exception):
            service.get_priorities(Principal("demo:rep-a", True), {"store_id": outside})   # unsupported filter
        alerts = service.get_priorities(Principal("demo:manager", True), {"alert_only": True})
        self.assertTrue(all(r["alert_count"] > 0 for r in alerts.rows))
        service.clear("demo:rep-a")
        self.assertFalse(any("demo:rep-a" in k for k in service._cache))
        stale = Module2Service(self.published["directory"], latest_source_as_of=date(2026, 7, 2))
        self.assertEqual(stale.get_priorities(Principal("demo:manager", True)).status, "Stale")

    def test_ranking_package_is_unique_and_reasoned(self):
        manifest, table = ranking.read_package(self.root / "ranking")
        self.assertEqual(sorted(table["priority_rank"]), list(range(1, len(table) + 1)))
        reasons = [json.loads(r) for r in table["reason_json"]]
        self.assertTrue(all("not a calibrated store probability" in " ".join(r["notes"]) for r in reasons))
        self.assertEqual(manifest["store_universe"], len(self.stores))
        scoring_manifest, predictions, _ = scoring.read_package(self.root / "scoring")
        self.assertFalse(scoring_manifest["labels_opened"] or scoring_manifest["models_fitted"])
        self.assertEqual(set(predictions["input_status"]) - {"Observed_context", "Missing_context", "Unknown_context"}, set())


if __name__ == "__main__":
    unittest.main()
