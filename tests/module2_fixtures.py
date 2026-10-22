"""Synthetic Module 2 frames for tests only; never real client rows or metrics."""

import numpy as np
import pandas as pd

from modules.module_2 import config

CHANNELS = ("Modern Trade", "Traditional Trade", "Distributor")
TIERS = ("Tier 1", "Tier 2", "Tier 3")


def synthetic_frame(rows, start="2026-01-01", *, days=1, seed=650, stores=60, positive_shift=0.0):
    """Learnable synthetic OOS frame with all 47 predictors and nine segments."""
    rng = np.random.default_rng(seed)
    data = pd.DataFrame({name: rng.random(rows) * 10 for name in config.NUMERIC})
    for name in ("is_msl", "oos_observed", "has_promotion_overlap", "planogram_compliance_latest",
                 "secondary_display_latest"):
        values = rng.random(rows) < 0.3
        series = pd.Series(values, dtype="boolean")      # nullable, like the shared reader
        series[rng.random(rows) < 0.1] = pd.NA
        data[name] = series
    inventory = rng.gamma(2.0, 20.0, rows)
    velocity = rng.gamma(2.0, 3.0, rows)
    data["inventory_observed"] = inventory
    data["in_stock_velocity_28d"] = velocity
    data["days_cover_observed"] = inventory / np.maximum(velocity, 0.1)
    data.loc[rng.random(rows) < 0.08, "inventory_observed"] = np.nan
    store_index = rng.integers(0, stores, rows)
    data["channel"] = [CHANNELS[i % 3] for i in store_index]
    data["segment_tier"] = [TIERS[(i // 3) % 3] for i in store_index]
    data["region"] = rng.choice(["East", "West", "North"], rows)
    data["category"] = rng.choice(["Laundry", "Dishwashing"], rows)
    data["inventory_data_status"] = np.where(data["inventory_observed"].isna(), "Estimated", "Observed")
    data["sales_data_status"] = "Observed"
    data["replenishment_feed_status"] = "assumed_complete_snapshot"
    data["consumer_promo_data_status"] = rng.choice(["Observed", "Unknown"], rows)
    risk = -0.08 * np.nan_to_num(data["days_cover_observed"].to_numpy(float), nan=5.0) + 0.6 * (
        data["oos_observed"].fillna(False).astype(float).to_numpy()) + positive_shift
    probability = 1 / (1 + np.exp(-(risk + rng.normal(0, 0.6, rows))))
    data["target_oos_7d"] = (rng.random(rows) < probability).astype(int)
    dates = pd.Timestamp(start) + pd.to_timedelta(rng.integers(0, days, rows), unit="D")
    data["build_id"] = "synthetic-build"
    data["scoring_date"] = dates
    data["store_id"] = [f"S{i:03d}" for i in store_index]
    data["sku_id"] = [f"P{i:03d}" for i in range(rows)]
    return data.reset_index(drop=True)
