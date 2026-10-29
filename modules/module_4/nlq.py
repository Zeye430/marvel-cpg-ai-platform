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

from .sandbox import TABLES, Sandbox
from .sql_guard import UnsafeSQL, check_sql, extract_sql

MAX_REPAIRS = 2

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
- Fill rate % = 100.0 * SUM(units_delivered) / NULLIF(SUM(units_ordered), 0) on deliveries. Late: delay_days > 0.
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
    ("Weekly sell-out units of Dishwashing products over the last 4 weeks",
     """SELECT week_start, SUM(units_sold) AS sell_out_units
FROM sku_daily
WHERE category = 'Dishwashing' AND date BETWEEN DATE '{w4_start}' AND DATE '{w4_end}'
GROUP BY week_start
ORDER BY week_start"""),
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
- today / latest day: date = DATE '{r['as_of']}'
- this week / last 7 days: date BETWEEN DATE '{r['this_week_start']}' AND DATE '{r['as_of']}'
- last week: date BETWEEN DATE '{r['last_week_start']}' AND DATE '{r['last_week_end']}'
- last 4 weeks / last 28 days: date BETWEEN DATE '{r['last_28_start']}' AND DATE '{r['as_of']}';
  on weekly_flow use the 4 latest complete weeks: week_start BETWEEN DATE '{r['w4_start']}' AND DATE '{r['w4_last']}'
- this month: BETWEEN DATE '{r['this_month_start']}' AND DATE '{r['as_of']}' (monthly_flow: month_start = DATE '{r['this_month_start']}')
- last month: BETWEEN DATE '{r['last_month_start']}' AND DATE '{r['last_month_end']}'
- this quarter (Q{(r['quarter_start'].month - 1) // 3 + 1} {r['quarter_start'].year}): BETWEEN DATE '{r['quarter_start']}' AND DATE '{r['as_of']}';
  last quarter (the previous one, Q{(r['last_quarter_start'].month - 1) // 3 + 1} {r['last_quarter_start'].year}): BETWEEN DATE '{r['last_quarter_start']}' AND DATE '{r['last_quarter_end']}'
- this year / year to date: BETWEEN DATE '{r['year_start']}' AND DATE '{r['as_of']}'; last year: BETWEEN DATE '{r['last_year_start']}' AND DATE '{r['last_year_end']}'
- no period in the question: use all data ({r['data_start']} to {r['as_of']})."""


def values_block(sandbox: Sandbox) -> str:
    def listed(sql):
        return ", ".join(f"'{v}'" for v in sandbox.values(sql) if v is not None)
    products = "; ".join(f"{sku} = {name}" for sku, name in zip(
        sandbox.values("SELECT sku_id FROM products ORDER BY sku_id"),
        sandbox.values("SELECT product_name FROM products ORDER BY sku_id")))
    return f"""VALUES:
- channel: {listed("SELECT DISTINCT channel FROM stores ORDER BY 1")}
- region: {listed("SELECT DISTINCT region FROM stores ORDER BY 1")}
- segment_tier: {listed("SELECT DISTINCT segment_tier FROM stores ORDER BY 1")}
- category: {listed("SELECT DISTINCT category FROM products ORDER BY 1")}
- brand: {listed("SELECT DISTINCT brand FROM products ORDER BY 1")}
- delivery_status: {listed("SELECT DISTINCT delivery_status FROM deliveries ORDER BY 1")}
- promo_type: {listed("SELECT DISTINCT promo_type FROM promotions ORDER BY 1")}
- products: {products}"""


def system_prompt(sandbox: Sandbox) -> str:
    ranges = date_ranges(sandbox.as_of)
    examples = "\n\n".join(f"Question: {q}\n```sql\n{sql.format(**ranges)}\n```" for q, sql in EXAMPLES)
    return ("You translate questions about Marvel CPG's commercial data into one DuckDB SQL query.\n\n"
            f"{SCHEMA}\n\n{values_block(sandbox)}\n\n{METRICS}\n\n{dates_block(ranges)}\n\n{RULES}\n\n"
            f"EXAMPLES:\n\n{examples}")


SUMMARY_PROMPT = """You are Marvel CPG's commercial copilot. Explain a database result in plain English for a sales team.
Rules:
- Start with the direct answer to the question. Use 2 to 4 short sentences.
- Use only numbers that appear in the result table. Do not calculate new figures and never invent any.
- Name the time period and filters used (they are in the SQL).
- If there are more rows than you name, name the top few and give the total number of rows.
  Do not mention the row count otherwise.
- If the result is empty, say that no data matched for this scope and period.
- Do not guess causes or give advice that the data does not show."""


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


def ungrounded_filters(sql: str, question: str) -> list[str]:
    """Text filters whose values never appear in the question (e.g. delivery_status = 'On Time').

    A soft check, not a security control: the model is asked once to drop filters the
    question does not ask for, and may keep them when the question implies them.
    """
    words = re.findall(r"[a-z0-9]+", question.lower())
    flagged = []
    for match in _FILTER.finditer(sql):
        column = match.group(1) or match.group(3)
        values = [match.group(2)] if match.group(1) else re.findall(r"'([^']*)'", match.group(4) or "")
        for value in values:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or not value.strip():
                continue
            tokens = [t for t in re.findall(r"[a-z0-9]+", value.lower()) if len(t) >= 3]
            grounded = any(t in question.lower() or any(len(w) >= 4 and t.startswith(w) for w in words)
                           for t in tokens)
            if tokens and not grounded:
                flagged.append(f"{column} = '{value}'")
    return flagged


def _markdown(frame: pd.DataFrame, limit: int = 15) -> str:
    shown = frame.head(limit).copy()
    for column in shown.columns:
        if pd.api.types.is_float_dtype(shown[column]):
            shown[column] = shown[column].round(2)
    try:
        return shown.to_markdown(index=False)
    except ImportError:  # tabulate is optional
        return shown.to_csv(index=False)


class NLQEngine:
    def __init__(self, llm):
        self.llm = llm

    def generate(self, question: str, sandbox: Sandbox) -> NLQResult:
        messages = [{"role": "system", "content": system_prompt(sandbox)},
                    {"role": "user", "content": f"Question: {question}"}]
        attempts, result, sql, checked_filters = [], None, None, False
        for attempt in range(MAX_REPAIRS + 1):
            reply = self.llm.chat(messages, max_tokens=500)
            sql = extract_sql(reply)
            try:
                sql = check_sql(sql, TABLES)
                result = sandbox.run(sql)
            except UnsafeSQL as error:
                attempts.append({"sql": sql, "error": str(error)})
                feedback = f"That query was rejected: {error} Return only the corrected SQL."
            except duckdb.Error as error:
                message = str(error).split("\n")[0][:300]
                attempts.append({"sql": sql, "error": message})
                feedback = f"That query failed in DuckDB: {message} Return only the corrected SQL."
            else:
                attempts.append({"sql": sql, "error": None, "rows": len(result.frame)})
                extra = [] if checked_filters or attempt == MAX_REPAIRS else ungrounded_filters(sql, question)
                if extra:
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
        if result is None:
            return NLQResult("failed", "I could not turn this question into a valid query. Try naming the measure "
                             "(for example OOS rate, sell-out units or shelf share) and the period.",
                             sql=sql, attempts=attempts)
        if len(result.frame) == 0:
            return NLQResult("empty", "No data matched this question for your stores and period.", sql=sql,
                             frame=result.frame, attempts=attempts, elapsed_s=result.elapsed_s)
        return NLQResult("ok", "", sql=sql, frame=result.frame, truncated=result.truncated, attempts=attempts,
                         elapsed_s=result.elapsed_s)

    def explain(self, question: str, result: NLQResult, sandbox: Sandbox) -> str:
        rows = len(result.frame)
        note = f"{rows}+ rows (display capped)" if result.truncated else f"{rows} rows"
        user = (f"Question: {question}\nData scope: {sandbox.scope_label}; data ends {sandbox.as_of}.\n"
                f"SQL:\n{result.sql}\n\nResult ({note}):\n{_markdown(result.frame)}")
        return self.llm.chat([{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": user}],
                             max_tokens=300)

    def answer(self, question: str, sandbox: Sandbox) -> NLQResult:
        result = self.generate(question, sandbox)
        if result.status == "ok":
            result.answer = self.explain(question, result, sandbox)
        return result
