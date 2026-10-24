"""Plain algorithm adapters: fit, raw probability, save and verified load.

No plugin discovery or user-supplied code. LightGBM and XGBoost use their
native model formats; RandomForest and the logistic baseline use joblib, which
can execute code on load, so loading is limited to local bundles whose bytes
and runtime versions match a verified manifest.
"""

import json
import warnings
from pathlib import Path

import numpy as np

from . import config
from .contracts import Module2Error, sha256_file


class ConvergenceFailure(Module2Error):
    """A baseline fit did not converge; recorded, never treated as success."""


def library_versions():
    import joblib
    import lightgbm
    import sklearn
    import xgboost
    return {"lightgbm": lightgbm.__version__, "xgboost": xgboost.__version__,
            "scikit-learn": sklearn.__version__, "joblib": joblib.__version__,
            "numpy": np.__version__}


def _check_inputs(X, y=None):
    if not isinstance(X, np.ndarray) or X.ndim != 2 or not np.isfinite(X).all():
        raise Module2Error("Model input must be a finite 2D array")
    if y is not None:
        y = np.asarray(y)
        if len(y) != len(X) or not np.isin(y, (0, 1)).all() or len(np.unique(y)) != 2:
            raise Module2Error("Training labels must be aligned binary 0/1 with both classes")
    return y


def _check_probabilities(score, n):
    score = np.asarray(score, dtype="float64").reshape(-1)
    if len(score) != n or not np.isfinite(score).all() or (score < 0).any() or (score > 1).any():
        raise Module2Error("Model produced invalid probabilities")
    return score


class Adapter:
    algorithm = None

    def __init__(self, model, params, info=None):
        self.model, self.params, self.info = model, dict(params), dict(info or {})

    def predict_raw_probability(self, X):
        _check_inputs(X)
        return _check_probabilities(self._predict(X), len(X))

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        filename = self._save(directory)
        meta = {"algorithm": self.algorithm, "file": filename, "sha256": sha256_file(directory / filename),
                "params": self.params, "fixed_parameters": config.FIXED_PARAMETERS[self.algorithm],
                "seed": config.SEED, "versions": library_versions(), "info": self.info}
        (directory / "model.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        return meta


class LightGBMAdapter(Adapter):
    algorithm = "lightgbm"

    @classmethod
    def fit(cls, params, X, y, seed, n_jobs):
        from lightgbm import LGBMClassifier
        model = LGBMClassifier(**config.FIXED_PARAMETERS["lightgbm"], **params,
                               random_state=seed, n_jobs=n_jobs, verbosity=-1)
        model.fit(X, y)
        return cls(model.booster_, params)

    def _predict(self, X):
        return self.model.predict(X, num_threads=config.THREADS)

    def _save(self, directory):
        self.model.save_model(str(directory / "model.txt"))
        return "model.txt"

    @classmethod
    def _load(cls, path, meta):
        import lightgbm
        return cls(lightgbm.Booster(model_file=str(path)), meta["params"], meta.get("info"))


class XGBoostAdapter(Adapter):
    algorithm = "xgboost"

    @classmethod
    def fit(cls, params, X, y, seed, n_jobs):
        from xgboost import XGBClassifier
        positives = int(np.sum(y == 1))
        weight = float((len(y) - positives) / positives)
        model = XGBClassifier(**config.FIXED_PARAMETERS["xgboost"], **params,
                              scale_pos_weight=weight, random_state=seed, n_jobs=n_jobs)
        model.fit(X, y)
        return cls(model.get_booster(), params, {"scale_pos_weight": weight})

    def _predict(self, X):
        import xgboost
        return self.model.predict(xgboost.DMatrix(X, nthread=config.THREADS))

    def _save(self, directory):
        self.model.save_model(str(directory / "model.ubj"))
        return "model.ubj"

    @classmethod
    def _load(cls, path, meta):
        import xgboost
        booster = xgboost.Booster()
        booster.load_model(str(path))
        booster.set_param({"nthread": config.THREADS})
        return cls(booster, meta["params"], meta.get("info"))


class RandomForestAdapter(Adapter):
    algorithm = "random_forest"

    @classmethod
    def fit(cls, params, X, y, seed, n_jobs):
        from sklearn.ensemble import RandomForestClassifier
        model = RandomForestClassifier(**config.FIXED_PARAMETERS["random_forest"], **params,
                                       random_state=seed, n_jobs=n_jobs)
        model.fit(X, y)
        nodes = int(sum(tree.tree_.node_count for tree in model.estimators_))
        return cls(model, params, {"node_count": nodes})

    def _predict(self, X):
        return self.model.predict_proba(X)[:, list(self.model.classes_).index(1)]

    def _save(self, directory):
        import joblib
        joblib.dump(self.model, directory / "model.joblib", compress=3)
        return "model.joblib"

    @classmethod
    def _load(cls, path, meta):
        import joblib
        return cls(joblib.load(path), meta["params"], meta.get("info"))


class LogisticAdapter(Adapter):
    algorithm = "logistic_regression"

    @classmethod
    def fit(cls, params, X, y, seed, n_jobs):
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        fixed = {k: v for k, v in config.FIXED_PARAMETERS["logistic_regression"].items() if k != "scaling"}
        model = Pipeline([("scale", StandardScaler()),
                          ("logistic", LogisticRegression(**fixed, **params, random_state=seed))])
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            try:
                model.fit(X, y)
            except ConvergenceWarning as error:
                raise ConvergenceFailure(f"Logistic baseline did not converge: {error}") from error
        return cls(model, params)

    def _predict(self, X):
        return self.model.predict_proba(X)[:, list(self.model.classes_).index(1)]

    def _save(self, directory):
        import joblib
        joblib.dump(self.model, directory / "model.joblib", compress=3)
        return "model.joblib"

    @classmethod
    def _load(cls, path, meta):
        import joblib
        return cls(joblib.load(path), meta["params"], meta.get("info"))


ADAPTERS = {a.algorithm: a for a in (LightGBMAdapter, XGBoostAdapter, RandomForestAdapter, LogisticAdapter)}


def fit_candidate(algorithm, params, X_train, y_train, seed=config.SEED, n_jobs=config.THREADS):
    if algorithm not in ADAPTERS:
        raise Module2Error(f"Unsupported algorithm: {algorithm}")
    y = _check_inputs(X_train, y_train)
    return ADAPTERS[algorithm].fit(params, X_train, y.astype(int), seed, n_jobs)


def load_candidate(directory, expected=None):
    """Load a local model only after its manifest, bytes and versions match."""
    directory = Path(directory)
    try:
        meta = json.loads((directory / "model.json").read_text())
    except (OSError, ValueError) as error:
        raise Module2Error("Model manifest missing or invalid") from error
    if expected is not None and meta != expected:
        raise Module2Error("Model manifest differs from the frozen bundle")
    algorithm = meta.get("algorithm")
    if algorithm not in ADAPTERS or Path(meta.get("file", "")).name != meta.get("file"):
        raise Module2Error("Unsupported or unsafe model manifest")
    path = directory / meta["file"]
    if not path.is_file() or sha256_file(path) != meta.get("sha256"):
        raise Module2Error("Model bytes differ from the manifest")
    if meta.get("versions") != library_versions():
        raise Module2Error("Installed model libraries differ from the frozen bundle")
    return ADAPTERS[algorithm]._load(path, meta)
