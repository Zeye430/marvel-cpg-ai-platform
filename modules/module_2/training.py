"""First reproducible offline experiment, not a model-release or UI service.

Export uses the locked data environment and the existing readers. Fit uses an
explicit ML environment reading only the exported inner-development batches.
No test/outer-validation mode or production promotion is implemented here.
"""

import argparse
import gc
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from data_platform.temporal_plan import TemporalPlan


CATEGORICAL = (
    "channel", "segment_tier", "region", "category", "inventory_data_status",
    "sales_data_status", "replenishment_feed_status", "consumer_promo_data_status",
)
NUMERIC = tuple("""
    inventory_observed sales_observed days_since_inventory_report expected_days_7d
    expected_days_28d observed_days_7d observed_days_28d sales_units_observed_7d
    sales_units_observed_28d velocity_days_7d velocity_days_prev7d velocity_days_28d
    in_stock_velocity_7d in_stock_velocity_prev7d in_stock_velocity_28d
    sales_acceleration_ratio days_cover_observed inventory_change_7d_observed
    oos_days_observed_7d oos_days_observed_28d receipt_units_7d receipt_units_28d
    days_since_receipt pending_order_count pending_units_ordered scheduled_units_next7d
    overdue_units_ordered days_until_next_scheduled_receipt eligible_promotion_count
    observed_promo_discount_pct consumer_price_observed days_since_last_audit
    shelf_share_pct_latest same_day_audit_count is_msl oos_observed
    has_promotion_overlap planogram_compliance_latest secondary_display_latest
""".split())
FEATURES = CATEGORICAL + NUMERIC
PARAMETERS = dict(n_estimators=260, learning_rate=.05, num_leaves=48,
                  min_child_samples=45, subsample=.85, colsample_bytree=.85,
                  reg_lambda=.7, class_weight="balanced", random_state=650,
                  verbosity=-1, n_jobs=2)
POLICIES = ("pooled_global", "pooled_segment_thresholds", "segmented")
INNER_FOLDS = ("rolling_dev_1", "rolling_dev_2")


def _hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _event(message, **details):
    print(json.dumps({"event": message, **details}), flush=True)


def export_inputs(workflow_run, output_dir):
    """Materialize only two existing inner folds through governed readers."""
    import duckdb
    from .data_adapter import load_development_partition

    workflow_run, output_dir = Path(workflow_run), Path(output_dir)
    run = json.loads(workflow_run.read_text())
    if run.get("status") != "Accepted" or run.get("evaluation_profile") != "trailing_30d":
        raise ValueError("An explicit accepted rolling workflow is required")
    plan = TemporalPlan.from_dict(run.get("temporal_plan"))
    if output_dir.exists():
        raise ValueError("Use a new experiment directory; inputs cannot be overwritten")
    output_dir.mkdir(parents=True)
    manifest = {
        "version": "module2.inner_training_inputs.v1", "status": "Running",
        "cohort": "all_complete_observed_labels_current_missing_and_oos_retained",
        "outer_validation": "Locked", "test": "Locked", "features": list(FEATURES),
        "parameters": PARAMETERS, "source_workflow": str(workflow_run.resolve()),
        "temporal_plan": plan.to_dict(),
        "workflow_sha256": _hash(workflow_run), "files": [],
        "training_code_sha256": _hash(__file__),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "export_environment": {"python": platform.python_version(), "pandas": pd.__version__,
                               "duckdb": duckdb.__version__},
    }
    _save(output_dir/"inputs.json", manifest)
    try:
        components = run["components"]
        plan_hash = None
        for fold in INNER_FOLDS:
            for partition in ("train", "validation"):
                batch = load_development_partition(
                    components["features"]["directory"], components["labels"]["directory"],
                    fold_id=fold, partition=partition, predictors=list(FEATURES))
                if plan_hash is not None and batch.metadata["plan_sha256"] != plan_hash:
                    raise ValueError("Training partitions must share one source plan")
                plan_hash = batch.metadata["plan_sha256"]
                if plan_hash != plan.to_dict()["plan_sha256"]:
                    raise ValueError("Reader source plan differs from the workflow")
                frame = pd.concat([batch.row_keys, batch.X, batch.y], axis=1)
                filename = f"{fold}-{partition}.parquet"
                with duckdb.connect(config={"memory_limit": "512MB", "threads": 2}) as con:
                    con.register("batch", frame)
                    # Destination is fixed under a newly created local directory.
                    destination = str((output_dir/filename).resolve()).replace("'", "''")
                    con.execute(f"COPY batch TO '{destination}' (FORMAT PARQUET)")
                manifest["files"].append({"fold": fold, "partition": partition,
                    "file": filename, "sha256": _hash(output_dir/filename),
                    "rows": len(frame), "metadata": batch.metadata})
                _save(output_dir/"inputs.json", manifest)
                _event("exported", fold=fold, partition=partition, rows=len(frame))
                del frame, batch
                gc.collect()
        manifest.update(status="Accepted", plan_sha256=plan_hash)
    except BaseException as error:
        manifest.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        _save(output_dir/"inputs.json", manifest)
    return manifest


def prepare_categories(train, validation):
    """Train-only levels; unseen validation values become missing, never new levels."""
    train, validation = train[list(FEATURES)].copy(), validation[list(FEATURES)].copy()
    vocabularies = {}
    for column in CATEGORICAL:
        levels = sorted(train[column].dropna().astype(str).unique().tolist())
        vocabularies[column] = levels
        train[column] = pd.Categorical(train[column], categories=levels)
        validation[column] = pd.Categorical(validation[column], categories=levels)
    for column in NUMERIC:
        for frame in (train, validation):
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
            if np.isinf(frame[column].to_numpy()).any():
                raise ValueError(f"Non-finite numeric predictor: {column}")
    return train, validation, vocabularies


def segment_keys(frame):
    return pd.Series([json.dumps([a, b], ensure_ascii=True) for a, b in
                      zip(frame["channel"].astype("string").fillna("<Unknown>"),
                          frame["segment_tier"].astype("string").fillna("<Unknown>"))],
                     index=frame.index)


def supported(y, minimum):
    y = np.asarray(y)
    return len(y) >= minimum and np.count_nonzero(y == 1) >= 20 and np.count_nonzero(y == 0) >= 20


def choose_threshold(y, score):
    from sklearn.metrics import precision_recall_curve
    y, score = np.asarray(y), np.asarray(score)
    if len(np.unique(y)) != 2:
        raise ValueError("Threshold selection requires both observed classes")
    precision, recall, thresholds = precision_recall_curve(y, score)
    usable = np.flatnonzero(recall[:-1] >= .75)
    index = max(usable, key=lambda i: (precision[i], thresholds[i]))
    return float(thresholds[index])


def threshold_policy(y, score, segments):
    global_threshold = choose_threshold(y, score)
    thresholds = {}
    for key in sorted(set(segments)):
        mask = np.asarray(segments == key)
        if supported(np.asarray(y)[mask], 200):
            thresholds[key] = choose_threshold(np.asarray(y)[mask], np.asarray(score)[mask])
    return {"global": global_threshold, "segments": thresholds}


def evaluate(y, score, thresholds):
    from sklearn.metrics import average_precision_score, roc_auc_score
    y, score = np.asarray(y, dtype=int), np.asarray(score, dtype=float)
    predicted = score >= np.asarray(thresholds)
    tp = int(np.sum((y == 1) & predicted)); fp = int(np.sum((y == 0) & predicted))
    fn = int(np.sum((y == 1) & ~predicted)); tn = int(np.sum((y == 0) & ~predicted))
    precision = tp/(tp+fp) if tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    two_classes = len(np.unique(y)) == 2
    auc = float(roc_auc_score(y, score)) if two_classes else None
    ap = float(average_precision_score(y, score)) if two_classes else None
    return {"rows": len(y), "positives": int(y.sum()), "TP": tp, "FP": fp,
            "FN": fn, "TN": tn, "alerts": int(predicted.sum()),
            "precision": precision, "recall": recall, "roc_auc": auc, "pr_auc": ap,
            "meets_numeric_targets": bool(precision is not None and recall is not None
                and auc is not None and precision >= .75 and recall >= .70 and auc >= .80)}


def _fit_fold(train, validation, directory):
    from lightgbm import LGBMClassifier
    y = train["target_oos_7d"].to_numpy(int)
    if len(np.unique(y)) != 2:
        raise ValueError("Pooled model training requires both observed classes")
    X, V, vocabularies = prepare_categories(train, validation)
    pooled = LGBMClassifier(**PARAMETERS)
    _event("fit_pooled", train_rows=len(X), validation_rows=len(V))
    pooled.fit(X, y, categorical_feature=list(CATEGORICAL))
    pooled_score = pooled.predict_proba(V)[:, 1]
    directory.mkdir(parents=True)
    pooled.booster_.save_model(str(directory/"pooled.txt"))
    _save(directory/"categories.json", vocabularies)
    train_segments, valid_segments = segment_keys(train), segment_keys(validation)
    segmented_score = pooled_score.copy()
    routes = np.full(len(validation), "pooled_fallback", dtype=object)
    support = []
    for key in sorted(set(train_segments) | set(valid_segments)):
        train_mask = train_segments.eq(key).to_numpy()
        valid_mask = valid_segments.eq(key).to_numpy()
        allowed = supported(y[train_mask], 1000)
        support.append({"segment": key, "train_rows": int(train_mask.sum()),
                        "train_positives": int(y[train_mask].sum()),
                        "validation_rows": int(valid_mask.sum()), "segment_model": bool(allowed)})
        if not allowed:
            continue
        _event("fit_segment", segment=key, rows=int(train_mask.sum()))
        model = LGBMClassifier(**PARAMETERS)
        model.fit(X.loc[train_mask], y[train_mask], categorical_feature=list(CATEGORICAL))
        if valid_mask.any():
            segmented_score[valid_mask] = model.predict_proba(V.loc[valid_mask])[:, 1]
            routes[valid_mask] = "segment_model"
        identity = hashlib.sha256(key.encode()).hexdigest()[:16]
        model.booster_.save_model(str(directory/f"segment-{identity}.txt"))
        del model
    _save(directory/"support.json", support)
    return pooled_score, segmented_score, routes


def fit_experiment(input_dir, output_dir):
    """Fit inner folds, freeze earlier-fold thresholds, report later transfer."""
    import lightgbm
    import sklearn
    import pyarrow

    input_dir, output_dir = Path(input_dir), Path(output_dir)
    source = json.loads((input_dir/"inputs.json").read_text())
    plan = TemporalPlan.from_dict(source.get("temporal_plan"))
    if (source.get("version") != "module2.inner_training_inputs.v1"
            or source.get("status") != "Accepted" or source.get("features") != list(FEATURES)
            or source.get("parameters") != PARAMETERS or source.get("test") != "Locked"
            or source.get("outer_validation") != "Locked"):
        raise ValueError("An accepted input package with the frozen experiment settings is required")
    expected = {(f, p) for f in INNER_FOLDS for p in ("train", "validation")}
    files = {(r["fold"], r["partition"]): r for r in source["files"]}
    if set(files) != expected or len(source["files"]) != 4:
        raise ValueError("Exactly the two inner development folds are required")
    for row in files.values():
        if Path(row["file"]).name != row["file"] or _hash(input_dir/row["file"]) != row["sha256"]:
            raise ValueError("Training input integrity mismatch")
        metadata = row["metadata"]
        if (metadata.get("plan_sha256") != plan.to_dict()["plan_sha256"]
                or metadata.get("purpose") != "development"
                or metadata.get("source_build_id") != plan.source_build_id
                or metadata.get("cutoff") != plan.to_dict()["validation"]["outcome_end"]
                or metadata.get("fold_id") != row["fold"]
                or metadata.get("partition") != row["partition"]):
            raise ValueError("Exported partition metadata differs from the frozen source plan")
    if output_dir.exists():
        raise ValueError("Use a new results directory; previous experiments stay immutable")
    output_dir.mkdir(parents=True)
    report = {"status": "Running", "purpose": "Exploratory_inner_development_not_release",
              "test": "Locked", "outer_validation": "Locked", "cohort": source["cohort"],
              "parameters": PARAMETERS, "features": list(FEATURES),
              "temporal_plan": plan.to_dict(),
              "input_manifest_sha256": _hash(input_dir/"inputs.json"),
              "training_code_sha256": _hash(__file__), "results": [], "folds": [],
              "environment": {"python": platform.python_version(), "lightgbm": lightgbm.__version__,
                "sklearn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__,
                "pyarrow": pyarrow.__version__}}
    _save(output_dir/"report.json", report)
    policies = None
    try:
        for fold_index, fold in enumerate(INNER_FOLDS):
            train = pd.read_parquet(input_dir/files[fold, "train"]["file"])
            validation = pd.read_parquet(input_dir/files[fold, "validation"]["file"])
            bounds = {row[0]: row for row in plan.development_folds()}[fold]
            for partition, frame in (("train", train), ("validation", validation)):
                if len(frame) != files[fold, partition]["rows"] or frame.empty:
                    raise ValueError("Input row counts differ from the accepted export")
                if not frame["target_oos_7d"].isin([0, 1]).all():
                    raise ValueError("Observed binary labels are required")
                if frame.duplicated(["build_id", "scoring_date", "store_id", "sku_id"]).any():
                    raise ValueError("Training keys must be unique")
                start, end = (plan.history_start.isoformat(), bounds[1]) if partition == "train" else bounds[2:4]
                if (frame[["build_id", "scoring_date", "store_id", "sku_id"]].isna().any().any()
                        or not frame["build_id"].eq(plan.source_build_id).all()
                        or not pd.to_datetime(frame["scoring_date"]).between(pd.Timestamp(start), pd.Timestamp(end)).all()):
                    raise ValueError("Exported rows fall outside their declared source/fold")
            pooled, segmented, routes = _fit_fold(train, validation, output_dir/"models"/fold)
            y = validation["target_oos_7d"].to_numpy(int)
            segments = segment_keys(validation)
            if fold_index == 0:
                policies = {"pooled": threshold_policy(y, pooled, segments),
                            "segmented": threshold_policy(y, segmented, segments)}
                _save(output_dir/"thresholds.json", {"selected_on": fold, "recall_floor": .75,
                      "policies": policies, "minimum_segment_rows": 200, "minimum_each_class": 20})
            predictions = validation[["build_id", "scoring_date", "store_id", "sku_id",
                                       "channel", "segment_tier", "oos_observed", "target_oos_7d"]].copy()
            predictions["pooled_score"] = pooled
            predictions["segmented_score"] = segmented
            predictions["model_route"] = routes
            scopes = [("all", np.ones(len(y), dtype=bool))]
            scopes += [("segment:"+key, segments.eq(key).to_numpy()) for key in sorted(set(segments))]
            scopes += [("current_not_oos", validation["oos_observed"].eq(False).fillna(False).to_numpy(bool)),
                       ("current_oos", validation["oos_observed"].eq(True).fillna(False).to_numpy(bool)),
                       ("current_unknown", validation["oos_observed"].isna().to_numpy())]
            for policy in POLICIES:
                score = segmented if policy == "segmented" else pooled
                values = policies["segmented" if policy == "segmented" else "pooled"]
                thresholds = np.asarray([values["global"] if policy == "pooled_global"
                    else values["segments"].get(key, values["global"]) for key in segments])
                predictions[policy+"_threshold"] = thresholds
                for scope, mask in scopes:
                    report["results"].append({"fold": fold, "policy": policy, "scope": scope,
                        "operating_point": "tuned_on_this_fold" if fold_index == 0 else "frozen_from_earlier_fold",
                        **evaluate(y[mask], score[mask], thresholds[mask])})
            predictions.to_parquet(output_dir/f"{fold}-development-predictions.parquet", index=False)
            report["folds"].append({"fold": fold, "train_rows": len(train), "validation_rows": len(validation),
                "train_metadata": files[fold, "train"]["metadata"],
                "validation_metadata": files[fold, "validation"]["metadata"],
                "pooled_fallback_rows": int(np.sum(routes == "pooled_fallback"))})
            _save(output_dir/"report.json", report)
            _event("evaluated", fold=fold, summaries=[r for r in report["results"] if r["fold"] == fold and r["scope"] == "all"])
            del train, validation, predictions
            gc.collect()
        report["model_files"] = {str(p.relative_to(output_dir)): _hash(p) for p in sorted((output_dir/"models").rglob("*")) if p.is_file()}
        report["status"] = "Completed_not_promoted"
        pd.DataFrame(report["results"]).to_csv(output_dir/"metrics.csv", index=False)
    except BaseException as error:
        report.update(status="Failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        _save(output_dir/"report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export")
    export.add_argument("--workflow-run", required=True)
    export.add_argument("--output-dir", required=True)
    fit = actions.add_parser("fit")
    fit.add_argument("--input-dir", required=True)
    fit.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    if args.action == "export":
        export_inputs(args.workflow_run, args.output_dir)
    else:
        fit_experiment(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
