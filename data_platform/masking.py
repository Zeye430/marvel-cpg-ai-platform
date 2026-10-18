"""Development-only account-day block masking with independent receipt inputs.

Hidden observations and diagnostics live in a separate restricted artifact.
No model is fitted and no June or May selection outcomes are inspected.
"""

import json
import uuid
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .estimation import EstimatorConfig, build_daily_state
from .ingestion import digest, literal
from .pipeline import PACKAGE, ROOT, atomic_json, code_provenance, now, writer_lock
from .quality import Quality


def select_blocks(reported_days, expected_counts, start, end, per_length=100, seed=650):
    """Select on reporting completeness, never sales, inventory or OOS outcomes."""
    if start > end or per_length < 1:
        raise ValueError("Invalid masking date range or block count")
    rng = np.random.default_rng(seed)
    reported = defaultdict(set)
    for store, day, count in reported_days:
        if count == expected_counts.get(store):
            reported[store].add(day)
    blocked, chosen = defaultdict(set), []
    for length in range(1, 5):
        candidates = []
        for store, days in sorted(reported.items()):
            for day in sorted(days):
                if start <= day and day + timedelta(days=length - 1) <= end:
                    # Require a visible anchor and all hidden days originally reported.
                    if all(day + timedelta(days=i) in days for i in range(-1, length)):
                        candidates.append((store, day))
        rng.shuffle(candidates)
        selected = 0
        for store, day in candidates:
            span = {day + timedelta(days=i) for i in range(length)}
            # Avoid overlapping blocks or hiding another block's anchor report.
            if span & blocked[store] or day - timedelta(days=1) in blocked[store]:
                continue
            chosen.append({"block_id": f"block-{len(chosen) + 1:05d}", "store_id": store,
                           "start_date": day, "block_length": length})
            blocked[store].update(span | {day - timedelta(days=1), day + timedelta(days=length)})
            selected += 1
            if selected == per_length:
                break
        if selected < per_length:
            raise ValueError(f"Only {selected} valid nonoverlapping length-{length} blocks; requested {per_length}")
    return pd.DataFrame(chosen)


def safe_ratio(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def metrics(frame):
    eligible = frame[frame.inventory_estimate.notna()]
    error = eligible.inventory_estimate - eligible.inventory_truth
    pred = eligible.inventory_estimate.eq(0)
    truth = eligible.oos_truth.astype(bool)
    tp, fp, fn = int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum())
    sales = frame[frame.sales_estimate.notna()]
    sales_error = sales.sales_estimate - sales.sales_truth
    return {
        "rows": int(len(frame)), "inventory_eligible_rows": int(len(eligible)),
        "inventory_coverage": safe_ratio(len(eligible), len(frame)),
        "inventory_mae": float(error.abs().mean()) if len(error) else None,
        "inventory_wape": safe_ratio(error.abs().sum(), eligible.inventory_truth.abs().sum()),
        "inventory_bias": float(error.mean()) if len(error) else None,
        "inventory_signed_error_p10": float(error.quantile(.1)) if len(error) else None,
        "inventory_signed_error_p90": float(error.quantile(.9)) if len(error) else None,
        "state_oos_precision": safe_ratio(tp, tp + fp), "state_oos_recall": safe_ratio(tp, tp + fn),
        "state_oos_tp": tp, "state_oos_fp": fp, "state_oos_fn": fn,
        "sales_eligible_rows": int(len(sales)),
        "sales_mae": float(sales_error.abs().mean()) if len(sales) else None,
        "sales_wape": safe_ratio(sales_error.abs().sum(), sales.sales_truth.abs().sum()),
    }


def store_bootstrap(frame, samples=300, seed=650):
    """Resample stores, preserving all correlated SKU/day rows within a store."""
    rows = frame[frame.inventory_estimate.notna()].copy()
    if rows.empty:
        return {}
    rows["abs_error"] = (rows.inventory_estimate - rows.inventory_truth).abs()
    rows["tp"] = rows.inventory_estimate.eq(0) & rows.oos_truth.astype(bool)
    rows["fp"] = rows.inventory_estimate.eq(0) & ~rows.oos_truth.astype(bool)
    rows["fn"] = rows.inventory_estimate.ne(0) & rows.oos_truth.astype(bool)
    grouped = rows.groupby("store_id").agg(
        abs_error=("abs_error", "sum"), n=("abs_error", "size"),
        tp=("tp", "sum"), fp=("fp", "sum"), fn=("fn", "sum")
    ).to_numpy(dtype=float)
    if len(grouped) < 2:
        return {"stores": len(grouped), "note": "Insufficient stores for clustered uncertainty"}
    rng = np.random.default_rng(seed)
    totals = grouped[rng.integers(0, len(grouped), size=(samples, len(grouped)))].sum(axis=1)
    result = {"stores": len(grouped), "samples": samples, "unit": "store"}
    for name, numerator, denominator in (
        ("inventory_mae", totals[:, 0], totals[:, 1]),
        ("state_oos_precision", totals[:, 2], totals[:, 2] + totals[:, 3]),
        ("state_oos_recall", totals[:, 2], totals[:, 2] + totals[:, 4]),
    ):
        values = numerator[denominator > 0] / denominator[denominator > 0]
        result[name + "_95_interval"] = [float(x) for x in np.quantile(values, [.025, .975])] if len(values) else None
    return result


def evaluate_missing(*, source_manifest=None, output_root=None, start=date(2026, 3, 1),
                     end=date(2026, 4, 30), per_length=100, seed=650,
                     estimator_config=None, memory_limit="1GB"):
    if not date(2026, 3, 1) <= start <= end <= date(2026, 4, 30):
        raise ValueError("Masking method development is restricted to March/April; May and June are locked here")
    estimator_config = estimator_config or EstimatorConfig()
    if estimator_config.promotion_fit_cutoff >= start:
        raise ValueError("Promotion fitting must end before the masking development window")
    source_manifest = Path(source_manifest or ROOT / "outputs/v3/platform/published/silver.json")
    output_root = Path(output_root or ROOT / "outputs/v3/platform/evaluation/missing")
    manifest = json.loads(source_manifest.read_text())
    if manifest["status"] != "Accepted" or digest(manifest["database"]) != manifest["database_sha256"]:
        raise ValueError("Source publication is not accepted or its database hash has changed")
    if end > date.fromisoformat(manifest["configuration"]["as_of"]):
        raise ValueError("Source Silver does not cover the requested development window")
    with writer_lock(output_root / ".writer.lock"):
        run_id = "mask-" + now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True)
        con = duckdb.connect(str(run_dir / "masking.duckdb"))
        con.execute("SET memory_limit = ?", [memory_limit])
        con.execute("SET threads=2")
        con.execute((PACKAGE / "sql/meta.sql").read_text())
        con.execute("CREATE SCHEMA evaluation")
        quality = Quality(con, run_id)
        result = {"run_id": run_id, "status": "Running", "source_build_id": manifest["build_id"],
                  "dataset_hash": manifest["dataset_hash"], "seed": seed,
                  "start": str(start), "end": str(end), "per_length": per_length,
                  "code_tree_hash": code_provenance()[1]}
        try:
            con.execute(f"ATTACH {literal(manifest['database'])} AS source (READ_ONLY)")
            for table, restriction in {
                "dim_stores": "true", "dim_products": "true",
                "fact_sell_out": f"date <= DATE {literal(end)}",
                "store_sku_calendar": f"date <= DATE {literal(end)}",
                "fact_execution_audits": f"date <= DATE {literal(end)}",
                "promotion_calendar": f"date <= DATE {literal(end)}",
                "replenishment_events": f"receipt_date <= DATE {literal(end)}",
            }.items():
                con.execute(f"CREATE TABLE silver.{table} AS SELECT * FROM source.silver.{table} WHERE {restriction}")
            con.execute("DETACH source")
            reported = con.execute("SELECT store_id, date, count(*) FROM silver.fact_sell_out GROUP BY 1,2").fetchall()
            expected = dict(con.execute(f"""SELECT store_id, count(DISTINCT sku_id) FROM silver.fact_sell_out
                WHERE date < DATE {literal(start)} GROUP BY store_id""").fetchall())
            blocks = select_blocks(reported, expected, start, end, per_length, seed)
            # Retrospective closure is evaluation-only. It is never passed to
            # the estimator and does not identify sales on individual missing days.
            con.execute("""
                CREATE TABLE evaluation.natural_gap_closure AS
                WITH reports AS (
                    SELECT *, lag(date) OVER w AS prior_date,
                           lag(inventory_level) OVER w AS prior_inventory
                    FROM silver.fact_sell_out
                    WINDOW w AS (PARTITION BY store_id,sku_id ORDER BY date)
                )
                SELECT o.date AS closing_report_date, o.store_id, o.sku_id, o.prior_date,
                       date_diff('day', o.prior_date, o.date)-1 AS missing_days,
                       coalesce(sum(r.receipt_units),0) AS receipts_including_closure,
                       coalesce(sum(r.receipt_units) FILTER(WHERE r.receipt_date < o.date),0) AS receipts_on_missing_days,
                       o.prior_inventory + coalesce(sum(r.receipt_units),0) - o.inventory_level
                           - o.units_sold_daily AS implied_gap_sales_under_balance
                FROM reports o LEFT JOIN silver.replenishment_events r
                  ON o.store_id=r.store_id AND o.sku_id=r.sku_id
                 AND r.receipt_date > o.prior_date AND r.receipt_date <= o.date
                WHERE date_diff('day', o.prior_date, o.date)>1
                GROUP BY o.date,o.store_id,o.sku_id,o.prior_date,o.prior_inventory,o.inventory_level,o.units_sold_daily
            """)
            result["natural_gap_balance_diagnostic"] = dict(zip(
                ["gap_rows", "zero_implied_gap_sales_fraction", "median_implied_gap_sales",
                 "no_missing_day_receipt_gap_rows", "no_receipt_zero_implied_fraction"],
                con.execute("""SELECT count(*), avg((implied_gap_sales_under_balance=0)::INTEGER),
                    median(implied_gap_sales_under_balance), count(*) FILTER(WHERE receipts_on_missing_days=0),
                    avg((implied_gap_sales_under_balance=0)::INTEGER) FILTER(WHERE receipts_on_missing_days=0)
                    FROM evaluation.natural_gap_closure""").fetchone()
            ))
            con.register("selected_blocks", blocks)
            con.execute("CREATE TABLE evaluation.mask_blocks AS SELECT * FROM selected_blocks")
            con.execute("""
                CREATE TABLE evaluation.mask_days AS
                SELECT block_id, store_id, block_length, cast(start_date AS DATE) AS start_date,
                       cast(start_date AS DATE) + cast(i AS INTEGER) AS date, i+1 AS gap_day
                FROM evaluation.mask_blocks,
                     LATERAL range(cast(block_length AS BIGINT)) t(i)
            """)
            con.execute("""
                CREATE TABLE evaluation.hidden_observations AS
                SELECT m.block_id, m.block_length, m.gap_day, o.date, o.store_id, o.sku_id,
                       o.units_sold_daily AS sales_truth, o.inventory_level AS inventory_truth,
                       o.is_out_of_stock AS oos_truth
                FROM silver.fact_sell_out o JOIN evaluation.mask_days m USING (date, store_id)
            """)
            # Delete all SKU observations for each selected account-day BEFORE
            # rebuilding every window, fallback statistic and report anchor.
            con.execute("DELETE FROM silver.fact_sell_out o USING evaluation.mask_days m WHERE o.date=m.date AND o.store_id=m.store_id")
            quality.query("complete_block_hidden", "masking", """
                SELECT o.date, o.store_id FROM silver.fact_sell_out o
                JOIN evaluation.mask_days m USING (date, store_id)
            """)
            quality.require_pass()
            result["daily_state"] = build_daily_state(con, run_id, end, quality, estimator_config)
            con.execute("""
                CREATE TABLE evaluation.masked_state_inputs AS
                SELECT h.*, g.channel, g.segment_tier, g.category, g.last_observed_inventory,
                       g.days_since_inventory_report, g.inventory_estimated,
                       g.units_sold_estimated, g.sales_baseline_unadjusted,
                       g.cumulative_gap_sales_unadjusted, g.cumulative_gap_sales_adjusted,
                       g.cumulative_gap_receipts, g.inventory_data_status,
                       sum(g.receipt_units_from_deliveries) OVER (PARTITION BY h.block_id, h.sku_id) > 0 AS receipt_in_block
                FROM evaluation.hidden_observations h
                JOIN gold.mart_store_sku_daily g USING (date, store_id, sku_id)
            """)
            quality.query("hidden_dates_not_in_history", "masking", """
                SELECT d.date, d.store_id, d.sku_id FROM gold.mart_store_sku_daily d
                JOIN evaluation.hidden_observations h USING(date,store_id,sku_id)
                WHERE d.sales_history_max_date >= h.date
            """)
            quality.require_pass()
            frame = con.execute("SELECT * FROM evaluation.masked_state_inputs").df()
            methods = {
                "zero_fill_diagnostic": (np.zeros(len(frame)), np.zeros(len(frame))),
                "locf_inventory": (frame.last_observed_inventory, frame.sales_baseline_unadjusted),
                "sales_depletion": ((frame.last_observed_inventory - frame.cumulative_gap_sales_unadjusted).clip(lower=0), frame.sales_baseline_unadjusted),
                "promotion_depletion": ((frame.last_observed_inventory - frame.cumulative_gap_sales_adjusted).clip(lower=0), frame.units_sold_estimated),
                "delivery_reconciled_no_promotion": ((frame.last_observed_inventory + frame.cumulative_gap_receipts - frame.cumulative_gap_sales_unadjusted).clip(lower=0), frame.sales_baseline_unadjusted),
                "delivery_reconciled": (frame.inventory_estimated, frame.units_sold_estimated),
            }
            rows = []
            for name, (inventory, sales) in methods.items():
                data = frame.copy()
                data["method"] = name
                data["inventory_estimate"] = inventory
                data["sales_estimate"] = sales
                # All baselines use the same four-day contract. Common-population
                # results and all-row coverage are both reported explicitly.
                ineligible = ~data.days_since_inventory_report.between(1, 4)
                data.loc[ineligible, ["inventory_estimate", "sales_estimate"]] = np.nan
                data["common_eligible"] = frame.inventory_estimated.notna()
                rows.append(data)
            long = pd.concat(rows, ignore_index=True)
            con.register("method_predictions", long)
            con.execute("CREATE TABLE evaluation.method_predictions AS SELECT * FROM method_predictions")
            result["masked_blocks"] = len(blocks)
            result["masked_store_days"] = int(blocks.block_length.sum())
            result["masked_sku_days"] = len(frame)
            result["overall_all_eligible"] = {name: metrics(data) for name, data in long.groupby("method")}
            common = long[long.common_eligible]
            result["overall_common_population"] = {name: metrics(data) for name, data in common.groupby("method")}
            result["store_cluster_uncertainty"] = {name: store_bootstrap(data, seed=seed) for name, data in common.groupby("method")}
            segments = []
            for dimension in ("channel", "segment_tier", "sku_id", "block_length", "gap_day", "receipt_in_block"):
                for (name, value), data in common.groupby(["method", dimension]):
                    segments.append({"method": name, "dimension": dimension, "value": str(value), **metrics(data)})
            con.register("segment_metrics", pd.DataFrame(segments))
            con.execute("CREATE TABLE evaluation.segment_metrics AS SELECT * FROM segment_metrics")
            con.execute(f"COPY evaluation.segment_metrics TO {literal(run_dir / 'segment_metrics.csv')} (HEADER, DELIMITER ',')")
            result.update(status="Complete", release_decision="Candidate only; no automatic operational/model promotion",
                          warning="Current-state masking metrics are not future-seven-day model precision/recall; natural gaps differ from artificial masks.")
        except Exception as error:
            result.update(status="Failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            atomic_json(run_dir / "quality.json", quality.report())
            con.close()
            result["finished_at"] = str(now())
            atomic_json(run_dir / "report.json", result)
        return result
