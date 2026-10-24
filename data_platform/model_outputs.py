"""Immutable serving snapshots that add released model outputs to curated facts.

The base is an already verified serving artifact. Its business database is
copied into a brand-new artifact; existing facts, scopes semantics and view
definitions stay unchanged, and only the empty typed model-output tables plus
aggregated health/monitoring tables receive rows. Inputs are JSON/Parquet
packages verified by their manifests and hashes; this module imports no model
or UI code. Publication is limited to an isolated candidate root.
"""

import json
import math
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .artifacts import artifact_attempt, require_separate_root, verified_artifact
from .ingestion import digest, literal
from .pipeline import atomic_json
from .quality import Quality
from .serving import MODEL_HEALTH_DDL, MODEL_MONITORING_DDL, VIEWS, publish_serving
from .test_release import _check_counts

PUBLICATION_KINDS = ("Local_review_candidate", "Synthetic_demo")
CANDIDATE_MARKER = "candidate-root.json"


def _package(directory, version):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != version or manifest.get("status") != "Accepted":
        raise ValueError(f"An accepted {version} package is required")
    for name, sha in manifest.get("files", {}).items():
        if Path(name).name != name or digest(directory / name) != sha:
            raise ValueError(f"Package file changed or unsafe: {name}")
    return manifest, digest(directory / "manifest.json")


HEALTH_COLUMNS = ["dataset_hash", "source_build_id", "model_version", "algorithm", "candidate_id", "report_id",
                  "evaluation_kind", "fold_id", "window_start", "window_end", "outcome_cutoff", "group_kind", "group_id",
                  "sample_count", "positive_count", "tp", "fp", "fn", "tn", "alert_count", "candidate_count", "precision",
                  "recall", "roc_auc", "average_precision", "label_coverage", "brier_score", "ece",
                  "meets_numeric_targets", "selected", "parameters_json", "fit_seconds", "calibration_method",
                  "evaluation_status", "limitations_json", "report_sha256", "schema_version"]
_COUNTS = ("sample_count", "positive_count", "tp", "fp", "fn", "tn", "alert_count", "candidate_count")
_FLOATS = ("precision", "recall", "roc_auc", "average_precision", "label_coverage", "brier_score", "ece", "fit_seconds")
_FLAGS = ("meets_numeric_targets", "selected")
_DATES = ("window_start", "window_end", "outcome_cutoff")
DEVELOPMENT_LIMITATIONS = (["Development result; dev2 informed the search, not an independent test",
                            "rolling_dev_1 thresholds tuned on this fold"],
                           ["Development result; dev2 informed the search, not an independent test",
                            "rolling_dev_1 calibrator/thresholds transferred"])


def _packaged(directory, manifest, name):
    entry = (manifest.get("evidence") or {}).get(name)
    if not entry or entry.get("sha256") != (manifest.get("files") or {}).get(entry.get("file")):
        return None, None
    path = Path(directory) / entry["file"]
    return json.loads(path.read_text()), entry["sha256"]


def _rows(base, results, candidates):
    out = []
    for r in results:
        overall = r["group_kind"] == "all"
        out.append({**base, "group_kind": r["group_kind"], "group_id": r["group_id"], "sample_count": r["rows"],
                    "positive_count": r["positives"], "tp": r["tp"], "fp": r["fp"], "fn": r["fn"], "tn": r["tn"],
                    "alert_count": r["alerts"], "candidate_count": candidates if overall else None,
                    "precision": r["precision"], "recall": r["recall"], "roc_auc": r["roc_auc"],
                    "average_precision": r.get("average_precision"),
                    "label_coverage": (r["rows"] / candidates) if (overall and candidates) else None,
                    "brier_score": r.get("brier"), "ece": r.get("ece"),
                    "meets_numeric_targets": r["meets_numeric_targets"]})
    return out


def derive_model_health(package_dir, manifest):
    """Deterministic model_health rows from the evidence packaged with the health manifest.

    Development rows keep the benchmark report's identity and the model's historical plan;
    evaluation rows keep each report's identity. Identity links between bundle, benchmark,
    reports and the declared evaluation source are verified; nothing is taken on trust.
    """
    from .temporal_plan import TemporalPlan
    bundle, bundle_sha = _packaged(package_dir, manifest, "bundle_manifest")
    benchmark, benchmark_sha = _packaged(package_dir, manifest, "benchmark_report")
    source = manifest.get("evaluation_source") or {}
    if benchmark is None:
        raise ValueError("Health package must contain its benchmark report")
    declared = manifest.get("bundle")
    if (bundle is None) != (declared is None):
        raise ValueError("Health bundle identity and packaged bundle manifest disagree")
    if bundle is not None:
        if (bundle_sha != declared.get("manifest_sha256")
                or any(bundle.get(k) != declared.get(k) for k in ("model_version", "candidate_id", "algorithm"))):
            raise ValueError("Packaged bundle manifest differs from the declared bundle identity")
        if bundle.get("selection", {}).get("benchmark_report_sha256") != benchmark_sha:
            raise ValueError("Packaged benchmark is not the one that selected this bundle")
        plan_record = bundle.get("temporal_plan")
        bundle_source = bundle.get("source") or {}
        if any(bundle_source.get(k) != source.get(k) for k in ("dataset_hash", "source_build_id", "plan_sha256")):
            raise ValueError("Bundle training source differs from the declared evaluation source")
    else:
        plan_record = manifest.get("evaluation_plan")
    plan = TemporalPlan.from_dict(plan_record)
    expected = plan.to_dict()
    if (expected["plan_sha256"] != source.get("plan_sha256") or expected["source"]["dataset_hash"] != source.get("dataset_hash")
            or expected["source"]["source_build_id"] != source.get("source_build_id")):
        raise ValueError("Evaluation plan differs from the declared evaluation source")
    if benchmark.get("plan_sha256") not in (None, expected["plan_sha256"]):
        raise ValueError("Benchmark was run under a different plan")
    folds = {row[0]: row for row in plan.development_folds()}
    candidates = benchmark.get("fold_candidates") or {}
    winner = (benchmark.get("selection") or {}).get("winner")
    common = {"dataset_hash": source["dataset_hash"], "source_build_id": source["source_build_id"],
              "calibration_method": "sigmoid", "schema_version": "module2.model_health.v1"}
    rows = []
    for trial in benchmark.get("trials") or []:
        selected = trial["trial_id"] == winner
        version = (bundle["model_version"] if (selected and bundle) else
                   f"development-candidate:{benchmark['run_id']}:{trial['trial_id']}")
        for index, fold in enumerate(("rolling_dev_1", "rolling_dev_2")):
            bounds = folds[fold]
            base = {**common, "model_version": version, "algorithm": trial["algorithm"], "candidate_id": trial["trial_id"],
                    "report_id": f"{benchmark['run_id']}:{trial['trial_id']}:{fold}", "evaluation_kind": "development",
                    "fold_id": fold, "window_start": date.fromisoformat(bounds[2]),
                    "window_end": date.fromisoformat(bounds[3]), "outcome_cutoff": date.fromisoformat(bounds[4]),
                    "selected": selected, "parameters_json": json.dumps(trial["params"], sort_keys=True),
                    "fit_seconds": (trial.get("fit_seconds") or {}).get(fold),
                    "evaluation_status": "Completed" if trial["status"] == "Complete" else trial["status"],
                    "limitations_json": json.dumps(DEVELOPMENT_LIMITATIONS[index]), "report_sha256": benchmark_sha}
            part = trial.get("dev1" if index == 0 else "dev2")
            if part:
                rows += _rows(base, part["results"], candidates.get(fold))
            else:
                rows.append({**base, "group_kind": "all", "group_id": "all", "candidate_count": candidates.get(fold)})
    for role, kind, fold_id in (("validation", "iterative_validation", "rolling_validation"),
                                ("holdout", "holdout_confirmation", "rolling_test")):
        report, report_sha = _packaged(package_dir, manifest, f"{role}_report")
        if report is None:
            continue
        if (bundle is None or report.get("kind") != kind or report.get("bundle_manifest_sha256") != bundle_sha
                or any(report.get(k) != source.get(k) for k in ("dataset_hash", "source_build_id", "plan_sha256"))
                or any(report.get(k) != bundle.get(k) for k in ("model_version", "candidate_id", "algorithm"))):
            raise ValueError(f"The {role} report does not belong to this bundle/evaluation source")
        window = report["window"]
        base = {**common, "model_version": bundle["model_version"], "algorithm": bundle["algorithm"],
                "candidate_id": bundle["candidate_id"], "report_id": f"{kind}:{report_sha[:16]}",
                "evaluation_kind": kind, "fold_id": fold_id,
                "window_start": date.fromisoformat(window["scoring_start"]),
                "window_end": date.fromisoformat(window["scoring_end"]),
                "outcome_cutoff": date.fromisoformat(window["outcome_end"]), "selected": True,
                "parameters_json": json.dumps(bundle.get("params") or {}, sort_keys=True),
                "fit_seconds": bundle.get("fit_seconds"), "evaluation_status": "Completed",
                "limitations_json": json.dumps(report.get("limitations") or []), "report_sha256": report_sha}
        rows += _rows(base, report["results"], report.get("candidate_rows"))
    return health_frame(rows)


def health_frame(rows):
    frame = pd.DataFrame(rows).reindex(columns=HEALTH_COLUMNS)
    for column in _COUNTS:
        frame[column] = frame[column].astype("Int64")
    for column in _FLAGS:
        frame[column] = frame[column].astype("boolean")
    for column in _FLOATS:
        frame[column] = frame[column].astype("Float64")
    for column in _DATES:
        frame[column] = pd.to_datetime(frame[column]).dt.date
    return frame


def _normalize(frame):
    out = frame.reindex(columns=HEALTH_COLUMNS).copy()
    for column in _DATES:
        out[column] = pd.to_datetime(out[column]).dt.date
    records = []
    for row in out.to_dict("records"):
        clean = {}
        for k, v in row.items():
            if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
                v = None
            elif k in _FLOATS:
                v = float(v)
            elif k in _COUNTS:
                v = int(v)
            elif k in _FLAGS:
                v = bool(v)
            clean[k] = v
        records.append(clean)
    key = ("model_version", "report_id", "group_kind", "group_id")
    return sorted(records, key=lambda r: tuple(str(r[k]) for k in key))


def reconcile_health(package_dir, manifest):
    """The supplied parquet must equal the derivation exactly; returns the trusted rows."""
    derived = derive_model_health(package_dir, manifest)
    supplied = pd.read_parquet(Path(package_dir) / "model_health.parquet")
    a, b = _normalize(supplied), _normalize(derived)
    if len(a) != len(b):
        raise ValueError("Health rows do not match the packaged evaluation evidence (row count)")
    for left, right in zip(a, b):
        for column in HEALTH_COLUMNS:
            x, y = left[column], right[column]
            if isinstance(x, float) and isinstance(y, float):
                if not math.isclose(x, y, rel_tol=0, abs_tol=1e-12):
                    raise ValueError(f"Health row differs from its report: {column}")
            elif x != y:
                raise ValueError(f"Health row differs from its report: {column}")
    return derived


def _qualifying_report(directory, health, role, bundle_sha, kinds):
    """A passing evaluation report of exactly this bundle, re-checked from its counts."""
    report, sha = _packaged(directory, health, f"{role}_report")
    if report is None:
        raise ValueError(f"Qualification requires the {role} report inside the health package")
    entry = {"sha256": sha}
    if (report.get("version") != "module2.evaluation_report.v2" or report.get("kind") not in kinds
            or report.get("status") != "Evaluated" or report.get("bundle_manifest_sha256") != bundle_sha):
        raise ValueError(f"The {role} report does not evaluate the scored bundle")
    results = report.get("results") or []
    overall = [r for r in results if r.get("group_kind") == "all"]
    if len(overall) != 1 or report.get("numeric_gate_passed") is not True:
        raise ValueError(f"The {role} report did not pass the numeric gate")
    _check_counts(overall[0], report.get("eligible_rows"))
    return entry["sha256"]


def _check_identities(base, cutoff, health, health_dir, scoring, monitoring, publication_kind, outputs_available):
    """Packages must belong together; status strings alone never qualify predictions."""
    synthetic = {bool(health.get("synthetic"))}
    if scoring is not None:
        synthetic.add(bool(scoring.get("synthetic")))
    if monitoring is not None:
        synthetic.add(bool(monitoring.get("synthetic")))
    if len(synthetic) != 1:
        raise ValueError("Synthetic and real packages cannot be mixed")
    is_synthetic = synthetic.pop()
    if (publication_kind == "Synthetic_demo") != is_synthetic:
        raise ValueError("Synthetic_demo requires synthetic packages; real candidates require real packages")
    bundle = health.get("bundle")
    if scoring is not None:
        scored = scoring.get("bundle") or {}
        if not bundle or any(bundle.get(k) != scored.get(k) for k in ("manifest_sha256", "model_version", "candidate_id")):
            raise ValueError("Health and scoring packages describe different model bundles")
        source = scored.get("training_source") or {}
        evaluation = health.get("evaluation_source") or {}
        if (evaluation.get("dataset_hash") != source.get("dataset_hash")
                or evaluation.get("source_build_id") != source.get("source_build_id")
                or evaluation.get("plan_sha256") != source.get("plan_sha256")):
            raise ValueError("Health evaluation source differs from the scored bundle's training source")
    if monitoring is not None:
        if (monitoring.get("source", {}).get("dataset_hash") != base["dataset_hash"]
                or monitoring.get("source", {}).get("source_build_id") != base["source_build_id"]
                or monitoring.get("observation_cutoff") != str(cutoff)
                or not bundle or monitoring.get("bundle_manifest_sha256") != bundle.get("manifest_sha256")):
            raise ValueError("Monitoring package belongs to a different source, date or model")
    if not outputs_available:
        return []
    if scoring is None:
        raise ValueError("Available model outputs require scoring and ranking packages")
    reports = [_qualifying_report(health_dir, health, "validation", bundle["manifest_sha256"], ("iterative_validation",))]
    if publication_kind == "Local_review_candidate":
        if not (scoring["bundle"].get("eligibility_status") == health.get("eligibility_status") == "Eligible_for_review"):
            raise ValueError("Real model outputs require an Eligible_for_review scored bundle")
        reports.append(_qualifying_report(health_dir, health, "holdout", bundle["manifest_sha256"],
                                          ("holdout_confirmation", "independent_test")))
    return reports


def _candidate_root(output_root, base_dir):
    root = Path(output_root).resolve()
    base_root = Path(base_dir).resolve().parent.parent
    if root == base_root or base_root in root.parents or root in base_root.parents:
        raise ValueError("Model-output candidates must use an isolated root, not the base serving root")
    marker = root / CANDIDATE_MARKER
    pointer = root / "published/serving.json"
    if pointer.exists() and not marker.exists():
        raise ValueError("Refusing to publish into an existing non-candidate (formal) serving root")
    if not marker.exists():
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(marker, {"purpose": "Isolated Module 2 review-candidate publications only",
                             "allowed_publication_kinds": list(PUBLICATION_KINDS)})
    return root


def build_serving_with_model_outputs(serving_artifact_dir, scoring_package_dir, ranking_package_dir, output_root, *,
                                     health_package_dir, monitoring_package_dir=None, access_policy=None,
                                     publish=False, publication_kind="Local_review_candidate",
                                     memory_limit="1GB", threads=2):
    if publication_kind not in PUBLICATION_KINDS:
        raise ValueError("Unsupported publication kind")
    base, base_db, cutoff = verified_artifact(serving_artifact_dir, kind="serving")
    if not base.get("application_ready"):
        raise ValueError("Base serving artifact is not application-ready")
    root = _candidate_root(output_root, serving_artifact_dir)
    inputs = [p for p in (serving_artifact_dir, scoring_package_dir, ranking_package_dir, health_package_dir) if p]
    require_separate_root(root, *inputs)
    health, health_sha = _package(health_package_dir, "module2.health_package.v1")
    monitoring, monitoring_sha = (_package(monitoring_package_dir, "module2.monitoring_package.v1")
                                  if monitoring_package_dir else (None, None))
    outputs_available = health.get("model_outputs_status") == "Available"
    scoring = ranking = None
    scoring_sha = ranking_sha = None
    if (scoring_package_dir is None) != (ranking_package_dir is None):
        raise ValueError("Scoring and ranking packages are published together")
    if scoring_package_dir is not None:
        scoring, scoring_sha = _package(scoring_package_dir, "module2.scoring_package.v1")
        ranking, ranking_sha = _package(ranking_package_dir, "module2.ranking_package.v1")
        if (scoring["source"]["dataset_hash"] != base["dataset_hash"] or scoring["source"]["source_build_id"] != base["source_build_id"]
                or scoring["scoring_date"] != str(cutoff) or ranking["scoring_run_id"] != scoring["scoring_run_id"]
                or ranking["scoring_manifest_sha256"] != scoring_sha or ranking["scoring_date"] != scoring["scoring_date"]):
            raise ValueError("Scoring/ranking packages differ from the base serving source, date or selected run")
    qualifying_reports = _check_identities(base, cutoff, health, health_package_dir, scoring, monitoring,
                                           publication_kind, outputs_available)
    health_rows = reconcile_health(health_package_dir, health)
    policy = json.loads(Path(access_policy).read_text()) if access_policy else None
    if policy is not None and (not isinstance(policy.get("version"), str) or not isinstance(policy.get("assignments"), list)):
        raise ValueError("Access policy needs a version and assignments list")
    before = {"serving": digest(base_db)}
    with artifact_attempt(root, "serving", {
        "as_of": str(cutoff), "dataset_hash": base["dataset_hash"], "source_build_id": base["source_build_id"],
        "feature_artifact_id": base["feature_artifact_id"], "metric_view": base["metric_view"],
        "feature_purpose": base.get("feature_purpose"), "estimate_policy": base.get("estimate_policy"),
        "estimate_views_status": base.get("estimate_views_status"),
        "available_metric_views": base.get("available_metric_views"),
        "access_policy_version": policy["version"] if policy else base.get("access_policy_version"),
        "base_serving_artifact_id": base["artifact_id"], "base_serving_database_sha256": base["database_sha256"],
    }) as (directory, record):
        target = directory / "business.duckdb"
        shutil.copyfile(base_db, target)
        con = duckdb.connect(str(target), config={"memory_limit": memory_limit, "threads": threads})
        q = Quality(con, record["artifact_id"])
        try:
            facts = [name for name in ("mart_store_sku_daily", "mart_store_day", "mart_commercial_period",
                                       "mart_execution_visit", "dim_stores", "dim_products")]
            fingerprint = {n: con.execute(f"SELECT count(*), sum(hash(COLUMNS(*))) FROM gold.{n}").fetchone() for n in facts}
            for name, ddl in (("model_health", MODEL_HEALTH_DDL), ("model_monitoring", MODEL_MONITORING_DDL)):
                exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='gold' AND table_name=?",
                                     [name]).fetchone()[0]
                if not exists:
                    con.execute(ddl)
                    con.execute(f"CREATE VIEW serving.vw_{name} AS SELECT * FROM gold.{name}")
            for table in ("predictions_oos", "mart_visit_priority", "model_health", "model_monitoring"):
                if con.execute(f"SELECT count(*) FROM gold.{table}").fetchone()[0]:
                    raise ValueError("Base serving already contains model outputs; refusing in-place reuse")
            if outputs_available:
                con.execute(f"""INSERT INTO gold.predictions_oos SELECT build_id,dataset_hash,scoring_run_id,
                    cast(scoring_date AS DATE),store_id,sku_id,probability,threshold,is_predicted_oos,drivers_json,
                    model_version,feature_version,input_status
                    FROM read_parquet({literal(Path(scoring_package_dir).resolve()/'predictions.parquet')})""")
                con.execute(f"""INSERT INTO gold.mart_visit_priority SELECT build_id,dataset_hash,scoring_run_id,
                    cast(scoring_date AS DATE),store_id,priority_rank,risk_score,msl_gap_count,ranking_policy_version,reason_json
                    FROM read_parquet({literal(Path(ranking_package_dir).resolve()/'ranking.parquet')})""")
            columns = [r[0] for r in con.execute("DESCRIBE gold.model_health").fetchall()]
            con.register("_trusted_health", health_rows)
            con.execute(f"INSERT INTO gold.model_health SELECT {','.join(columns)} FROM _trusted_health")
            con.unregister("_trusted_health")
            if monitoring:
                columns = [r[0] for r in con.execute("DESCRIBE gold.model_monitoring").fetchall()]
                con.execute(f"INSERT INTO gold.model_monitoring SELECT {','.join(columns)} FROM read_parquet({literal(Path(monitoring_package_dir).resolve()/'model_monitoring.parquet')})")
            if policy is not None:
                valid = {r[0] for r in con.execute("SELECT store_id FROM gold.dim_stores").fetchall()}
                con.execute("DELETE FROM gold.account_scope")
                for row in policy["assignments"]:
                    if set(row) != {"principal_id", "store_id", "role"} or not row["principal_id"] or row["store_id"] not in valid \
                            or row["role"] not in {"sales", "manager"}:
                        raise ValueError("Invalid or unknown account-scope assignment")
                    con.execute("INSERT INTO gold.account_scope VALUES (?,?,?,?)",
                                [row["principal_id"], row["store_id"], row["role"], policy["version"]])
            for n in facts:
                q.record("fact_parity", n, int(con.execute(f"SELECT count(*), sum(hash(COLUMNS(*))) FROM gold.{n}").fetchone() != fingerprint[n]))
            q.query("prediction_grain", "predictions_oos", "SELECT scoring_date,store_id,sku_id FROM gold.predictions_oos GROUP BY ALL HAVING count(*)<>1")
            q.query("single_scoring_run", "predictions_oos", "SELECT 1 FROM (SELECT DISTINCT scoring_run_id FROM gold.predictions_oos) HAVING count(*)>1")
            q.query("probability_range", "predictions_oos", "SELECT * FROM gold.predictions_oos WHERE probability NOT BETWEEN 0 AND 1 OR threshold NOT BETWEEN 0 AND 1 OR probability IS NULL")
            q.query("prediction_source", "predictions_oos", f"""SELECT * FROM gold.predictions_oos
                WHERE dataset_hash<>{literal(base['dataset_hash'])} OR build_id<>{literal(base['source_build_id'])}
                   OR scoring_date<>DATE {literal(cutoff)}""")
            if outputs_available:
                q.query("prediction_coverage", "predictions_oos", f"""SELECT d.store_id,d.sku_id FROM gold.mart_store_sku_daily d
                    LEFT JOIN gold.predictions_oos p USING (store_id,sku_id) WHERE d.date=DATE {literal(cutoff)} AND p.sku_id IS NULL
                    UNION ALL SELECT p.store_id,p.sku_id FROM gold.predictions_oos p LEFT JOIN gold.mart_store_sku_daily d
                    ON d.store_id=p.store_id AND d.sku_id=p.sku_id AND d.date=DATE {literal(cutoff)} WHERE d.sku_id IS NULL""")
                q.query("rank_unique_dense", "mart_visit_priority", """SELECT 1 FROM gold.mart_visit_priority
                    HAVING count(DISTINCT priority_rank)<>count(*) OR min(priority_rank)<>1 OR max(priority_rank)<>count(*)
                       OR count(DISTINCT store_id)<>count(*)""")
                q.query("rank_run_match", "mart_visit_priority", """SELECT * FROM gold.mart_visit_priority
                    WHERE scoring_run_id NOT IN (SELECT DISTINCT scoring_run_id FROM gold.predictions_oos)""")
            q.query("health_grain", "model_health", "SELECT model_version,report_id,group_kind,group_id FROM gold.model_health GROUP BY ALL HAVING count(*)<>1")
            q.query("health_metric_range", "model_health", """SELECT * FROM gold.model_health WHERE
                precision NOT BETWEEN 0 AND 1 OR recall NOT BETWEEN 0 AND 1 OR roc_auc NOT BETWEEN 0 AND 1
                OR average_precision NOT BETWEEN 0 AND 1 OR label_coverage NOT BETWEEN 0 AND 1 OR fit_seconds<0
                OR (sample_count IS NOT NULL AND tp+fp+fn+tn<>sample_count)""")
            q.query("no_restricted_tables", "serving", "SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('bronze','silver','feature_input','evaluation')")
            q.query("no_label_columns", "serving", """SELECT table_name,column_name FROM information_schema.columns
                WHERE table_schema IN ('gold','serving') AND column_name IN ('target_oos_7d','observed_future_oos_days','confirmed_observed_oos','label_source')""")
            q.query("no_private_paths", "model_health", "SELECT * FROM gold.model_health WHERE parameters_json LIKE '%/%' OR limitations_json LIKE '%/Users/%'")
            if health.get("bundle"):
                q.query("health_selected_model", "model_health", f"""SELECT * FROM gold.model_health
                    WHERE evaluation_kind IN ('iterative_validation','holdout_confirmation','independent_test')
                      AND model_version<>{literal(health['bundle']['model_version'])}""")
            q.record("health_rows_derived_from_evidence", "model_health", 0, checked_count=len(health_rows))
            for name in ("model_health", "model_monitoring"):
                q.record("view_present", name, int(not con.execute("SELECT count(*) FROM information_schema.views WHERE table_schema='serving' AND table_name=?",
                                                                   ["vw_" + name]).fetchone()[0]))
            q.record("base_immutable", "serving", int(digest(base_db) != before["serving"]))
            q.require_pass()
            status = "Available" if outputs_available else health.get("model_outputs_status", "Unavailable_model_not_released")
            con.execute("DROP TABLE IF EXISTS meta.publication_info")
            con.execute("CREATE TABLE meta.publication_info AS SELECT ?::VARCHAR AS dataset_hash,?::DATE AS as_of,?::VARCHAR AS model_outputs_status,?::VARCHAR AS metric_view,?::VARCHAR AS publication_kind",
                        [base["dataset_hash"], cutoff, status, base["metric_view"], publication_kind])
            record.update(database="business.duckdb", application_ready=True, model_outputs_status=status,
                          readiness={**base.get("readiness", {}), "module2": f"{publication_kind}: model outputs {status}"},
                          rows={name: con.execute(f"SELECT count(*) FROM gold.{relation}").fetchone()[0]
                                for name, (relation, _, _) in VIEWS.items()},
                          model_outputs={"status": status, "publication_kind": publication_kind,
                                         "synthetic": bool(health.get("synthetic")),
                                         "scoring_run_id": scoring["scoring_run_id"] if scoring else None,
                                         "scoring_date": scoring["scoring_date"] if scoring else None,
                                         "scoring_manifest_sha256": scoring_sha, "ranking_manifest_sha256": ranking_sha,
                                         "health_manifest_sha256": health_sha, "monitoring_manifest_sha256": monitoring_sha,
                                         "bundle_manifest_sha256": scoring["bundle"]["manifest_sha256"] if scoring else None,
                                         "model_version": scoring["bundle"]["model_version"] if scoring else health.get("model_version"),
                                         "eligibility_status": health.get("eligibility_status"),
                                         "qualifying_report_sha256": qualifying_reports,
                                         "benchmark_status": health.get("benchmark_status"),
                                         "ranking_policy_version": ranking["ranking_policy_version"] if ranking else None})
        finally:
            atomic_json(directory / "quality.json", q.report())
            con.close()
    if publish:
        record = {**record, "publication": publish_serving(root, record["directory"])}
    return record
