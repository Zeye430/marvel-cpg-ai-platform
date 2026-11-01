"""Business-priority display policy (proposed, versioned): a projection over published data.

Computed at read time from the published predictions (drivers evidence), the scoped
negotiation context (price and latest shelf audit) and the stored visit ranking. It
does not change stored ranks, scores, thresholds or model outputs, and it is not an
approved business rule: the weights, cut-offs and price basis below are proposals.

Sales at risk is a seven-day *scenario*, not a forecast of lost revenue. Demand
continues at the prior 28-day in-stock daily rate; current reported inventory
covers it first; the units on the known schedule for the next 7 days are assumed
to arrive together on the next scheduled receipt day (not guaranteed). Units
short = demand not covered before that receipt + demand not covered after it,
valued at the chosen price basis. Receipts already delivered are inside the
reported inventory and are not added again. No shortfall is $0 whatever the price;
otherwise any missing input leaves that SKU unestimated; it never becomes zero.
"""

import json
from datetime import date

POLICY_VERSION = "business-priority.v2-proposed"
WINDOW_DAYS = 7
WEIGHTS = {"impact": 0.8, "shelf": 0.1, "msl": 0.1}          # proposal, not an Owner decision
AUDIT_MAX_AGE_DAYS = 28                                     # proposal
PRICE_BASIS = "reported_net_trade_28d"                      # Owner-preferred; client confirmation pending
PRICE_BASES = {"reported_net_trade_28d": ("reported_net_trade_price_28d", "Reported net trade price, past 28 days"),
               "reference_list_price": ("reference_list_price", "Reference list price (static)")}
PRICE_RATIO_BOUNDS = (0.5, 1.5)        # net trade / list price outside this band suggests a unit (e.g. case) mismatch
CONFIDENCE_MEDIUM_SHARE = 0.5          # proposal
SHELF_CONCERN = 50.0                   # proposal: shelf risk at or above this is named in the risk category
URGENCY_LABELS = {3: "Urgent", 2: "High", 1: "Monitor"}
URGENCY_MARKS = {3: "▲▲", 2: "▲", 1: "–"}
SORT_KEYS = ("rank", "score", "sales_at_risk", "shelf_risk", "msl_risk", "urgency")


def evidence(drivers):
    return {e.get("feature"): e.get("value") for e in drivers.get("evidence", [])}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None              # NaN is unknown


def price_for(context, basis=PRICE_BASIS):
    """(price, None) or (None, reason). Checks presence, sign and a list-price unit ratio."""
    if context is None:
        return None, "No price context for this SKU"
    column, _ = PRICE_BASES[basis]
    price = _number(context.get(column))
    if price is None:
        return None, ("No reported trade price in the past 28 days" if basis == "reported_net_trade_28d"
                      else "No reference list price")
    if price <= 0:
        return None, "Price is not positive"
    reference = _number(context.get("reference_list_price"))
    if basis != "reference_list_price" and reference:
        low, high = PRICE_RATIO_BOUNDS
        if not low <= price / reference <= high:
            return None, "Price unit check failed (trade price not comparable with the unit list price)"
    return price, None


def shortfall_units(inventory, velocity, scheduled, arrival_day):
    """Units of demand not covered in the 7-day window; None when any needed input is unknown.

    ``arrival_day`` 1..7 = the window day the scheduled units become available (1 = first day).
    """
    if inventory is None or velocity is None or scheduled is None or min(inventory, velocity, scheduled) < 0:
        return None
    if scheduled == 0:
        gap = WINDOW_DAYS                         # nothing arrives inside the window
    elif arrival_day is None:
        return None
    else:
        gap = int(min(max(arrival_day - 1, 0), WINDOW_DAYS))
    before = max(0.0, velocity * gap - inventory)
    left = max(0.0, inventory - velocity * gap)
    after = max(0.0, velocity * (WINDOW_DAYS - gap) - left - (scheduled if gap < WINDOW_DAYS else 0.0))
    return before + after


def scenario_path(inventory, velocity, scheduled, arrival_day):
    """Day-by-day form of ``shortfall_units`` for charts: the same scenario, never a model forecast.

    Day k (1..7): stock available at the start of the day (+ the scheduled units on the receipt
    day), units sold = min(available, daily rate), units short = rate - sold. The sum of ``short``
    equals ``shortfall_units`` for the same inputs. None when any needed input is unknown.
    """
    if shortfall_units(inventory, velocity, scheduled, arrival_day) is None:
        return None
    receipt_day = None if scheduled == 0 else (arrival_day if 1 <= arrival_day <= WINDOW_DAYS else None)
    if scheduled and arrival_day is not None and arrival_day < 1:
        receipt_day = 1
    stock, path = inventory, []
    for day in range(1, WINDOW_DAYS + 1):
        receipt = scheduled if day == receipt_day else 0.0
        available = stock + receipt
        sold = min(available, velocity)
        short = velocity - sold
        stock = available - sold
        path.append({"day": day, "receipt": receipt, "demand": velocity, "sold": sold, "short": short,
                     "stock_end": stock})
    return path


def sku_estimate(prediction, context, scoring_date, basis=PRICE_BASIS):
    """Seven-day scenario for one store-SKU; every unknown input is reported, never zero-filled."""
    drivers = json.loads(prediction["drivers_json"]) if isinstance(prediction.get("drivers_json"), str) \
        else (prediction.get("drivers_json") or {})
    ev = evidence(drivers)
    inventory = _number(ev.get("inventory_observed"))
    velocity = _number(ev.get("in_stock_velocity_28d"))
    scheduled = _number(ev.get("scheduled_units_next7d"))
    reasons = []
    if inventory is None or inventory < 0:
        reasons.append("No current inventory report")
    if velocity is None or velocity < 0:
        reasons.append("No prior in-stock sales rate")
    if scheduled is None or scheduled < 0:
        reasons.append("Scheduled receipts unknown")
    if context is not None and str(context.get("as_of_date")) != str(scoring_date):
        context = None
        reasons.append("Price context is not for the scoring date")
    elif context is not None and context.get("last_invoice_date") is not None \
            and str(context["last_invoice_date"]) > str(scoring_date):
        context = None
        reasons.append("Price context contains invoices after the scoring date")
    price, price_reason = price_for(context, basis)
    if price_reason and not any(r.startswith("Price context") for r in reasons):
        reasons.append(price_reason)
    arrival = _number(ev.get("days_until_next_scheduled_receipt"))
    if scheduled is not None and scheduled > 0 and arrival is None:
        reasons.append("Scheduled receipt date unknown")
    units = shortfall_units(inventory, velocity, scheduled, arrival)
    value = None if units is None else (0.0 if units == 0 else (None if price is None else units * price))
    return {"sku_id": prediction["sku_id"], "units_short": units, "price": price, "sales_at_risk": value,
            "estimable": value is not None, "reasons": reasons, "inventory": inventory, "velocity": velocity,
            "scheduled": scheduled, "oos_now": ev.get("oos_observed"), "is_msl": ev.get("is_msl"),
            "days_until_next_receipt": _number(ev.get("days_until_next_scheduled_receipt")),
            "probability": _number(prediction.get("probability")),
            "is_predicted_oos": bool(prediction.get("is_predicted_oos"))}


def audit_state(context, scoring_date):
    """Latest audit for one store-SKU: fresh/compliant flags, or why it cannot be used."""
    if context is None or context.get("last_audit_date") is None:
        return {"status": "No audit", "date": None, "compliant": None, "secondary_display": None}
    audit = context["last_audit_date"]
    audit = audit if isinstance(audit, date) else date.fromisoformat(str(audit)[:10])
    scoring = scoring_date if isinstance(scoring_date, date) else date.fromisoformat(str(scoring_date))
    if audit > scoring:
        return {"status": "Future audit excluded", "date": audit.isoformat(), "compliant": None,
                "secondary_display": None}
    age = (scoring - audit).days
    fresh = age <= AUDIT_MAX_AGE_DAYS
    return {"status": "Fresh" if fresh else "Expired", "date": audit.isoformat(), "age_days": age,
            "compliant": context.get("planogram_compliance_latest") if fresh else None,
            "secondary_display": context.get("secondary_display_latest"),
            "shelf_share_pct": context.get("shelf_share_pct_latest")}


def store_components(predictions, contexts, scoring_date, msl_gap_count, prevention_alert_count, basis=PRICE_BASIS):
    """Per-store components from all of its published SKU predictions.

    ``contexts`` maps sku_id -> negotiation-context row (or None when unavailable).
    """
    seen, duplicates = set(), set()
    for p in predictions:
        (duplicates if p["sku_id"] in seen else seen).add(p["sku_id"])
    skus, audits = [], {}
    for p in sorted(predictions, key=lambda r: r["sku_id"]):
        if p["sku_id"] in duplicates:
            continue
        context = contexts.get(p["sku_id"]) if contexts is not None else None
        estimate = sku_estimate(p, context, scoring_date, basis)
        if contexts is None:
            estimate["reasons"].append("Price and audit context unavailable")
        skus.append(estimate)
        audits[p["sku_id"]] = audit_state(context, scoring_date)
    for sku in sorted(duplicates):
        skus.append({"sku_id": sku, "units_short": None, "price": None, "sales_at_risk": None, "estimable": False,
                     "reasons": ["Duplicate prediction rows; not counted"], "inventory": None, "velocity": None,
                     "scheduled": None, "oos_now": None, "is_msl": None, "days_until_next_receipt": None,
                     "probability": None, "is_predicted_oos": False})
    estimable = [s for s in skus if s["estimable"]]
    if not skus or not estimable:
        impact = {"value": None, "status": "Unavailable", "estimated_skus": 0, "sku_count": len(skus)}
    else:
        impact = {"value": sum(s["sales_at_risk"] for s in estimable),
                  "status": "Complete" if len(estimable) == len(skus) else "Partial",
                  "estimated_skus": len(estimable), "sku_count": len(skus)}
    fresh = [a for a in audits.values() if a["status"] == "Fresh" and a["compliant"] is not None]
    shelf = None if not fresh else 100.0 * sum(1 for a in fresh if a["compliant"] is False) / len(fresh)
    msl_skus = [s for s in skus if s["is_msl"] is True]
    assessable = [s for s in msl_skus if s["oos_now"] is not None and s["probability"] is not None]
    if any(s["is_msl"] is None for s in skus) and not assessable:
        msl = None
    elif not msl_skus:
        msl = 0.0                       # no MSL SKUs listed for the store: no MSL exposure, not missing data
    elif not assessable:
        msl = None
    else:
        msl = 100.0 * sum(1.0 if s["oos_now"] is True else s["probability"] for s in assessable) / len(assessable)
    if msl_gap_count:
        urgency = 3
    elif prevention_alert_count or any(s["oos_now"] is True for s in skus):
        urgency = 2
    elif prevention_alert_count is None and not skus:
        urgency = None
    else:
        urgency = 1
    return {"sales_at_risk": impact, "shelf_risk": shelf, "msl_risk": msl, "urgency": urgency,
            "audit_fresh_skus": len(fresh), "skus": skus, "audits": audits}


def impact_percentiles(values):
    """Within-scope impact component in [0, 1]: 0 for no shortfall, else the mid-rank among
    positive values (the largest gets 1). Unavailable stays None. Never global positions."""
    positives = sorted(v for v in values.values() if v is not None and v > 0)
    out = {}
    for key, v in values.items():
        if v is None:
            out[key] = None
        elif v <= 0:
            out[key] = 0.0
        else:
            below = sum(1 for x in positives if x < v)
            equal = sum(1 for x in positives if x == v)
            out[key] = (below + (equal + 1) / 2) / len(positives)
    return out


def priority_score(impact_pct, shelf, msl):
    """0-100 weighted score; None when the primary (impact) component is unavailable. A missing
    secondary component is excluded and the remaining weights are rescaled (reported as such)."""
    if impact_pct is None:
        return None, []
    parts = {"impact": impact_pct, "shelf": None if shelf is None else shelf / 100,
             "msl": None if msl is None else msl / 100}
    used = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] for k in used)
    return 100.0 * sum(WEIGHTS[k] * v for k, v in used.items()) / total, sorted(set(parts) - set(used))


def risk_category(row):
    """Concise label; current state, predicted risk and shelf concern stay distinct."""
    parts = []
    if row.get("msl_gap_count"):
        parts.append("Current MSL stockout")
    elif row.get("current_oos_skus"):
        parts.append("Current stockout")
    if row.get("prevention_alert_count"):
        parts.append("Future stockout risk")
    if (row.get("shelf_risk") or 0) >= SHELF_CONCERN:
        parts.append("Shelf execution")
    if not parts:
        return "Verify data" if row.get("data_check_reasons") else "Routine"
    return " + ".join(parts)


def assign_overall_ranks(rows):
    """Stable overall rank within the given (authorized) set: score desc, then the stored visit
    order, then store id; stores without a score follow, by stored order then store id."""
    def key(r):
        stored = r["display_rank"] if r["display_rank"] is not None else float("inf")
        return (r["priority_score"] is None, -(r["priority_score"] or 0.0), stored, r["store_id"])
    for i, r in enumerate(sorted(rows, key=key), start=1):
        r["overall_rank"] = i
    return rows


def sort_rows(rows, sort_key="score", descending=True):
    """Sort the whole filtered set. Missing values are last in both directions; ties keep the
    overall rank, then store id. Sorting never re-ranks."""
    if sort_key not in SORT_KEYS:
        raise ValueError(f"Unsupported sort: {sort_key}")
    if sort_key == "rank":
        return sorted(rows, key=lambda r: ((-1 if descending else 1) * r["overall_rank"], r["store_id"]))
    getter = {"score": lambda r: r["priority_score"], "sales_at_risk": lambda r: r["sales_at_risk_usd"],
              "shelf_risk": lambda r: r["shelf_risk"], "msl_risk": lambda r: r["msl_risk"],
              "urgency": lambda r: r["urgency"]}[sort_key]
    present = [r for r in rows if getter(r) is not None]
    missing = [r for r in rows if getter(r) is None]
    sign = -1 if descending else 1
    present.sort(key=lambda r: (sign * getter(r), r["overall_rank"], r["store_id"]))
    missing.sort(key=lambda r: (r["overall_rank"], r["store_id"]))
    return present + missing


def confidence(components):
    """Detail-only estimate confidence from input coverage (proposed thresholds)."""
    impact = components["sales_at_risk"]
    if impact["status"] == "Unavailable":
        return "Unavailable"
    share = impact["estimated_skus"] / impact["sku_count"]
    if share == 1 and components["audit_fresh_skus"]:
        return "High"
    return "Medium" if share >= CONFIDENCE_MEDIUM_SHARE else "Low"
