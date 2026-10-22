"""Fair multi-algorithm development benchmark with Optuna and fixed selection.

Each trial runs in its own subprocess (hard timeout, killed only if it is this
run's worker): fit on rolling_dev_1 training -> held-out raw scores -> global
sigmoid calibration -> dev1 thresholds; refit the same parameters on
rolling_dev_2 training and evaluate dev2 held-out rows with the dev1 calibrator
and thresholds. Only development data is ever read. Trial models are never
persisted; parameters, metrics and scores are.
"""

import argparse
import gc
import json
import os
import resource
import signal
import subprocess
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration, config, evaluation, inputs, models
from .contracts import (Module2Error, ResourceExhausted, check_resources, read_json, sha256_file,
                        utc_now, write_json)
from .preprocessing import Preprocessor
from .training import segment_keys

FOLDS = inputs.INNER_FOLDS
COMPLETE = "Complete"


def _log(run_dir, event, **details):
    line = json.dumps({"at": utc_now(), "event": event, **details}, sort_keys=True, default=str)
    path = Path(run_dir) / "logs" / "benchmark.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def _peak_rss_bytes(who=resource.RUSAGE_SELF):
    value = resource.getrusage(who).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


# ----------------------------------------------------------------------------
# Fold cache: train-only preprocessing per fold, shared by every algorithm.
# ----------------------------------------------------------------------------

def prepare_cache(run_dir):
    run_dir = Path(run_dir)
    source = run_dir / "inputs" / "development"
    manifest = read_json(source / "manifest.json", "development input manifest")
    cache = run_dir / "benchmark" / "cache"
    record = {"version": "module2.fold_cache.v1", "folds": {}, "created_at": utc_now()}
    for fold in FOLDS:
        check_resources(run_dir, stage=f"cache {fold}")
        train = inputs.read_export(source, f"{fold}-train", manifest)
        valid = inputs.read_export(source, f"{fold}-validation", manifest)
        pre = Preprocessor.fit(train)
        folder = cache / fold
        folder.mkdir(parents=True, exist_ok=False)
        np.save(folder / "X_train.npy", pre.transform(train))
        np.save(folder / "y_train.npy", train["target_oos_7d"].to_numpy(np.int8))
        np.save(folder / "X_valid.npy", pre.transform(valid))
        np.save(folder / "y_valid.npy", valid["target_oos_7d"].to_numpy(np.int8))
        meta = valid[inputs.KEYS + ["channel", "segment_tier", "oos_observed", "target_oos_7d"]].copy()
        meta["segment_key"] = segment_keys(valid).to_numpy()
        meta.to_parquet(folder / "valid_meta.parquet", index=False)
        write_json(folder / "preprocessor.json", pre.to_dict())
        record["folds"][fold] = {
            "train_input_sha256": manifest["files"][f"{fold}-train"]["sha256"],
            "valid_input_sha256": manifest["files"][f"{fold}-validation"]["sha256"],
            "train_rows": int(len(train)), "valid_rows": int(len(valid)),
            "preprocessor_sha256": pre.sha256, "transformed_columns": len(pre.columns),
            "files": {p.name: sha256_file(p) for p in sorted(folder.iterdir())}}
        del train, valid, pre
        gc.collect()
    write_json(cache / "cache.json", record, exclusive=True)
    return record


def _load_fold(cache, fold):
    folder = Path(cache) / fold
    return (np.load(folder / "X_train.npy"), np.load(folder / "y_train.npy").astype(int),
            np.load(folder / "X_valid.npy"), np.load(folder / "y_valid.npy").astype(int),
            pd.read_parquet(folder / "valid_meta.parquet"))


# ----------------------------------------------------------------------------
# One trial (runs inside a worker subprocess).
# ----------------------------------------------------------------------------

def run_trial(spec):
    """Two-fold development evaluation of one algorithm/parameter set."""
    started = time.monotonic()
    cache, out = Path(spec["cache"]), Path(spec["output"])
    result = {"algorithm": spec["algorithm"], "trial_id": spec["trial_id"], "params": spec["params"],
              "status": "Running", "started_at": utc_now(), "fit_seconds": {}, "dev1": None, "dev2": None}
    scores = []
    try:
        # Fold 1: fit, raw held-out scores, calibrator and thresholds.
        X, y, V, yv, meta = _load_fold(cache, "rolling_dev_1")
        t0 = time.monotonic()
        model = models.fit_candidate(spec["algorithm"], spec["params"], X, y)
        result["fit_seconds"]["rolling_dev_1"] = time.monotonic() - t0
        result["model_info"] = {"rolling_dev_1": model.info}
        raw1 = model.predict_raw_probability(V)
        del model, X, y
        gc.collect()
        try:
            calibrator = calibration.fit_sigmoid(raw1, yv)
        except Module2Error as error:
            result.update(status="Calibration_invalid", error=str(error))
            return result
        cal1 = calibration.apply(calibrator, raw1)
        thresholds = evaluation.threshold_policy(yv, cal1, meta["segment_key"])
        thr1, routes1 = evaluation.route_thresholds(thresholds, meta["segment_key"])
        result["calibrator"], result["thresholds"] = calibrator, thresholds
        result["dev1"] = {"operating_point": "tuned_on_this_fold",
                          "results": evaluation.evaluate_groups(meta, cal1, thr1),
                          "raw_roc_auc": evaluation.scope_metrics(yv, raw1, thr1)["roc_auc"]}
        scores.append(pd.DataFrame({"fold": "rolling_dev_1", "row": np.arange(len(yv)), "raw": raw1,
                                    "calibrated": cal1, "threshold": thr1, "route": routes1}))
        del V, yv, meta
        gc.collect()
        # Fold 2: same parameters refit; dev1 calibrator/thresholds transferred.
        X, y, V, yv, meta = _load_fold(cache, "rolling_dev_2")
        t0 = time.monotonic()
        model = models.fit_candidate(spec["algorithm"], spec["params"], X, y)
        result["fit_seconds"]["rolling_dev_2"] = time.monotonic() - t0
        result["model_info"]["rolling_dev_2"] = model.info
        raw2 = model.predict_raw_probability(V)
        del model, X, y
        gc.collect()
        cal2 = calibration.apply(calibrator, raw2)
        thr2, routes2 = evaluation.route_thresholds(thresholds, meta["segment_key"])
        groups = evaluation.evaluate_groups(meta, cal2, thr2)
        result["dev2"] = {"operating_point": "frozen_from_rolling_dev_1", "results": groups,
                          "routes": {"segment": int(np.sum(routes2 == "segment")),
                                     "global_fallback": int(np.sum(routes2 == "global_fallback"))}}
        scores.append(pd.DataFrame({"fold": "rolling_dev_2", "row": np.arange(len(yv)), "raw": raw2,
                                    "calibrated": cal2, "threshold": thr2, "route": routes2}))
        overall = groups[0]
        result["objective"] = overall["precision"]
        result["constraints"] = [
            1.0 if overall["recall"] is None else config.TARGETS["recall"] - overall["recall"],
            1.0 if overall["roc_auc"] is None else config.TARGETS["roc_auc"] - overall["roc_auc"]]
        result["min_supported_segment_recall"] = evaluation.min_supported_segment_recall(groups)
        result["status"] = COMPLETE
    except models.ConvergenceFailure as error:
        result.update(status="Convergence_failed", error=str(error))
    except MemoryError as error:
        result.update(status="Resource_exhausted", error=f"MemoryError: {error}")
    except Exception as error:  # recorded, never treated as a completed trial
        result.update(status="Failed", error=f"{type(error).__name__}: {error}")
    finally:
        result["total_fit_seconds"] = float(sum(result["fit_seconds"].values()))
        result["wall_seconds"] = time.monotonic() - started
        result["peak_rss_bytes"] = _peak_rss_bytes()
        result["finished_at"] = utc_now()
        out.mkdir(parents=True, exist_ok=True)
        if scores:
            pd.concat(scores, ignore_index=True).to_parquet(out / "scores.parquet", index=False)
            result["scores_sha256"] = sha256_file(out / "scores.parquet")
        write_json(out / "result.json", result)
    return result


def _spawn(run_dir, spec, timeout):
    """Run a trial worker; only this run's own process group is ever killed."""
    out = Path(spec["output"])
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "spec.json", spec)
    env = {**os.environ, "OMP_NUM_THREADS": str(config.THREADS), "PYTHONHASHSEED": "0"}
    with (out / "worker.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "modules.module_2.benchmark", "worker", str(out / "spec.json")],
                                   cwd=str(config.ROOT), stdout=log, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    process.wait(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    continue
            result = {"algorithm": spec["algorithm"], "trial_id": spec["trial_id"], "params": spec["params"],
                      "status": "Timeout", "error": f"Exceeded {timeout} s trial limit", "finished_at": utc_now(),
                      "total_fit_seconds": None, "peak_rss_bytes": _peak_rss_bytes(resource.RUSAGE_CHILDREN)}
            write_json(out / "result.json", result)
            return result
    if not (out / "result.json").exists():
        result = {"algorithm": spec["algorithm"], "trial_id": spec["trial_id"], "params": spec["params"],
                  "status": "Failed", "error": f"Worker exited {process.returncode} without a result",
                  "finished_at": utc_now(), "total_fit_seconds": None}
        write_json(out / "result.json", result)
        return result
    return read_json(out / "result.json", "trial result")


# ----------------------------------------------------------------------------
# Studies and selection.
# ----------------------------------------------------------------------------

def _distributions(algorithm):
    import optuna.distributions as d
    out = {}
    for name, (kind, values) in config.SEARCH_SPACES[algorithm].items():
        if kind == "categorical":
            out[name] = d.CategoricalDistribution(values)
        elif kind == "float":
            out[name] = d.FloatDistribution(values[0], values[1])
        elif kind == "log_float":
            out[name] = d.FloatDistribution(values[0], values[1], log=True)
        elif kind == "int":
            out[name] = d.IntDistribution(values[0], values[1])
    return out


def _constraints(trial):
    return trial.user_attrs.get("constraints", [1.0, 1.0])


def _run_study(run_dir, algorithm, budget, state, deadline):
    import optuna
    from optuna.trial import TrialState
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    storage = optuna.storages.RDBStorage(f"sqlite:///{(Path(run_dir) / 'benchmark' / 'optuna.sqlite3').resolve()}")
    try:
        _study_loop(run_dir, algorithm, budget, state, deadline, storage)
    finally:
        storage.engine.dispose()


def _study_loop(run_dir, algorithm, budget, state, deadline, storage):
    import optuna
    from optuna.trial import TrialState
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
        sampler = optuna.samplers.TPESampler(seed=config.SEED, n_startup_trials=budget["n_startup_trials"],
                                             constraints_func=_constraints)
    name = f"{state['run_id']}-{algorithm}"
    study = optuna.create_study(study_name=name, storage=storage, sampler=sampler,
                                direction="maximize", load_if_exists=True)
    for stale in study.get_trials(deepcopy=False, states=(TrialState.RUNNING,)):
        study.tell(stale.number, state=TrialState.FAIL)  # interrupted earlier; not refunded
    progress = state["studies"].setdefault(algorithm, {"elapsed_seconds": 0.0, "status": "Running"})
    if progress["status"] != "Running":
        return
    if algorithm == "random_forest" and not study.trials:
        for params in config.RF_STARTING_TRIALS:
            study.enqueue_trial(dict(params))
    distributions = _distributions(algorithm)
    while True:
        if len(study.trials) >= budget["max_trials"]:
            progress["status"] = "Trial_limit_reached"; break
        if progress["elapsed_seconds"] >= budget["study_seconds"]:
            progress["status"] = "Study_time_budget_reached"; break
        if time.monotonic() >= deadline:
            progress["status"] = "Total_ML_budget_reached"; break
        check_resources(run_dir, stage=f"{algorithm} trial")
        trial = study.ask(distributions)
        spec = {"algorithm": algorithm, "trial_id": f"{algorithm}-{trial.number:03d}", "params": dict(trial.params),
                "cache": str(Path(run_dir) / "benchmark" / "cache"),
                "output": str(Path(run_dir) / "benchmark" / "trials" / f"{algorithm}-{trial.number:03d}")}
        start = time.monotonic()
        result = _spawn(run_dir, spec, budget["trial_seconds"])
        progress["elapsed_seconds"] += time.monotonic() - start
        if result["status"] == COMPLETE:
            trial.set_user_attr("constraints", result["constraints"])
            study.tell(trial, result["objective"] if result["objective"] is not None else 0.0)
        else:
            trial.set_user_attr("constraints", [1.0, 1.0])
            study.tell(trial, state=TrialState.FAIL)
        _log(run_dir, "trial_finished", trial=spec["trial_id"], status=result["status"],
             objective=result.get("objective"), seconds=round(time.monotonic() - start, 1))
        write_json(Path(run_dir) / "benchmark" / "state.json", state)


def _run_baseline(run_dir, budget, deadline):
    for index, params in enumerate(config.BASELINE_GRID):
        out = Path(run_dir) / "benchmark" / "trials" / f"logistic_regression-{index:03d}"
        if (out / "result.json").exists():
            continue
        if time.monotonic() >= deadline:
            break
        check_resources(run_dir, stage="baseline trial")
        spec = {"algorithm": "logistic_regression", "trial_id": out.name, "params": dict(params),
                "cache": str(Path(run_dir) / "benchmark" / "cache"), "output": str(out)}
        result = _spawn(run_dir, spec, budget["trial_seconds"])
        _log(run_dir, "trial_finished", trial=out.name, status=result["status"], objective=result.get("objective"))


def collect(run_dir):
    trials = []
    for path in sorted((Path(run_dir) / "benchmark" / "trials").glob("*/result.json")):
        trials.append(read_json(path, "trial result"))
    return trials


def is_feasible(result):
    if result.get("status") != COMPLETE or not result.get("dev2"):
        return False
    overall = result["dev2"]["results"][0]
    return (overall["precision"] is not None and overall["recall"] is not None and overall["roc_auc"] is not None
            and overall["recall"] >= config.TARGETS["recall"] and overall["roc_auc"] >= config.TARGETS["roc_auc"])


def selection_key(result):
    overall = result["dev2"]["results"][0]
    segment = result.get("min_supported_segment_recall")
    return (-overall["precision"], -(segment if segment is not None else -1.0),
            -(overall["average_precision"] or 0.0), result.get("total_fit_seconds") or float("inf"),
            result["algorithm"], result["trial_id"])


def select(trials, budget):
    by_algorithm = {}
    for algorithm in config.ALGORITHMS:
        mine = [t for t in trials if t["algorithm"] == algorithm]
        complete = [t for t in mine if t["status"] == COMPLETE]
        feasible = sorted((t for t in complete if is_feasible(t)), key=selection_key)
        by_algorithm[algorithm] = {
            "trials": len(mine), "complete_trials": len(complete), "feasible_trials": len(feasible),
            "statuses": {s: sum(t["status"] == s for t in mine) for s in sorted({t["status"] for t in mine})},
            "best_feasible": feasible[0]["trial_id"] if feasible else None}
    incomplete = [a for a in config.MAIN_ALGORITHMS
                  if by_algorithm[a]["complete_trials"] < budget["min_complete_trials"]]
    finalists = [t for t in trials if by_algorithm[t["algorithm"]]["best_feasible"] == t["trial_id"]]
    winner = sorted(finalists, key=selection_key)[0] if finalists else None
    closest = None
    if winner is None:
        complete = [t for t in trials if t["status"] == COMPLETE]
        closest = max(complete, key=lambda t: -sum(max(0, c) for c in t["constraints"]))["trial_id"] if complete else None
    return {"per_algorithm": by_algorithm, "benchmark_status": "Benchmark_incomplete" if incomplete else "Complete",
            "incomplete_algorithms": incomplete, "winner": winner["trial_id"] if winner else None,
            "winner_algorithm": winner["algorithm"] if winner else None,
            "selection_status": "Selected" if winner else "No_feasible_candidate",
            "closest_infeasible_diagnostic": closest,
            "rule": "max dev2 precision s.t. recall>=.70 and AUC>=.80; ties: min supported segment recall, AP, "
                    "lower total fit seconds, algorithm, trial id"}


def run_benchmark(run_dir, *, profile, run_id):
    run_dir = Path(run_dir)
    if profile not in config.BUDGETS:
        raise Module2Error("Unknown budget profile")
    budget = config.BUDGETS[profile]
    state_path = run_dir / "benchmark" / "state.json"
    state = read_json(state_path) if state_path.exists() else {
        "run_id": run_id, "profile": profile, "budget": budget, "started_at": utc_now(), "studies": {}}
    write_json(state_path, state)
    deadline = time.monotonic() + budget["total_ml_seconds"] - state.get("ml_seconds_used", 0.0)
    begun = time.monotonic()
    try:
        if not (run_dir / "benchmark" / "cache" / "cache.json").exists():
            prepare_cache(run_dir)
        for algorithm in config.MAIN_ALGORITHMS:
            _run_study(run_dir, algorithm, budget, state, deadline)
        _run_baseline(run_dir, budget, deadline)
    except ResourceExhausted as error:
        state["resource_status"] = f"Resource_exhausted: {error}"
    finally:
        state["ml_seconds_used"] = state.get("ml_seconds_used", 0.0) + time.monotonic() - begun
        write_json(state_path, state)
    trials = collect(run_dir)
    selection = select(trials, budget)
    if state.get("resource_status"):
        selection["benchmark_status"] = "Benchmark_incomplete"
        selection["resource_status"] = state["resource_status"]
    development = read_json(run_dir / "inputs" / "development" / "manifest.json")
    report = {"version": config.VERSIONS["benchmark"], "run_id": run_id, "profile": profile, "budget": budget,
              "plan_sha256": development.get("plan_sha256"),
              "fold_candidates": {fold: (development.get("files", {}).get(f"{fold}-validation", {})
                                         .get("metadata", {}).get("candidate_rows")) for fold in FOLDS},
              "kind": "development", "status": selection["benchmark_status"], "selection": selection,
              "trials": trials, "study_progress": state["studies"], "ml_seconds_used": state["ml_seconds_used"],
              "cache": (read_json(run_dir / "benchmark" / "cache" / "cache.json")
                        if (run_dir / "benchmark" / "cache" / "cache.json").exists() else None),
              "finished_at": utc_now(), "note": "dev2 informed the search; development result, not an independent test"}
    write_json(run_dir / "benchmark" / "report.json", report)
    cleanup_cache(run_dir)
    return report


def cleanup_cache(run_dir):
    """Remove this run's fold matrices and serialized preprocessors after use."""
    cache = Path(run_dir) / "benchmark" / "cache"
    removed = []
    for path in sorted(cache.glob("*/*")):
        if path.suffix in (".npy", ".parquet") or path.name == "preprocessor.json":
            removed.append({"path": str(path.relative_to(run_dir)), "sha256": sha256_file(path),
                            "bytes": path.stat().st_size})
            path.unlink()
    if removed:
        ledger = Path(run_dir) / "benchmark" / "cleanup.json"
        previous = read_json(ledger) if ledger.exists() else {"removed": []}
        previous["removed"] += removed
        previous["reason"] = "Run-owned scratch: fold matrices/preprocessors not needed after benchmark"
        write_json(ledger, previous)
    return removed


def main():
    parser = argparse.ArgumentParser(description="Internal benchmark worker")
    parser.add_argument("action", choices=["worker"])
    parser.add_argument("spec")
    args = parser.parse_args()
    spec = read_json(args.spec, "trial spec")
    result = run_trial(spec)
    sys.exit(0 if result["status"] else 1)


if __name__ == "__main__":
    main()
