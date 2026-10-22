"""Train-only preprocessing shared by every algorithm.

Numeric: median imputation plus an explicit missing indicator for every input.
Categorical: one-hot of training levels; unseen/missing values map to all-zero
plus an explicit missing indicator (handle_unknown=ignore). The fitted state is
plain JSON, so loading it never executes code. Infinite or non-numeric values
fail instead of becoming zero.
"""

import numpy as np
import pandas as pd

from . import config
from .contracts import Module2Error, json_sha256

VERSION = "module2.preprocessor.v1"


def _numeric(series, name):
    if isinstance(series.dtype, pd.BooleanDtype) or pd.api.types.is_bool_dtype(series):
        return series.astype("Float64").to_numpy(dtype="float64", na_value=np.nan)
    if series.dtype == object:
        kind = pd.api.types.infer_dtype(series, skipna=True)
        if kind not in ("boolean", "integer", "floating", "mixed-integer-float", "empty"):
            raise Module2Error(f"Non-numeric value in numeric predictor {name}")
        array = pd.to_numeric(series.map(lambda v: float(v) if v is not None and v is not pd.NA else np.nan),
                              errors="raise").to_numpy(dtype="float64")
    else:
        try:
            array = pd.to_numeric(series, errors="raise").astype("float64").to_numpy()
        except (TypeError, ValueError) as error:
            raise Module2Error(f"Non-numeric value in numeric predictor {name}") from error
    if np.isinf(array).any():
        raise Module2Error(f"Infinite value in numeric predictor {name}")
    return array


def _categories(series):
    return series.astype("string")


class Preprocessor:
    def __init__(self, state):
        if state.get("version") != VERSION or state.get("features") != list(config.FEATURES):
            raise Module2Error("Incompatible preprocessor state")
        self.state = state
        self.columns = list(state["columns"])

    @classmethod
    def fit(cls, frame):
        missing = [c for c in config.FEATURES if c not in frame.columns]
        if missing:
            raise Module2Error(f"Training frame lacks predictors: {missing[:3]}")
        if frame.empty:
            raise Module2Error("Preprocessing requires training rows")
        numeric = {}
        for name in config.NUMERIC:
            values = _numeric(frame[name], name)
            observed = values[~np.isnan(values)]
            median = float(np.median(observed)) if observed.size else None
            numeric[name] = {"median": median, "all_missing": median is None,
                             "train_missing_rate": float(np.isnan(values).mean())}
        categorical = {}
        for name in config.CATEGORICAL:
            values = _categories(frame[name])
            levels = sorted(values.dropna().unique().tolist())
            categorical[name] = {"levels": levels, "train_missing_rate": float(values.isna().mean())}
        columns, mapping = [], {}
        for name in config.CATEGORICAL:
            mapped = [f"{name}={level}" for level in categorical[name]["levels"]] + [f"{name}__missing"]
            columns += mapped
            mapping[name] = mapped
        for name in config.NUMERIC:
            mapped = [name, f"{name}__missing"]
            columns += mapped
            mapping[name] = mapped
        state = {"version": VERSION, "features": list(config.FEATURES), "numeric": numeric,
                 "categorical": categorical, "columns": columns, "mapping": mapping,
                 "train_rows": int(len(frame)), "dtype": "float32"}
        return cls(state)

    def transform(self, frame):
        missing = [c for c in config.FEATURES if c not in frame.columns]
        if missing:
            raise Module2Error(f"Frame lacks predictors: {missing[:3]}")
        out = np.zeros((len(frame), len(self.columns)), dtype=np.float32)
        position = {name: i for i, name in enumerate(self.columns)}
        for name in config.CATEGORICAL:
            values = _categories(frame[name])
            levels = self.state["categorical"][name]["levels"]
            for level in levels:
                out[:, position[f"{name}={level}"]] = values.eq(level).fillna(False).to_numpy(dtype=bool)
            out[:, position[f"{name}__missing"]] = values.isna().to_numpy(dtype=bool)
        for name in config.NUMERIC:
            values = _numeric(frame[name], name)
            is_missing = np.isnan(values)
            fill = self.state["numeric"][name]["median"]
            out[:, position[name]] = np.where(is_missing, 0.0 if fill is None else fill, values)
            out[:, position[f"{name}__missing"]] = is_missing
        if not np.isfinite(out).all():
            raise Module2Error("Transformed matrix contains non-finite values")
        return out

    def unknown_category_rates(self, frame):
        rates = {}
        for name in config.CATEGORICAL:
            values = _categories(frame[name]).dropna()
            levels = self.state["categorical"][name]["levels"]
            rates[name] = float((~values.isin(levels)).mean()) if len(values) else 0.0
        return rates

    def to_dict(self):
        return dict(self.state)

    @property
    def sha256(self):
        return json_sha256(self.state)

    def raw_feature(self, column):
        for raw, mapped in self.state["mapping"].items():
            if column in mapped:
                return raw
        raise Module2Error(f"Unknown transformed column {column}")
