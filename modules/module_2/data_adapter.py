"""Small offline readers for prepared OOS features and development labels.

The shared platform owns ETL, feature definitions and label eligibility. These
readers only verify artifacts, select existing rows and align model inputs.
They do not train models, open test truth or provide an end-user data API.
"""

from dataclasses import dataclass

import duckdb
import pandas as pd

from data_platform.artifacts import verified_artifact
from data_platform.temporal_plan import TemporalPlan


KEYS = ("build_id", "scoring_date", "store_id", "sku_id")
CONTEXT = (
    "channel", "segment_tier", "category", "is_msl", "inventory_data_status",
    "sales_data_status", "oos_observed", "last_report_date",
    "days_since_inventory_report", "replenishment_feed_status",
    "estimation_release_status",
)
CANDIDATE_PREDICTORS = frozenset({
    "inventory_candidate", "sales_candidate", "days_cover_candidate",
})
# A positive allowlist prevents IDs, labels and newly added diagnostic columns
# from silently becoming predictors. This is not a selected model feature set.
PREDICTORS = frozenset("""
    channel segment_tier region category is_msl inventory_observed sales_observed
    inventory_data_status sales_data_status oos_observed days_since_inventory_report
    expected_days_7d expected_days_28d observed_days_7d observed_days_28d
    sales_units_observed_7d sales_units_observed_28d velocity_days_7d
    velocity_days_prev7d velocity_days_28d in_stock_velocity_7d
    in_stock_velocity_prev7d in_stock_velocity_28d sales_acceleration_ratio
    days_cover_observed inventory_change_7d_observed oos_days_observed_7d
    oos_days_observed_28d replenishment_feed_status receipt_units_7d receipt_units_28d
    days_since_receipt pending_order_count pending_units_ordered scheduled_units_next7d
    overdue_units_ordered days_until_next_scheduled_receipt eligible_promotion_count
    has_promotion_overlap observed_promo_discount_pct consumer_price_observed
    consumer_promo_data_status days_since_last_audit planogram_compliance_latest
    secondary_display_latest shelf_share_pct_latest same_day_audit_count
""".split()) | CANDIDATE_PREDICTORS


@dataclass
class DevelopmentBatch:
    row_keys: pd.DataFrame
    X: pd.DataFrame
    y: pd.Series
    context: pd.DataFrame
    metadata: dict


@dataclass
class ScoringBatch:
    row_keys: pd.DataFrame
    X: pd.DataFrame
    context: pd.DataFrame
    metadata: dict


def _requested(predictors, include_candidate_estimates):
    if type(include_candidate_estimates) is not bool:
        raise ValueError("include_candidate_estimates must be a boolean")
    if (not isinstance(predictors, (list, tuple)) or not predictors
            or any(not isinstance(name, str) or name not in PREDICTORS for name in predictors)
            or len(set(predictors)) != len(predictors)):
        raise ValueError("Choose a nonempty ordered list/tuple of unique safe predictors")
    if not include_candidate_estimates and CANDIDATE_PREDICTORS.intersection(predictors):
        raise ValueError("Candidate estimates require explicit opt-in")
    return list(predictors)


def _artifact(directory, kind):
    try:
        return verified_artifact(directory, kind=kind)
    except (OSError, KeyError, TypeError) as error:
        raise ValueError(f"A readable accepted {kind} artifact directory is required") from error


def _feature_artifact(directory, purpose):
    record, database, cutoff = _artifact(directory, "features")
    plan = TemporalPlan.from_dict(record.get("temporal_plan"))
    if record.get("purpose") != purpose:
        raise ValueError(f"A purpose={purpose} feature artifact is required")
    return record, database, cutoff, plan


def _query(database, sql, parameters=()):
    try:
        with duckdb.connect(str(database), read_only=True) as con:
            return con.execute(sql, parameters).df()
    except duckdb.Error as error:
        raise ValueError("Required prepared-data table/columns could not be read") from error


def _check_keys(frame, record):
    if frame[list(KEYS)].isna().any().any() or frame.duplicated(list(KEYS)).any():
        raise ValueError("Prepared rows must have non-null unique model keys")
    if (not frame["build_id"].eq(record["source_build_id"]).fillna(False).all()
            or not frame["dataset_hash"].eq(record["dataset_hash"]).fillna(False).all()):
        raise ValueError("Prepared row identities differ from the artifact source")


def _features(database, record, predictors, start, end):
    columns = list(dict.fromkeys([*KEYS, "dataset_hash", "feature_version", *CONTEXT, *predictors]))
    frame = _query(database, "SELECT " + ",".join(columns)
                   + " FROM gold.features_oos_daily WHERE scoring_date BETWEEN ? AND ?", [start, end])
    _check_keys(frame, record)
    if not frame["feature_version"].eq(record["feature_version"]).fillna(False).all():
        raise ValueError("Prepared feature rows have an incompatible version")
    return frame


def _metadata(record, plan, predictors, include_candidates, candidate_rows, returned_rows):
    return {
        "purpose": record["purpose"], "cutoff": record["as_of"],
        "dataset_hash": record["dataset_hash"], "source_build_id": record["source_build_id"],
        "source_database_sha256": record["source_database_sha256"],
        "feature_artifact_id": record["artifact_id"], "feature_version": record["feature_version"],
        "plan_id": plan.to_dict()["plan_id"], "plan_sha256": plan.to_dict()["plan_sha256"],
        "predictors": list(predictors), "include_candidate_estimates": include_candidates,
        "candidate_rows": int(candidate_rows), "returned_rows": int(returned_rows),
    }


def load_development_partition(feature_dir, label_dir, *, fold_id, partition,
                               predictors, include_candidate_estimates=False):
    """Read one existing eligible inner fold; outer validation/test stay closed."""
    selected = _requested(predictors, include_candidate_estimates)
    if fold_id not in ("rolling_dev_1", "rolling_dev_2") or partition not in ("train", "validation"):
        raise ValueError("Choose one inner rolling development fold and train/validation partition")
    features, feature_db, cutoff, plan = _feature_artifact(feature_dir, "development")
    folds = {row[0]: row for row in plan.development_folds()}
    if fold_id not in folds:
        raise RuntimeError("Requested inner fold is unavailable for this source history")
    labels, label_db, label_cutoff = _artifact(label_dir, "labels")
    identity = ("dataset_hash", "source_build_id", "source_database_sha256")
    if (labels.get("purpose") != "development" or cutoff != label_cutoff
            or TemporalPlan.from_dict(labels.get("temporal_plan")) != plan
            or any(features[key] != labels.get(key) for key in identity)):
        raise ValueError("Development feature/label source, plan and cutoff must match")
    expected = folds[fold_id]
    fold = _query(label_db, """SELECT cast(train_last AS VARCHAR),cast(validation_start AS VARCHAR),
        cast(validation_end AS VARCHAR),cast(outcome_end AS VARCHAR),outcomes_available
        FROM evaluation.temporal_folds WHERE fold_id=?""", [fold_id])
    if len(fold) != 1 or tuple(fold.iloc[0]) != (*expected[1:], True):
        raise ValueError("Prepared fold bounds/outcome availability differ from the shared plan")
    counts = _query(label_db, """SELECT count(*) AS candidates FROM evaluation.fold_samples
        WHERE fold_id=? AND partition=?""", [fold_id, partition])
    truth = _query(label_db, "SELECT " + ",".join([*KEYS, "dataset_hash", "target_oos_7d"])
                   + " FROM evaluation.fold_samples WHERE fold_id=? AND partition=? AND eligible",
                   [fold_id, partition])
    _check_keys(truth, labels)
    if truth.empty:
        raise RuntimeError("No complete eligible labels in the requested partition")
    if not truth["target_oos_7d"].isin([0, 1]).all():
        raise ValueError("Eligible observed targets must be binary and non-null")
    start, end = (plan.history_start.isoformat(), expected[1]) if partition == "train" else expected[2:4]
    if not truth["scoring_date"].between(pd.Timestamp(start), pd.Timestamp(end)).all():
        raise ValueError("Prepared label keys fall outside the requested shared fold")
    frame = _features(feature_db, features, selected, start, end)
    data = truth[[*KEYS, "target_oos_7d"]].merge(
        frame, on=list(KEYS), how="left", validate="one_to_one", indicator=True)
    if not data["_merge"].eq("both").all():
        raise ValueError("Eligible labels do not all have matching feature rows")
    data = data.sort_values(["scoring_date", "store_id", "sku_id"]).reset_index(drop=True)
    metadata = _metadata(features, plan, selected, include_candidate_estimates,
                         counts.iloc[0, 0], len(data))
    metadata.update(label_artifact_id=labels["artifact_id"], label_version=labels["label_version"],
                    fold_id=fold_id, partition=partition, eligible_rows=len(truth))
    return DevelopmentBatch(data[list(KEYS)].copy(), data[selected].copy(),
                            data["target_oos_7d"].astype("int64"), data[list(CONTEXT)].copy(), metadata)


def load_scoring_inputs(feature_dir, *, predictors, include_candidate_estimates=False):
    """Read the manifest's latest scoring date only, with no label dependency."""
    selected = _requested(predictors, include_candidate_estimates)
    record, database, cutoff, plan = _feature_artifact(feature_dir, "scoring")
    if cutoff != plan.source_as_of:
        raise ValueError("Latest scoring cutoff must match the source plan")
    data = _features(database, record, selected, cutoff, cutoff)
    if data.empty:
        raise RuntimeError("No feature rows for the latest scoring date")
    data = data.sort_values(["scoring_date", "store_id", "sku_id"]).reset_index(drop=True)
    metadata = _metadata(record, plan, selected, include_candidate_estimates, len(data), len(data))
    metadata["scoring_date"] = cutoff.isoformat()
    return ScoringBatch(data[list(KEYS)].copy(), data[selected].copy(), data[list(CONTEXT)].copy(), metadata)
