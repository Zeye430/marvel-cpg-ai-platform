"""Clearly labelled SYNTHETIC V3-shaped source files for demos and tests.

Generates the eight source CSVs with the shared contract's columns and
business rules (inventory identity on consecutive reported days, linked
restocks, store-level missing report days, Monday invoices, normalized shelf
share). Every store/product name says SYNTHETIC. Nothing here is client data,
and metrics computed from it are never evidence about the real business.
"""

import csv
from datetime import date, timedelta
from pathlib import Path

import numpy as np

CHANNELS = ("Modern Trade", "Traditional Trade", "Distributor")
TIERS = ("Tier 1", "Tier 2", "Tier 3")
REGIONS = ("Northeast", "Southeast", "Midwest", "West")
CATEGORIES = ("Surface Cleaners", "Dishwashing", "Laundry")


def _status(delay, ordered, delivered):
    label = "Early" if delay < 0 else ("On Time" if delay == 0 else "Late")
    return label + (" / Short Ship" if delivered < ordered else "")


def generate(source_dir, *, start=date(2025, 1, 1), end=date(2026, 6, 30), stores_per_segment=2,
             skus=8, seed=650):
    """Write the eight CSVs into ``source_dir``; returns row counts."""
    rng = np.random.default_rng(seed)
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    stores = []
    for c, channel in enumerate(CHANNELS):
        for t, tier in enumerate(TIERS):
            for k in range(stores_per_segment):
                number = len(stores) + 1
                stores.append({"store_id": f"SYN-S{number:03d}", "store_name": f"SYNTHETIC Store {number:03d}",
                               "channel": channel, "region": REGIONS[number % len(REGIONS)],
                               "state": ["NY", "GA", "IL", "CA"][number % 4], "segment_tier": tier,
                               "city": f"Synthetic City {number % 5}"})
    products = []
    for i in range(skus):
        price = round(float(rng.uniform(4, 15)), 2)
        products.append({"sku_id": f"SYN-P{i + 1:03d}", "product_name": f"SYNTHETIC Product {i + 1:03d}",
                         "category": CATEGORIES[i % 3], "brand": "Synthetic Brand", "is_msl": i % 3 == 0,
                         "unit_cost": round(price * 0.55, 2), "list_price": price,
                         "consumer_price": round(price * 1.35, 2)})
    guardrails = [{"channel": ch, "category": cat, "max_discount_pct": 20, "min_target_margin": 18,
                   "required_exec_tradeoff": "Secondary display"} for ch in CHANNELS for cat in CATEGORIES]
    promotions = [{"promo_id": "SYN-PROMO-1", "promo_name": "SYNTHETIC Campaign", "promo_type": "TPR",
                   "category": "Laundry", "channel": "Modern Trade", "start_date": "2025-11-01",
                   "end_date": "2025-11-30", "discount_pct": 10, "min_volume_units": 5}]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    missing = {s["store_id"]: set(np.flatnonzero(rng.random(len(days)) < 0.03).tolist()) for s in stores}
    sell_out, deliveries, sell_in, audits = [], [], [], []
    delivery_number = 0
    for s_index, store in enumerate(stores):
        store_scale = 0.6 + 0.8 * rng.random()
        for product in products:
            demand = (1.0 + 4.0 * rng.random()) * store_scale
            reliability = 0.80 + 0.20 * rng.random()          # chance a weekly order is placed
            inventory = int(rng.integers(10, 40))
            arrivals = {}
            next_order = int(rng.integers(0, 7))
            for d_index, day in enumerate(days):
                if d_index == next_order:
                    next_order += 7
                    if rng.random() < reliability:
                        quantity = max(1, int(demand * rng.uniform(6.5, 11)))
                        delivered = quantity if rng.random() > 0.15 else max(1, int(quantity * rng.uniform(0.4, 0.9)))
                        delay = int(rng.choice([-1, 0, 0, 0, 1, 2, 4]))
                        scheduled = day + timedelta(days=3)
                        actual = scheduled + timedelta(days=delay)
                        delivery_number += 1
                        delivery_id = f"SYN-D{delivery_number:07d}"
                        deliveries.append({"delivery_id": delivery_id, "store_id": store["store_id"],
                                           "sku_id": product["sku_id"], "order_date": day.isoformat(),
                                           "scheduled_delivery": scheduled.isoformat(),
                                           "actual_delivery": actual.isoformat(), "delay_days": delay,
                                           "delivery_status": _status(delay, quantity, delivered),
                                           "units_ordered": quantity, "units_delivered": delivered,
                                           "fill_rate_pct": round(100.0 * delivered / quantity, 4)})
                        arrivals.setdefault(actual, []).append((delivery_id, delivered))
                received = arrivals.pop(day, [])
                restock = sum(units for _, units in received)
                inventory += restock
                sold = int(min(inventory, rng.poisson(demand)))
                inventory -= sold
                if d_index in missing[store["store_id"]]:
                    continue
                # One reported restock per day keeps the restock-to-delivery link exact.
                linked = received[0] if len(received) == 1 else None
                if len(received) > 1:
                    # Merge simultaneous arrivals into the first delivery's units is not allowed;
                    # report the day but leave both deliveries unlinked (no restock column).
                    restock_reported, delivery_ref = 0, ""
                else:
                    restock_reported = restock
                    delivery_ref = linked[0] if linked else ""
                sell_out.append({"date": day.isoformat(), "store_id": store["store_id"], "sku_id": product["sku_id"],
                                 "units_sold_daily": sold, "inventory_level": inventory,
                                 "is_out_of_stock": inventory == 0, "consumer_price_actual": product["consumer_price"],
                                 "promo_id": "", "promo_discount_pct": 0, "restock_units": restock_reported,
                                 "restock_delivery_id": delivery_ref, "_true_restock": restock})
                if day.isoweekday() == 1 and rng.random() < 0.5:
                    units = int(rng.integers(5, 40))
                    gross = round(units * product["list_price"], 2)
                    sell_in.append({"transaction_id": f"SYN-T{len(sell_in) + 1:07d}", "date": day.isoformat(),
                                    "store_id": store["store_id"], "sku_id": product["sku_id"], "units_sold": units,
                                    "gross_sales": gross, "discount_pct": 5, "extra_discount_pct": 0,
                                    "net_sales": round(gross * 0.95, 2), "promo_id": "", "guardrail_violation": False})
        for d_index in range(s_index % 14, len(days), 14):
            day = days[d_index]
            for category in CATEGORIES:
                members = [p for p in products if p["category"] == category]
                if not members:
                    continue
                weights = rng.random(len(members)) + 0.1
                shares = np.round(100 * weights / weights.sum(), 2)
                shares[-1] = round(100 - float(shares[:-1].sum()), 2)
                for product, share in zip(members, shares):
                    audits.append({"audit_id": f"SYN-A{len(audits) + 1:07d}", "date": day.isoformat(),
                                   "store_id": store["store_id"], "sku_id": product["sku_id"],
                                   "planogram_compliance": bool(rng.random() < 0.8), "shelf_share_pct": float(share),
                                   "secondary_display": bool(rng.random() < 0.3)})
    _fix_inventory_identity(sell_out)
    files = {"dim_stores": stores, "dim_products": products, "dim_guardrails": guardrails,
             "dim_promotions": promotions, "fact_sell_in": sell_in, "fact_deliveries": deliveries,
             "fact_execution_audits": audits, "fact_sell_out": sell_out}
    from data_platform.pipeline import BuildConfig
    import json
    contract = json.loads(BuildConfig().contract_path.read_text())
    for source in contract["sources"]:
        names = [c["name"] for c in source["columns"]]
        with (source_dir / source["filename"]).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(files[source["name"]])
    return {name: len(rows) for name, rows in files.items()}


def _fix_inventory_identity(rows):
    """Consecutive reported days must satisfy inv = prev - sold + reported restock.

    Days with several simultaneous arrivals cannot link one restock to one
    delivery, so the whole store-day report is withheld (a store-level missing
    report day, which the shared contract treats as Unknown, never zero).
    """
    while True:
        broken, last = set(), {}
        for row in rows:
            key = (row["store_id"], row["sku_id"])
            previous = last.get(key)
            day = date.fromisoformat(row["date"])
            if previous and date.fromisoformat(previous["date"]) == day - timedelta(days=1):
                expected = previous["inventory_level"] - row["units_sold_daily"] + row["restock_units"]
                if expected != row["inventory_level"]:
                    broken.add((row["store_id"], row["date"]))
            last[key] = row
        if not broken:
            return
        rows[:] = [r for r in rows if (r["store_id"], r["date"]) not in broken]
