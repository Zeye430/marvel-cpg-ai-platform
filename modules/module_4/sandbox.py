"""
modules/module_4/sandbox.py
===========================
Scoped, in-memory analytics sandbox for model-written SQL (Module 4 guide, section 4).

Trusted code (never the model) builds one sandbox per verified principal:

1. materialize approved serving views from the verified DuckDB or PostgreSQL
   reader, restricted to the principal's current account scope;
2. project ONLY that scoped data into curated, analyst-friendly tables (names
   and channels joined in, ids kept), then discard the intermediate inputs.
   Module 2's predictions and stored visit ranking are added only when the
   publication released them and every row is from its selected scoring run;
   Module 2's computed business ranking only when its service supplied it for
   this exact scope and run;
3. switch off file/network access and lock the configuration. No cloud credentials
   or source database connection are available to model-written SQL.

Every query branch, join and aggregate therefore sees only the principal's stores:
the other stores' rows are not in the sandbox at all. Queries are also limited by
time (interrupt) and returned rows (the full input is always aggregated; only the
displayed result is capped, and that cap is reported).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta

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
BUSINESS_VIEWS = ("stores", "products", "store_sku_daily", "store_day", "commercial_period",
                  "sell_in_events", "execution_visits", "delivery_events", "promotions")

# Module 2's published model outputs, only when the publication released them and every scoped row
# comes from its one selected scoring run. Built after `stores`, so the join re-applies the scope.
MODEL_VIEWS = ("oos_predictions", "visit_priority")
PREDICTION_WINDOW_DAYS = 7  # Module 2 target: a stockout within the 7 days after the scoring date
MODEL_TABLE_SQL = OrderedDict([
    ("sku_risk", f"""SELECT o.scoring_run_id, o.scoring_date, o.scoring_date + 1 AS window_start,
        o.scoring_date + {PREDICTION_WINDOW_DAYS} AS window_end,
        o.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        o.sku_id, p.product_name, p.category, p.is_msl,
        o.probability AS predicted_oos_probability, o.threshold AS alert_threshold, o.is_predicted_oos AS is_alert,
        o.model_version, o.input_status
        FROM biz.serving.vw_oos_predictions o JOIN stores s USING (store_id) LEFT JOIN products p USING (sku_id)"""),
    # The stored v1 rank is renumbered inside the principal's stores: the published global position
    # (priority_rank) would reveal other stores' rows to a partial scope, so it is not copied.
    ("store_priority", """SELECT v.scoring_run_id, v.scoring_date,
        CAST(row_number() OVER (ORDER BY v.priority_rank, v.store_id) AS BIGINT) AS stored_rank_in_scope,
        v.store_id, s.store_name, s.channel, s.region, s.state, s.city, s.segment_tier,
        v.risk_score AS risk_index, json_extract_string(v.reason_json, '$.bucket_label') AS priority_bucket,
        CAST(json_extract(v.reason_json, '$.alert_count') AS BIGINT) AS alert_sku_count, v.msl_gap_count,
        CAST(json_extract(v.reason_json, '$.msl_unknown_count') AS BIGINT) AS msl_unknown_count,
        CAST(json_extract(v.reason_json, '$.inventory_unknown_count') AS BIGINT) AS inventory_unknown_count,
        CAST(json_extract(v.reason_json, '$.sku_count') AS BIGINT) AS scored_sku_count, v.ranking_policy_version
        FROM biz.serving.vw_visit_priority v JOIN stores s USING (store_id)"""),
])
# Module 2's computed business ranking, copied from the Module 2 service (never recomputed here).
BUSINESS_RANKING_COLUMNS = (
    ("store_id", "VARCHAR"), ("store_name", "VARCHAR"), ("channel", "VARCHAR"), ("region", "VARCHAR"),
    ("business_rank", "BIGINT"), ("priority_score", "DOUBLE"), ("urgency", "VARCHAR"), ("risk_category", "VARCHAR"),
    ("score_missing", "VARCHAR"), ("stored_rank_in_scope", "BIGINT"), ("scoring_run_id", "VARCHAR"),
    ("scoring_date", "DATE"), ("ranking_policy_version", "VARCHAR"), ("policy_status", "VARCHAR"))

UNRELEASED = ("Stockout predictions and the stored visit ranking are not released in this publication "
              "(model output status: {status}). Missing predictions are not zero risk.")
NOT_PUBLISHED = "This publication does not include the prediction and ranking views."
NO_SELECTED_RUN = "The publication names no selected scoring run, so predictions cannot be attributed to one run."
MISMATCHED_RUNS = ("The published predictions and ranking do not all come from the publication's selected scoring "
                   "run and model; prediction answers are unavailable rather than mixing runs.")
NO_BUSINESS_RANKING = ("Module 2's business-priority ranking is not connected to the copilot here; only the stored "
                       "v1 visit ranking is available.")


def model_outputs_info(record: dict, reason: str | None = None) -> dict:
    """Release metadata for answers: status, run, scoring date, prediction window and model."""
    outputs = record.get("model_outputs") or {}
    scoring = outputs.get("scoring_date")
    start = end = None
    if scoring:
        start = date.fromisoformat(str(scoring)) + timedelta(days=1)
        end = date.fromisoformat(str(scoring)) + timedelta(days=PREDICTION_WINDOW_DAYS)
    return {"status": record.get("model_outputs_status") or "Unavailable_model_not_released",
            "publication_kind": outputs.get("publication_kind"), "synthetic": bool(outputs.get("synthetic")),
            "model_version": outputs.get("model_version"), "scoring_run_id": outputs.get("scoring_run_id"),
            "scoring_date": str(scoring) if scoring else None,
            "window_start": str(start) if start else None, "window_end": str(end) if end else None,
            "ranking_policy_version": outputs.get("ranking_policy_version"),
            "eligibility_status": outputs.get("eligibility_status"),
            "available": False, "reason": reason,
            "business_ranking": {"available": False, "reason": NO_BUSINESS_RANKING}}


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
    tables: tuple = TABLES  # what model-written SQL may read (the guard allowlist)
    # model_outputs_info() with this sandbox's availability; by default nothing released.
    model: dict = field(default_factory=lambda: model_outputs_info({}, reason=UNRELEASED.format(
        status="Unavailable_model_not_released")))

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


def _model_tables(con, model: dict) -> bool:
    """Create the prediction/ranking tables if every scoped row is from the selected run and model."""
    expected = (model["scoring_run_id"], model["scoring_date"])
    runs = set(con.execute("SELECT DISTINCT scoring_run_id, CAST(scoring_date AS VARCHAR) FROM input.vw_oos_predictions "
                           "UNION SELECT DISTINCT scoring_run_id, CAST(scoring_date AS VARCHAR) "
                           "FROM input.vw_visit_priority").fetchall())
    versions = {r[0] for r in con.execute("SELECT DISTINCT model_version FROM input.vw_oos_predictions").fetchall()}
    if runs - {expected} or versions - {model["model_version"]}:
        model["reason"] = MISMATCHED_RUNS
        return False
    for name, sql in MODEL_TABLE_SQL.items():
        con.execute(f"CREATE TABLE {name} AS {sql.replace('biz.serving.', 'input.')}")
    model["available"] = True
    return True


def _business_ranking_table(con, model: dict, provider, principal_id, scope_fingerprint) -> bool:
    """Copy Module 2's computed ranking for this principal, if it matches this sandbox exactly."""
    business = model["business_ranking"]
    if provider is None:
        return False
    try:
        ranking = provider(principal_id)
    except Exception as error:  # noqa: BLE001 - the stored ranking still answers; say why this one cannot
        business["reason"] = (f"Module 2's business-priority ranking could not be read ({type(error).__name__}); "
                              "only the stored v1 visit ranking is available.")
        return False
    rows = ranking["rows"]
    ranked_runs = {(r["scoring_run_id"], r["scoring_date"]) for r in rows if r["stored_rank_in_scope"] is not None}
    scoped = {r[0] for r in con.execute("SELECT store_id FROM stores").fetchall()}
    if (ranking["scope_fingerprint"] != scope_fingerprint or ranking["scoring_run_id"] != model["scoring_run_id"]
            or ranking["model_version"] != model["model_version"]
            or ranked_runs - {(model["scoring_run_id"], model["scoring_date"])}
            or {r["store_id"] for r in rows} != scoped):
        business["reason"] = ("Module 2's business-priority ranking did not match this data scope or scoring run, "
                              "so it is not shown; only the stored v1 visit ranking is available.")
        return False
    frame = pd.DataFrame([[r[c] for c, _ in BUSINESS_RANKING_COLUMNS] for r in rows],
                         columns=[c for c, _ in BUSINESS_RANKING_COLUMNS], dtype=object)
    con.register("_business", frame)
    try:
        casts = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in BUSINESS_RANKING_COLUMNS)
        con.execute(f"CREATE TABLE business_priority AS SELECT {casts} FROM _business")
    finally:
        con.unregister("_business")
    business.update(available=True, reason=None, policy_version=ranking["policy_version"],
                    policy_status=ranking["policy_status"])
    return True


def build_sandbox(reader, principal_id: str, scope_fingerprint: str,
                  memory_limit: str = "1GB", business_ranking=None) -> Sandbox:
    """``business_ranking(principal_id)``: optional Module 2 adapter (see shared.runtime)."""
    from data_platform.scoped_cache import CATALOG, materialize_business_views
    model = model_outputs_info(reader.record)
    if model["status"] != "Available":
        model["reason"] = UNRELEASED.format(status=model["status"])
    elif not set(MODEL_VIEWS) <= set(reader.schema()):
        model["reason"] = NOT_PUBLISHED
    elif not model["scoring_run_id"] or not model["scoring_date"]:
        model["reason"] = NO_SELECTED_RUN
    # Business data never depends on the model outputs: they are only read when released.
    names = BUSINESS_VIEWS + (MODEL_VIEWS if model["reason"] is None else ())
    cache = materialize_business_views(reader, principal_id, names, memory_limit=memory_limit)
    con = cache.con
    record = cache.record
    tables = list(TABLES)
    try:
        if cache.scope_fingerprint != scope_fingerprint:
            raise ScopeDenied("Account scope changed during sandbox construction")
        scope = cache.check_access()
        scope_role = "manager" if scope["roles"] == ["manager"] else "sales"
        store_count = scope["stores"]
        # This count is metadata, not unrestricted store rows.
        total = reader.record.get("rows", {}).get("stores", store_count)
        # Compressed in-memory catalog: about a third of the plain in-memory footprint.
        con.execute("ATTACH ':memory:' AS sandbox (COMPRESS)")
        con.execute("USE sandbox")
        con.execute("CREATE SCHEMA input")
        for name in names:
            con.execute(f'CREATE VIEW input.vw_{name} AS SELECT * FROM {CATALOG}.main."{name}"')
        con.execute("CREATE TEMP TABLE _scope AS SELECT store_id FROM input.vw_stores")
        for name, sql in TABLE_SQL.items():
            con.execute(f"CREATE TABLE {name} AS {sql.replace('biz.serving.', 'input.')}")
        if model["reason"] is None and _model_tables(con, model):
            tables += MODEL_TABLE_SQL
            if _business_ranking_table(con, model, business_ranking, principal_id, scope_fingerprint):
                tables.append("business_priority")
        con.execute("DROP TABLE _scope")
        con.execute("DROP SCHEMA input CASCADE")
        for name in names:
            con.execute(f'DROP TABLE {CATALOG}.main."{name}"')
        con.execute(f"DETACH {CATALOG}")
        con.execute("CHECKPOINT sandbox")  # applies the in-memory compression
        cache.check_access()
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
    except Exception:
        con.close()
        raise
    stores = con.execute("SELECT count(*) FROM stores").fetchone()[0]
    return Sandbox(principal_id=principal_id, artifact_id=record["artifact_id"],
                   as_of=date.fromisoformat(str(record["as_of"])), dataset_hash=record["dataset_hash"],
                   role=scope_role, store_count=stores, total_stores=total,
                   scope_fingerprint=scope_fingerprint, con=con, tables=tuple(tables), model=model)


class SandboxPool:
    """Keeps a few recent sandboxes; scope is re-checked on every request.

    One build per (publication, principal, scope fingerprint): concurrent requests of the
    same principal wait for it instead of building twice. Evicted sandboxes are closed
    under their own query lock, so a query that is still running finishes (or times out)
    first; a later request for that principal builds a fresh sandbox.
    """

    def __init__(self, reader, size: int = 3, business_ranking=None):
        self.reader = reader  # verified shared reader pinned to one accepted publication
        self.business_ranking = business_ranking  # optional Module 2 ranking adapter (same reader)
        self.size = size
        self._items: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._building: dict = {}

    @staticmethod
    def _close(sandbox):
        with sandbox.lock:
            sandbox.con.close()

    def get(self, principal_id: str) -> Sandbox:
        try:
            scope = self.reader.principal_scope(principal_id)
            self.reader.read("stores", principal_id, limit=1)
        except PermissionError as error:
            raise ScopeDenied(str(error)) from error
        key = (self.reader.record["artifact_id"], principal_id, scope["scope_fingerprint"])
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            build_lock = self._building.setdefault(key, threading.Lock())
        evicted = []
        with build_lock:
            try:
                with self._lock:
                    if key in self._items:
                        return self._items[key]
                sandbox = build_sandbox(self.reader, principal_id, scope["scope_fingerprint"],
                                        business_ranking=self.business_ranking)
                with self._lock:
                    self._items[key] = sandbox
                    # Drop stale entries for the same principal (changed scope) and the oldest beyond the size.
                    evicted += [self._items.pop(k) for k in list(self._items) if k[1] == principal_id and k != key]
                    while len(self._items) > self.size:
                        evicted.append(self._items.popitem(last=False)[1])
            finally:
                with self._lock:
                    self._building.pop(key, None)
        for old in evicted:
            self._close(old)
        return sandbox
