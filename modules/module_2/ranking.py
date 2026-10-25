"""Fixed store visit ranking (visit_priority.v1): transparent heuristic, not routing.

risk_score is the max SKU probability (a risk index, not a calibrated store
probability). The reference sales proxy is 7 x prior in-stock velocity x
probability x static list price; it is not lost units or net sales and has no
currency. Unknown inputs are surfaced for data follow-up, never treated as 0.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, scoring
from .contracts import Module2Error, read_json, sha256_file, utc_now, write_json

BUCKETS = {1: "Observed MSL gap", 2: "Predicted OOS alert", 3: "Data follow-up: inventory/MSL unknown",
           4: "Monitor"}
RANK_COLUMNS = ["build_id", "dataset_hash", "scoring_run_id", "scoring_date", "store_id", "priority_rank",
                "risk_score", "msl_gap_count", "ranking_policy_version", "reason_json"]


def percentile(values):
    """Average tied ranks: (rank - 1) / (N - 1); a single store gets 1.0."""
    values = pd.Series(values, dtype=float)
    if len(values) == 1:
        return pd.Series([1.0], index=values.index)
    ranks = values.rank(method="average")
    return (ranks - 1) / (len(values) - 1)


def sku_frame(predictions, detail, products):
    frame = predictions.merge(detail, on=["store_id", "sku_id"], how="left", validate="one_to_one")
    prices = products.set_index("sku_id")["list_price"]
    frame["list_price"] = frame["sku_id"].map(prices).astype(float)
    velocity = pd.to_numeric(frame["in_stock_velocity_28d"], errors="coerce")
    frame["demand_proxy_units"] = 7 * velocity * frame["probability"]
    frame["sales_proxy"] = frame["demand_proxy_units"] * frame["list_price"]
    frame.loc[velocity.isna() | frame["list_price"].isna(), ["demand_proxy_units", "sales_proxy"]] = np.nan
    msl = frame["is_msl"].astype("boolean")
    oos = frame["oos_observed"].astype("boolean")
    frame["msl_gap"] = (msl.fillna(False) & oos.fillna(False)).astype(bool)
    frame["msl_unknown"] = (msl.fillna(False) & oos.isna()).astype(bool) | msl.isna().to_numpy()
    frame["inventory_unknown"] = frame["inventory_observed"].isna()
    return frame


def rank_stores(frame, stores):
    rows = []
    for store_id, group in frame.groupby("store_id", sort=True):
        scored = group["probability"].notna()
        if not scored.any():
            continue
        proxies = group["sales_proxy"]
        supported = int(proxies.notna().sum())
        proxy_status = "Complete" if supported == len(group) else ("Partial" if supported else "Unknown")
        msl_gap = int(group["msl_gap"].sum())
        alerts = int(group["is_predicted_oos"].sum())
        unknown = int(group["inventory_unknown"].sum()) + int(group["msl_unknown"].sum())
        bucket = 1 if msl_gap else 2 if alerts else 3 if unknown else 4
        top = group.sort_values(["probability", "sku_id"], ascending=[False, True]).head(5)
        rows.append({"store_id": store_id, "bucket": bucket, "risk_score": float(group["probability"].max()),
                     "msl_gap_count": msl_gap, "msl_unknown_count": int(group["msl_unknown"].sum()),
                     "alert_count": alerts, "inventory_unknown_count": int(group["inventory_unknown"].sum()),
                     "sku_count": int(len(group)), "proxy_status": proxy_status,
                     "proxy_known_sum": float(proxies.sum(min_count=1)) if supported else None,
                     "proxy_supported": supported, "proxy_expected": int(len(group)),
                     "top_skus": [{"sku_id": r.sku_id, "probability": round(float(r.probability), 6),
                                   "threshold": round(float(r.threshold), 6), "alert": bool(r.is_predicted_oos)}
                                  for r in top.itertuples()]})
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["risk_percentile"] = np.nan
    table["impact_percentile"] = np.nan
    table["impact_fallback"] = True
    for bucket, part in table.groupby("bucket"):
        table.loc[part.index, "risk_percentile"] = percentile(part["risk_score"])
        complete = part[part["proxy_status"] == "Complete"]
        if len(complete):
            table.loc[complete.index, "impact_percentile"] = percentile(complete["proxy_known_sum"])
            table.loc[complete.index, "impact_fallback"] = False
        missing = part.index.difference(complete.index)
        table.loc[missing, "impact_percentile"] = table.loc[missing, "risk_percentile"]
    table["priority_index"] = 0.5 * table["risk_percentile"] + 0.5 * table["impact_percentile"]
    table = table.sort_values(["bucket", "priority_index", "msl_gap_count", "risk_score", "store_id"],
                              ascending=[True, False, False, False, True]).reset_index(drop=True)
    table["priority_rank"] = np.arange(1, len(table) + 1)
    known = set(table["store_id"])
    if len(known) != len(table):
        raise Module2Error("Ranking must contain each store once")
    _ = stores
    return table


def reason(row):
    return json.dumps({
        "ranking_policy_version": config.RANKING_POLICY_VERSION, "bucket": int(row.bucket),
        "bucket_label": BUCKETS[int(row.bucket)], "risk_score": row.risk_score,
        "risk_percentile": float(row.risk_percentile), "impact_percentile": float(row.impact_percentile),
        "impact_fallback_to_risk": bool(row.impact_fallback), "priority_index": float(row.priority_index),
        "msl_gap_count": int(row.msl_gap_count), "msl_unknown_count": int(row.msl_unknown_count),
        "alert_count": int(row.alert_count), "inventory_unknown_count": int(row.inventory_unknown_count),
        "sku_count": int(row.sku_count),
        "reference_sales_proxy": {"status": row.proxy_status, "known_sum": row.proxy_known_sum,
                                  "supported_skus": int(row.proxy_supported), "expected_skus": int(row.proxy_expected),
                                  "formula": "7 x in_stock_velocity_28d x probability x list_price",
                                  "nature": "reference proxy, not lost units or net sales; static list price; currency not supplied"},
        "top_skus": row.top_skus,
        "notes": ["risk_score is the maximum SKU probability: a risk index, not a calibrated store probability",
                  "Transparent heuristic; not route optimization; weights not tuned on test data"]},
        sort_keys=True, ensure_ascii=True)


def rank(scoring_dir, products, stores, output_dir):
    """Write an immutable ranking package for one scoring run."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise Module2Error("Ranking packages are immutable; use a new directory")
    manifest, predictions, detail = scoring.read_package(scoring_dir)
    frame = sku_frame(predictions, detail.drop(columns=["calibrated_probability"]), products)
    table = rank_stores(frame, stores)
    ranked = set(table["store_id"]) if len(table) else set()
    followups = sorted(set(stores["store_id"]) - ranked)
    meta = predictions.iloc[0]
    out = pd.DataFrame({
        "build_id": meta["build_id"], "dataset_hash": meta["dataset_hash"], "scoring_run_id": manifest["scoring_run_id"],
        "scoring_date": meta["scoring_date"], "store_id": table["store_id"],
        "priority_rank": table["priority_rank"].astype("int64"), "risk_score": table["risk_score"].astype(float),
        "msl_gap_count": table["msl_gap_count"].astype("int64"),
        "ranking_policy_version": config.RANKING_POLICY_VERSION,
        "reason_json": [reason(r) for r in table.itertuples()]})
    output_dir.mkdir(parents=True)
    out.to_parquet(output_dir / "ranking.parquet", index=False)
    frame[["store_id", "sku_id", "probability", "is_predicted_oos", "list_price", "demand_proxy_units",
           "sales_proxy", "msl_gap", "msl_unknown", "inventory_unknown"]].to_parquet(output_dir / "sku-components.parquet",
                                                                                     index=False)
    record = {"version": config.VERSIONS["ranking"], "status": "Accepted",
              "ranking_policy_version": config.RANKING_POLICY_VERSION,
              "scoring_run_id": manifest["scoring_run_id"], "scoring_date": manifest["scoring_date"],
              "scoring_manifest_sha256": sha256_file(Path(scoring_dir) / "manifest.json"),
              "ranked_stores": int(len(out)), "store_universe": int(len(stores)),
              "data_followup_unscored_stores": followups,
              "bucket_counts": {BUCKETS[int(k)]: int(v) for k, v in table["bucket"].value_counts().sort_index().items()}
              if len(table) else {},
              "proxy_status_counts": {k: int(v) for k, v in table["proxy_status"].value_counts().items()} if len(table) else {},
              "files": {n: sha256_file(output_dir / n) for n in ("ranking.parquet", "sku-components.parquet")},
              "created_at": utc_now()}
    write_json(output_dir / "manifest.json", record, exclusive=True)
    return record


def read_package(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json", "ranking manifest")
    if manifest.get("version") != config.VERSIONS["ranking"] or manifest.get("status") != "Accepted":
        raise Module2Error("Not an accepted ranking package")
    for name, sha in manifest["files"].items():
        if sha256_file(directory / name) != sha:
            raise Module2Error(f"Ranking package file changed: {name}")
    return manifest, pd.read_parquet(directory / "ranking.parquet")
