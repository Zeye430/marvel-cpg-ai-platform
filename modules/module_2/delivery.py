"""End-to-end orchestration: doctor, deliver (synthetic|v3) and the daily batch.

Every path is explicit; nothing discovers the "newest" directory. The v3
profile requires a genuine owner execution policy and a new output root inside
its authorized roots; the synthetic profile never reads real sources.
"""

import importlib.util
import json
import platform
import shutil
import sys
import time
from pathlib import Path

from . import bundles, config, evaluation, governance, inputs, monitoring, publishing, ranking, scoring
from .contracts import (Module2Error, check_resources, code_hashes, config_sha256, read_json, sha256_file,
                        utc_now, write_json)


def _log(run_dir, step, **details):
    line = json.dumps({"at": utc_now(), "step": step, **details}, sort_keys=True, default=str)
    path = Path(run_dir) / "logs" / "deliver.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def doctor(workflow_run=None, output_root=None):
    """Read-only environment/input readiness; no training, network or pointer writes."""
    report = {"python": platform.python_version(), "checks": []}

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    for module in ("duckdb", "pandas", "numpy", "pyarrow", "sklearn", "lightgbm", "xgboost", "optuna", "joblib",
                   "shiny", "shinywidgets", "plotly"):
        found = importlib.util.find_spec(module) is not None
        check(f"import:{module}", found, "" if found else "install with: uv sync --extra module2 --extra app")
    check("python_3.13", sys.version_info[:2] == (3, 13), platform.python_version())
    target = Path(output_root or config.DELIVERY_ROOT)
    probe = target if target.exists() else target.parent
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    check("free_space_reserve", free >= config.RESOURCE_LIMITS["free_space_reserve_bytes"], f"{free / 1024**3:.1f} GiB free")
    if workflow_run:
        try:
            workflow = inputs.load_workflow(workflow_run)
            check("workflow_accepted_trailing_30d", True, workflow["plan"].to_dict()["plan_id"])
            for name in ("base", "features", "labels", "scoring_features", "serving"):
                component = workflow["components"].get(name, {})
                directory = component.get("directory") or str(Path(component.get("database", "")).parent)
                check(f"component:{name}", Path(directory).exists(), directory)
        except (Module2Error, OSError, ValueError, KeyError) as error:
            check("workflow_accepted_trailing_30d", False, str(error))
    for name in ("training.py", "validation.py", "data_adapter.py"):
        check(f"legacy_present:{name}", (Path(__file__).with_name(name)).exists())
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    check("browser_for_acceptance", chrome.exists() or shutil.which("google-chrome") is not None,
          "Headless Chrome for tests/browser (optional for running the app)")
    check("node_for_acceptance", shutil.which("node") is not None, "Node for tests/browser (optional)")
    report["ok"] = all(c["ok"] for c in report["checks"] if not c["check"].endswith("_for_acceptance"))
    return report


def _synthetic_workflow(output_root):
    """Generate SYNTHETIC sources and build the shared rolling workflow inside the run root."""
    from data_platform.workflow import build_all

    from . import synthetic
    platform_root = output_root / "synthetic-platform"
    if not platform_root.exists():
        synthetic.generate(platform_root / "raw", stores_per_segment=4, skus=12)
        scope = platform_root / "base-scope.json"
        write_json(scope, {"version": "synthetic-unconfigured", "assignments": []})
        build_all(config.SYNTHETIC_AS_OF, platform_root / "workflow", source_dir=platform_root / "raw",
                  snapshot_root=platform_root / "snapshots", access_policy=scope, rolling_evaluation=True,
                  assume_complete_snapshot=True, threads=config.THREADS)
    runs = sorted((platform_root / "workflow" / "runs").glob("*/run.json"))
    accepted = [r for r in runs if read_json(r).get("status") == "Accepted"]
    if len(accepted) != 1:
        raise Module2Error("Synthetic platform must contain exactly one accepted workflow run")
    return accepted[0]


def _synthetic_policy(output_root, workflow):
    path = output_root / "governance" / "synthetic-policy.json"
    if not path.exists():
        governance.create_policy(
            path, human_authority={"approver": "SYNTHETIC DEMO (no human approval; synthetic data only)",
                                   "approved_at": utc_now(), "message": "synthetic profile",
                                   "message_reference": "synthetic-profile-auto"},
            workflow=workflow, allowed_output_roots=[output_root], ledger_path=output_root / "governance" / "ledger.jsonl",
            known_history="SYNTHETIC: generated data, no history",
            prior_exposures=[], synthetic=True)
    return path


def deliver(*, profile, output_root, authority_file=None, workflow_run=None):
    """Run the approved chain once in a new output root; returns the final report."""
    started = time.monotonic()
    output_root = Path(output_root).resolve()
    if profile not in ("synthetic", "v3"):
        raise Module2Error("profile must be synthetic or v3")
    synthetic = profile == "synthetic"
    if output_root.exists() and any(p for p in output_root.iterdir() if p.name != "synthetic-platform"):
        raise Module2Error("deliver needs a new output root; existing runs are never rewritten")
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{profile}-{utc_now()[:19].replace(':', '').replace('-', '')}"
    if synthetic:
        workflow_run = _synthetic_workflow(output_root)
    else:
        if authority_file is None:
            raise Module2Error("The v3 profile requires --authority-file (genuine owner execution policy)")
        workflow_run = Path(workflow_run or config.PINNED_WORKFLOW)
    workflow = inputs.load_workflow(workflow_run)
    if synthetic:
        authority_file = _synthetic_policy(output_root, workflow)
    policy = governance.check_policy(authority_file, workflow, output_root)
    if synthetic != bool(policy.get("synthetic")):
        raise Module2Error("Synthetic and real profiles cannot share an execution policy")
    run_dir = output_root
    write_json(run_dir / "config.json", {
        "version": config.VERSIONS["deliver"], "run_id": run_id, "profile": profile, "synthetic": synthetic,
        "workflow_run": workflow["path"], "workflow_sha256": workflow["sha256"],
        "plan": workflow["plan"].to_dict(), "authority_file": str(Path(authority_file).resolve()),
        "authority_sha256": sha256_file(authority_file), "config_sha256": config_sha256(),
        "code_hashes": code_hashes(), "rules": config.frozen_rules(), "created_at": utc_now()}, exclusive=True)
    _log(run_dir, "start", run_id=run_id, profile=profile)
    check_resources(run_dir, stage="deliver start")
    final = {"run_id": run_id, "profile": profile, "synthetic": synthetic, "output_root": str(run_dir), "steps": {}}
    inputs.export_development(workflow, run_dir / "inputs" / "development")
    _log(run_dir, "development_exported")
    report = benchmark_run(run_dir, profile, run_id)
    final["steps"]["benchmark"] = {"status": report["status"], "winner": report["selection"]["winner"],
                                   "selection_status": report["selection"]["selection_status"],
                                   "report_sha256": sha256_file(run_dir / "benchmark" / "report.json")}
    bundle_dir = run_dir / "bundle"
    if report["status"] == "Complete" and report["selection"]["selection_status"] == "Selected":
        bundles.freeze_winner(run_dir, workflow)
        _log(run_dir, "bundle_frozen")
        validation = evaluation.validate(run_dir, workflow)
        final["steps"]["validation"] = _summary(validation, run_dir / "evaluation" / "validation" / "report.json")
        _log(run_dir, "validated", passed=validation["numeric_gate_passed"])
        if validation["numeric_gate_passed"]:
            holdout = evaluation.run_holdout(run_dir, workflow, authority_file)
            final["steps"]["holdout"] = _summary(holdout, run_dir / "evaluation" / "holdout" / "report.json")
            _log(run_dir, "holdout_evaluated", passed=holdout["numeric_gate_passed"])
        else:
            final["steps"]["holdout"] = {"status": "Not_run_validation_failed"}
    else:
        final["steps"]["validation"] = {"status": "Not_run_benchmark_incomplete_or_no_winner"}
        final["steps"]["holdout"] = {"status": "Not_run"}
    chain = {"steps": {}, "attempt": 1, "started_at": utc_now()}
    final.update(serving_candidate(run_dir, workflow, synthetic=synthetic, record=chain))
    if (bundle_dir / "bundle.json").exists():
        final["daily_key_id"] = record_completion(run_dir, workflow, sha256_file(bundle_dir / "bundle.json"), chain)
    final["eligibility"] = read_json(bundle_dir / "eligibility.json") if (bundle_dir / "eligibility.json").exists() \
        else {"status": "No_bundle"}
    final["wall_seconds"] = time.monotonic() - started
    final["model_files"] = sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*")
                                  if p.suffix in (".txt", ".ubj", ".joblib") and "synthetic-platform" not in p.parts)
    final["finished_at"] = utc_now()
    write_json(run_dir / "final-report.json", final)
    _log(run_dir, "finished", eligibility=final["eligibility"]["status"])
    return final


def _summary(report, path):
    overall = report["results"][0]
    return {"kind": report["kind"], "numeric_gate_passed": report["numeric_gate_passed"],
            "precision": overall["precision"], "recall": overall["recall"], "roc_auc": overall["roc_auc"],
            "eligible_rows": report["eligible_rows"], "candidate_rows": report["candidate_rows"],
            "label_coverage": report["label_coverage"], "report_sha256": sha256_file(path)}


def benchmark_run(run_dir, profile, run_id):
    from . import benchmark
    return benchmark.run_benchmark(run_dir, profile="synthetic" if profile == "synthetic" else "v3", run_id=run_id)


def _base_serving(workflow):
    return workflow["components"]["serving"]["directory"]


# ----------------------------------------------------------------------------
# Resumable serving chain and durable daily completion records.
# ----------------------------------------------------------------------------

PACKAGE_VERSIONS = {"scoring": "module2.scoring_package.v1", "ranking": "module2.ranking_package.v1",
                    "health": "module2.health_package.v1", "monitoring": "module2.monitoring_package.v1"}
DAILY_STATE = "daily-state.json"


def verified_package(directory, version):
    """Manifest SHA-256 of an accepted, intact package; None for partial/foreign/empty directories."""
    directory = Path(directory)
    try:
        manifest = read_json(directory / "manifest.json")
    except Module2Error:
        return None
    if manifest.get("version") != version or manifest.get("status") != "Accepted":
        return None
    for name, sha in (manifest.get("files") or {}).items():
        if Path(name).name != name or not (directory / name).is_file() or sha256_file(directory / name) != sha:
            return None
    return sha256_file(directory / "manifest.json")


def daily_key(workflow, bundle_sha):
    plan = workflow["plan"]
    key = {"dataset_hash": plan.dataset_hash, "source_build_id": plan.source_build_id,
           "scoring_date": plan.source_as_of.isoformat(), "bundle_manifest_sha256": bundle_sha}
    from .contracts import json_sha256
    return key, json_sha256(key)[:16]


def _load_state(run_dir):
    path = Path(run_dir) / DAILY_STATE
    state = read_json(path, "daily state") if path.exists() else {"version": "module2.daily_state.v1", "runs": {}}
    if state.get("version") != "module2.daily_state.v1" or not isinstance(state.get("runs"), dict):
        raise Module2Error("Unsupported daily state; refusing to guess completion")
    return state


def _save_state(run_dir, state):
    write_json(Path(run_dir) / DAILY_STATE, state)


def publication_valid(serving_root, publication_id, scoring_sha, health_sha, artifact=None):
    """History record + verified serving artifact (database/quality hashes) naming exactly these packages."""
    from data_platform.artifacts import verified_artifact
    history = Path(serving_root) / "published" / "history" / f"{publication_id}.json"
    try:
        record = read_json(history, "publication history")
        verified_artifact(record["directory"], kind="serving")
    except (Module2Error, ValueError, OSError, KeyError):
        return None
    outputs = record.get("model_outputs") or {}
    if ((artifact is not None and str(Path(record["directory"])) != str(Path(artifact)))
            or outputs.get("scoring_manifest_sha256") != scoring_sha or outputs.get("health_manifest_sha256") != health_sha):
        return None
    return record


def _published(serving_root, scoring_sha, health_sha):
    """An existing *valid* publication of exactly these packages (crash after publish, before the record)."""
    history = Path(serving_root) / "published" / "history"
    for path in sorted(history.glob("*.json")) if history.exists() else []:
        record = publication_valid(serving_root, path.stem, scoring_sha, health_sha)
        if record is not None:
            return record
    return None


def serving_candidate(run_dir, workflow, *, synthetic, label=None, record=None, save=None):
    """Score/rank (if a bundle exists), issue, monitor, health package and isolated publication.

    ``record`` holds verified step identities; on retry each step reuses its verified
    immutable package, partial directories are never trusted, and side effects
    (issued history, publication) are adopted instead of repeated.
    """
    import duckdb

    from data_platform.artifacts import verified_artifact
    run_dir = Path(run_dir)
    label = label or workflow["plan"].source_as_of.isoformat()
    record = record if record is not None else {"steps": {}, "attempt": 1}
    steps = record.setdefault("steps", {})
    save = save or (lambda: None)
    bundle_dir = run_dir / "bundle"
    base_dir = _base_serving(workflow)
    record_base, database, _ = verified_artifact(base_dir, kind="serving")
    with duckdb.connect(str(database), read_only=True) as con:
        products = con.execute("SELECT sku_id,list_price,is_msl FROM gold.dim_products").df()
        stores = con.execute("SELECT store_id FROM gold.dim_stores").df()
        daily = con.execute("SELECT date,store_id,sku_id,oos_observed FROM gold.mart_store_sku_daily").df()

    def fresh(kind):
        path = run_dir / kind / label
        n = record.get("attempt", 1)
        while path.exists():
            n += 1
            path = run_dir / kind / f"{label}-a{n}"
        return path

    def recorded(kind):
        entry = steps.get(kind)
        if entry and verified_package(entry["dir"], PACKAGE_VERSIONS[kind]) == entry["manifest_sha256"]:
            return Path(entry["dir"])
        return None

    def remember(kind, directory):
        steps[kind] = {"dir": str(directory), "manifest_sha256": verified_package(directory, PACKAGE_VERSIONS[kind])}
        save()
        return Path(directory)

    result = {}
    scoring_dir = ranking_dir = monitoring_dir = None
    if (bundle_dir / "bundle.json").exists():
        bundle = bundles.load_bundle(bundle_dir)
        plan = workflow["plan"]

        def matches(directory):
            manifest = read_json(Path(directory) / "manifest.json")
            return (manifest["source"]["dataset_hash"] == plan.dataset_hash
                    and manifest["source"]["source_build_id"] == plan.source_build_id
                    and manifest["scoring_date"] == plan.source_as_of.isoformat()
                    and manifest["bundle"]["manifest_sha256"] == bundle.manifest_sha256
                    and bool(manifest.get("synthetic")) == bool(synthetic))
        scoring_dir = recorded("scoring")
        if scoring_dir is None:
            # Prefer the package already in the issued history, then any intact package of this identity.
            entries, _ = monitoring.issued_history(run_dir / "issued-history")
            issued = {e["scoring_manifest_sha256"] for e in entries}
            candidates = [d for d in sorted((run_dir / "scoring").glob(f"{label}*")) if d.is_dir()
                          and verified_package(d, PACKAGE_VERSIONS["scoring"]) and matches(d)]
            candidates.sort(key=lambda d: sha256_file(d / "manifest.json") not in issued)
            if candidates:
                scoring_dir = remember("scoring", candidates[0])
            else:
                target = fresh("scoring")
                scoring.score(workflow, bundle_dir, target, synthetic=synthetic)
                scoring_dir = remember("scoring", target)
        scoring_sha = steps["scoring"]["manifest_sha256"]
        ranking_dir = recorded("ranking")
        if ranking_dir is None or read_json(ranking_dir / "manifest.json")["scoring_manifest_sha256"] != scoring_sha:
            candidates = [d for d in sorted((run_dir / "ranking").glob(f"{label}*")) if d.is_dir()
                          and verified_package(d, PACKAGE_VERSIONS["ranking"])
                          and read_json(d / "manifest.json")["scoring_manifest_sha256"] == scoring_sha]
            if candidates:
                ranking_dir = remember("ranking", candidates[0])
            else:
                target = fresh("ranking")
                ranking.rank(scoring_dir, products, stores, target)
                ranking_dir = remember("ranking", target)
        current = inputs.scoring_features(workflow, plan.source_as_of, plan.source_as_of, purpose_label="drift check")[1]
        health_dir = recorded("health") or remember("health", _health(run_dir, workflow, fresh("health"), synthetic))
        health = read_json(health_dir / "manifest.json")
        if health["model_outputs_status"] == "Available" and not steps.get("issued"):
            steps["issued"] = monitoring.record_issued(scoring_dir, run_dir / "issued-history", synthetic=synthetic)
            save()
        monitoring_dir = recorded("monitoring")
        if monitoring_dir is None:
            target = fresh("monitoring")
            monitoring.monitor(history_root=run_dir / "issued-history", daily=daily,
                               observation_cutoff=plan.source_as_of, current_features=current, bundle=bundle,
                               source=plan.to_dict()["source"], output_dir=target, synthetic=synthetic)
            monitoring_dir = remember("monitoring", target)
        result["scoring"] = {"dir": str(scoring_dir), "manifest_sha256": scoring_sha,
                             **{k: read_json(scoring_dir / "manifest.json")[k] for k in ("rows", "alerts", "input_status_counts")}}
        rank_manifest = read_json(ranking_dir / "manifest.json")
        result["ranking"] = {"dir": str(ranking_dir), "manifest_sha256": steps["ranking"]["manifest_sha256"],
                             "ranked_stores": rank_manifest["ranked_stores"], "bucket_counts": rank_manifest["bucket_counts"]}
        result["monitoring"] = {"dir": str(monitoring_dir), "manifest_sha256": steps["monitoring"]["manifest_sha256"]}
    else:
        health_dir = recorded("health") or remember("health", _health(run_dir, workflow, fresh("health"), synthetic))
        health = read_json(health_dir / "manifest.json")
    from .service import demo_scope
    scope_path = run_dir / "governance" / "demo-scope.json"
    if not scope_path.exists():
        write_json(scope_path, demo_scope(stores["store_id"].tolist()))
    serving_root = run_dir / "serving"
    available = health["model_outputs_status"] == "Available"
    published = None
    scoring_sha = steps["scoring"]["manifest_sha256"] if available else None
    if steps.get("publication"):
        published = publication_valid(serving_root, steps["publication"]["publication_id"], scoring_sha,
                                      steps["health"]["manifest_sha256"], steps["publication"]["artifact"])
        if published is None:
            record.setdefault("recoveries", []).append(
                {"at": utc_now(), "reason": "Recorded publication missing, corrupt or not matching its packages",
                 "previous_publication_id": steps["publication"]["publication_id"]})
            del steps["publication"]
            save()
    if published is None:
        published = _published(serving_root, scoring_sha, steps["health"]["manifest_sha256"])
    if published is None:
        published = publishing.publish_candidate(base_dir, scoring_dir if available else None,
                                                 ranking_dir if available else None, health_dir, serving_root,
                                                 monitoring_dir=monitoring_dir, access_policy=scope_path,
                                                 synthetic=synthetic)
        published = {**published["publication"], "model_outputs": published["model_outputs"]}
    steps["publication"] = {"publication_id": published["publication_id"], "artifact": published["directory"]}
    record["required_steps"] = (["scoring", "ranking", "monitoring"] if scoring_dir is not None else []) \
        + (["issued"] if available and scoring_dir is not None else []) + ["health", "publication"]
    record["outputs_available"] = available
    save()
    result["health"] = {"dir": str(health_dir), "manifest_sha256": steps["health"]["manifest_sha256"],
                        "model_outputs_status": health["model_outputs_status"]}
    result["serving_candidate"] = {"root": str(serving_root), "artifact": published["directory"],
                                   "database_sha256": published.get("database_sha256"),
                                   "publication_id": published["publication_id"],
                                   "model_outputs_status": published.get("model_outputs_status"),
                                   "publication_kind": (published.get("model_outputs") or {}).get("publication_kind")}
    return result


def _health(run_dir, workflow, target, synthetic):
    publishing.build_health_package(run_dir, workflow["plan"], target, synthetic=synthetic)
    return target


def _completed(record, run_dir):
    """Completion is trusted only after re-verifying every required step and its identity links."""
    if record.get("status") != "Completed":
        return False
    steps, required, key = record.get("steps") or {}, record.get("required_steps"), record.get("key") or {}
    if not required or not {"health", "publication"} <= set(required) or not set(required) <= set(steps):
        return False
    manifests = {}
    for kind in PACKAGE_VERSIONS:
        if kind in required:
            entry = steps[kind]
            if verified_package(entry["dir"], PACKAGE_VERSIONS[kind]) != entry["manifest_sha256"]:
                return False
            manifests[kind] = read_json(Path(entry["dir"]) / "manifest.json")
    bundle_sha = key.get("bundle_manifest_sha256")
    if (manifests["health"].get("bundle") or {}).get("manifest_sha256") != bundle_sha:
        return False
    if "scoring" in manifests:
        scored = manifests["scoring"]
        if (scored["source"]["dataset_hash"] != key.get("dataset_hash")
                or scored["source"]["source_build_id"] != key.get("source_build_id")
                or scored["scoring_date"] != key.get("scoring_date")
                or scored["bundle"]["manifest_sha256"] != bundle_sha
                or manifests["ranking"]["scoring_manifest_sha256"] != steps["scoring"]["manifest_sha256"]
                or manifests["monitoring"].get("bundle_manifest_sha256") != bundle_sha):
            return False
    if "issued" in required:
        entries, _ = monitoring.issued_history(Path(run_dir) / "issued-history")
        if not any(e["scoring_manifest_sha256"] == steps["scoring"]["manifest_sha256"] for e in entries):
            return False
    scoring_sha = steps["scoring"]["manifest_sha256"] if record.get("outputs_available") else None
    return publication_valid(Path(run_dir) / "serving", steps["publication"]["publication_id"], scoring_sha,
                             steps["health"]["manifest_sha256"], steps["publication"]["artifact"]) is not None


def record_completion(run_dir, workflow, bundle_sha, record):
    state = _load_state(run_dir)
    key, key_id = daily_key(workflow, bundle_sha)
    record.update(key=key, status="Completed", finished_at=utc_now())
    state["runs"][key_id] = record
    _save_state(run_dir, state)
    return key_id


def daily(*, run_dir, workflow_run):
    """Idempotent batch for a new accepted source; never trains. Safe to retry after a crash."""
    run_dir = Path(run_dir)
    workflow = inputs.load_workflow(workflow_run)
    config = read_json(run_dir / "config.json")
    synthetic = bool(config.get("synthetic"))
    bundle = bundles.load_bundle(run_dir / "bundle")
    key, key_id = daily_key(workflow, bundle.manifest_sha256)
    state = _load_state(run_dir)
    record = state["runs"].get(key_id)
    if record and _completed(record, run_dir):
        _log(run_dir, "daily_noop", key_id=key_id, **key)
        return {"status": "No_op_already_processed", "key_id": key_id, **key}
    if bundle.eligibility().get("status") != "Eligible_for_review" and not synthetic:
        _log(run_dir, "daily_unavailable", status=bundle.eligibility().get("status"))
        return {"status": "Unavailable_model_not_eligible", "key_id": key_id, **key}
    if record is None or record.get("key") != key:
        record = {"key": key, "steps": {}, "attempt": 0, "started_at": utc_now()}
    superseded = [k for k, r in state["runs"].items() if k != key_id and r.get("status") == "Completed"
                  and r["key"]["scoring_date"] == key["scoring_date"]
                  and r["key"]["bundle_manifest_sha256"] == key["bundle_manifest_sha256"]
                  and (r["key"]["dataset_hash"], r["key"]["source_build_id"]) != (key["dataset_hash"], key["source_build_id"])]
    record.update(status="Running", attempt=record.get("attempt", 0) + 1, supersedes=superseded,
                  last_attempt_at=utc_now())
    record.pop("error", None)
    state["runs"][key_id] = record

    def save():
        _save_state(run_dir, state)
    save()
    label = f"{key['scoring_date']}-{key_id[:8]}"
    try:
        result = serving_candidate(run_dir, workflow, synthetic=synthetic, label=label, record=record, save=save)
    except BaseException as error:
        record.update(status="Failed", error=f"{type(error).__name__}: {error}")
        save()
        raise
    record.update(status="Completed", finished_at=utc_now())
    save()
    _log(run_dir, "daily_done", key_id=key_id, supersedes=superseded, **key)
    return {"status": "Processed", "key_id": key_id, "attempt": record["attempt"], "supersedes": superseded,
            **key, **result}


def challenger_request(run_dir):
    """Surface the latest monitoring trigger; never trains or swaps the champion."""
    requests = sorted(Path(run_dir).glob("monitoring/*/challenger-request.json"))
    return read_json(requests[-1]) if requests else {"status": "Not_requested"}

