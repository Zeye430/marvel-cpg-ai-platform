"""Typed facts and common relational foundations; no imputations or model labels."""

from .ingestion import identifier as ident, literal


def cast_expression(column, contract):
    name, dtype = ident(column["name"]), column["type"]
    value = f"nullif({name}, '')"
    if dtype == "BOOLEAN":
        true = ", ".join(literal(t) for t in contract["boolean_tokens"]["true"])
        false = ", ".join(literal(t) for t in contract["boolean_tokens"]["false"])
        return f"CASE WHEN {name} IN ({true}) THEN true WHEN {name} IN ({false}) THEN false END"
    return f"try_cast({value} AS {dtype})"


def typed_sources(connection, config, contract, quality, build_id):
    cutoff = literal(config.as_of)
    for source in contract["sources"]:
        name = source["name"]
        bronze = "bronze." + ident(name)
        for column in source["columns"]:
            field, dtype = ident(column["name"]), column["type"]
            expression = cast_expression(column, contract)
            invalid = f"({field} <> '' AND ({expression}) IS NULL)"
            if dtype == "BIGINT":
                invalid += f" OR ({field} <> '' AND NOT regexp_full_match({field}, '[+-]?[0-9]+'))"
            elif dtype == "DATE":
                invalid += f" OR ({field} <> '' AND NOT regexp_full_match({field}, '[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'))"
            elif dtype.startswith("DECIMAL"):
                invalid += (f" OR ({field} <> '' AND (NOT regexp_full_match({field}, '[+-]?[0-9]+(\\.[0-9]+)?')"
                            f" OR try_cast({field} AS DECIMAL(38,10)) <> ({expression})))")
            quality.query("type_" + column["name"], name,
                          f"SELECT source_row_number, {field} AS raw_value FROM {bronze} WHERE {invalid}")
        expressions = [f"{cast_expression(c, contract)} AS {ident(c['name'])}" for c in source["columns"]]
        lineage = ("data_version, dataset_hash, source_file, source_file_hash, source_row_number, "
                   f"ingestion_run_id, ingested_at, {literal(build_id)} AS build_id")
        connection.execute(f"CREATE TEMP TABLE _typed_{name} AS SELECT {', '.join(expressions)}, {lineage} FROM {bronze}")
        required = [c["name"] for c in source["columns"] if c["name"] not in source["nullable_columns"]]
        condition = " OR ".join(f"{ident(c)} IS NULL" for c in required)
        quality.query("required_values", name,
                      f"SELECT source_row_number FROM _typed_{name} WHERE {condition}")
        keys = ", ".join(ident(c) for c in source["primary_key"])
        quality.query("natural_key", name,
                      f"SELECT {keys}, count(*) AS duplicate_rows FROM _typed_{name} GROUP BY {keys} HAVING count(*) > 1")
    quality.require_pass()
    # All-file checks above are structural ingestion checks, not test-outcome analysis.
    # Only authorized realized events enter persistent Silver tables.
    for source in contract["sources"]:
        name = source["name"]
        event = source.get("event_date_column")
        restriction = f" WHERE {ident(event)} <= DATE {cutoff}" if event else ""
        known_at = (f"cast({ident(event)} AS TIMESTAMP) + INTERVAL 1 DAY - INTERVAL 1 MICROSECOND"
                    if event else "NULL::TIMESTAMP")
        extra = f", {known_at} AS source_known_at, 'same_day_end_simulation_v1' AS availability_assumption_id"
        if name == "dim_stores":
            extra += ", channel = 'Distributor' AS is_distributor"
        elif name == "dim_guardrails":
            extra += ", 'Requires review' AS rule_status, source_file_hash AS rule_version"
        connection.execute(f"CREATE TABLE silver.{ident(name)} AS SELECT *{extra} FROM _typed_{name}{restriction}")
        if name == "fact_sell_out":
            rows = connection.execute("SELECT count(*) FROM silver.fact_sell_out").fetchone()[0]
            quality.record("authorized_history_nonempty", name, int(rows == 0), checked_count=rows)
    for source in contract["sources"]:
        name = source["name"]
        table = "silver." + ident(name)
        for fk in source.get("foreign_keys", []):
            # The restock FK is tested after future outcomes have been projected away.
            on = " AND ".join(f"a.{ident(a)} = b.{ident(b)}" for a, b in zip(fk["columns"], fk["references"]))
            present = " AND ".join(f"a.{ident(c)} IS NOT NULL" for c in fk["columns"])
            quality.query("fk_" + "_".join(fk["columns"]), name,
                          f"SELECT a.source_row_number FROM {table} a LEFT JOIN silver.{ident(fk['source'])} b ON {on} "
                          f"WHERE {present} AND b.{ident(fk['references'][0])} IS NULL")
        for column in source.get("nonnegative_columns", []):
            quality.query("nonnegative_" + column, name,
                          f"SELECT source_row_number, {ident(column)} FROM {table} WHERE {ident(column)} < 0")
        for column in source.get("percent_columns", []):
            quality.query("percentage_" + column, name,
                          f"SELECT source_row_number, {ident(column)} FROM {table} WHERE {ident(column)} NOT BETWEEN 0 AND 100")
        for column, values in source.get("allowed_values", {}).items():
            allowed = ", ".join(literal(value) for value in values)
            quality.query("allowed_" + column, name,
                          f"SELECT source_row_number, {ident(column)} FROM {table} WHERE {ident(column)} NOT IN ({allowed})")
    quality.require_pass()


def source_business_checks(connection, contract, quality):
    tolerance = contract["quality_tolerances"]
    quality.query("date_interval", "dim_promotions",
                  "SELECT promo_id FROM silver.dim_promotions WHERE start_date > end_date")
    quality.query("invoice_monday", "fact_sell_in",
                  "SELECT transaction_id FROM silver.fact_sell_in WHERE isodow(date) <> 1")
    quality.query("gross_arithmetic", "fact_sell_in", f"""
        SELECT transaction_id FROM silver.fact_sell_in i JOIN silver.dim_products p USING (sku_id)
        WHERE abs(i.gross_sales - i.units_sold * p.list_price) > {tolerance['currency_absolute']}
    """, tolerance=tolerance["currency_absolute"])
    quality.query("net_arithmetic", "fact_sell_in", f"""
        SELECT transaction_id FROM silver.fact_sell_in
        WHERE abs(net_sales - gross_sales * (1 - discount_pct / 100.0)) > {tolerance['currency_absolute']}
    """, tolerance=tolerance["currency_absolute"])
    quality.query("promo_scope", "dim_promotions", """
        SELECT p.promo_id FROM silver.dim_promotions p
        LEFT JOIN silver.dim_guardrails g USING (channel, category) WHERE g.channel IS NULL
    """)
    quality.query("promo_scope_discount", "fact_sell_out", """
        SELECT o.date, o.store_id, o.sku_id FROM silver.fact_sell_out o
        JOIN silver.dim_promotions p USING (promo_id)
        JOIN silver.dim_stores s USING (store_id) JOIN silver.dim_products g USING (sku_id)
        WHERE o.date NOT BETWEEN p.start_date AND p.end_date OR s.channel <> p.channel
           OR g.category <> p.category OR o.promo_discount_pct <> p.discount_pct
    """)
    quality.query("no_promo_discount", "fact_sell_out", """
        SELECT date, store_id, sku_id FROM silver.fact_sell_out
        WHERE promo_id IS NULL AND promo_discount_pct <> 0
    """)
    # Consumer prices vary independently of the product reference price in this simulation.
    # No unsupplied regular-price history is invented to assert an exact retail-price identity.
    quality.query("oos_inventory", "fact_sell_out", """
        SELECT date, store_id, sku_id FROM silver.fact_sell_out
        WHERE is_out_of_stock <> (inventory_level = 0)
    """)
    quality.query("restock_presence", "fact_sell_out", """
        SELECT date, store_id, sku_id FROM silver.fact_sell_out
        WHERE (restock_units > 0) <> (restock_delivery_id IS NOT NULL)
    """)
    quality.query("restock_once", "fact_sell_out", """
        SELECT restock_delivery_id, count(*) AS references FROM silver.fact_sell_out
        WHERE restock_delivery_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1
    """)
    quality.query("restock_event_match", "fact_sell_out", """
        SELECT o.date, o.store_id, o.sku_id, o.restock_delivery_id FROM silver.fact_sell_out o
        JOIN silver.fact_deliveries d ON o.restock_delivery_id = d.delivery_id
        WHERE o.date <> d.actual_delivery OR o.store_id <> d.store_id
           OR o.sku_id <> d.sku_id OR o.restock_units <> d.units_delivered
    """)
    quality.query("delivery_dates", "fact_deliveries", """
        SELECT delivery_id FROM silver.fact_deliveries
        WHERE order_date > actual_delivery OR order_date > scheduled_delivery
           OR delay_days <> date_diff('day', scheduled_delivery, actual_delivery)
    """)
    quality.query("delivery_labels", "fact_deliveries", """
        SELECT delivery_id FROM silver.fact_deliveries
        WHERE delivery_status <> (
            CASE WHEN delay_days < 0 THEN 'Early' WHEN delay_days = 0 THEN 'On Time' ELSE 'Late' END
            || CASE WHEN units_delivered < units_ordered THEN ' / Short Ship' ELSE '' END)
           OR (delay_days < 0 AND delay_days <> -1)
    """)
    quality.query("fill_arithmetic", "fact_deliveries", f"""
        SELECT delivery_id FROM silver.fact_deliveries WHERE units_ordered > 0 AND
        abs(fill_rate_pct - 100.0 * units_delivered / units_ordered) > {tolerance['fill_rate_absolute_percentage_points']}
    """, tolerance=tolerance["fill_rate_absolute_percentage_points"])
    quality.query("zero_order_review", "fact_deliveries", """
        SELECT delivery_id FROM silver.fact_deliveries WHERE units_ordered = 0
    """, note="Zero-order deliveries need an explicit source convention before acceptance.")
    quality.query("shelf_normalization", "fact_execution_audits", f"""
        SELECT a.store_id, a.date, p.category, sum(a.shelf_share_pct) AS allocated_pct
        FROM silver.fact_execution_audits a JOIN silver.dim_products p USING (sku_id)
        GROUP BY 1,2,3 HAVING abs(sum(a.shelf_share_pct) - 100) > {tolerance['shelf_sum_absolute_percentage_points']}
    """, tolerance=tolerance["shelf_sum_absolute_percentage_points"])
    quality.record("policy_approval", "dim_guardrails", 1, checked_count=1, severity="warning",
                   note="Retained source rules require review; inventory analytics may proceed.")
    quality.require_pass()


def shared_relations(connection, config, contract, quality, build_id):
    start, end = literal(config.calendar_start), literal(config.as_of)
    build = literal(build_id)
    connection.execute(f"""
        CREATE TABLE silver.order_schedule_events AS
        SELECT {', '.join(ident(c) for c in next(s for s in contract['sources'] if s['name'] == 'fact_deliveries')['planned_projection_columns'])},
               dataset_hash, data_version, source_file_hash, source_row_number, build_id,
               'initial_snapshot_v1' AS schedule_version,
               cast(order_date AS TIMESTAMP) AS source_known_at,
               'initial_schedule_known_at_order_simulation_v1' AS availability_assumption_id
        FROM _typed_fact_deliveries WHERE order_date <= DATE {end}
    """)
    quality.query("planned_fk", "order_schedule_events", """
        SELECT o.delivery_id FROM silver.order_schedule_events o
        LEFT JOIN silver.dim_stores s USING (store_id) LEFT JOIN silver.dim_products p USING (sku_id)
        WHERE s.store_id IS NULL OR p.sku_id IS NULL
    """)
    quality.query("planned_values", "order_schedule_events", """
        SELECT delivery_id FROM silver.order_schedule_events
        WHERE units_ordered < 0 OR order_date > scheduled_delivery
    """)
    # The complete static simulated assortment is explicitly labeled; first-observation
    # availability remains visible so historical models can exclude unconfirmed membership.
    connection.execute(f"""
        CREATE TABLE silver.active_assortment AS
        SELECT store_id, sku_id, DATE {start} AS effective_from, DATE {end} AS effective_to,
               min(date) AS first_observed_date, max(date) AS last_observed_date,
               cast(min(date) AS TIMESTAMP) + INTERVAL 1 DAY - INTERVAL 1 MICROSECOND AS source_known_at,
               'static_simulation_assortment_v1' AS availability_assumption_id,
               dataset_hash, data_version, {build} AS build_id,
               'fact_sell_out; authorized cutoff only' AS membership_provenance
        FROM silver.fact_sell_out GROUP BY store_id, sku_id, dataset_hash, data_version
    """)
    quality.query("audit_active_pair", "fact_execution_audits", """
        SELECT a.audit_id FROM silver.fact_execution_audits a
        LEFT JOIN silver.active_assortment s USING (store_id, sku_id) WHERE s.store_id IS NULL
    """)
    quality.query("delivery_active_pair", "fact_deliveries", """
        SELECT d.delivery_id FROM silver.fact_deliveries d
        LEFT JOIN silver.active_assortment s USING (store_id, sku_id) WHERE s.store_id IS NULL
    """)
    connection.execute("""
        CREATE TABLE silver.replenishment_events AS
        SELECT d.delivery_id, d.store_id, d.sku_id, d.actual_delivery AS receipt_date,
               d.units_delivered AS receipt_units, o.restock_delivery_id IS NOT NULL AS has_observed_restock,
               o.date AS corroborating_sell_out_date, o.source_row_number AS sell_out_source_row_number,
               d.dataset_hash, d.data_version, d.build_id, d.source_file_hash, d.source_row_number,
               d.source_known_at, d.availability_assumption_id,
               CASE WHEN o.restock_delivery_id IS NOT NULL THEN 'Linked observed restock'
                    WHEN r.date IS NULL THEN 'Sell-out not reported'
                    WHEN d.units_delivered = 0 THEN 'Zero-unit delivery'
                    ELSE 'Unexplained' END AS reconciliation_status
        FROM silver.fact_deliveries d
        LEFT JOIN silver.fact_sell_out o ON d.delivery_id = o.restock_delivery_id
        LEFT JOIN silver.fact_sell_out r ON d.store_id = r.store_id AND d.sku_id = r.sku_id
                                         AND d.actual_delivery = r.date
    """)
    quality.query("unlinked_receipt_explained", "replenishment_events", """
        SELECT delivery_id FROM silver.replenishment_events WHERE reconciliation_status = 'Unexplained'
    """)
    connection.execute(f"""
        CREATE TABLE silver.store_sku_calendar AS
        WITH store_days AS (SELECT DISTINCT date, store_id FROM silver.fact_sell_out)
        SELECT cast(d.day AS DATE) AS date, a.store_id, a.sku_id,
               o.date IS NOT NULL AS is_sku_day_reported,
               r.date IS NOT NULL AS is_store_day_reported,
               cast(d.day AS DATE) >= a.first_observed_date AS assortment_is_confirmed_asof,
               o.units_sold_daily AS units_sold_observed, o.inventory_level AS inventory_observed,
               o.is_out_of_stock AS oos_observed, o.restock_units AS restock_units_observed,
               o.source_row_number AS sell_out_source_row_number,
               o.source_file_hash AS sell_out_source_file_hash,
               a.dataset_hash, a.data_version, a.build_id, a.availability_assumption_id
        FROM silver.active_assortment a
        CROSS JOIN generate_series(DATE {start}, DATE {end}, INTERVAL 1 DAY) d(day)
        LEFT JOIN silver.fact_sell_out o ON o.date = cast(d.day AS DATE)
            AND o.store_id = a.store_id AND o.sku_id = a.sku_id
        LEFT JOIN store_days r ON r.date = cast(d.day AS DATE) AND r.store_id = a.store_id
    """)
    quality.query("calendar_grain", "store_sku_calendar", """
        SELECT date, store_id, sku_id FROM silver.store_sku_calendar GROUP BY 1,2,3 HAVING count(*) <> 1
    """)
    expected = connection.execute(f"""
        SELECT count(*) * (date_diff('day', DATE {start}, DATE {end}) + 1)
        FROM silver.active_assortment
    """).fetchone()[0]
    actual = connection.execute("SELECT count(*) FROM silver.store_sku_calendar").fetchone()[0]
    quality.record("calendar_cardinality", "store_sku_calendar", abs(actual - expected), checked_count=expected)
    quality.query("missing_stays_null", "store_sku_calendar", """
        SELECT date, store_id, sku_id FROM silver.store_sku_calendar WHERE NOT is_sku_day_reported
        AND (units_sold_observed IS NOT NULL OR inventory_observed IS NOT NULL
             OR oos_observed IS NOT NULL OR restock_units_observed IS NOT NULL)
    """)
    quality.query("complete_reported_assortment", "store_sku_calendar", """
        SELECT date, store_id, sku_id FROM silver.store_sku_calendar
        WHERE is_store_day_reported AND NOT is_sku_day_reported
    """)
    connection.execute("""
        CREATE TABLE silver.inventory_reconciliation AS
        WITH history AS (
            SELECT *, lag(date) OVER w AS previous_report_date,
                   lag(inventory_level) OVER w AS previous_inventory
            FROM silver.fact_sell_out WINDOW w AS (PARTITION BY store_id, sku_id ORDER BY date)
        )
        SELECT date, store_id, sku_id, previous_report_date, previous_inventory,
               units_sold_daily, restock_units, inventory_level,
               previous_report_date = date - 1 AS is_consecutive,
               CASE WHEN previous_report_date = date - 1
                    THEN inventory_level - (previous_inventory - units_sold_daily + restock_units) END AS residual_units,
               dataset_hash, data_version, build_id, source_file_hash, source_row_number,
               source_known_at, availability_assumption_id
        FROM history
    """)
    quality.query("consecutive_inventory_identity", "inventory_reconciliation", """
        SELECT date, store_id, sku_id, residual_units FROM silver.inventory_reconciliation
        WHERE is_consecutive AND residual_units <> 0
    """, tolerance=0)
    connection.execute(f"""
        CREATE TABLE silver.promotion_calendar AS
        SELECT cast(d.day AS DATE) AS date, p.channel, p.category, p.promo_id,
               p.discount_pct, p.min_volume_units, p.dataset_hash, p.data_version, p.build_id,
               p.source_file_hash, p.source_row_number,
               cast(p.start_date AS TIMESTAMP) AS source_known_at,
               'campaign_known_at_start_simulation_v1' AS availability_assumption_id,
               count(*) OVER (PARTITION BY cast(d.day AS DATE), channel, category) > 1 AS has_overlap
        FROM silver.dim_promotions p,
             LATERAL generate_series(greatest(start_date, DATE {start}),
                                     least(end_date, DATE {end}), INTERVAL 1 DAY) d(day)
    """)
    quality.require_pass()
    tables = ("order_schedule_events", "active_assortment", "replenishment_events",
              "store_sku_calendar", "inventory_reconciliation", "promotion_calendar")
    for name in tables:
        column_names = [r[0] for r in connection.execute(f"DESCRIBE silver.{name}").fetchall()]
        quality.record("build_lineage", name, int("build_id" not in column_names or "dataset_hash" not in column_names))
    quality.require_pass()
