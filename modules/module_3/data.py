"""
modules/module_3/data.py
========================
Read-only inputs for Module 3, from the signed-in principal's scoped copy of the shared publication
(the same copy Module 1 builds: modules/module_1/data.Module1Pool), so opening the simulator after the
dashboard needs no second load. Fixed SQL only; scope is the copy's scope, and a store or SKU outside it
simply has no rows. Every read re-checks the principal's access and the pinned publication before and
after the query (a revocation during the read is caught), and a cached result is re-checked before it
is returned; a failed check raises and closes the copy.

- Scenario A: the negotiation context of one store × SKU (reference prices, the recent trade discount,
  the guardrail and its required commitment, stock and audit evidence).
- Scenario B: observed in-stock sell-out of the last 13 weeks for a SKU or category, the reference
  prices and the recent trade discount, and a price elasticity estimated from the same scoped history.
"""

from __future__ import annotations

import math

import pandas as pd

BASELINE_DAYS = 91           # 13 weeks
ELASTICITY_MIN_ROWS = 200


def _num(value):
    return None if value is None or pd.isna(value) else float(value)


class Module3Data:
    """Module 3's view of one principal's scoped copy (a Module1Data)."""

    def __init__(self, item):
        self.item = item
        self.record = item.record
        self.scope = item.scope
        self.manager = item.manager
        self._elasticity = {}
        self._baseline = {}

    def check(self):
        self.scope = self.item.check()
        return self.scope

    def q(self, sql, params=None):
        """One fixed query, between two access checks."""
        self.check()
        frame = self.item.query(sql, params)
        self.check()
        return frame

    # ------------------------------------------------------------------ Scenario A
    def accounts(self):
        """Stores in scope that have a negotiation context, with their channel."""
        return self.q("""SELECT DISTINCT n.store_id, s.store_name, n.channel, n.region, n.segment_tier
                         FROM negotiation_context n JOIN stores s USING (store_id)
                         ORDER BY s.store_name, n.store_id""")

    def products_for(self, store_id):
        return self.q("""SELECT n.sku_id, p.product_name, n.category, n.is_msl
                         FROM negotiation_context n JOIN products p USING (sku_id)
                         WHERE n.store_id = $store ORDER BY n.category, p.product_name""", {"store": store_id})

    def context(self, store_id, sku_id):
        """One store × SKU context row as a dict, or None (not in scope or no context)."""
        frame = self.q("""SELECT n.*, s.store_name, p.product_name, p.brand
                          FROM negotiation_context n JOIN stores s USING (store_id) JOIN products p USING (sku_id)
                          WHERE n.store_id = $store AND n.sku_id = $sku
                          ORDER BY n.rule_version DESC LIMIT 1""", {"store": store_id, "sku": sku_id})
        if not len(frame):
            return None
        row = frame.iloc[0].to_dict()
        return {k: (None if (not isinstance(v, (list, tuple, dict)) and pd.isna(v)) else v) for k, v in row.items()}

    # ------------------------------------------------------------------ Scenario B
    def categories(self):
        return [r for r in self.q("SELECT DISTINCT category FROM products ORDER BY 1")["category"] if r]

    def products(self):
        return self.q("SELECT sku_id, product_name, category FROM products ORDER BY category, product_name")

    def _where(self, kind, value):
        if kind == "sku":
            return "d.sku_id = $value"
        if kind == "category":
            return "d.category = $value"
        raise ValueError("kind must be sku or category")

    def baseline(self, kind, value):
        """Per SKU: observed units on in-stock days in the last 13 weeks, reference prices and the recent
        reported trade discount. Units are reported sell-out, not complete demand."""
        where = self._where(kind, value)
        key = (kind, value)
        if key in self._baseline:
            self.check()                     # a cached result is only returned to a principal still in scope
            return self._baseline[key].copy()
        frame = self.q(f"""WITH d AS (SELECT * FROM store_sku_daily d WHERE {where}
                              AND d.date > (SELECT max(date) FROM store_sku_daily) - INTERVAL {BASELINE_DAYS} DAY),
                     units AS (SELECT sku_id, sum(units_sold_observed) AS units, count(*) AS instock_days,
                                      count(DISTINCT store_id) AS stores, min(date) AS first_day, max(date) AS last_day
                               FROM d WHERE inventory_observed > 0 AND units_sold_observed IS NOT NULL GROUP BY 1),
                     disc AS (SELECT sku_id, sum(gross_sales_reported_28d) AS gross, sum(net_sales_reported_28d) AS net
                              FROM negotiation_context GROUP BY 1)
                SELECT p.sku_id, p.product_name, p.category, CAST(p.list_price AS DOUBLE) AS list_price,
                       CAST(p.unit_cost AS DOUBLE) AS unit_cost, CAST(p.consumer_price AS DOUBLE) AS consumer_price,
                       u.units, u.instock_days, u.stores, u.first_day, u.last_day,
                       CASE WHEN disc.gross > 0 THEN 100 * (disc.gross - disc.net) / disc.gross END AS trade_discount
                FROM products p JOIN units u USING (sku_id) LEFT JOIN disc USING (sku_id)
                ORDER BY p.category, p.product_name""", {"value": value})
        self._baseline[key] = frame
        return frame.copy()

    def elasticity(self, category):
        """Own-price elasticity of observed daily units for a category, from the scoped history.

        Log units on log shelf price within store × SKU × month (in-stock days with a price), so store size,
        product and season do not drive it. The price variation in the data comes almost only from
        temporary promotions; the standard error ignores clustering, so the interval is approximate."""
        if category in self._elasticity:
            self.check()                     # a cached result is only returned to a principal still in scope
            return dict(self._elasticity[category])
        row = self.q("""WITH d AS (SELECT store_id, sku_id, date_trunc('month', date) AS m,
                                      ln(units_sold_observed + 0.5) AS y, ln(consumer_price_observed) AS x
                               FROM store_sku_daily WHERE category = $category AND inventory_observed > 0
                                 AND units_sold_observed IS NOT NULL AND consumer_price_observed > 0),
                       f AS (SELECT y - avg(y) OVER w AS y, x - avg(x) OVER w AS x FROM d
                             WINDOW w AS (PARTITION BY store_id, sku_id, m))
                  SELECT regr_slope(y, x) AS slope, regr_count(y, x) AS n, regr_sxx(y, x) AS sxx,
                         regr_syy(y, x) AS syy, regr_sxy(y, x) AS sxy FROM f""",
                     {"category": category}).iloc[0].to_dict()
        # Observed shelf prices relative to the reference consumer price: an increase beyond the top of
        # this range is an extrapolation.
        span = self.q("""SELECT min(d.consumer_price_observed / p.consumer_price) - 1 AS lo,
                                 max(d.consumer_price_observed / p.consumer_price) - 1 AS hi
                          FROM store_sku_daily d JOIN products p USING (sku_id)
                          WHERE d.category = $category AND d.consumer_price_observed > 0 AND p.consumer_price > 0""",
                      {"category": category}).iloc[0].to_dict()
        n, sxx = int(row.get("n") or 0), _num(row.get("sxx"))
        result = {"category": category, "estimate": None, "se": None, "low": None, "high": None, "n": n,
                  "price_range": None}
        if n >= ELASTICITY_MIN_ROWS and sxx and sxx > 0:
            slope = _num(row["slope"])
            ssr = max(0.0, _num(row["syy"]) - _num(row["sxy"]) ** 2 / sxx)
            se = math.sqrt(ssr / max(1, n - 2) / sxx)
            result.update(estimate=slope, se=se, low=slope - 1.96 * se, high=slope + 1.96 * se,
                          price_range=None if _num(span.get("hi")) is None else (_num(span["lo"]), _num(span["hi"])))
        self._elasticity[category] = result
        return dict(result)
