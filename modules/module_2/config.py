"""Fixed Module 2 delivery rules: inputs, search spaces, budgets and gates.

Every value here is part of the frozen run identity. Changing one changes
``config_sha256`` and therefore cannot silently reuse earlier evidence.
"""

from datetime import date
from pathlib import Path

from .training import CATEGORICAL, NUMERIC, FEATURES

ROOT = Path(__file__).resolve().parents[2]

FEATURE_VERSION = "oos_features.v1-complete"
LABEL_VERSION = "oos_next7.complete_window.v1"
COHORT = "all_complete_observed_labels_current_missing_and_oos_retained"
THRESHOLD_POLICY = "pooled_segment_thresholds"
HORIZON_DAYS = 7

SEED = 650
THREADS = 2

MAIN_ALGORITHMS = ("lightgbm", "xgboost", "random_forest")
BASELINE_ALGORITHM = "logistic_regression"
ALGORITHMS = MAIN_ALGORITHMS + (BASELINE_ALGORITHM,)

# Contract section 6.2: (kind, values) per searchable parameter.
SEARCH_SPACES = {
    "lightgbm": {
        "n_estimators": ("categorical", [150, 260, 400, 600]),
        "learning_rate": ("log_float", [0.02, 0.15]),
        "num_leaves": ("categorical", [15, 31, 48, 63]),
        "min_child_samples": ("categorical", [20, 45, 80, 150]),
        "colsample_bytree": ("float", [0.6, 1.0]),
        "subsample": ("float", [0.7, 1.0]),
        "reg_lambda": ("log_float", [0.1, 10.0]),
    },
    "xgboost": {
        "n_estimators": ("categorical", [150, 300, 450, 600]),
        "learning_rate": ("log_float", [0.02, 0.15]),
        "max_depth": ("int", [3, 8]),
        "min_child_weight": ("float", [1.0, 20.0]),
        "subsample": ("float", [0.7, 1.0]),
        "colsample_bytree": ("float", [0.6, 1.0]),
        "reg_lambda": ("log_float", [0.1, 10.0]),
    },
    "random_forest": {
        "n_estimators": ("categorical", [100, 150, 200]),
        "max_depth": ("categorical", [8, 12, 16]),
        "min_samples_leaf": ("categorical", [50, 100, 200]),
        "max_features": ("categorical", [0.4, 0.7]),
        "max_samples": ("categorical", [0.5, 0.75]),
    },
}
FIXED_PARAMETERS = {
    "lightgbm": {"subsample_freq": 1, "class_weight": "balanced", "objective": "binary"},
    "xgboost": {"tree_method": "hist", "objective": "binary:logistic", "eval_metric": "logloss"},
    "random_forest": {"bootstrap": True, "max_leaf_nodes": 1024, "class_weight": "balanced"},
    "logistic_regression": {"solver": "liblinear", "max_iter": 1000, "class_weight": "balanced",
                            "scaling": "train_only_standard_scaler"},
}
RF_STARTING_TRIALS = (
    {"n_estimators": 100, "max_depth": 8, "min_samples_leaf": 200, "max_features": 0.4, "max_samples": 0.5},
    {"n_estimators": 100, "max_depth": 12, "min_samples_leaf": 100, "max_features": 0.4, "max_samples": 0.5},
    {"n_estimators": 150, "max_depth": 12, "min_samples_leaf": 100, "max_features": 0.4, "max_samples": 0.5},
)
BASELINE_GRID = ({"C": 0.1}, {"C": 1.0}, {"C": 10.0})

# Real-run budget (contract section 6.2). Profiles may only shrink it for
# explicitly labelled synthetic smoke runs; real runs use "v3" unchanged.
BUDGETS = {
    "v3": {"max_trials": 20, "study_seconds": 45 * 60, "trial_seconds": 20 * 60,
           "total_ml_seconds": 4 * 3600, "min_complete_trials": 3, "n_startup_trials": 5},
    "synthetic": {"max_trials": 4, "study_seconds": 240, "trial_seconds": 120,
                  "total_ml_seconds": 1800, "min_complete_trials": 3, "n_startup_trials": 2},
}

TARGETS = {"precision": 0.75, "recall": 0.70, "roc_auc": 0.80}
SELECTION_RECALL_FLOOR = 0.75
SEGMENT_MIN_ROWS = 200
SEGMENT_MIN_EACH_CLASS = 20
CALIBRATION = {"method": "sigmoid", "clip": 1e-6, "C": 1e6, "input": "logit_raw_probability"}
ECE_BINS = 10

RESOURCE_LIMITS = {"run_payload_bytes": 8 * 1024**3, "bundle_bytes": 512 * 1024**2,
                   "free_space_reserve_bytes": 10 * 1024**3}

RANKING_POLICY_VERSION = "visit_priority.v1"
MONITORING = {"window_days": 30, "min_rows": 1000, "min_positives": 100, "min_alerts": 100,
              "psi_warning": 0.20, "psi_severe": 0.30, "unknown_category_rate": 0.05,
              "missing_rate_increase": 0.10, "drift_min_rows": 1000, "psi_smoothing": 1e-6}

VERSIONS = {
    "bundle": "module2.model_bundle.v1",
    "evaluation": "module2.evaluation_report.v2",
    "benchmark": "module2.benchmark_report.v1",
    "scoring": "module2.scoring_package.v1",
    "ranking": "module2.ranking_package.v1",
    "health": "module2.model_health.v1",
    "monitoring": "module2.model_monitoring.v1",
    "policy": "owner_execution_policy.v1",
    "issued": "module2.issued_predictions.v1",
    "drivers": "module2.drivers.v1",
    "deliver": "module2.delivery_run.v1",
}

PINNED_WORKFLOW = ROOT / "outputs/v3/platform/rolling-verification-20261020/runs/20261020T230945-c00941cadd14/run.json"
GOVERNANCE_DIR = ROOT / "outputs/v3/module2/governance"
DELIVERY_ROOT = ROOT / "outputs/v3/module2/delivery"
# Synthetic profile source date (generated data only; mirrors the V3 business end).
SYNTHETIC_AS_OF = date(2026, 6, 30)


def frozen_rules():
    """The rule set bound into policies, bundles and reports."""
    return {
        "features": list(FEATURES), "categorical": list(CATEGORICAL), "numeric": list(NUMERIC),
        "feature_version": FEATURE_VERSION, "label_version": LABEL_VERSION, "cohort": COHORT,
        "threshold_policy": THRESHOLD_POLICY, "seed": SEED, "threads": THREADS,
        "algorithms": list(ALGORITHMS), "search_spaces": SEARCH_SPACES,
        "fixed_parameters": FIXED_PARAMETERS, "rf_starting_trials": list(RF_STARTING_TRIALS),
        "baseline_grid": list(BASELINE_GRID), "budget_v3": BUDGETS["v3"], "targets": TARGETS,
        "selection_recall_floor": SELECTION_RECALL_FLOOR, "segment_min_rows": SEGMENT_MIN_ROWS,
        "segment_min_each_class": SEGMENT_MIN_EACH_CLASS, "calibration": CALIBRATION,
        "ece_bins": ECE_BINS, "resource_limits": RESOURCE_LIMITS,
        "ranking_policy_version": RANKING_POLICY_VERSION, "monitoring": MONITORING,
    }
