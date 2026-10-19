"""
shared/data.py
==============
Centralized data management for Marvel CPG AI Platform.
Provides high-performance, memory-conscious data loading from Aiven PostgreSQL
with automatic local CSV fallback, thread-safe caching, and shared data scoping.
"""

import gc
import os
import threading
import urllib.parse
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
CSV_CANDIDATE_DIRS = [
    BASE_DIR / "new_marvel_data",
    BASE_DIR / "module_1" / "data",
    BASE_DIR / "data",
    BASE_DIR / "data" / "raw",
]
DATA_DIR: Optional[Path] = next((d for d in CSV_CANDIDATE_DIRS if d.exists() and (d / "dim_products.csv").exists()), None)

# Rep Profiles & Manager Definition
REPS = [f"Rep {c}" for c in "ABCDEF"]
REP_PROFILES = {
    "Rep A": {
        "name": "Alex Mercer",
        "title": "Senior Field Representative",
        "region": "Northeast Territory",
        "email": "alex.mercer@marvelcpg.com",
        "badge": "East Commercial Accounts",
    },
    "Rep B": {
        "name": "Beatrice Vance",
        "title": "Regional Account Executive",
        "region": "Southeast Territory",
        "email": "beatrice.vance@marvelcpg.com",
        "badge": "Southeast Retail Accounts",
    },
    "Rep C": {
        "name": "Carlos Delgado",
        "title": "Commercial Execution Lead",
        "region": "Midwest Territory",
        "email": "carlos.delgado@marvelcpg.com",
        "badge": "Midwest Route Operations",
    },
    "Rep D": {
        "name": "Danielle Wu",
        "title": "Territory Account Executive",
        "region": "West Territory",
        "email": "danielle.wu@marvelcpg.com",
        "badge": "Pacific & Mountain Routes",
    },
    "Rep E": {
        "name": "Marcus Stone",
        "title": "Modern Trade Specialist",
        "region": "National Modern Trade",
        "email": "marcus.stone@marvelcpg.com",
        "badge": "Hypermarket & Supercenter Lead",
    },
    "Rep F": {
        "name": "Sarah Jenkins",
        "title": "Traditional Trade Specialist",
        "region": "Independent Retail Network",
        "email": "sarah.jenkins@marvelcpg.com",
        "badge": "Bodega & Distributor Routes",
    },
}

MANAGER_PROFILE = {
    "name": "Elena Rostova",
    "title": "Director of Commercial Operations & Execution",
    "role": "Regional Commercial Director",
    "email": "elena.rostova@marvelcpg.com",
    "scope": "All Commercial Territories (221 Stores · 4 Regions · 23 SKUs)",
}

# Standard Theme Channels & Colors
CH = ["Modern Trade", "Traditional Trade", "Distributor"]
REG = ["Midwest", "Northeast", "Southeast", "West"]

COL = {
    "Modern Trade": "#0071E3",
    "Traditional Trade": "#FF9500",
    "Distributor": "#AF52DE",
}

DEFAULT_START = "2026-04-01"
DEFAULT_END = "2026-06-30"


# Internal Thread-safe Caching Store
_DATASET_LOCK = threading.Lock()
_DATASET: Optional[Dict[str, Any]] = None
_PRELOAD_THREAD: Optional[threading.Thread] = None


def _load_raw_data() -> Dict[str, Any]:
    """Loads all warehouse tables from Aiven PostgreSQL or local CSV fallback."""
    db_url = os.environ.get("DATABASE_URL")
    so_raw = None
    prods, stores, guardrails, si_raw, au_raw = None, None, None, None, None

    if db_url and psycopg2:
        try:
            if db_url.startswith("postgres://"):
                db_url = "postgresql://" + db_url[len("postgres://"):]
            conn = psycopg2.connect(db_url)
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='fact_sell_out';")
                if not cur.fetchone():
                    cur.execute("SELECT 1 FROM pg_database WHERE datname='marvel_cpg';")
                    if cur.fetchone():
                        conn.close()
                        p = urllib.parse.urlsplit(db_url)
                        marvel_url = urllib.parse.urlunsplit(p._replace(path="/marvel_cpg"))
                        conn = psycopg2.connect(marvel_url)

            print("[AIVEN] Connected to Aiven PostgreSQL. Loading platform dimensions and facts...")
            prods = pd.read_sql("SELECT sku_id, product_name, category, brand, is_msl, unit_cost, list_price, consumer_price FROM dim_products;", conn)
            for col in ["unit_cost", "list_price", "consumer_price"]:
                prods[col] = prods[col].astype(float)
            prods["category"] = prods["category"].astype("category")
            prods["brand"] = prods["brand"].astype("category")

            stores = pd.read_sql("SELECT store_id, store_name, channel, region, state, segment_tier, city FROM dim_stores;", conn)
            rng = np.random.default_rng(42)
            stores["rep_id"] = rng.choice(REPS, len(stores))
            for col in ["channel", "region", "state", "segment_tier", "city", "rep_id"]:
                stores[col] = stores[col].astype("category")

            guardrails = pd.read_sql("SELECT channel, category, max_discount_pct, min_target_margin, required_exec_tradeoff FROM dim_guardrails;", conn)
            guardrails["max_discount_pct"] = guardrails["max_discount_pct"].astype(float)
            guardrails["min_target_margin"] = guardrails["min_target_margin"].astype(float)

            si_raw = pd.read_sql("SELECT date, store_id, sku_id, units_sold, gross_sales, discount_pct, net_sales FROM fact_sell_in;", conn)
            si_raw["date"] = pd.to_datetime(si_raw["date"])
            si_raw["week"] = si_raw["date"] - pd.to_timedelta(si_raw["date"].dt.dayofweek, unit="D")
            for col in ["gross_sales", "discount_pct", "net_sales"]:
                si_raw[col] = si_raw[col].astype("float32")
            si_raw["units_sold"] = si_raw["units_sold"].astype("int32")
            si_raw["store_id"] = si_raw["store_id"].astype("category")
            si_raw["sku_id"] = si_raw["sku_id"].astype("category")

            au_raw = pd.read_sql("SELECT date, store_id, sku_id, planogram_compliance, shelf_share_pct, secondary_display FROM fact_execution_audits;", conn)
            au_raw["date"] = pd.to_datetime(au_raw["date"])
            au_raw["shelf_share_pct"] = au_raw["shelf_share_pct"].astype("float32")
            au_raw["store_id"] = au_raw["store_id"].astype("category")
            au_raw["sku_id"] = au_raw["sku_id"].astype("category")

            # Stream fact_sell_out via server-side cursor to stay under memory thresholds
            store_categories = stores["store_id"].unique().tolist()
            sku_categories = prods["sku_id"].unique().tolist()
            store_to_code = {s: i for i, s in enumerate(store_categories)}
            sku_to_code = {s: i for i, s in enumerate(sku_categories)}

            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM fact_sell_out;")
                total_rows = cur.fetchone()[0]

            dates = np.empty(total_rows, dtype="datetime64[D]")
            store_codes = np.empty(total_rows, dtype=np.uint8)
            sku_codes = np.empty(total_rows, dtype=np.uint8)
            units = np.empty(total_rows, dtype=np.int16)
            inv = np.empty(total_rows, dtype=np.int16)
            oos = np.empty(total_rows, dtype=bool)
            price = np.empty(total_rows, dtype=np.float32)

            named_cur = conn.cursor("platform_sell_out_cursor")
            named_cur.itersize = 100000
            named_cur.execute("SELECT date, store_id, sku_id, units_sold_daily, inventory_level, is_out_of_stock, consumer_price_actual FROM fact_sell_out;")

            offset = 0
            batch_size = 100000
            while True:
                rows = named_cur.fetchmany(batch_size)
                if not rows:
                    break
                n = len(rows)
                end = offset + n
                r_date, r_store, r_sku, r_units, r_inv, r_oos, r_price = zip(*rows)
                dates[offset:end] = np.array(r_date, dtype="datetime64[D]")
                store_codes[offset:end] = [store_to_code.get(s, 0) for s in r_store]
                sku_codes[offset:end] = [sku_to_code.get(s, 0) for s in r_sku]
                units[offset:end] = r_units
                inv[offset:end] = r_inv
                oos[offset:end] = r_oos
                price[offset:end] = r_price
                offset = end

            named_cur.close()
            conn.close()

            store_cat = pd.Categorical.from_codes(store_codes, categories=store_categories)
            sku_cat = pd.Categorical.from_codes(sku_codes, categories=sku_categories)
            del store_codes, sku_codes

            so_raw = pd.DataFrame({
                "date": pd.to_datetime(dates),
                "store_id": store_cat,
                "sku_id": sku_cat,
                "units_sold_daily": units,
                "inventory_level": inv,
                "is_out_of_stock": oos,
                "consumer_price_actual": price,
            })
            del dates, units, inv, oos, price, store_cat, sku_cat
            so_raw["week"] = so_raw["date"] - pd.to_timedelta(so_raw["date"].dt.dayofweek, unit="D")
            gc.collect()

        except Exception as e:
            warnings.warn(f"[AIVEN] Database query encountered an issue: {e}. Falling back to CSV directory...")
            so_raw = None

    if so_raw is None:
        if DATA_DIR is None:
            raise RuntimeError("DATABASE_URL unavailable and local CSV data directory not found.")
        print(f"[LOCAL] Loading datasets from CSV directory: {DATA_DIR}")
        prods = pd.read_csv(DATA_DIR / "dim_products.csv")
        stores = pd.read_csv(DATA_DIR / "dim_stores.csv")
        guardrails = pd.read_csv(DATA_DIR / "dim_guardrails.csv")

        for col in ["unit_cost", "list_price", "consumer_price"]:
            prods[col] = prods[col].astype(float)
        prods["category"] = prods["category"].astype("category")
        prods["brand"] = prods["brand"].astype("category")

        rng = np.random.default_rng(42)
        stores["rep_id"] = rng.choice(REPS, len(stores))
        for col in ["channel", "region", "state", "segment_tier", "city", "rep_id"]:
            stores[col] = stores[col].astype("category")

        guardrails["max_discount_pct"] = guardrails["max_discount_pct"].astype(float)
        guardrails["min_target_margin"] = guardrails["min_target_margin"].astype(float)

        au_raw = pd.read_csv(DATA_DIR / "fact_execution_audits.csv")
        au_raw["date"] = pd.to_datetime(au_raw["date"])
        au_raw["shelf_share_pct"] = au_raw["shelf_share_pct"].astype("float32")
        au_raw["store_id"] = au_raw["store_id"].astype("category")
        au_raw["sku_id"] = au_raw["sku_id"].astype("category")

        si_raw = pd.read_csv(DATA_DIR / "fact_sell_in.csv")
        si_raw["date"] = pd.to_datetime(si_raw["date"])
        si_raw["week"] = si_raw["date"] - pd.to_timedelta(si_raw["date"].dt.dayofweek, unit="D")
        for col in ["gross_sales", "discount_pct", "net_sales"]:
            si_raw[col] = si_raw[col].astype("float32")
        si_raw["units_sold"] = si_raw["units_sold"].astype("int32")
        si_raw["store_id"] = si_raw["store_id"].astype("category")
        si_raw["sku_id"] = si_raw["sku_id"].astype("category")

        so_raw = pd.read_csv(DATA_DIR / "fact_sell_out.csv", low_memory=False)
        so_raw["units_sold_daily"] = so_raw["units_sold_daily"].astype("int16")
        so_raw["inventory_level"] = so_raw["inventory_level"].astype("int16")
        so_raw["is_out_of_stock"] = so_raw["is_out_of_stock"].astype("bool")
        so_raw["consumer_price_actual"] = so_raw["consumer_price_actual"].astype("float32")
        so_raw["date"] = pd.to_datetime(so_raw["date"])
        so_raw["store_id"] = so_raw["store_id"].astype("category")
        so_raw["sku_id"] = so_raw["sku_id"].astype("category")
        so_raw["week"] = so_raw["date"] - pd.to_timedelta(so_raw["date"].dt.dayofweek, unit="D")
        gc.collect()

    channels = sorted(stores["channel"].unique().tolist())
    regions = sorted(stores["region"].unique().tolist())
    sku_choices = {row.sku_id: f"{row.product_name} ({row.category})" for _, row in prods.iterrows()}
    rep_choices = {
        rep_id: f"{prof['name']} — {prof['region']} ({rep_id})"
        for rep_id, prof in REP_PROFILES.items()
    }
    min_date = str(so_raw["date"].min().date())
    max_date = str(so_raw["date"].max().date())

    return {
        "prods": prods,
        "stores": stores,
        "guardrails": guardrails,
        "so_raw": so_raw,
        "si_raw": si_raw,
        "au_raw": au_raw,
        "channels": channels,
        "regions": regions,
        "sku_choices": sku_choices,
        "rep_choices": rep_choices,
        "min_date": min_date,
        "max_date": max_date,
    }


def get_dataset() -> Dict[str, Any]:
    """Returns singleton platform dataset, ensuring thread-safe one-time loading."""
    global _DATASET
    if _DATASET is not None:
        return _DATASET

    with _DATASET_LOCK:
        if _DATASET is None:
            _DATASET = _load_raw_data()
    return _DATASET


def start_background_preload() -> None:
    """Spawns non-blocking worker thread to preload data so port binds immediately."""
    global _PRELOAD_THREAD
    if _DATASET is not None or _PRELOAD_THREAD is not None:
        return
    _PRELOAD_THREAD = threading.Thread(target=get_dataset, daemon=True, name="MarvelDataPreloader")
    _PRELOAD_THREAD.start()


def scope_dataset(
    df_raw: pd.DataFrame,
    col: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    channels: List[str],
    regions: List[str],
    skus: List[str],
    stores: pd.DataFrame,
    prods: pd.DataFrame,
    active_role: str,
    active_rep: Optional[str] = None
) -> pd.DataFrame:
    """High-performance vectorized filter and metadata join for active scope."""
    m_stores = stores["channel"].isin(channels) & stores["region"].isin(regions)
    if active_role == "Field Rep" and active_rep:
        m_stores &= (stores["rep_id"] == active_rep)
    valid_store_ids = set(stores.loc[m_stores, "store_id"])

    m = (
        df_raw[col].between(start_date, end_date)
        & df_raw["store_id"].isin(valid_store_ids)
        & df_raw["sku_id"].isin(skus)
    )
    sub = df_raw[m]
    store_meta = ["store_id", "store_name", "channel", "region", "state", "segment_tier", "city", "rep_id"]
    prod_meta = ["sku_id", "product_name", "category", "brand", "is_msl", "unit_cost", "list_price"]
    return sub.merge(stores[store_meta], on="store_id").merge(prods[prod_meta], on="sku_id")
