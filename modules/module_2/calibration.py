"""Global sigmoid calibration on logit(raw score), plus calibration diagnostics.

Calibration is an estimation method, not a guarantee of reliable probabilities;
validation/holdout reports always include Brier, ECE and a reliability table.
"""

import warnings

import numpy as np

from . import config
from .contracts import Module2Error

VERSION = "module2.calibration.v1"


def _logit(raw):
    clip = config.CALIBRATION["clip"]
    raw = np.clip(np.asarray(raw, dtype="float64"), clip, 1 - clip)
    return np.log(raw / (1 - raw))


def fit_sigmoid(raw, y):
    """Fit on held-out development scores only. Invalid fits raise."""
    from sklearn.linear_model import LogisticRegression
    raw, y = np.asarray(raw, dtype="float64"), np.asarray(y, dtype=int)
    if len(raw) != len(y) or len(np.unique(y)) != 2 or not np.isfinite(raw).all():
        raise Module2Error("Calibration requires aligned finite scores and both classes")
    model = LogisticRegression(C=config.CALIBRATION["C"], solver="lbfgs", max_iter=1000)
    with warnings.catch_warnings():
        # sklearn 1.6 passes a legacy option newer scipy ignores; not a fit problem.
        warnings.filterwarnings("ignore", message="Unknown solver options: iprint")
        model.fit(_logit(raw).reshape(-1, 1), y)
    coef, intercept = float(model.coef_[0, 0]), float(model.intercept_[0])
    if not np.isfinite([coef, intercept]).all() or coef <= 0:
        raise Module2Error("Calibration is non-monotonic or undefined")
    return {"version": VERSION, "method": "sigmoid", "input": config.CALIBRATION["input"],
            "clip": config.CALIBRATION["clip"], "coef": coef, "intercept": intercept,
            "fit_rows": int(len(y)), "fit_positives": int(y.sum())}


def check(calibrator):
    if (not isinstance(calibrator, dict) or calibrator.get("version") != VERSION
            or calibrator.get("method") != "sigmoid" or calibrator.get("clip") != config.CALIBRATION["clip"]
            or not isinstance(calibrator.get("coef"), float) or not isinstance(calibrator.get("intercept"), float)
            or not np.isfinite([calibrator["coef"], calibrator["intercept"]]).all() or calibrator["coef"] <= 0):
        raise Module2Error("Invalid calibration record")
    return calibrator


def apply(calibrator, raw):
    check(calibrator)
    z = calibrator["coef"] * _logit(raw) + calibrator["intercept"]
    return 1.0 / (1.0 + np.exp(-z))


def brier(y, probability):
    y, p = np.asarray(y, dtype=float), np.asarray(probability, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(y) else None


def reliability(y, probability, bins=config.ECE_BINS):
    """Fixed equal-width bins; empty bins are reported as empty, not zero."""
    y, p = np.asarray(y, dtype=float), np.asarray(probability, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    index = np.minimum(np.digitize(p, edges[1:-1], right=False), bins - 1)
    rows = []
    for b in range(bins):
        mask = index == b
        count = int(mask.sum())
        rows.append({"bin": b, "lower": float(edges[b]), "upper": float(edges[b + 1]), "count": count,
                     "mean_probability": float(p[mask].mean()) if count else None,
                     "observed_rate": float(y[mask].mean()) if count else None})
    return rows


def ece(y, probability, bins=config.ECE_BINS):
    if not len(y):
        return None
    table = reliability(y, probability, bins)
    n = len(y)
    return float(sum(r["count"] / n * abs(r["observed_rate"] - r["mean_probability"])
                     for r in table if r["count"]))
