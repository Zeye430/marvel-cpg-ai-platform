"""Module 1 data service over the shared business publication (Reported or Estimated view).

Trusted server code only. One complete, account-scoped cache per verified
principal and data view (data_platform.scoped_cache) is queried with fixed SQL;
filters are bound parameters, never SQL text. KPIs aggregate the shared numerator
and denominator components (docs/data/v3-module-1-integration.md, section 4):

- all-SKU OOS/coverage/MSL use ``store_day`` components; a SKU subset recomputes
  the same components from ``store_sku_daily`` (all-store-day denominators are
  never reused for a filtered SKU set);
- Sell-in/Sell-out gap uses one commercial period family (biweek, event-date
  alignment, complete periods inside the range) and comparable flows only;
- execution uses the ``Individual audited SKU`` scope only (scopes overlap);
- unknown values stay NULL/NaN and zero denominators give None (N/A).

Data views. ``reported`` (the default) reads the reported marts. ``estimated`` is the
explicit opt-in of the integration guide: it reads the separate candidate views
(``estimated_store_sku_daily``, ``estimated_store_day``, ``estimated_commercial_period``)
with ``allow_candidate_estimates=True``. There, an observed value comes first, a short
gap carries the candidate estimate (status ``Estimated``) and the rest stays Unknown.
Both views are exposed to the queries below under the same private names
(``m1_sku_daily`` with ``inv``/``units``/``inv_status``, ``m1_store_day``, ``m1_period``),
so every query is written once and the two views can never be mixed in one answer.
Audits (``execution_visits``) and invoices (``sell_in_events``) are observed in both.

Nothing here reads raw CSVs or legacy tables.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date

import pandas as pd

VIEWS = ("stores", "products", "store_sku_daily", "store_day", "commercial_period",
         "sell_in_events", "execution_visits", "negotiation_context")
ESTIMATED_VIEWS = ("stores", "products", "estimated_store_sku_daily", "estimated_store_day",
                   "estimated_commercial_period", "sell_in_events", "execution_visits", "negotiation_context")
MODES = ("reported", "estimated")
PERIOD_TYPE = "biweek"  # recommended for every channel; families are never mixed
SKU_SCOPE = "Individual audited SKU"
REPORTED = "reported_only"
ABOVE_CEILING = "Above standard + maximum additional (manager review range)"
WITHIN_CEILING = "Within standard + maximum additional"

# The private per-view names every query uses (created once per cache, before it is shared).
_MODE_VIEWS = {
    "reported": (
        "CREATE VIEW m1_sku_daily AS SELECT *, inventory_observed AS inv, units_sold_observed AS units, "
        "inventory_data_status AS inv_status FROM store_sku_daily",
        f"CREATE VIEW m1_store_day AS SELECT * FROM store_day WHERE metric_view = '{REPORTED}'",
        f"CREATE VIEW m1_period AS SELECT * FROM commercial_period WHERE metric_view = '{REPORTED}'"),
    "estimated": (
        "CREATE VIEW m1_sku_daily AS SELECT *, inventory_resolved AS inv, sales_resolved AS units, "
        "inventory_data_status AS inv_status FROM estimated_store_sku_daily",
        "CREATE VIEW m1_store_day AS SELECT * FROM estimated_store_day WHERE metric_view = 'operational_estimate'",
        "CREATE VIEW m1_period AS SELECT * FROM estimated_commercial_period "
        "WHERE metric_view = 'operational_estimate'"),
}

# Per-store component columns that add up across stores (ratios are recomputed from them).
SUM_COLUMNS = ("expected", "known", "observed", "estimated", "oos", "msl_eligible", "msl_assessable",
               "msl_compliant", "msl_unknown", "msl_zero", "msl_unknown_latest", "msl_required_latest",
               "skus_zero", "skus_unknown", "skus_estimated", "skus_latest", "compliant", "audited",
               "allocation_sum", "visits", "gap_units", "sell_out", "sell_in", "net_sales")

# The composite store score: the five KPIs, each turned into a percentile among the stores in view (100 = best
# in view), averaged with equal team-set weights. ``better``: "up" (higher is better), "down" or "zero".
SCORE_MEASURES = (("msl_pct", "MSL compliance", "up"), ("oos_pct", "OOS rate", "down"),
                  ("gap_pct", "Sell-in vs sell-out gap", "zero"), ("alloc_pct", "Shelf allocation", "up"),
                  ("plano_pct", "Planogram compliance", "up"))
SCORE_WEIGHTS = {column: 0.2 for column, _, _ in SCORE_MEASURES}
SCORE_MIN_MEASURES = 3


@dataclass(frozen=True)
class Filters:
    channels: tuple
    regions: tuple
    skus: tuple          # selected SKU ids
    all_skus: bool       # the selection covers every product (all-SKU store_day scope)
    start: date
    end: date

    def params(self):
        return {"channels": list(self.channels), "regions": list(self.regions), "skus": list(self.skus),
                "start": self.start, "end": self.end}


def _int(value):
    return 0 if value is None or pd.isna(value) else int(value)


def _ratio(numerator, denominator, scale=100.0):
    if numerator is None or denominator is None or pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
        return None
    return scale * float(numerator) / float(denominator)


def with_ratios(frame):
    """Rates recomputed from summed components (never an average of rates)."""
    out = frame.copy()
    for name in SUM_COLUMNS:
        if name not in out:
            out[name] = pd.NA
    pairs = {"oos_pct": ("oos", "known"), "coverage_pct": ("known", "expected"),
             "observed_coverage_pct": ("observed", "expected"), "estimated_share_pct": ("estimated", "known"),
             "msl_pct": ("msl_compliant", "msl_assessable"), "plano_pct": ("compliant", "audited"),
             "gap_pct": ("gap_units", "sell_out")}
    for name, (num, den) in pairs.items():
        out[name] = [_ratio(n, d) for n, d in zip(out[num], out[den])]
    out["alloc_pct"] = [_ratio(n, d, scale=1.0) for n, d in zip(out["allocation_sum"], out["visits"])]
    return out


def composite_scores(stores):
    """Percentile of each KPI among the stores in view (ties share a percentile; a missing value is left out),
    then the weighted mean over the measures a store has. Fewer than SCORE_MIN_MEASURES measures, or fewer than
    two stores with a value for a measure: no score for that part. Adds ``p_<column>`` per measure, ``score``
    (0-100, higher = better), ``measures`` (count used) and ``rank`` (1 = best; ties by store id)."""
    out = stores.copy()
    for column, _, better in SCORE_MEASURES:
        values = pd.to_numeric(out[column], errors="coerce").astype(float)
        if better == "zero":
            values = -values.abs()
        elif better == "down":
            values = -values
        values = values.round(1)            # display precision: noise below 0.1 never separates stores
        known = values.notna().sum()
        if known < 2:
            out[f"p_{column}"] = None
            continue
        ranks = values.rank(method="average")
        out[f"p_{column}"] = (ranks - 1) / (known - 1) * 100
    parts = out[[f"p_{c}" for c, _, _ in SCORE_MEASURES]].astype(float)
    weights = pd.Series({f"p_{c}": SCORE_WEIGHTS[c] for c, _, _ in SCORE_MEASURES})
    used = parts.notna()
    total = (parts.fillna(0) * weights).sum(axis=1)
    weight = (used * weights).sum(axis=1)
    out["measures"] = used.sum(axis=1)
    out["score"] = [None if m < SCORE_MIN_MEASURES or w == 0 else t / w
                    for t, w, m in zip(total, weight, out["measures"])]
    order = out.assign(_s=pd.to_numeric(out["score"], errors="coerce")).sort_values(
        ["_s", "store_id"], ascending=[False, True], na_position="last")
    ranks = {sid: (i + 1 if pd.notna(sc) else None) for i, (sid, sc) in enumerate(zip(order["store_id"], order["_s"]))}
    out["rank"] = out["store_id"].map(ranks)
    return out


def aggregate(stores, keys):
    """Store rows summed to ``keys`` (e.g. state, or city + state), with the store count and recomputed rates."""
    if stores is None or not len(stores):
        return pd.DataFrame(columns=list(keys) + ["stores"])
    present = [c for c in SUM_COLUMNS if c in stores]
    grouped = stores.groupby(list(keys), dropna=False)
    out = grouped[present].sum(min_count=1).reset_index()
    out["stores"] = grouped["store_id"].nunique().values
    out["stores_with_msl_zero"] = grouped["msl_zero"].apply(lambda s: int((s.fillna(0) > 0).sum())).values
    if "last_report" in stores:
        out["last_report"] = grouped["last_report"].max().values
    return with_ratios(out)


def streaks(periods, values):
    """Runs of positive / negative gap over consecutive comparable periods.

    ``periods``: every complete period start in the range, in order; ``values``: the gap per period
    (None/NaN = no comparable flow that period). Zero and a missing period both end a run; a run is
    never extended by filling a gap. Returns the run that ends at the last period (``current``: None
    when the last period is zero or missing) and the longest positive and negative runs, each as
    {"direction": "+"/"-", "length": periods, "first": start, "last": start}."""
    def sign(v):
        if v is None or pd.isna(v) or v == 0:
            return 0
        return 1 if v > 0 else -1
    runs, run = [], None
    for period, value in zip(periods, values):
        s = sign(value)
        if run is not None and s == run["s"] and s != 0:
            run["length"] += 1
            run["last"] = period
        else:
            if run is not None and run["s"] != 0:
                runs.append(run)
            run = {"s": s, "length": 1, "first": period, "last": period}
    if run is not None and run["s"] != 0:
        runs.append(run)

    def public(r):
        return None if r is None else {"direction": "+" if r["s"] > 0 else "-", "length": r["length"],
                                       "first": r["first"], "last": r["last"]}
    last = list(periods)[-1] if len(periods) else None
    current = runs[-1] if runs and runs[-1]["last"] == last else None
    positive = max((r for r in runs if r["s"] > 0), key=lambda r: r["length"], default=None)
    negative = max((r for r in runs if r["s"] < 0), key=lambda r: r["length"], default=None)
    return {"current": public(current), "longest_positive": public(positive), "longest_negative": public(negative)}


TREND_STABLE_PTS = 1.0     # a change smaller than this (percentage points) reads as stable
TREND_SCALE = {"msl": 100.0, "oos": 100.0, "plano": 100.0, "alloc": 1.0, "gap": 100.0}


def trend_direction(trend, metric, better):
    """Improving / Worsening / Stable for one store measure: the later half of its periods against the earlier
    half (equal numbers of periods, components summed, never an average of rates). Fewer than four periods
    with a value: None (not enough to tell)."""
    part = trend[(trend["metric"] == metric) & trend["denominator"].notna() & (trend["denominator"] != 0)]
    part = part.sort_values("period")
    if len(part) < 4:
        return None
    half = len(part) // 2
    scale = TREND_SCALE[metric]
    early = _ratio(part["numerator"].iloc[:half].sum(), part["denominator"].iloc[:half].sum(), scale)
    late = _ratio(part["numerator"].iloc[-half:].sum(), part["denominator"].iloc[-half:].sum(), scale)
    if early is None or late is None:
        return None
    change = abs(late) - abs(early) if better == "zero" else late - early
    if abs(change) < TREND_STABLE_PTS:
        verdict = "Stable"
    elif better == "up":
        verdict = "Improving" if change > 0 else "Worsening"
    else:                                   # lower is better, or closer to zero is better
        verdict = "Improving" if change < 0 else "Worsening"
    return {"early": early, "late": late, "change": late - early, "verdict": verdict, "periods": len(part),
            "first": part["period"].iloc[0], "last": part["period"].iloc[-1]}


class Module1Data:
    """Read-only analytics for one principal and one data view; discard on scope or source change."""

    def __init__(self, cache, mode="reported"):
        if mode not in MODES:
            raise ValueError(f"Unknown data view {mode!r}")
        self.cache = cache
        self.mode = mode
        self.estimated = mode == "estimated"
        self.record = cache.record
        self.principal_id = cache.principal_id
        self.lock = threading.Lock()
        self.scope = cache.reader.principal_scope(self.principal_id)
        self.manager = self.scope["roles"] == ["manager"]
        with self.lock:
            for statement in _MODE_VIEWS[mode]:
                cache.con.execute(statement)

    # ------------------------------------------------------------------ plumbing
    @property
    def closed(self):
        return self.cache.closed

    def close(self):
        with self.lock:
            self.cache.close()

    def check(self):
        """Fresh scope/publication check; raises (and closes) on any change."""
        with self.lock:
            self.scope = self.cache.check_access()
            return self.scope

    def _df(self, sql, params=None):
        with self.lock:
            if self.cache.closed:
                raise PermissionError("Module 1 data was closed after an account or source change")
            # DuckDB requires exactly the referenced named parameters.
            used = {k: v for k, v in (params or {}).items() if f"${k}" in sql}
            return self.cache.con.execute(sql, used).df()

    def _one(self, sql, params=None):
        frame = self._df(sql, params)
        return frame.iloc[0].to_dict() if len(frame) else {}

    def query(self, sql, params=None):
        """Fixed application SQL over this principal's scoped copy (never user or model text). Used by
        Module 3, which reads the same (Reported) scoped copy rather than building a second one."""
        return self._df(sql, params)

    _STORES = "(SELECT store_id FROM stores WHERE list_contains($channels, channel) AND list_contains($regions, region))"
    _SKU_ROWS = (f"SELECT * FROM m1_sku_daily WHERE date BETWEEN $start AND $end AND list_contains($skus, sku_id) "
                 f"AND store_id IN {_STORES}")

    # ------------------------------------------------------------------ choices
    def choices(self):
        stores = self._df("SELECT channel, region, segment_tier, count(*) AS n FROM stores GROUP BY ALL ORDER BY ALL")
        products = self._df("SELECT sku_id, product_name, category, is_msl FROM products ORDER BY category, product_name")
        bounds = self._one("SELECT min(date) AS first, max(date) AS last FROM m1_sku_daily")
        store_list = self._df("SELECT store_id, store_name, city, state FROM stores ORDER BY store_name, store_id")
        return {"channels": sorted(stores["channel"].unique().tolist()),
                "regions": sorted(stores["region"].unique().tolist()),
                "tiers": sorted(stores["segment_tier"].dropna().unique().tolist()),
                "categories": sorted(products["category"].dropna().unique().tolist()),
                "store_count": int(stores["n"].sum()),
                "products": {r.sku_id: f"{r.product_name} ({r.category})" for r in products.itertuples()},
                "product_names": {r.sku_id: r.product_name for r in products.itertuples()},
                "product_categories": {r.sku_id: r.category for r in products.itertuples()},
                "msl_products": [r.sku_id for r in products.itertuples() if r.is_msl is True or r.is_msl == 1],
                "stores": {r.store_id: f"{r.store_name} · {r.city}, {r.state}" for r in store_list.itertuples()},
                "first_date": bounds.get("first"), "last_date": bounds.get("last"),
                "as_of": str(self.record.get("as_of"))}

    def estimate_info(self):
        """Estimator identity and status of the Estimated view (None for Reported)."""
        if not self.estimated:
            return None
        row = self._one("""SELECT any_value(estimator_version) AS version,
                                  any_value(estimation_release_status) AS status FROM m1_sku_daily""")
        return {"version": row.get("version"), "status": row.get("status")}

    def store_count(self, f):
        return int(self._one(f"SELECT count(*) AS n FROM stores WHERE store_id IN {self._STORES}", f.params())["n"])

    # ------------------------------------------------------------------ KPI components
    def _day_components(self, f):
        """SQL for one row per store-day with the availability/MSL components of the filter: the shared
        all-SKU ``store_day`` components, or the same components recomputed for a SKU subset."""
        if f.all_skus:
            estimated = "estimated_inventory_sku_count" if self.estimated else "0"
            return f"""SELECT date, store_id, expected_sku_count AS expected, known_inventory_sku_count AS known,
                       observed_inventory_sku_count AS observed, {estimated} AS estimated, oos_sku_count AS oos,
                       msl_eligible_store_count AS msl_eligible, msl_assessable_store_count AS msl_assessable,
                       msl_compliant_store_count AS msl_compliant, msl_unknown_store_count AS msl_unknown
                FROM m1_store_day WHERE date BETWEEN $start AND $end AND store_id IN {self._STORES}"""
        # MSL state per store-day: N/A without an applicable MSL SKU; Noncompliant if any is known at zero;
        # Compliant if all are known positive; otherwise Unknown (not a failure).
        return f"""WITH c AS (
                  SELECT date, store_id, count(*) AS expected, count(inv) AS known,
                         count(*) FILTER (WHERE inv_status = 'Observed') AS observed,
                         count(*) FILTER (WHERE inv_status = 'Estimated') AS estimated,
                         count(*) FILTER (WHERE inv = 0) AS oos,
                         count(*) FILTER (WHERE is_msl) AS r,
                         count(*) FILTER (WHERE is_msl AND inv IS NOT NULL) AS k,
                         count(*) FILTER (WHERE is_msl AND inv = 0) AS u
                  FROM ({self._SKU_ROWS}) GROUP BY date, store_id)
            SELECT date, store_id, expected, known, observed, estimated, oos,
                   CAST(r > 0 AS INTEGER) AS msl_eligible,
                   CAST(r > 0 AND (u > 0 OR k = r) AS INTEGER) AS msl_assessable,
                   CAST(r > 0 AND u = 0 AND k = r AS INTEGER) AS msl_compliant,
                   CAST(r > 0 AND u = 0 AND k < r AS INTEGER) AS msl_unknown
            FROM c"""

    def availability_components(self, f):
        """OOS / coverage / MSL numerator and denominator components for the filter."""
        sql = f"""SELECT sum(expected) AS expected, sum(known) AS known, sum(observed) AS observed,
                         sum(estimated) AS estimated, sum(oos) AS oos, sum(msl_eligible) AS msl_eligible,
                         sum(msl_assessable) AS msl_assessable, sum(msl_compliant) AS msl_compliant,
                         sum(msl_unknown) AS msl_unknown, count(*) AS store_days
                  FROM ({self._day_components(f)})"""
        return {k: (None if pd.isna(v) else int(v)) for k, v in self._one(sql, f.params()).items()}

    # One period family and alignment; only complete calendar periods fully inside the date range.
    _PERIODS = f"""FROM m1_period WHERE period_type = '{PERIOD_TYPE}' AND alignment_basis = 'event_date'
             AND period_is_complete AND period_start >= $start AND period_end <= $end"""

    def flow_components(self, f):
        return self._one(f"""SELECT sum(comparable_gap_units) AS gap_units, sum(comparable_sell_out_units) AS sell_out,
                   sum(comparable_sell_in_units) AS sell_in, count(*) AS rows,
                   count(*) FILTER (WHERE flow_is_comparable) AS comparable_rows,
                   count(DISTINCT period_start) AS periods, min(period_start) AS first_day, max(period_end) AS last_day,
                   bool_or(NOT invoice_extract_complete_assumed) AS completeness_unconfirmed
            {self._PERIODS} AND list_contains($skus, sku_id) AND store_id IN {self._STORES}""", f.params())

    _AUDIT = f"""FROM execution_visits WHERE scope_label = '{SKU_SCOPE}' AND audit_date BETWEEN $start AND $end
                  AND list_contains($skus, replace(sku_scope_id, 'sku:', ''))"""

    def execution_components(self, f):
        return self._one(f"""WITH v AS (SELECT store_id, audit_date, category, sum(shelf_allocation_pct) AS allocation,
                       sum(audited_row_count) AS audited, sum(compliant_audit_row_count) AS compliant,
                       sum(secondary_display_row_count) AS secondary
                {self._AUDIT} AND store_id IN {self._STORES} GROUP BY ALL)
            SELECT count(*) AS visits, sum(allocation) AS allocation_sum, sum(audited) AS audited,
                   sum(compliant) AS compliant, sum(secondary) AS secondary FROM v""", f.params())

    def kpi_trend(self, f, key):
        """One KPI per period inside the filter, with the same components as the tile (period = week; the gap
        uses its complete biweekly periods). Columns: period, numerator, denominator, value."""
        week = "CAST(date_trunc('week', {}) AS DATE)"
        if key in ("msl", "oos"):
            num, den = (("msl_compliant", "msl_assessable") if key == "msl" else ("oos", "known"))
            sql = f"""SELECT {week.format('date')} AS period, sum({num}) AS numerator, sum({den}) AS denominator
                      FROM ({self._day_components(f)}) GROUP BY 1 ORDER BY 1"""
        elif key == "gap":
            sql = f"""SELECT period_start AS period, sum(comparable_gap_units) AS numerator,
                         sum(comparable_sell_out_units) AS denominator
                  {self._PERIODS} AND list_contains($skus, sku_id) AND store_id IN {self._STORES}
                  GROUP BY 1 ORDER BY 1"""
        elif key in ("shelf", "plano"):
            sql = f"""WITH v AS (SELECT store_id, audit_date, category, sum(shelf_allocation_pct) AS allocation,
                           sum(audited_row_count) AS audited, sum(compliant_audit_row_count) AS compliant
                    {self._AUDIT} AND store_id IN {self._STORES} GROUP BY ALL)
                SELECT {week.format('audit_date')} AS period,
                       {'sum(allocation)' if key == 'shelf' else 'sum(compliant)'} AS numerator,
                       {'count(*)' if key == 'shelf' else 'sum(audited)'} AS denominator
                FROM v GROUP BY 1 ORDER BY 1"""
        else:
            raise ValueError(f"Unknown KPI {key}")
        frame = self._df(sql, f.params())
        scale = 1.0 if key == "shelf" else 100.0
        frame["value"] = [_ratio(n, d, scale=scale) for n, d in zip(frame["numerator"], frame["denominator"])]
        return frame

    def kpis(self, f):
        a = self.availability_components(f)
        flow = self.flow_components(f)
        ex = self.execution_components(f)
        gap = None if flow.get("gap_units") is None or pd.isna(flow.get("gap_units")) else float(flow["gap_units"])
        unconfirmed = flow.get("completeness_unconfirmed")
        return {
            "oos": _ratio(a["oos"], a["known"]), "oos_known": a["known"], "oos_count": a["oos"],
            "coverage": _ratio(a["observed"], a["expected"]), "expected": a["expected"],
            "known_coverage": _ratio(a["known"], a["expected"]),
            "estimated": a["estimated"], "estimated_share": _ratio(a["estimated"], a["known"]),
            "msl": _ratio(a["msl_compliant"], a["msl_assessable"]),
            "msl_coverage": _ratio(a["msl_assessable"], a["msl_eligible"]),
            "msl_unknown": a["msl_unknown"], "msl_eligible": a["msl_eligible"], "store_days": a["store_days"],
            "msl_assessable": a["msl_assessable"], "msl_compliant": a["msl_compliant"],
            "gap_pct": _ratio(flow.get("gap_units"), flow.get("sell_out")), "gap_units": gap,
            "sell_in": None if pd.isna(flow.get("sell_in")) else float(flow["sell_in"]),
            "sell_out": None if pd.isna(flow.get("sell_out")) else float(flow["sell_out"]),
            "gap_periods": _int(flow.get("periods")), "gap_rows": _int(flow.get("rows")),
            "gap_comparable_rows": _int(flow.get("comparable_rows")),
            "gap_completeness_unconfirmed": bool(unconfirmed) if not pd.isna(unconfirmed) else False,
            "gap_first": None if pd.isna(flow.get("first_day")) else pd.Timestamp(flow["first_day"]).date(),
            "gap_last": None if pd.isna(flow.get("last_day")) else pd.Timestamp(flow["last_day"]).date(),
            "allocation": _ratio(ex.get("allocation_sum"), ex.get("visits"), scale=1.0),
            "visits": _int(ex.get("visits")),
            "plano": _ratio(ex.get("compliant"), ex.get("audited")), "audited": _int(ex.get("audited")),
            "plano_compliant": _int(ex.get("compliant")),
        }

    # ------------------------------------------------------------------ store level
    def store_metrics(self, f):
        """One row per store in the filter: availability/MSL components over the range, the stock state on the
        range's latest day, audits, comparable flows and invoiced sales. Unknown stays NULL; rates come from
        :func:`with_ratios`, so they can be summed up to a city, state, region or channel exactly."""
        p = f.params()
        stores = self._df(f"""SELECT store_id, store_name, channel, region, state, city, segment_tier FROM stores
                              WHERE store_id IN {self._STORES} ORDER BY store_id""", p)
        if stores.empty:
            return with_ratios(stores)
        avail = self._df(f"""SELECT store_id, sum(expected) AS expected, sum(known) AS known, sum(observed) AS observed,
                   sum(estimated) AS estimated, sum(oos) AS oos, sum(msl_eligible) AS msl_eligible,
                   sum(msl_assessable) AS msl_assessable, sum(msl_compliant) AS msl_compliant,
                   sum(msl_unknown) AS msl_unknown
            FROM ({self._day_components(f)}) GROUP BY store_id""", p)
        latest = self._df(f"""WITH d AS ({self._SKU_ROWS}), last AS (SELECT max(date) AS date FROM d)
            SELECT store_id, any_value(date) AS latest_date,
                   count(*) FILTER (WHERE is_msl AND inv = 0) AS msl_zero,
                   count(*) FILTER (WHERE is_msl AND inv IS NULL) AS msl_unknown_latest,
                   count(*) FILTER (WHERE is_msl) AS msl_required_latest,
                   count(*) FILTER (WHERE inv = 0) AS skus_zero, count(*) FILTER (WHERE inv IS NULL) AS skus_unknown,
                   count(*) FILTER (WHERE inv_status = 'Estimated') AS skus_estimated, count(*) AS skus_latest
            FROM d JOIN last USING (date) GROUP BY store_id""", p)
        reports = self._df(f"""SELECT store_id, max(last_report_date) AS last_report,
                   max(date) FILTER (WHERE receipt_delivery_count > 0) AS last_delivery
            FROM ({self._SKU_ROWS}) GROUP BY store_id""", p)
        audits = self._df(f"""WITH v AS (SELECT store_id, audit_date, category,
                       sum(CAST(shelf_allocation_pct AS DOUBLE)) AS allocation,
                       sum(audited_row_count) AS audited, sum(compliant_audit_row_count) AS compliant
                {self._AUDIT} AND store_id IN {self._STORES} GROUP BY ALL)
            SELECT store_id, sum(compliant) AS compliant, sum(audited) AS audited, max(audit_date) AS last_audit,
                   sum(allocation) AS allocation_sum, count(*) AS visits
            FROM v GROUP BY store_id""", p)
        flows = self._df(f"""SELECT store_id, sum(comparable_gap_units) AS gap_units,
                   sum(comparable_sell_out_units) AS sell_out, sum(comparable_sell_in_units) AS sell_in,
                   count(DISTINCT period_start) FILTER (WHERE flow_is_comparable) AS gap_periods
            {self._PERIODS} AND list_contains($skus, sku_id) AND store_id IN {self._STORES} GROUP BY store_id""", p)
        sales = self._df(f"""SELECT store_id, sum(CAST(net_sales AS DOUBLE)) AS net_sales FROM sell_in_events
            WHERE date BETWEEN $start AND $end AND list_contains($skus, sku_id) AND store_id IN {self._STORES}
            GROUP BY store_id""", p)
        out = stores
        for part in (avail, latest, reports, audits, flows, sales):
            out = out.merge(part, on="store_id", how="left")
        return with_ratios(out)

    def store_trend(self, f, store_id, weeks=13):
        """One store's KPIs per week over the ``weeks`` weeks ending with the filter's last day (a fixed window,
        so "improving / worsening" always means the same span): MSL, OOS, planogram and shelf allocation per
        week, the gap per complete biweekly period. Long frame: metric, period, numerator, denominator, value."""
        end = pd.Timestamp(f.end)
        start = end - pd.Timedelta(days=7 * weeks - 1)
        g = Filters(f.channels, f.regions, f.skus, f.all_skus, start.date(), end.date())
        p = dict(g.params(), store_id=store_id)
        week = "CAST(date_trunc('week', {}) AS DATE)"
        day = self._df(f"""SELECT {week.format('date')} AS period, sum(msl_compliant) AS msl_n,
                   sum(msl_assessable) AS msl_d, sum(oos) AS oos_n, sum(known) AS oos_d
            FROM ({self._day_components(g)}) WHERE store_id = $store_id GROUP BY 1 ORDER BY 1""", p)
        audit = self._df(f"""WITH v AS (SELECT audit_date, category, sum(CAST(shelf_allocation_pct AS DOUBLE)) AS a,
                       sum(audited_row_count) AS audited, sum(compliant_audit_row_count) AS compliant
                {self._AUDIT} AND store_id = $store_id GROUP BY ALL)
            SELECT {week.format('audit_date')} AS period, sum(compliant) AS plano_n, sum(audited) AS plano_d,
                   sum(a) AS alloc_n, count(*) AS alloc_d FROM v GROUP BY 1 ORDER BY 1""", p)
        flow = self._df(f"""SELECT period_start AS period, sum(comparable_gap_units) AS gap_n,
                   sum(comparable_sell_out_units) AS gap_d
            {self._PERIODS} AND list_contains($skus, sku_id) AND store_id = $store_id GROUP BY 1 ORDER BY 1""", p)
        rows = []
        for frame, metric, scale in ((day, "msl", 100.0), (day, "oos", 100.0), (audit, "plano", 100.0),
                                     (audit, "alloc", 1.0), (flow, "gap", 100.0)):
            for r in frame.to_dict("records"):
                n, d = r[f"{metric}_n"], r[f"{metric}_d"]
                rows.append({"metric": metric, "period": r["period"], "numerator": n, "denominator": d,
                             "value": _ratio(n, d, scale=scale)})
        out = pd.DataFrame(rows, columns=["metric", "period", "numerator", "denominator", "value"])
        out.attrs.update(start=start.date(), end=end.date())
        return out

    def latest_inventory(self, f, top=None):
        """Latest date in range: inventory per store x SKU (MSL SKUs when any are selected). ``top``: keep the
        stores with the most zero-stock products (ties: lower stock first)."""
        frame = self._df(f"""WITH d AS ({self._SKU_ROWS}),
                   m AS (SELECT * FROM d WHERE is_msl OR NOT (SELECT bool_or(is_msl) FROM d)),
                   latest AS (SELECT max(date) AS date FROM m)
            SELECT m.date, m.store_id, s.store_name, m.sku_id, p.product_name, m.is_msl, m.inv AS inventory,
                   m.inv_status AS inventory_data_status, m.last_report_date
            FROM m JOIN latest USING (date) JOIN stores s USING (store_id) JOIN products p USING (sku_id)""",
                         f.params())
        stores = frame["store_id"].nunique() if len(frame) else 0
        if top and stores > top:
            rank = frame.assign(oos=frame["inventory"].eq(0)).groupby("store_id").agg(
                oos=("oos", "sum"), inv=("inventory", "mean")).sort_values(["oos", "inv"], ascending=[False, True])
            frame = frame[frame["store_id"].isin(rank.index[:top])]
        frame.attrs["stores_total"] = stores
        return frame

    def store_skus(self, f, store_id):
        """The stock state of every selected SKU of one store on the range's latest day (for its store card)."""
        p = dict(f.params(), store_id=store_id)
        return self._df(f"""WITH d AS ({self._SKU_ROWS}), last AS (SELECT max(date) AS date FROM d)
            SELECT d.sku_id, p.product_name, p.category, d.is_msl, d.inv AS inventory,
                   d.inv_status AS inventory_data_status, d.last_report_date
            FROM d JOIN last USING (date) JOIN products p USING (sku_id) WHERE d.store_id = $store_id
            ORDER BY d.is_msl DESC, (d.inv = 0) DESC, p.product_name""", p)

    # ------------------------------------------------------------------ sell-in vs sell-out
    def flow_trend(self, f):
        """Comparable sell-in, sell-out and gap units per complete biweekly period in the range (a period with
        no comparable row has NULL flows: it is a gap in the line, never zero)."""
        frame = self._df(f"""SELECT period_start, any_value(period_end) AS period_end,
                   sum(comparable_sell_in_units) AS sell_in, sum(comparable_sell_out_units) AS sell_out,
                   sum(comparable_gap_units) AS gap_units, count(*) AS rows,
                   count(*) FILTER (WHERE flow_is_comparable) AS comparable_rows
            {self._PERIODS} AND list_contains($skus, sku_id) AND store_id IN {self._STORES}
            GROUP BY 1 ORDER BY 1""", f.params())
        frame["gap_pct"] = [_ratio(g, o) for g, o in zip(frame["gap_units"], frame["sell_out"])]
        return frame

    _FLOW_KEYS = {"channel": ("s.channel", "s.channel"), "region": ("s.region", "s.region"),
                  "store": ("s.store_id", "s.store_name"), "sku": ("p.sku_id", "p.product_name")}

    def flow_breakdown(self, f, dimension):
        """Per channel / region / store / SKU: gap over the range, the current and longest runs of positive or
        negative gap (complete comparable biweekly periods), the OOS rate and the latest delivery receipt."""
        key, label = self._FLOW_KEYS[dimension]
        p = f.params()
        periods = self._df(f"SELECT DISTINCT period_start {self._PERIODS} ORDER BY 1", p)["period_start"].tolist()
        rows = self._df(f"""SELECT {key} AS key, any_value({label}) AS label, c.period_start,
                   sum(c.comparable_sell_in_units) AS sell_in, sum(c.comparable_sell_out_units) AS sell_out,
                   sum(c.comparable_gap_units) AS gap_units
            FROM m1_period c JOIN stores s ON s.store_id = c.store_id JOIN products p ON p.sku_id = c.sku_id
            WHERE c.period_type = '{PERIOD_TYPE}' AND c.alignment_basis = 'event_date' AND c.period_is_complete
              AND c.period_start >= $start AND c.period_end <= $end AND list_contains($skus, c.sku_id)
              AND c.store_id IN {self._STORES} AND c.flow_is_comparable
            GROUP BY 1, 3 ORDER BY 1, 3""", p)
        stock_key = key.replace("s.", "d.").replace("p.", "d.")
        stock = self._df(f"""SELECT {stock_key} AS key, count(*) FILTER (WHERE d.inv = 0) AS oos, count(d.inv) AS known,
                   max(d.date) FILTER (WHERE d.receipt_delivery_count > 0) AS last_delivery
            FROM ({self._SKU_ROWS}) d GROUP BY 1""", p)
        out = []
        for name, part in rows.groupby("key", sort=True):
            by_period = dict(zip(part["period_start"], part["gap_units"]))
            run = streaks(periods, [by_period.get(x) for x in periods])
            gap, sell_out = part["gap_units"].sum(min_count=1), part["sell_out"].sum(min_count=1)
            out.append({"key": name, "label": part["label"].iloc[0], "sell_in": part["sell_in"].sum(min_count=1),
                        "sell_out": sell_out, "gap_units": gap, "gap_pct": _ratio(gap, sell_out),
                        "periods": len(part), "current": run["current"],
                        "longest_positive": run["longest_positive"], "longest_negative": run["longest_negative"]})
        frame = pd.DataFrame(out, columns=["key", "label", "sell_in", "sell_out", "gap_units", "gap_pct", "periods",
                                           "current", "longest_positive", "longest_negative"])
        frame = frame.merge(stock, on="key", how="left")
        frame["oos_pct"] = [_ratio(o, k) for o, k in zip(frame["oos"], frame["known"])]
        frame.attrs["periods"] = periods
        return frame

    # ------------------------------------------------------------------ diagnostics
    def lost_sales_heuristic(self, f, group):
        """Zero-stock days x in-stock average daily units x static list price (heuristic)."""
        column = {"region": "d.region", "category": "d.category"}[group]
        return self._df(f"""WITH d AS ({self._SKU_ROWS}),
                  base AS (SELECT store_id, sku_id, avg(units) AS baseline FROM d
                           WHERE inv > 0 AND units IS NOT NULL GROUP BY ALL)
            SELECT CAST(date_trunc('week', d.date) AS DATE) AS week, {column} AS grp,
                   sum(b.baseline * CAST(p.list_price AS DOUBLE)) AS lost_value,
                   count(*) AS oos_days
            FROM d JOIN base b USING (store_id, sku_id) JOIN products p USING (sku_id)
            WHERE d.inv = 0 GROUP BY ALL ORDER BY week, grp""", f.params())

    def heuristic_excluded(self, f):
        """Zero-stock store-SKU-days without an in-stock baseline (left out, not estimated)."""
        return int(self._one(f"""WITH d AS ({self._SKU_ROWS})
            SELECT count(*) AS n FROM d WHERE inv = 0 AND NOT EXISTS
              (SELECT 1 FROM d b WHERE b.store_id = d.store_id AND b.sku_id = d.sku_id
                 AND b.inv > 0 AND b.units IS NOT NULL)""", f.params())["n"])

    def oos_episodes(self, f, group):
        """Consecutive zero-stock calendar days; any gap or Unknown day ends an episode."""
        column = {"channel": "s.channel", "segment_tier": "s.segment_tier"}[group]
        return self._df(f"""WITH o AS (SELECT store_id, sku_id, date FROM ({self._SKU_ROWS}) WHERE inv = 0),
                 g AS (SELECT *, date - CAST(row_number() OVER (PARTITION BY store_id, sku_id ORDER BY date) AS INTEGER)
                                 AS island FROM o)
            SELECT {column} AS grp, g.store_id, g.sku_id, min(g.date) AS first_day, count(*) AS days
            FROM g JOIN stores s USING (store_id) GROUP BY grp, g.store_id, g.sku_id, g.island
            ORDER BY grp, g.store_id, g.sku_id, first_day""", f.params())

    def execution_by_brand(self, f):
        audit = self._df(f"""SELECT p.category, p.brand, sum(compliant_audit_row_count) AS compliant,
                   sum(audited_row_count) AS audited
            {self._AUDIT} AND store_id IN {self._STORES}
            GROUP BY ALL""".replace("FROM execution_visits", "FROM execution_visits JOIN products p "
                                    "ON p.sku_id = replace(sku_scope_id, 'sku:', '')"), f.params())
        msl = self._df(f"""SELECT p.category, p.brand, count(*) FILTER (WHERE d.inv = 0) AS oos,
                   count(d.inv) AS known
            FROM ({self._SKU_ROWS}) d JOIN products p USING (sku_id)
            WHERE d.is_msl GROUP BY ALL""", f.params())
        out = audit.merge(msl, on=["category", "brand"], how="outer")
        out["planogram_pct"] = [_ratio(c, a) for c, a in zip(out["compliant"], out["audited"])]
        out["msl_availability_pct"] = [None if k is None or pd.isna(k) or k == 0 else 100.0 * (1 - o / k)
                                       for o, k in zip(out["oos"], out["known"])]
        return out.sort_values(["category", "brand"]).reset_index(drop=True)

    # ------------------------------------------------------------------ trade terms (invoices; same in both views)
    def references(self):
        """Source guardrail references per channel x category; inconsistent versions are not resolved.

        ``discount_ceiling_pct`` is the provisional total discount without manager review: standard plus
        maximum additional discount. A release without the standard (an earlier guardrail table) has no
        ceiling, so no store is placed above or below it."""
        columns = set(self._df("SELECT column_name FROM information_schema.columns "
                               "WHERE table_name = 'negotiation_context'")["column_name"])
        standard = ("CAST(standard_discount_pct_source AS DOUBLE)" if "standard_discount_pct_source" in columns
                    else "CAST(NULL AS DOUBLE)")
        return self._df(f"""SELECT channel, category, count(DISTINCT ({standard}, max_discount_pct_source,
                       min_target_margin_source, required_exec_tradeoff, rule_version)) AS variants,
                   any_value({standard}) AS standard_discount_pct_source,
                   any_value(CAST(max_discount_pct_source AS DOUBLE)) AS max_discount_pct_source,
                   any_value({standard} + CAST(max_discount_pct_source AS DOUBLE)) AS discount_ceiling_pct,
                   any_value(CAST(min_target_margin_source AS DOUBLE)) AS min_target_margin_source,
                   any_value(required_exec_tradeoff) AS required_exec_tradeoff, any_value(rule_version) AS rule_version,
                   any_value(policy_status) AS policy_status
            FROM negotiation_context GROUP BY ALL""")

    def trade_terms(self, f):
        frame = self._df(f"""SELECT e.store_id, s.store_name, s.channel, s.region, p.category,
                   sum(CAST(e.gross_sales AS DOUBLE)) AS gross, sum(CAST(e.net_sales AS DOUBLE)) AS net,
                   sum(e.units_sold * CAST(p.unit_cost AS DOUBLE)) AS static_cost, count(*) AS lines,
                   count(*) FILTER (WHERE e.guardrail_violation) AS source_flagged_lines
            FROM sell_in_events e JOIN stores s USING (store_id) JOIN products p USING (sku_id)
            WHERE e.date BETWEEN $start AND $end AND list_contains($skus, e.sku_id) AND e.store_id IN {self._STORES}
            GROUP BY ALL ORDER BY e.store_id, p.category""", f.params())
        frame["trade_discount_pct"] = [_ratio(g - n, g) for g, n in zip(frame["gross"], frame["net"])]
        frame["margin_pct_static_cost"] = [_ratio(n - c, n) for n, c in zip(frame["net"], frame["static_cost"])]
        refs = self.references()
        merged = frame.merge(refs, on=["channel", "category"], how="left")

        def status(row):
            if pd.isna(row["variants"]) or row["variants"] != 1 or pd.isna(row["discount_ceiling_pct"]) \
                    or row["trade_discount_pct"] is None:
                return "No single source reference"
            if row["trade_discount_pct"] > row["discount_ceiling_pct"] + 1e-9:
                return ABOVE_CEILING
            return WITHIN_CEILING
        merged["reference_status"] = merged.apply(status, axis=1) if len(merged) else []
        return merged

    def tradeoff_evidence(self, f):
        """Audit evidence per required trade-off text; no pass/fail rule is applied."""
        refs = self.references()
        visits = self._df(f"""SELECT s.channel, v.category, sum(audited_row_count) AS audited,
                   sum(compliant_audit_row_count) AS compliant, sum(secondary_display_row_count) AS secondary,
                   count(DISTINCT v.store_id) AS stores
            FROM execution_visits v JOIN stores s USING (store_id)
            WHERE v.scope_label = '{SKU_SCOPE}' AND v.audit_date BETWEEN $start AND $end
              AND list_contains($skus, replace(v.sku_scope_id, 'sku:', '')) AND v.store_id IN {self._STORES}
            GROUP BY ALL""", f.params())
        merged = visits.merge(refs[refs["variants"] == 1], on=["channel", "category"], how="inner")
        out = merged.groupby("required_exec_tradeoff").agg(audited=("audited", "sum"), compliant=("compliant", "sum"),
                                                           secondary=("secondary", "sum"), stores=("stores", "sum")
                                                           ).reset_index()
        out["Planogram compliant audit rows %"] = [_ratio(c, a) for c, a in zip(out["compliant"], out["audited"])]
        out["Secondary display audit rows %"] = [_ratio(c, a) for c, a in zip(out["secondary"], out["audited"])]
        return out


class Module1Pool:
    """Recent per-principal caches for one pinned publication, one per data view; never shared across
    principals."""

    def __init__(self, reader, size=4, *, materialize=None):
        self.reader = reader
        self.size = size
        self._items = OrderedDict()
        self._lock = threading.Lock()
        self._building = {}
        self._materialize = materialize

    def get(self, principal_id, mode="reported"):
        if mode not in MODES:
            raise ValueError(f"Unknown data view {mode!r}")
        scope = self.reader.principal_scope(principal_id)  # PermissionError for an unassigned principal
        key = (self.reader.record["artifact_id"], principal_id, scope["scope_fingerprint"], mode)
        with self._lock:
            item = self._items.get(key)
            if item is not None and not item.closed:
                self._items.move_to_end(key)
                return item
            build_lock = self._building.setdefault(key, threading.Lock())
        with build_lock:  # one build per key; concurrent sessions of the same principal wait
            try:
                with self._lock:
                    item = self._items.get(key)
                    if item is not None and not item.closed:
                        return item
                if self._materialize is None:
                    from data_platform.scoped_cache import materialize_business_views as materialize
                else:
                    materialize = self._materialize
                if mode == "estimated":   # the explicit opt-in to the candidate estimate views
                    cache = materialize(self.reader, principal_id, ESTIMATED_VIEWS, allow_candidate_estimates=True)
                else:
                    cache = materialize(self.reader, principal_id, VIEWS)
                if cache.scope_fingerprint != scope["scope_fingerprint"]:
                    cache.close()
                    raise PermissionError("Account scope changed while loading; sign in again")
                try:
                    item = Module1Data(cache, mode)
                except Exception:
                    cache.close()
                    raise
                evicted = []
                with self._lock:
                    existing = self._items.get(key)
                    if existing is not None and not existing.closed:  # a concurrent build finished first
                        evicted.append(item)
                        item = existing
                    self._items[key] = item
                    # The principal's caches for another publication or scope go; its other data view stays.
                    evicted += [self._items.pop(k) for k in list(self._items)
                                if k[1] == principal_id and k[:3] != key[:3]]
                    while len(self._items) > self.size:
                        evicted.append(self._items.popitem(last=False)[1])
            finally:
                with self._lock:
                    self._building.pop(key, None)
        for old in evicted:
            old.close()  # waits for an in-flight query of that cache
        return item
