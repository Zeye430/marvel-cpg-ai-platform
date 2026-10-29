"""
modules/module_4/sandbox.py
===========================
Scoped, in-memory analytics sandbox for model-written SQL (Module 4 guide, section 4).

Trusted code (never the model) builds one sandbox per verified principal:

1. open the accepted business.duckdb publication read-only and look up the
   principal's store assignments in gold.account_scope;
2. copy ONLY those stores' rows from the serving views into curated, analyst-friendly
   tables (names and channels joined in, ids kept);
3. detach the publication, switch off file/network access and lock the configuration.

Every query branch, join and aggregate therefore sees only the principal's stores:
the other stores' rows are not in the sandbox at all. Queries are also limited by
time (interrupt) and returned rows (the full input is always aggregated; only the
displayed result is capped, and that cap is reported).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

MAX_ROWS = 200
QUERY_TIMEOUT_S = 20.0

# Curated tables. Each SELECT reads one serving view from the attached publication
# ("biz") and is restricted to the principal's stores via the scoped `stores` table.
TABLE_SQL = OrderedDict([
    ("stores", """SELECT store_id, store_name, channel, region, state, city, segment_tier
        FROM biz.serving.vw_stores WHERE store_id IN (SELECT store_id FROM _scope)"""),
    ("products", """SELECT sku_id, product_name, category, brand, is_msl,
        CAST(unit_cost AS DOUBLE) AS unit_cost, CAST(list_price AS DOUBLE) AS list_price,
        CAST(consumer_price AS DOUBLE) AS consumer_price FROM biz.serving.vw_products"""),
    ("sku_daily", """SELECT d.date, CAST(date_trunc('week', d.date) AS DATE) AS week_start,
        CAST(date_trunc('month', d.date) AS DATE) AS month_start,
        d.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        d.sku_id, p.product_name, p.category, p.brand, d.is_msl,
        d.units_sold_observed AS units_sold, d.inventory_observed AS inventory_units,
        d.oos_observed AS is_oos, CAST(d.consumer_price_observed AS DOUBLE) AS consumer_price,
        d.receipt_units_from_deliveries AS delivered_units
        FROM biz.serving.vw_store_sku_daily d JOIN stores s USING (store_id) JOIN products p USING (sku_id)"""),
    ("store_daily", """SELECT d.date, d.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        d.expected_sku_count, d.known_inventory_sku_count AS skus_with_known_stock, d.oos_sku_count AS skus_oos,
        d.msl_eligible_store_count AS msl_eligible, d.msl_assessable_store_count AS msl_assessable,
        d.msl_compliant_store_count AS msl_compliant, d.reporting_coverage_pct
        FROM biz.serving.vw_store_day d JOIN stores s USING (store_id)"""),
    ("weekly_flow", """SELECT c.period_start AS week_start, c.period_end AS week_end, c.period_is_complete AS is_complete_week,
        c.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        c.sku_id, p.product_name, p.category,
        c.sell_in_units_available AS sell_in_units, c.sell_out_units_available AS sell_out_units,
        c.reported_sell_out_days, c.expected_sku_days,
        CAST(c.invoice_gross_sales AS DOUBLE) AS gross_sales, CAST(c.invoice_net_sales AS DOUBLE) AS net_sales
        FROM biz.serving.vw_commercial_period c JOIN stores s USING (store_id) JOIN products p USING (sku_id)
        WHERE c.period_type = 'week'"""),
    ("monthly_flow", """SELECT c.period_start AS month_start, c.period_end AS month_end, c.period_is_complete AS is_complete_month,
        c.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        c.sku_id, p.product_name, p.category,
        c.sell_in_units_available AS sell_in_units, c.sell_out_units_available AS sell_out_units,
        c.reported_sell_out_days, c.expected_sku_days,
        CAST(c.invoice_gross_sales AS DOUBLE) AS gross_sales, CAST(c.invoice_net_sales AS DOUBLE) AS net_sales
        FROM biz.serving.vw_commercial_period c JOIN stores s USING (store_id) JOIN products p USING (sku_id)
        WHERE c.period_type = 'month'"""),
    ("sell_in", """SELECT e.transaction_id, e.date AS invoice_date, e.store_id, s.store_name, s.channel, s.region,
        s.state, s.city, s.segment_tier, e.sku_id, p.product_name, p.category, e.units_sold AS units,
        CAST(e.gross_sales AS DOUBLE) AS gross_sales, CAST(e.discount_pct AS DOUBLE) AS discount_pct,
        CAST(e.extra_discount_pct AS DOUBLE) AS extra_discount_pct, CAST(e.net_sales AS DOUBLE) AS net_sales,
        e.promo_id, e.guardrail_violation
        FROM biz.serving.vw_sell_in_events e JOIN stores s USING (store_id) JOIN products p USING (sku_id)"""),
    ("shelf_audits", """SELECT v.audit_date, v.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        p.sku_id, p.product_name, p.category, CAST(v.shelf_allocation_pct AS DOUBLE) AS shelf_share_pct,
        v.compliant_audit_row_count > 0 AS planogram_compliant, v.secondary_display_row_count > 0 AS has_secondary_display
        FROM biz.serving.vw_execution_visits v JOIN stores s USING (store_id)
        JOIN products p ON p.sku_id = replace(v.sku_scope_id, 'sku:', '')
        WHERE v.scope_label = 'Individual audited SKU'"""),
    ("deliveries", """SELECT d.delivery_id, d.store_id, s.store_name, s.channel, s.region, d.sku_id, p.product_name,
        p.category, d.order_date, d.scheduled_delivery, d.actual_delivery, d.delay_days, d.delivery_status,
        d.units_ordered, d.units_delivered, CAST(d.fill_rate_pct AS DOUBLE) AS fill_rate_pct
        FROM biz.serving.vw_delivery_events d JOIN stores s USING (store_id) JOIN products p USING (sku_id)"""),
    ("promotions", """SELECT promo_id, promo_name, promo_type, category, channel, start_date, end_date,
        CAST(discount_pct AS DOUBLE) AS discount_pct, min_volume_units FROM biz.serving.vw_promotions"""),
])
TABLES = tuple(TABLE_SQL)


class ScopeDenied(PermissionError):
    """The verified principal has no store assignments in this publication."""


@dataclass
class QueryResult:
    frame: pd.DataFrame
    truncated: bool
    elapsed_s: float


@dataclass
class Sandbox:
    principal_id: str
    artifact_id: str
    as_of: date
    dataset_hash: str
    role: str
    store_count: int
    total_stores: int
    scope_fingerprint: str
    con: duckdb.DuckDBPyConnection = field(repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def scope_label(self) -> str:
        if self.store_count == self.total_stores:
            return f"all {self.total_stores} stores"
        return f"your {self.store_count} assigned stores (of {self.total_stores})"

    def values(self, sql: str) -> list:
        with self.lock:
            return [row[0] for row in self.con.execute(sql).fetchall()]

    def run(self, sql: str, *, max_rows: int = MAX_ROWS, timeout_s: float = QUERY_TIMEOUT_S) -> QueryResult:
        """Run already-checked SQL; interrupts after timeout_s and caps the returned rows."""
        import time
        # The sandbox's own connection (a new cursor would not keep `USE sandbox`); the lock
        # serializes queries, so interrupting the connection only stops this query.
        with self.lock:
            timer = threading.Timer(timeout_s, self.con.interrupt)
            started = time.perf_counter()
            timer.start()
            try:
                cursor = self.con.execute(sql)
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchmany(max_rows + 1)
            finally:
                timer.cancel()
        return QueryResult(pd.DataFrame(rows[:max_rows], columns=columns), len(rows) > max_rows,
                           time.perf_counter() - started)


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def build_sandbox(database: Path, record: dict, principal_id: str, scope_fingerprint: str,
                  memory_limit: str = "1GB") -> Sandbox:
    con = duckdb.connect(":memory:", config={"threads": 2, "memory_limit": memory_limit,
                                             "autoinstall_known_extensions": False,
                                             "autoload_known_extensions": False})
    try:
        # Compressed in-memory catalog: about a third of the plain in-memory footprint.
        con.execute("ATTACH ':memory:' AS sandbox (COMPRESS)")
        con.execute("USE sandbox")
        con.execute(f"ATTACH {_literal(database)} AS biz (READ_ONLY)")
        scope = con.execute("SELECT store_id, role FROM biz.gold.account_scope WHERE principal_id = ?",
                            [principal_id]).fetchall()
        if not scope:
            raise ScopeDenied("No configured account scope for this user")
        roles = sorted({role for _, role in scope})
        con.execute("CREATE TEMP TABLE _scope AS SELECT DISTINCT store_id FROM biz.gold.account_scope "
                    "WHERE principal_id = ?", [principal_id])
        total = con.execute("SELECT count(*) FROM biz.serving.vw_stores").fetchone()[0]
        for name, sql in TABLE_SQL.items():
            con.execute(f"CREATE TABLE {name} AS {sql}")
        con.execute("DROP TABLE _scope")
        con.execute("DETACH biz")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
    except Exception:
        con.close()
        raise
    stores = con.execute("SELECT count(*) FROM stores").fetchone()[0]
    return Sandbox(principal_id=principal_id, artifact_id=record["artifact_id"],
                   as_of=date.fromisoformat(str(record["as_of"])), dataset_hash=record["dataset_hash"],
                   role="manager" if roles == ["manager"] else "sales", store_count=stores, total_stores=total,
                   scope_fingerprint=scope_fingerprint, con=con)


class SandboxPool:
    """Keeps a few recent sandboxes; scope is re-checked on every request."""

    def __init__(self, reader, size: int = 3):
        self.reader = reader  # data_platform ServingReader pinned to one accepted publication
        self.size = size
        self._items: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, principal_id: str) -> Sandbox:
        try:
            scope = self.reader.principal_scope(principal_id)
        except PermissionError as error:
            raise ScopeDenied(str(error)) from error
        key = (self.reader.record["artifact_id"], principal_id, scope["scope_fingerprint"])
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        sandbox = build_sandbox(Path(self.reader.database), self.reader.record, principal_id,
                                scope["scope_fingerprint"])
        with self._lock:
            self._items[key] = sandbox
            # Drop stale entries for the same principal (changed scope) and the oldest beyond the size.
            for other in [k for k in self._items if k[1] == principal_id and k != key]:
                self._items.pop(other).con.close()
            while len(self._items) > self.size:
                self._items.popitem(last=False)[1].con.close()
        return sandbox
