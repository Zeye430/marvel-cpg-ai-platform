"""Module 1 data service over the shared business publication (Reported view).

Trusted server code only. One complete, account-scoped cache per verified
principal (data_platform.scoped_cache) is queried with fixed SQL; filters are
bound parameters, never SQL text. KPIs aggregate the shared numerator and
denominator components (docs/data/v3-module-1-integration.md, section 4):

- all-SKU OOS/coverage/MSL use ``store_day`` components; a SKU subset recomputes
  the same components from ``store_sku_daily`` (all-store-day denominators are
  never reused for a filtered SKU set);
- Sell-in/Sell-out gap uses one commercial period family (biweek, event-date
  alignment, complete periods inside the range) and comparable flows only;
- execution uses the ``Individual audited SKU`` scope only (scopes overlap);
- unknown values stay NULL/NaN and zero denominators give None (N/A).

Nothing here reads raw CSVs, legacy tables, labels or estimate views.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date

import pandas as pd

VIEWS = ("stores", "products", "store_sku_daily", "store_day", "commercial_period",
         "sell_in_events", "execution_visits", "negotiation_context")
PERIOD_TYPE = "biweek"  # recommended for every channel; families are never mixed
SKU_SCOPE = "Individual audited SKU"
REPORTED = "reported_only"


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


class Module1Data:
    """Read-only analytics for one principal; discard on scope or source change."""

    def __init__(self, cache):
        self.cache = cache
        self.record = cache.record
        self.principal_id = cache.principal_id
        self.lock = threading.Lock()
        self.scope = cache.reader.principal_scope(self.principal_id)
        self.manager = self.scope["roles"] == ["manager"]

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

    _STORES = "(SELECT store_id FROM stores WHERE list_contains($channels, channel) AND list_contains($regions, region))"

    # ------------------------------------------------------------------ choices
    def choices(self):
        stores = self._df("SELECT channel, region, segment_tier, count(*) AS n FROM stores GROUP BY ALL ORDER BY ALL")
        products = self._df("SELECT sku_id, product_name, category, is_msl FROM products ORDER BY category, product_name")
        bounds = self._one("SELECT min(date) AS first, max(date) AS last FROM store_sku_daily")
        return {"channels": sorted(stores["channel"].unique().tolist()),
                "regions": sorted(stores["region"].unique().tolist()),
                "tiers": sorted(stores["segment_tier"].dropna().unique().tolist()),
                "categories": sorted(products["category"].dropna().unique().tolist()),
                "store_count": int(stores["n"].sum()),
                "products": {r.sku_id: f"{r.product_name} ({r.category})" for r in products.itertuples()},
                "product_names": {r.sku_id: r.product_name for r in products.itertuples()},
                "product_categories": {r.sku_id: r.category for r in products.itertuples()},
                "msl_products": [r.sku_id for r in products.itertuples() if r.is_msl is True or r.is_msl == 1],
                "first_date": bounds.get("first"), "last_date": bounds.get("last"),
                "as_of": str(self.record.get("as_of"))}

    def store_count(self, f):
        return int(self._one(f"SELECT count(*) AS n FROM stores WHERE store_id IN {self._STORES}", f.params())["n"])

    # ------------------------------------------------------------------ KPI components
    def availability_components(self, f):
        """OOS / coverage / MSL numerator and denominator components for the filter."""
        if f.all_skus:
            sql = f"""SELECT sum(expected_sku_count) AS expected, sum(known_inventory_sku_count) AS known,
                         sum(observed_inventory_sku_count) AS observed, sum(oos_sku_count) AS oos,
                         sum(msl_eligible_store_count) AS msl_eligible, sum(msl_assessable_store_count) AS msl_assessable,
                         sum(msl_compliant_store_count) AS msl_compliant, sum(msl_unknown_store_count) AS msl_unknown,
                         count(*) AS store_days
                  FROM store_day WHERE metric_view = '{REPORTED}' AND date BETWEEN $start AND $end
                   AND store_id IN {self._STORES}"""
        else:
            sql = f"""WITH c AS (
                  SELECT date, store_id, count(*) AS expected, count(inventory_observed) AS known,
                         count(*) FILTER (WHERE inventory_data_status = 'Observed') AS observed,
                         count(*) FILTER (WHERE inventory_observed = 0) AS oos,
                         count(*) FILTER (WHERE is_msl) AS msl_required,
                         count(*) FILTER (WHERE is_msl AND inventory_observed IS NOT NULL) AS msl_known,
                         count(*) FILTER (WHERE is_msl AND inventory_observed = 0) AS msl_unavailable
                  FROM store_sku_daily WHERE date BETWEEN $start AND $end AND list_contains($skus, sku_id)
                   AND store_id IN {self._STORES} GROUP BY date, store_id),
                s AS (SELECT *, CASE WHEN msl_required = 0 THEN 'N/A' WHEN msl_unavailable > 0 THEN 'Noncompliant'
                                     WHEN msl_known = msl_required THEN 'Compliant' ELSE 'Unknown' END AS state FROM c)
                SELECT sum(expected) AS expected, sum(known) AS known, sum(observed) AS observed, sum(oos) AS oos,
                       count(*) FILTER (WHERE state <> 'N/A') AS msl_eligible,
                       count(*) FILTER (WHERE state IN ('Compliant', 'Noncompliant')) AS msl_assessable,
                       count(*) FILTER (WHERE state = 'Compliant') AS msl_compliant,
                       count(*) FILTER (WHERE state = 'Unknown') AS msl_unknown, count(*) AS store_days FROM s"""
        return {k: (None if pd.isna(v) else int(v)) for k, v in self._one(sql, f.params()).items()}

    # One period family and alignment; only complete calendar periods fully inside the date range.
    _PERIODS = f"""FROM commercial_period WHERE period_type = '{PERIOD_TYPE}' AND alignment_basis = 'event_date'
             AND metric_view = '{REPORTED}' AND period_is_complete AND period_start >= $start AND period_end <= $end"""

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
        uses its complete biweekly periods). Columns: period, numerator, denominator."""
        week = "CAST(date_trunc('week', {}) AS DATE)"
        if key in ("msl", "oos"):
            num, den = (("msl_compliant", "msl_assessable") if key == "msl" else ("oos", "known"))
            if f.all_skus:
                sql = f"""SELECT {week.format('date')} AS period,
                             sum({'msl_compliant_store_count' if key == 'msl' else 'oos_sku_count'}) AS numerator,
                             sum({'msl_assessable_store_count' if key == 'msl' else 'known_inventory_sku_count'})
                               AS denominator
                      FROM store_day WHERE metric_view = '{REPORTED}' AND date BETWEEN $start AND $end
                       AND store_id IN {self._STORES} GROUP BY 1 ORDER BY 1"""
            else:
                sql = f"""WITH c AS (
                      SELECT date, store_id, count(inventory_observed) AS known,
                             count(*) FILTER (WHERE inventory_observed = 0) AS oos,
                             count(*) FILTER (WHERE is_msl) AS msl_required,
                             count(*) FILTER (WHERE is_msl AND inventory_observed IS NOT NULL) AS msl_known,
                             count(*) FILTER (WHERE is_msl AND inventory_observed = 0) AS msl_unavailable
                      FROM store_sku_daily WHERE date BETWEEN $start AND $end AND list_contains($skus, sku_id)
                       AND store_id IN {self._STORES} GROUP BY date, store_id),
                    s AS (SELECT *, CASE WHEN msl_required = 0 THEN 'N/A' WHEN msl_unavailable > 0 THEN 'Noncompliant'
                                         WHEN msl_known = msl_required THEN 'Compliant' ELSE 'Unknown' END AS state FROM c)
                    SELECT {week.format('date')} AS period,
                           {"count(*) FILTER (WHERE state = 'Compliant')" if key == 'msl' else 'sum(oos)'} AS numerator,
                           {"count(*) FILTER (WHERE state IN ('Compliant', 'Noncompliant'))" if key == 'msl'
                            else 'sum(known)'} AS denominator
                    FROM s GROUP BY 1 ORDER BY 1"""
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
        return {
            "oos": _ratio(a["oos"], a["known"]), "oos_known": a["known"], "oos_count": a["oos"],
            "coverage": _ratio(a["observed"], a["expected"]), "expected": a["expected"],
            "msl": _ratio(a["msl_compliant"], a["msl_assessable"]),
            "msl_coverage": _ratio(a["msl_assessable"], a["msl_eligible"]),
            "msl_unknown": a["msl_unknown"], "msl_eligible": a["msl_eligible"], "store_days": a["store_days"],
            "gap_pct": _ratio(flow.get("gap_units"), flow.get("sell_out")), "gap_units": gap,
            "gap_periods": _int(flow.get("periods")), "gap_rows": _int(flow.get("rows")),
            "gap_comparable_rows": _int(flow.get("comparable_rows")),
            "gap_completeness_unconfirmed": bool(flow.get("completeness_unconfirmed")) if not pd.isna(flow.get("completeness_unconfirmed")) else False,
            "gap_first": None if pd.isna(flow.get("first_day")) else pd.Timestamp(flow["first_day"]).date(),
            "gap_last": None if pd.isna(flow.get("last_day")) else pd.Timestamp(flow["last_day"]).date(),
            "allocation": _ratio(ex.get("allocation_sum"), ex.get("visits"), scale=1.0),
            "visits": _int(ex.get("visits")),
            "plano": _ratio(ex.get("compliant"), ex.get("audited")), "audited": _int(ex.get("audited")),
        }

    # ------------------------------------------------------------------ chart data
    def latest_inventory(self, f, top=None):
        """Latest date in range: inventory per store x SKU (MSL SKUs when any are selected)."""
        frame = self._df(f"""WITH d AS (SELECT * FROM store_sku_daily WHERE date BETWEEN $start AND $end
                       AND list_contains($skus, sku_id) AND store_id IN {self._STORES}),
                   m AS (SELECT * FROM d WHERE is_msl OR NOT (SELECT bool_or(is_msl) FROM d)),
                   latest AS (SELECT max(date) AS date FROM m)
            SELECT m.date, s.store_name, p.product_name, m.inventory_observed, m.inventory_data_status,
                   m.last_report_date
            FROM m JOIN latest USING (date) JOIN stores s USING (store_id) JOIN products p USING (sku_id)""",
                         f.params())
        if top and frame["store_name"].nunique() > top:
            rank = frame.assign(oos=frame["inventory_observed"].eq(0)).groupby("store_name").agg(
                oos=("oos", "sum"), inv=("inventory_observed", "mean")).sort_values(["oos", "inv"], ascending=[False, True])
            frame = frame[frame["store_name"].isin(rank.index[:top])]
        return frame

    def lost_sales_heuristic(self, f, group):
        """Observed OOS days x observed in-stock average daily units x static list price (heuristic)."""
        column = {"region": "d.region", "category": "d.category"}[group]
        return self._df(f"""WITH d AS (SELECT * FROM store_sku_daily WHERE date BETWEEN $start AND $end
                      AND list_contains($skus, sku_id) AND store_id IN {self._STORES}),
                  base AS (SELECT store_id, sku_id, avg(units_sold_observed) AS baseline FROM d
                           WHERE inventory_observed > 0 AND units_sold_observed IS NOT NULL GROUP BY ALL)
            SELECT CAST(date_trunc('week', d.date) AS DATE) AS week, {column} AS grp,
                   sum(b.baseline * CAST(p.list_price AS DOUBLE)) AS lost_value,
                   count(*) AS oos_days
            FROM d JOIN base b USING (store_id, sku_id) JOIN products p USING (sku_id)
            WHERE d.inventory_observed = 0 GROUP BY ALL ORDER BY week, grp""", f.params())

    def heuristic_excluded(self, f):
        """Observed OOS store-SKU-days without an observed in-stock baseline (not estimated)."""
        return int(self._one(f"""WITH d AS (SELECT * FROM store_sku_daily WHERE date BETWEEN $start AND $end
                      AND list_contains($skus, sku_id) AND store_id IN {self._STORES})
            SELECT count(*) AS n FROM d WHERE inventory_observed = 0 AND NOT EXISTS
              (SELECT 1 FROM d b WHERE b.store_id = d.store_id AND b.sku_id = d.sku_id
                 AND b.inventory_observed > 0 AND b.units_sold_observed IS NOT NULL)""",
                             f.params())["n"])

    def oos_episodes(self, f, group):
        """Consecutive observed OOS calendar days; any gap or Unknown day ends an episode."""
        column = {"channel": "s.channel", "segment_tier": "s.segment_tier"}[group]
        return self._df(f"""WITH o AS (SELECT store_id, sku_id, date FROM store_sku_daily
                     WHERE date BETWEEN $start AND $end AND inventory_observed = 0 AND list_contains($skus, sku_id)
                       AND store_id IN {self._STORES}),
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
        msl = self._df(f"""SELECT p.category, p.brand, count(*) FILTER (WHERE d.inventory_observed = 0) AS oos,
                   count(d.inventory_observed) AS known
            FROM store_sku_daily d JOIN products p USING (sku_id)
            WHERE d.is_msl AND d.date BETWEEN $start AND $end AND list_contains($skus, d.sku_id)
              AND d.store_id IN {self._STORES} GROUP BY ALL""", f.params())
        out = audit.merge(msl, on=["category", "brand"], how="outer")
        out["planogram_pct"] = [_ratio(c, a) for c, a in zip(out["compliant"], out["audited"])]
        out["msl_availability_pct"] = [None if k is None or pd.isna(k) or k == 0 else 100.0 * (1 - o / k)
                                       for o, k in zip(out["oos"], out["known"])]
        return out.sort_values(["category", "brand"]).reset_index(drop=True)

    def store_allocation_sales(self, f):
        return self._df(f"""WITH v AS (SELECT store_id, audit_date, category, sum(shelf_allocation_pct) AS allocation
                    {self._AUDIT} AND store_id IN {self._STORES} GROUP BY ALL),
                 a AS (SELECT store_id, avg(allocation) AS allocation, count(*) AS visits FROM v GROUP BY ALL),
                 n AS (SELECT store_id, sum(net_sales) AS net_sales FROM sell_in_events
                       WHERE date BETWEEN $start AND $end AND list_contains($skus, sku_id) GROUP BY ALL)
            SELECT s.store_id, s.store_name, s.channel, s.region, s.segment_tier, a.allocation, a.visits,
                   CAST(n.net_sales AS DOUBLE) AS net_sales
            FROM a JOIN stores s USING (store_id) JOIN n USING (store_id)
            WHERE s.store_id IN {self._STORES} ORDER BY s.store_id""", f.params())

    def scorecard(self, f, group):
        column = {"region": "region", "segment_tier": "segment_tier", "channel": "channel"}[group]
        audit = self._df(f"""WITH v AS (SELECT store_id, audit_date, category, sum(shelf_allocation_pct) AS allocation,
                      sum(audited_row_count) AS audited, sum(compliant_audit_row_count) AS compliant,
                      sum(secondary_display_row_count) AS secondary
                    {self._AUDIT} AND store_id IN {self._STORES} GROUP BY ALL)
            SELECT s.{column} AS grp, sum(compliant) AS compliant, sum(audited) AS audited,
                   sum(secondary) AS secondary, avg(allocation) AS allocation
            FROM v JOIN stores s USING (store_id) GROUP BY ALL""", f.params())
        stock = self._df(f"""SELECT s.{column} AS grp, count(*) FILTER (WHERE d.inventory_observed = 0) AS oos,
                   count(d.inventory_observed) AS known,
                   count(*) FILTER (WHERE d.is_msl AND d.inventory_observed = 0) AS msl_oos,
                   count(*) FILTER (WHERE d.is_msl AND d.inventory_observed IS NOT NULL) AS msl_known
            FROM store_sku_daily d JOIN stores s USING (store_id)
            WHERE d.date BETWEEN $start AND $end AND list_contains($skus, d.sku_id) AND d.store_id IN {self._STORES}
            GROUP BY ALL""", f.params())
        out = audit.merge(stock, on="grp", how="outer").sort_values("grp")
        out["Planogram Compliance %"] = [_ratio(c, a) for c, a in zip(out["compliant"], out["audited"])]
        out["Secondary Display %"] = [_ratio(c, a) for c, a in zip(out["secondary"], out["audited"])]
        out["Audited SKU Allocation %"] = out["allocation"]
        out["In-Stock % (observed)"] = [None if not k or pd.isna(k) else 100.0 * (1 - o / k)
                                        for o, k in zip(out["oos"], out["known"])]
        out["MSL Availability % (observed)"] = [None if not k or pd.isna(k) else 100.0 * (1 - o / k)
                                                for o, k in zip(out["msl_oos"], out["msl_known"])]
        return out

    def references(self):
        """Source guardrail references per channel x category; inconsistent versions are not resolved."""
        return self._df("""SELECT channel, category, count(DISTINCT (max_discount_pct_source, min_target_margin_source,
                       required_exec_tradeoff, rule_version)) AS variants,
                   any_value(CAST(max_discount_pct_source AS DOUBLE)) AS max_discount_pct_source,
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
            if pd.isna(row["variants"]) or row["variants"] != 1 or pd.isna(row["max_discount_pct_source"]) \
                    or row["trade_discount_pct"] is None:
                return "No single source reference"
            if row["trade_discount_pct"] > row["max_discount_pct_source"]:
                return "Above source discount reference (requires review)"
            return "Within source discount reference (requires review)"
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

    def state_execution(self, f):
        sales = self._df(f"""SELECT s.state, sum(CAST(e.net_sales AS DOUBLE)) AS net_sales FROM sell_in_events e
            JOIN stores s USING (store_id) WHERE e.date BETWEEN $start AND $end AND list_contains($skus, e.sku_id)
              AND e.store_id IN {self._STORES} GROUP BY ALL""", f.params())
        audit = self._df(f"""SELECT s.state, sum(compliant_audit_row_count) AS compliant, sum(audited_row_count) AS audited
            {self._AUDIT.replace('FROM execution_visits', 'FROM execution_visits v JOIN stores s USING (store_id)')}
              AND v.store_id IN {self._STORES} GROUP BY ALL""", f.params())
        stock = self._df(f"""SELECT s.state, count(*) FILTER (WHERE d.inventory_observed = 0) AS oos,
                   count(d.inventory_observed) AS known, count(DISTINCT d.store_id) AS store_count
            FROM store_sku_daily d JOIN stores s USING (store_id) WHERE d.date BETWEEN $start AND $end
              AND list_contains($skus, d.sku_id) AND d.store_id IN {self._STORES} GROUP BY ALL""", f.params())
        out = stock.merge(sales, on="state", how="left").merge(audit, on="state", how="left")
        out["planogram_pct"] = [_ratio(c, a) for c, a in zip(out["compliant"], out["audited"])]
        out["in_stock_pct"] = [None if not k else 100.0 * (1 - o / k) for o, k in zip(out["oos"], out["known"])]
        out["execution_index"] = [None if p is None or i is None else (p + i) / 2
                                  for p, i in zip(out["planogram_pct"], out["in_stock_pct"])]
        return out.sort_values("state").reset_index(drop=True)

    def flow_by_channel(self, f):
        return self._df(f"""SELECT c.category, c.channel, sum(comparable_sell_in_units) AS sell_in,
                   sum(comparable_sell_out_units) AS sell_out
            {self._PERIODS.replace('FROM commercial_period', 'FROM commercial_period c')} AND flow_is_comparable
             AND list_contains($skus, sku_id) AND store_id IN {self._STORES} GROUP BY ALL ORDER BY ALL""",
                        f.params())


class Module1Pool:
    """Recent per-principal caches for one pinned publication; never shared across principals."""

    def __init__(self, reader, size=3, *, materialize=None):
        self.reader = reader
        self.size = size
        self._items = OrderedDict()
        self._lock = threading.Lock()
        self._building = {}
        self._materialize = materialize

    def get(self, principal_id):
        scope = self.reader.principal_scope(principal_id)  # PermissionError for an unassigned principal
        key = (self.reader.record["artifact_id"], principal_id, scope["scope_fingerprint"])
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
                cache = materialize(self.reader, principal_id, VIEWS)
                if cache.scope_fingerprint != scope["scope_fingerprint"]:
                    cache.close()
                    raise PermissionError("Account scope changed while loading; sign in again")
                try:
                    item = Module1Data(cache)
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
                    evicted += [self._items.pop(k) for k in list(self._items) if k[1] == principal_id and k != key]
                    while len(self._items) > self.size:
                        evicted.append(self._items.popitem(last=False)[1])
            finally:
                with self._lock:
                    self._building.pop(key, None)
        for old in evicted:
            old.close()  # waits for an in-flight query of that cache
        return item
