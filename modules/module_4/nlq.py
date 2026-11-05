"""
modules/module_4/nlq.py
=======================
Component A: natural-language questions -> SQL -> answer (+ chart).

Flow: prompt with the sandbox schema, shared KPI formulas and fixed date ranges
-> model writes one SELECT -> sql_guard checks it -> the scoped sandbox runs it
(time and row limits) -> on an error the model gets the message and one or two
chances to fix the SQL -> the model explains the result using only its numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
import pandas as pd

from .formatting import display_frame
from .sandbox import Sandbox
from .sql_guard import UnsafeSQL, answer_sources, check_sql, extract_sql

MAX_REPAIRS = 2
SUMMARY_ROWS = 15     # rows of a result the summary model sees

SCHEMA = """TABLES (DuckDB). "store cols" = store_id, store_name, channel, region, state, city, segment_tier.
"product cols" = sku_id, product_name, category.

sku_daily: one row per store x product x day. Sell-out and stock.
  date, week_start (Monday), month_start, store cols, product cols, brand, is_msl (must-stock SKU, BOOLEAN),
  units_sold (consumer units sold; NULL = day not reported), inventory_units (NULL = unknown),
  is_oos (TRUE = out of stock, FALSE = in stock, NULL = unknown), consumer_price, delivered_units
store_daily: one row per store x day. date, store cols, msl_assessable (1/0), msl_compliant (1/0),
  skus_oos, skus_with_known_stock, reporting_coverage_pct
weekly_flow: one row per store x product x week (Monday to Sunday). week_start, week_end, is_complete_week,
  store cols, product cols, sell_in_units (units invoiced to the store), sell_out_units (consumer units sold on
  reported days), gross_sales, net_sales
monthly_flow: same as weekly_flow per calendar month: month_start, month_end, is_complete_month, ...
sell_in: one row per invoice line. invoice_date, store cols, product cols, units, gross_sales, discount_pct,
  extra_discount_pct, net_sales, promo_id, guardrail_violation (BOOLEAN)
shelf_audits: one row per audited SKU per store visit. audit_date, store cols, product cols,
  shelf_share_pct (% of the category shelf held by that SKU, 0-100), planogram_compliant (BOOLEAN),
  has_secondary_display (BOOLEAN)
deliveries: one row per delivery. store_id, store_name, channel, region, product cols, order_date,
  scheduled_delivery, actual_delivery, delay_days, delivery_status, units_ordered, units_delivered, fill_rate_pct
stores: store cols.   products: sku_id, product_name, category, brand, is_msl, unit_cost, list_price, consumer_price
promotions: promo_id, promo_name, promo_type, category, channel, start_date, end_date, discount_pct, min_volume_units"""

METRICS = """METRICS (shared team definitions; use exactly these):
- OOS rate % = 100.0 * COUNT(*) FILTER (WHERE is_oos) / NULLIF(COUNT(is_oos), 0) on sku_daily.
  Unknown days (is_oos NULL) are excluded. "highest OOS", "most out of stock" = highest OOS rate.
- Sell-out units = SUM(units_sold) on sku_daily, or SUM(sell_out_units) on weekly_flow / monthly_flow.
- Sell-in units = SUM(sell_in_units) on weekly_flow / monthly_flow, or SUM(units) on sell_in.
  To compare sell-in with sell-out, use weekly_flow (or monthly_flow) so both cover the same weeks.
- Shelf share % = AVG(shelf_share_pct) on shelf_audits.
- Planogram compliance % = 100.0 * AVG(CASE WHEN planogram_compliant THEN 1 ELSE 0 END) on shelf_audits
  (secondary display rate: the same with has_secondary_display).
- MSL compliance % = 100.0 * SUM(msl_compliant) / NULLIF(SUM(msl_assessable), 0) on store_daily.
- Trade discount % = 100.0 * SUM(gross_sales - net_sales) / NULLIF(SUM(gross_sales), 0) on sell_in.
- Invoiced revenue = SUM(net_sales) on sell_in.
- Fill rate % = 100.0 * SUM(units_delivered) / NULLIF(SUM(units_ordered), 0) on deliveries.
  Late (only when the question asks about late or delayed deliveries): delay_days > 0.
- Never average a percentage column across groups (except shelf_share_pct, which is already per audit)."""

RULES = """RULES:
- Write ONE DuckDB SELECT query (CTEs allowed). Use only the tables above, with no schema prefix.
- Text values are case-sensitive: use the exact values listed. For product or store names use ILIKE '%...%'.
- Add only the filters the question asks for (no extra status, channel or category filters).
- To count stores or products, use the stores / products tables or COUNT(DISTINCT store_id / sku_id);
  COUNT(*) on a daily or weekly table counts rows, not stores.
- Return readable names (store_name, product_name) next to ids.
- Round percentages to 1 decimal and money to 2 decimals.
- "Top N", "highest", "lowest", "best", "worst": ORDER BY the measure and LIMIT N (5 if N is not given).
  For "which stores/products ..." lists, return every matching row (no LIMIT), sorted by the key measure.
- For a rate per group, also return its denominator (e.g. known_sku_days) so the answer can be checked.
- Return one row per item asked about (store, product, region, ...; use GROUP BY or a single day), never one
  row per day, unless the question asks for a trend over time ("by day", "by week", "trend", "over time").
- A word that is a listed value (category, region, channel, tier, ...) filters that column, never a name: Laundry
  means category = 'Laundry'. Use ILIKE on a name only for a specific product or store the question names, with
  the question's own words; never a placeholder.
- Reply with the SQL only, inside ```sql ... ```."""

EXAMPLES = [
    ("Which 3 regions had the highest OOS rate for must-stock (MSL) products last month?",
     """SELECT region,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_oos) / NULLIF(COUNT(is_oos), 0), 1) AS oos_rate_pct,
       COUNT(is_oos) AS known_sku_days
FROM sku_daily
WHERE is_msl AND date BETWEEN DATE '{last_month_start}' AND DATE '{last_month_end}'
GROUP BY region
ORDER BY oos_rate_pct DESC
LIMIT 3"""),
    ("Which Distributor stores invoiced fewer units than they sold over the last 4 weeks?",
     """SELECT store_id, store_name, SUM(sell_in_units) AS sell_in_units, SUM(sell_out_units) AS sell_out_units,
       SUM(sell_in_units) - SUM(sell_out_units) AS gap_units
FROM weekly_flow
WHERE channel = 'Distributor' AND week_start BETWEEN DATE '{w4_start}' AND DATE '{w4_last}'
GROUP BY store_id, store_name
HAVING SUM(sell_in_units) < SUM(sell_out_units)
ORDER BY gap_units"""),
    ("Show planogram compliance and average shelf share by channel this month",
     """SELECT channel,
       ROUND(100.0 * AVG(CASE WHEN planogram_compliant THEN 1 ELSE 0 END), 1) AS planogram_compliance_pct,
       ROUND(AVG(shelf_share_pct), 1) AS avg_shelf_share_pct,
       COUNT(*) AS audited_skus
FROM shelf_audits
WHERE audit_date BETWEEN DATE '{this_month_start}' AND DATE '{as_of}'
GROUP BY channel
ORDER BY channel"""),
    ("When does the 2025 Holiday Bundle run, and what discount does it give?",
     """SELECT promo_name, category, channel, start_date, end_date, discount_pct
FROM promotions
WHERE promo_name = 'Holiday Bundle' AND year(start_date) = 2025
ORDER BY start_date, category, channel"""),
    ("Which products are out of stock right now in store S001?",
     """SELECT sku_id, product_name, is_msl, inventory_units
FROM sku_daily
WHERE store_id = 'S001' AND is_oos
  AND date = (SELECT MAX(date) FROM sku_daily WHERE store_id = 'S001' AND is_oos IS NOT NULL)
ORDER BY is_msl DESC, product_name"""),
    ("Weekly sell-out units of Dishwashing products over the last 4 weeks",
     """SELECT week_start, SUM(units_sold) AS sell_out_units
FROM sku_daily
WHERE category = 'Dishwashing' AND date BETWEEN DATE '{w4_start}' AND DATE '{w4_end}'
GROUP BY week_start
ORDER BY week_start"""),
]

# Module 2's published outputs: in the prompt only when this sandbox has them (released, one selected run).
MODEL_SCHEMA = """sku_risk: Module 2's published stockout PREDICTIONS, one row per store x product for one scoring run.
  scoring_run_id, scoring_date, window_start, window_end (the predicted period), store cols, product cols, is_msl,
  predicted_oos_probability (0-1 model estimate of a stockout in the window), alert_threshold, is_alert (BOOLEAN,
  the published alert decision), model_version, input_status
store_priority: Module 2's STORED v1 visit ranking, one row per scored store. scoring_run_id, scoring_date,
  stored_rank_in_scope (1 = first visit among the user's stores), store cols, risk_index (highest SKU probability
  in the store, 0-1), priority_bucket, alert_sku_count, msl_gap_count, msl_unknown_count, inventory_unknown_count,
  scored_sku_count, ranking_policy_version"""
BUSINESS_SCHEMA = """business_priority: Module 2's computed BUSINESS-PRIORITY ranking (a proposed policy, different from the
  stored v1 rank), one row per store in the user's scope. store_id, store_name, channel, region, business_rank
  (1 = highest; NULL = not ranked), priority_score (0-100; NULL = unavailable), urgency, risk_category,
  score_missing (secondary components left out of a computed score), sales_at_risk_usd (Module 2's estimate, USD),
  sales_at_risk_status (Complete, or a lower bound or unavailable), stored_rank_in_scope, scoring_run_id, scoring_date, ranking_policy_version, policy_status"""
MODEL_METRICS = """- Predicted stockout probability = predicted_oos_probability on sku_risk; alerts = is_alert. This is a model
  prediction for window_start..window_end, NOT observed stock: observed out-of-stock is is_oos on sku_daily.
  Never call a prediction "out of stock" and never use is_oos to answer a risk or prediction question.
- Store risk index = risk_index on store_priority (highest SKU probability, not a store-level probability).
- Do not compute your own ranking or score from probabilities, sales or shelf data.
- Return predicted_oos_probability, alert_threshold and risk_index unrounded (the app shows them as percentages;
  rounding can turn a small risk into 0).
- Questions about what will happen (next days, upcoming stockouts, what will run out) are prediction questions:
  answer them only from sku_risk / store_priority, never from is_oos or sales history.
- sku_risk holds ONE published window (window_start..window_end, the 7 days after the scoring date): "next 7 days",
  "next week", "soon" all mean that window, so do NOT filter sku_risk on dates. "Will run out" / "at risk" = is_alert
  (order by predicted_oos_probability DESC)."""
STORED_RANK_METRIC = "- Stored priority / visit ranking = stored_rank_in_scope on store_priority (ORDER BY stored_rank_in_scope)."
BUSINESS_METRICS = """- Visit ranking, priority ranking, which store first = business_rank on business_priority (ORDER BY business_rank
  NULLS LAST): the ranking the user sees in Module 2. Use stored_rank_in_scope on store_priority only when the
  question says stored or v1. Priority score = priority_score. Urgent stores = urgency = 'Urgent'.
- Estimated sales at risk (USD, 7-day scenario) = SUM(sales_at_risk_usd) on business_priority, returned with
  COUNT(*) FILTER (WHERE sales_at_risk_status <> 'Complete') AS stores_incomplete. Never estimate it from
  deliveries, sales or probabilities."""
MODEL_EXAMPLES = [
    ("Which SKUs in my stores have a stockout alert?",
     """SELECT store_id, store_name, sku_id, product_name, predicted_oos_probability,
       alert_threshold, window_start, window_end
FROM sku_risk
WHERE is_alert
ORDER BY predicted_oos_probability DESC"""),
    ("Which SKUs will run out in the next 7 days?",
     """SELECT store_id, store_name, sku_id, product_name, predicted_oos_probability, alert_threshold,
       window_start, window_end
FROM sku_risk
WHERE is_alert
ORDER BY predicted_oos_probability DESC"""),
    ("Which SKU should I restock first in store S001?",
     """SELECT sku_id, product_name, is_msl, predicted_oos_probability, is_alert, window_start, window_end
FROM sku_risk
WHERE store_id = 'S001'
ORDER BY is_alert DESC, is_msl DESC, predicted_oos_probability DESC
LIMIT 5"""),
]
STORED_RANK_EXAMPLES = [
    ("Top 5 stores in the visit priority ranking",
     """SELECT stored_rank_in_scope, store_id, store_name, risk_index, alert_sku_count,
       msl_gap_count, scoring_date
FROM store_priority
ORDER BY stored_rank_in_scope
LIMIT 5"""),
]
BUSINESS_EXAMPLES = [
    ("Top 5 stores in the visit priority ranking",
     """SELECT business_rank, store_id, store_name, priority_score, urgency, risk_category
FROM business_priority
ORDER BY business_rank NULLS LAST
LIMIT 5"""),
    ("What is the estimated sales at risk in my stores?",
     """SELECT ROUND(SUM(sales_at_risk_usd), 2) AS est_sales_at_risk_usd,
       COUNT(*) FILTER (WHERE sales_at_risk_status <> 'Complete') AS stores_incomplete
FROM business_priority"""),
    ("Which stores are urgent?",
     """SELECT business_rank, store_id, store_name, urgency, risk_category
FROM business_priority
WHERE urgency = 'Urgent'
ORDER BY business_rank NULLS LAST"""),
]


def date_ranges(as_of: date) -> dict:
    d = as_of
    last_sunday = d - timedelta(days=(d.weekday() + 1) % 7)
    month_start = d.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    q_start_month = 3 * ((d.month - 1) // 3) + 1
    quarter_start = date(d.year, q_start_month, 1)
    last_quarter_end = quarter_start - timedelta(days=1)
    last_quarter_start = date(last_quarter_end.year, 3 * ((last_quarter_end.month - 1) // 3) + 1, 1)
    return {
        "as_of": d, "this_week_start": d - timedelta(days=6), "last_week_start": d - timedelta(days=13),
        "last_week_end": d - timedelta(days=7), "last_28_start": d - timedelta(days=27),
        "w4_start": last_sunday - timedelta(days=27), "w4_last": last_sunday - timedelta(days=6),
        "w4_end": last_sunday, "this_month_start": month_start,
        "last_month_start": last_month_end.replace(day=1), "last_month_end": last_month_end,
        "quarter_start": quarter_start, "last_quarter_start": last_quarter_start,
        "last_quarter_end": last_quarter_end, "year_start": date(d.year, 1, 1),
        "last_year_start": date(d.year - 1, 1, 1), "last_year_end": date(d.year - 1, 12, 31),
        "data_start": date(2025, 1, 1),
    }


def dates_block(r: dict) -> str:
    return f"""DATES: the data ends on {r['as_of']} ("today"); there is no later data. Use these exact ranges:
- today / latest day / right now / currently / at the moment: date = DATE '{r['as_of']}'
  (for one named store, use that store's latest reported day instead:
  date = (SELECT MAX(date) FROM sku_daily WHERE store_id = '...' AND is_oos IS NOT NULL))
- this week / last 7 days: date BETWEEN DATE '{r['this_week_start']}' AND DATE '{r['as_of']}'
- last week: date BETWEEN DATE '{r['last_week_start']}' AND DATE '{r['last_week_end']}'
- last 4 weeks / last 28 days: date BETWEEN DATE '{r['last_28_start']}' AND DATE '{r['as_of']}';
  on weekly_flow use the 4 latest complete weeks: week_start BETWEEN DATE '{r['w4_start']}' AND DATE '{r['w4_last']}'
- this month: BETWEEN DATE '{r['this_month_start']}' AND DATE '{r['as_of']}' (monthly_flow: month_start = DATE '{r['this_month_start']}')
- last month: BETWEEN DATE '{r['last_month_start']}' AND DATE '{r['last_month_end']}'
- this quarter (Q{(r['quarter_start'].month - 1) // 3 + 1} {r['quarter_start'].year}): BETWEEN DATE '{r['quarter_start']}' AND DATE '{r['as_of']}';
  last quarter (the previous one, Q{(r['last_quarter_start'].month - 1) // 3 + 1} {r['last_quarter_start'].year}): BETWEEN DATE '{r['last_quarter_start']}' AND DATE '{r['last_quarter_end']}'
- this year / year to date: BETWEEN DATE '{r['year_start']}' AND DATE '{r['as_of']}'; last year: BETWEEN DATE '{r['last_year_start']}' AND DATE '{r['last_year_end']}'
- no period in the question: use all data ({r['data_start']} to {r['as_of']}).
The ranges are written for sku_daily / store_daily `date`; on other tables apply them to that table's own date
column: sell_in invoice_date, shelf_audits audit_date, deliveries actual_delivery (arrived or delivered) or
order_date (ordered), weekly_flow week_start, monthly_flow month_start, promotions start_date."""


def values_block(sandbox: Sandbox) -> str:
    def listed(sql):
        return ", ".join(f"'{v}'" for v in sandbox.values(sql) if v is not None)
    products = "; ".join(f"{sku} = {name}" for sku, name in zip(
        sandbox.values("SELECT sku_id FROM products ORDER BY sku_id"),
        sandbox.values("SELECT product_name FROM products ORDER BY sku_id")))
    buckets = (f"\n- priority_bucket: {listed('SELECT DISTINCT priority_bucket FROM store_priority ORDER BY 1')}"
               if "store_priority" in sandbox.tables else "")
    if "business_priority" in sandbox.tables:
        buckets += f"\n- urgency: {listed('SELECT DISTINCT urgency FROM business_priority ORDER BY 1')}"
    return f"""VALUES:
- channel: {listed("SELECT DISTINCT channel FROM stores ORDER BY 1")}
- region: {listed("SELECT DISTINCT region FROM stores ORDER BY 1")}
- segment_tier: {listed("SELECT DISTINCT segment_tier FROM stores ORDER BY 1")}
- category: {listed("SELECT DISTINCT category FROM products ORDER BY 1")}
- brand: {listed("SELECT DISTINCT brand FROM products ORDER BY 1")}
- delivery_status: {listed("SELECT DISTINCT delivery_status FROM deliveries ORDER BY 1")}
- promo_type: {listed("SELECT DISTINCT promo_type FROM promotions ORDER BY 1")}
- promo_name (names repeat every year; a year in the question means year(start_date) = that year, not part of the name;
  use no other date filter for when a promotion runs):
  {listed("SELECT DISTINCT promo_name FROM promotions ORDER BY 1")}
- products: {products}{buckets}"""


def system_prompt(sandbox: Sandbox) -> str:
    ranges = date_ranges(sandbox.as_of)
    model = sandbox.model or {}
    schema, metrics, examples = SCHEMA, METRICS, list(EXAMPLES)
    if model.get("available"):
        schema += "\n" + MODEL_SCHEMA
        metrics += "\n" + MODEL_METRICS
        examples += MODEL_EXAMPLES
        if model["business_ranking"]["available"]:
            schema += "\n" + BUSINESS_SCHEMA
            metrics += "\n" + BUSINESS_METRICS
            examples += BUSINESS_EXAMPLES
        else:
            metrics += "\n" + STORED_RANK_METRIC
            examples += STORED_RANK_EXAMPLES
    else:
        schema += ("\nNo stockout predictions or visit ranking are available: do not answer prediction or "
                   "ranking questions from is_oos or sales.")
    examples = "\n\n".join(f"Question: {q}\n```sql\n{sql.format(**ranges)}\n```" for q, sql in examples)
    return ("You translate questions about Marvel CPG's commercial data into one DuckDB SQL query.\n\n"
            f"{schema}\n\n{values_block(sandbox)}\n\n{metrics}\n\n{dates_block(ranges)}\n\n{RULES}\n\n"
            f"EXAMPLES:\n\n{examples}")


SUMMARY_PROMPT = """You are Marvel CPG's commercial copilot. Explain a database result in plain English for a sales team.
Rules:
- Start with the direct answer to the question. Use 2 to 4 short sentences.
- Use only numbers that appear in the result table. Do not calculate new figures and never invent any.
- Name the time period and filters used (they are in the SQL).
- Every row in the result matches the question's filters (for example, every listed product is out of stock).
  Never say that a listed row does not match, and copy names and numbers exactly as they appear.
- With 8 rows or fewer, name every row. With more, name the top few and give the total number of rows.
  Do not mention the row count otherwise. If only the first rows are shown to you, never describe the rows you
  cannot see (no ranges, no "the remaining").
- Columns such as known_sku_days, known_days and audited_skus are supporting counts. known_sku_days is the number
  of SKU-days with known stock, not days out of stock: leave it out or name it exactly.
- is_msl = True means a must-stock (MSL) product and False means not must-stock: describe each row by its own value.
  If the question asks what to do first, say only what the table supports (for example: the must-stock products
  first) and nothing more.
- If stores_incomplete is above 0, write the sales at risk as "at least" that amount and say how many stores have
  incomplete inputs.
- If the result is empty, say that no data matched for this scope and period.
- Do not guess causes or give advice that the data does not show.
- A predicted probability or alert is a model estimate, never an observed stockout. Blank values are unknown,
  not zero. A stored rank and a business rank are different orderings; never say they agree unless the table shows it."""


@dataclass
class NLQResult:
    status: str  # ok | empty | failed
    answer: str
    sql: str | None = None
    frame: pd.DataFrame | None = None
    truncated: bool = False
    attempts: list = field(default_factory=list)
    elapsed_s: float = 0.0


_FILTER = re.compile(r"\b(\w+)\s*(?:=|<>|!=)\s*'([^']*)'|\b(\w+)\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.I)
_LIKE = re.compile(r"\b(\w+)\s+(?:NOT\s+)?I?LIKE\s+'([^']*)'", re.I)


def ungrounded_filters(sql: str, question: str) -> list[str]:
    """Text filters whose values never appear in the question (e.g. delivery_status = 'On Time').

    A soft check, not a security control: the model is asked once to drop filters the
    question does not ask for, and may keep them when the question implies them.
    """
    words = re.findall(r"[a-z0-9]+", question.lower())
    flagged = []

    def mentioned(token):
        return token in question.lower() or any(len(w) >= 4 and token.startswith(w) for w in words)
    for column, pattern in _LIKE.findall(sql):     # every word must come from the question (no placeholders)
        tokens = [t for t in re.findall(r"[a-z0-9]+", pattern.lower()) if len(t) >= 3]
        if tokens and not all(mentioned(t) for t in tokens):
            flagged.append(f"{column} LIKE '{pattern}'")
    for match in _FILTER.finditer(sql):
        column = match.group(1) or match.group(3)
        values = [match.group(2)] if match.group(1) else re.findall(r"'([^']*)'", match.group(4) or "")
        for value in values:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or not value.strip():
                continue
            tokens = [t for t in re.findall(r"[a-z0-9]+", value.lower()) if len(t) >= 3]
            grounded = any(mentioned(t) for t in tokens)
            if tokens and not grounded:
                flagged.append(f"{column} = '{value}'")
    return flagged


def category_name_filters(sql: str, categories) -> list[str]:
    """A name filter that is really a listed category: product_name ILIKE '%Laundry%' misses Laundry
    products not named so (fabric softener, stain remover, bleach)."""
    listed = {str(value).lower(): str(value) for value in categories}
    return [f"use category = '{listed[pattern.strip('%').strip().lower()]}' instead of {column} LIKE '{pattern}'"
            for column, pattern in _LIKE.findall(sql)
            if column.lower().endswith("name") and pattern.strip("%").strip().lower() in listed]


# The columns that name a row, most specific first; a short result's summary must mention each row by one of them.
ROW_NAMES = (("product_name", "sku_id"), ("store_name", "store_id"), ("promo_name",), ("region",), ("channel",),
             ("category",))
SHORT_RESULT = 8


def unnamed_rows(frame: pd.DataFrame, text: str) -> list[str]:
    """Rows of a short result that the summary never mentions."""
    if not 1 < len(frame) <= SHORT_RESULT:
        return []
    columns = next(([c for c in group if c in frame.columns] for group in ROW_NAMES
                    if any(c in frame.columns for c in group)), [])
    written = (text or "").lower()
    missing = [next((str(v) for v in values if pd.notna(v)), "") for values in frame[columns].itertuples(index=False)
               if not any(pd.notna(v) and str(v).lower() in written for v in values)] if columns else []
    return list(dict.fromkeys(name for name in missing if name))


def _markdown(frame: pd.DataFrame, limit: int = SUMMARY_ROWS) -> str:
    # The values exactly as the table shows them: probabilities as percentages (0.004 -> 0.4%, never 0.0).
    shown = display_frame(frame.head(limit))
    try:
        return shown.to_markdown(index=False)
    except ImportError:  # tabulate is optional
        return shown.to_csv(index=False)


class NLQEngine:
    def __init__(self, llm):
        self.llm = llm

    def generate(self, question: str, sandbox: Sandbox, required=None) -> NLQResult:
        """``required``: published tables the question needs (predictions / rankings); a query that reads none of
        them gets one correction request. The caller still enforces it on the final query."""
        messages = [{"role": "system", "content": system_prompt(sandbox)},
                    {"role": "user", "content": f"Question: {question}"}]
        attempts, result, sql, checked_filters, checked_tables = [], None, None, False, False
        checked_names = False
        best = None                                   # the latest query that ran, with its own result
        order = ("sku_risk", "store_priority", "business_priority")          # predictions first
        needed = sorted(set(required or ()) & set(sandbox.tables), key=lambda t: (order + (t,)).index(t))
        for attempt in range(MAX_REPAIRS + 1):
            reply = self.llm.chat(messages, max_tokens=500)
            sql = extract_sql(reply)
            try:
                sql = check_sql(sql, sandbox.tables)
                result = sandbox.run(sql)
            except UnsafeSQL as error:
                attempts.append({"sql": sql, "error": str(error)})
                feedback = f"That query was rejected: {error} Return only the corrected SQL."
            except duckdb.Error as error:
                message = str(error).split("\n")[0][:300]
                attempts.append({"sql": sql, "error": message})
                feedback = f"That query failed in DuckDB: {message} Return only the corrected SQL."
            else:
                best = (sql, result)
                attempts.append({"sql": sql, "error": None, "rows": len(result.frame)})
                extra = [] if checked_filters or attempt == MAX_REPAIRS else ungrounded_filters(sql, question)
                names = [] if checked_names or attempt == MAX_REPAIRS else category_name_filters(
                    sql, sandbox.values("SELECT DISTINCT category FROM products"))
                if needed and not checked_tables and attempt < MAX_REPAIRS and \
                        not answer_sources(sql)[0] & set(needed):
                    checked_tables = True
                    feedback = ("The question asks for Module 2's predictions or ranking. Observed is_oos and sales "
                                "are history, not predictions, and cannot answer it. Use " + " or ".join(needed) +
                                ". Return only the SQL.")
                elif names:
                    checked_names = True
                    feedback = "That filter misses products: " + "; ".join(names) + ". Return only the SQL."
                elif extra:
                    checked_filters = True
                    feedback = ("The question does not mention " + "; ".join(extra) + ". Remove filters the question "
                                "does not ask for. Keep one only if the question clearly means it (for example by an "
                                "abbreviation or synonym). Return only the SQL.")
                elif len(result.frame) == 0 and attempt == 0:
                    feedback = ("That query returned no rows. If a filter value or date range may be wrong, correct it "
                                "using the listed values and dates; otherwise return the same SQL.")
                else:
                    break
            messages += [{"role": "assistant", "content": f"```sql\n{sql}\n```"}, {"role": "user", "content": feedback}]
        if best is None:
            return NLQResult("failed", "I could not turn this question into a valid query. Try naming the measure "
                             "(for example OOS rate, sell-out units or shelf share) and the period.",
                             sql=sql, attempts=attempts)
        sql, result = best                            # a failed correction never takes over an earlier result
        if len(result.frame) == 0:
            return NLQResult("empty", "No data matched this question for your stores and period.", sql=sql,
                             frame=result.frame, attempts=attempts, elapsed_s=result.elapsed_s)
        return NLQResult("ok", "", sql=sql, frame=result.frame, truncated=result.truncated, attempts=attempts,
                         elapsed_s=result.elapsed_s)

    def explain(self, question: str, result: NLQResult, sandbox: Sandbox) -> str:
        rows = len(result.frame)
        note = f"{rows}+ rows (display capped)" if result.truncated else f"{rows} rows"
        if rows > SUMMARY_ROWS:
            note += f"; only the first {SUMMARY_ROWS} are shown to you"
        user = (f"Question: {question}\nData scope: {sandbox.scope_label}; data ends {sandbox.as_of}.\n"
                f"SQL:\n{result.sql}\n\nResult ({note}):\n{_markdown(result.frame)}")
        return self.llm.chat([{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": user}],
                             max_tokens=300)

    def answer(self, question: str, sandbox: Sandbox, required=None) -> NLQResult:
        result = self.generate(question, sandbox, required)
        if result.status == "ok":
            result.answer = self.explain(question, result, sandbox)
            left_out = unnamed_rows(result.frame, result.answer)
            if left_out:      # the table is the source: a short list is never shown partly named
                result.answer = f"{result.answer}\n\nAlso in the result: {', '.join(left_out)}."
        return result
