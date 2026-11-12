"""
modules/module_1/server.py
==========================
Server logic for Module 1: Business Performance (Commercial Execution Analytics): the five KPI
cards (the selected one's weekly trend with its team target below them, and how it is calculated
with what the number rests on, collapsed) and ten Plotly charts. What each chart means is in its
"?" help (ui.py), not in small print under it.

Data comes only from the shared business publication pinned by the app runtime
(modules/module_1/data.py), restricted to the signed-in principal's account scope and loaded
on the first visit to the module, in a background task. Values are Reported data; unknown
inputs stay Unknown/N/A, and heuristics or unapproved rules are labelled as such instead of
being shown as measured results or approvals. Seven charts are for the regional manager view;
a field rep sees the heatmap, the brand compliance bars and the discount scatter.

Charts are rendered like Module 2's (Plotly JSON + the workspace's local plotly.js). Every
figure is checked for strict JSON first: a NaN or infinite value can never reach the browser
and close the session. A chart that fails shows a sanitized error card, never other data.
"""

import asyncio
import functools
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from htmltools import TagList, tags
from shiny import reactive, render, req, ui

from shared import ui_kit as kit
from shared.chart_palette import CHART_COLORS, CHART_NEUTRAL

from .data import ABOVE_CEILING, PERIOD_TYPE, WITHIN_CEILING, Filters
from .ui import PAGE_ICONS, PAGES, TOPOJSON_URL

HEATMAP_TOP_STORES = 25
MODULE_KEY = "module_1"

KPI_SPECS = {
    "msl": {
        "title": "MSL Compliance %",
        "desc": "Share of assessable store-days where every applicable mandatory (is_msl) SKU had known positive inventory.",
        "source": "store_day (all SKUs) or store_sku_daily (selected SKUs) · shared_kpi.v1",
        "columns": "msl_compliant_store_count, msl_assessable_store_count, msl_eligible_store_count",
        "formula": "100 × SUM(compliant store-days) / SUM(assessable store-days)",
        "logic": "Noncompliant if any applicable MSL SKU is known at zero; Compliant if all are known positive; "
                 "otherwise Unknown (not a failure). No applicable MSL SKU = N/A. Summed over dates: store-days.",
    },
    "oos": {
        "title": "Reported OOS Rate %",
        "desc": "Share of store-SKU-days with known inventory that were at zero (Reported Data Only).",
        "source": "store_day (all SKUs) or store_sku_daily (selected SKUs) · shared_kpi.v1",
        "columns": "oos_sku_count, known_inventory_sku_count, observed_inventory_sku_count, expected_sku_count",
        "formula": "100 × SUM(oos_sku_count) / SUM(known_inventory_sku_count)",
        "logic": "Unknown inventory is excluded from the denominator and shown as reporting coverage; it is never "
                 "treated as in stock.",
    },
    "gap": {
        "title": "Sell-in vs Sell-out Gap %",
        "desc": "Invoiced units versus consumer sell-out units over complete aligned periods.",
        "source": f"commercial_period · period_type={PERIOD_TYPE}, alignment_basis=event_date · shared_kpi.v1",
        "columns": "comparable_gap_units, comparable_sell_out_units, flow_is_comparable, period_is_complete",
        "formula": "100 × SUM(comparable_gap_units) / SUM(comparable_sell_out_units)",
        "logic": "Only complete periods fully inside the date range and rows where both flows are known. A missing "
                 "invoice is not zero while invoice-extract completeness is unconfirmed. Zero sell-out gives N/A.",
    },
    "shelf": {
        "title": "Audited SKU Shelf Allocation %",
        "desc": "Shelf allocation of the selected SKUs within audited category visits; not Marvel vs competitor share.",
        "source": "execution_visits · scope_label = Individual audited SKU · shared_kpi.v1",
        "columns": "shelf_allocation_pct per store × audit date × category visit",
        "formula": "Sum the selected SKUs' allocation in each visit, then average across those visits",
        "logic": "Supplied SKUs of a category sum to ~100% by construction, so selecting every SKU gives ~100%. "
                 "The competitor shelf universe is not in the data; no market-share target is applied.",
    },
    "plano": {
        "title": "Planogram Compliance %",
        "desc": "Share of audited SKU rows compliant with the planogram.",
        "source": "execution_visits · scope_label = Individual audited SKU · shared_kpi.v1",
        "columns": "compliant_audit_row_count, audited_row_count",
        "formula": "100 × SUM(compliant_audit_row_count) / SUM(audited_row_count)",
        "logic": "Actual audit rows only; a day without an audit is not noncompliance.",
    },
}

PAGE_HEADS = {
    "overview": ("Business performance", None),
    "geo": ("Commercial strategy & geospatial execution",
            "Where territories sell well or execute poorly, and how invoiced stock compares with what consumers buy."),
    "oos": ("Out-of-stock & supply chain insights",
            "Which must-stock products are out of stock, for how long, and what it may cost."),
    "shelf": ("Shelf execution & compliance",
              "Planogram compliance, must-stock availability and shelf space by brand, store and region."),
    "trade": ("Trade terms & commercial guardrails",
              "Trade discounts against the guardrail reference, and whether execution commitments are delivered."),
}


# ------------------------------------------------------------------ chart plumbing

class NonFiniteChart(ValueError):
    """A figure still carries NaN/Infinity after encoding (never sent to the browser)."""


def _reject(token):
    raise NonFiniteChart(f"non-finite value {token} in chart data")


def figure_json(fig):
    """Plotly's encoder writes NaN as null; anything non-finite left is rejected before sending."""
    payload = fig.to_json()
    json.loads(payload, parse_constant=_reject)
    return payload


def chart_layout(fig, height):
    kit.chart_layout(fig, height)
    # The legend sits above the plot, so long axis labels never run into it.
    fig.update_layout(margin=dict(l=16, r=16, t=40, b=16), plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, title=None, font=dict(size=12)),
                      hoverlabel=dict(bgcolor="#ffffff", font_size=12, font_family=kit.FONT_STACK,
                                      bordercolor="#dfd4d4"))
    fig.update_xaxes(showgrid=False, zerolinecolor="#ece4e4", automargin=True, linecolor="#ece4e4")
    fig.update_yaxes(gridcolor="#f3eeee", zerolinecolor="#ece4e4", automargin=True)
    return fig


def plot(fig, element_id, label, height, layout=None):
    """One validated Plotly figure as HTML for the workspace's local plotly.js (``layout``: chart overrides)."""
    chart_layout(fig, height)
    if layout:
        fig.update_layout(**layout)
    payload = figure_json(fig)
    config = json.dumps({"displayModeBar": False, "responsive": True, "topojsonURL": TOPOJSON_URL})
    script = (f"(function(){{var f={payload};var el=document.getElementById('{element_id}');"
              f"if(el&&window.Plotly){{Plotly.newPlot(el,f.data,f.layout,{config});}}}})();")
    return ui.HTML(f'<div class="m1-chart" role="img" aria-label="{label}">'
                   f'<div id="{element_id}" style="height:{height}px"></div><script>{script}</script></div>')


def palette(names):
    """Fixed slot per entity (the scope's full sorted list), so a filter never repaints survivors."""
    return {name: CHART_COLORS[i] if i < len(CHART_COLORS) else CHART_NEUTRAL for i, name in enumerate(names)}


def _rgba(hex_color, alpha):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


@dataclass
class Chart:
    fig: object = None
    note: str = ""
    height: int = 420
    empty: tuple = None          # (title, text) when there is nothing to draw
    layout: dict = None          # applied after the shared chart layout


def wrap(text, width=26):
    """Line breaks for a long category label (Plotly does not wrap tick text)."""
    lines, line = [], ""
    for word in str(text).split():
        if line and len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return "<br>".join(lines + [line])


def _fmt(value, pattern="{:.1f}%"):
    return "N/A" if value is None or pd.isna(value) else pattern.format(value)


def state(title, text, kind="info"):
    return tags.div(tags.strong(title), tags.div(text), class_=f"ws-state m1-state {kind}", role="status")


# ---------------------------------------------------------------- filter pills and the welcome screen
# Weeks run Monday to Sunday. "Last week" and "Last 4 weeks" are the latest complete weeks; the to-date
# presets run through the data's last day.
DATE_PRESETS = {"1w": "Last week", "4w": "Last 4 weeks", "mtd": "Month to date", "qtd": "Quarter to date",
                "ytd": "Year to date", "all": "All data"}
DEFAULT_PRESET = "4w"
MSL_OPTIONS = {"all": "All products", "msl": "MSL products only", "non": "Non-MSL products only"}
MSL_PILL = {"all": "All", "msl": "MSL only", "non": "Non-MSL only"}


def snap(a, b, first, last):
    """Whole weeks (Monday to Sunday) covering a..b, within the data's first and last day."""
    a, b, first, last = (pd.Timestamp(x) for x in (a, b, first, last))
    if a > b:
        a, b = b, a
    return max(a - pd.Timedelta(days=a.dayofweek), first), min(b + pd.Timedelta(days=6 - b.dayofweek), last)


def preset_span(key, first, last):
    """The whole weeks a date preset covers, counted back from the data's last day."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    if key in ("1w", "4w"):
        end = last if last.dayofweek == 6 else last - pd.Timedelta(days=last.dayofweek + 1)   # last complete week
        if end < first:
            return snap(first, last, first, last)
        return snap(end - pd.Timedelta(days=6 if key == "1w" else 27), end, first, last)
    start = {"mtd": pd.Timestamp(last.year, last.month, 1), "qtd": last.to_period("Q").start_time,
             "ytd": pd.Timestamp(last.year, 1, 1), "all": first}[key]
    return snap(max(first, start), last, first, last)


def matching_preset(span_, first, last, chosen=None):
    """The preset whose span is ``span_``, or None (a custom range). Presets can cover the same weeks
    (a month starting on a Monday: month to date = last 4 weeks plus the open week); the one the user
    ``chosen`` is named then."""
    if chosen in DATE_PRESETS and preset_span(chosen, first, last) == tuple(span_):
        return chosen
    for key in DATE_PRESETS:
        if preset_span(key, first, last) == tuple(span_):
            return key
    return None


def choice_label(selected, names, empty_means_all=True):
    """(pill value, changed): "All", one name, "k of n" or "None"; changed = narrower than everything."""
    selected = [s for s in selected or () if s in names]
    if not selected:
        return ("All", False) if empty_means_all else ("None", True)
    if len(selected) == len(names):
        return ("All" if empty_means_all else f"All {len(names)}"), False
    if len(selected) == 1:
        return names[selected[0]], True
    return f"{len(selected)} of {len(names)}", True


def msl_skus(info, msl="all"):
    """The SKUs the MSL pill allows, in list order: all, MSL products only or non-MSL products only."""
    required = set(info.get("msl_products") or ())
    return [s for s in info["products"] if msl not in ("msl", "non") or (s in required) == (msl == "msl")]


def sku_choices(info, skus=None):
    """SKU checkbox labels in category order (``skus``: only these); the category is carried for the list's
    group headings."""
    names, categories = info.get("product_names") or {}, info.get("product_categories") or {}
    keep = set(info["products"] if skus is None else skus)
    return {sku: tags.span(names.get(sku, label), **{"data-category": categories.get(sku, "")})
            for sku, label in info["products"].items() if sku in keep}


def greeting(hour):
    """A time-of-day greeting from the browser's local hour (None: no clock received)."""
    if hour is None:
        return "Welcome"
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 23:
        return "Good evening"
    return "Working late"


def display_name(name):
    return (name or "").replace("(demo)", "").strip()


def welcome_detail(stores=None, as_of=None):
    """The line under the greeting; the scope summary is added once it has been read (in the background)."""
    detail = ["Getting your business performance ready"]
    if stores:
        detail.append(f" for {stores} stores")
    if as_of:
        detail.append(f", with data through {as_of}")
    return "".join(detail) + "."


def welcome(name, clock=None, detail=None):
    """Shown at once while the first load runs (it reads nothing): who it is for, what is being prepared, and
    that it is still working. ``detail``: the line under the greeting (default: without the scope summary)."""
    clock = clock if isinstance(clock, dict) else {}
    hour = clock.get("hour")
    who = display_name(name)
    title = f"{greeting(hour if isinstance(hour, int) else None)}{', ' + who if who else ''}"
    return tags.section(
        tags.p(clock.get("day") or "", class_="m1-welcome-day") if clock.get("day") else None,
        tags.h1(title, class_="m1-welcome-title"),
        tags.div(detail if detail is not None else welcome_detail(), class_="m1-welcome-sub"),
        tags.div(tags.span(class_="m1-progress-bar"), class_="m1-progress", role="progressbar",
                 **{"aria-label": "Loading business performance"}),
        tags.p("The first visit takes a few seconds. Stockout Risk and the Copilot are ready in the sidebar "
               "meanwhile.", class_="m1-welcome-note"),
        tags.div(*[tags.div(tags.span(class_="m1-skel-line short"), tags.span(class_="m1-skel-line big"),
                            tags.span(class_="m1-skel-line"), class_="m1-skel-card") for _ in range(5)],
                 class_="m1-skeleton", **{"aria-hidden": "true"}),
        class_="m1-welcome", id="m1-welcome", role="status", **{"aria-live": "polite"})


def locked(title):
    return tags.div(tags.strong("Regional manager access required"),
                    tags.span(f"{title} is part of the regional manager view. Field sales reps see the stock "
                              "heatmap, brand compliance and discount charts for their assigned stores."),
                    class_="ws-state m1-locked", role="note")


def safe(function):
    """Unexpected failures render a sanitized error state, never a stack trace or other data."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - deliberate UI boundary
            if type(error).__name__ in ("SilentException", "SilentCancelOutputException"):
                raise
            return state("This section could not load", f"({type(error).__name__}). Change a filter or reload the "
                         "page; other sections are not affected.", "bad")
    return wrapper


# ------------------------------------------------------------------ chart builders (pure: data -> Chart)

def chart_heatmap(item, f, option, colors):
    """Latest-day inventory heatmap; Unknown reports stay Unknown (never zero)."""
    frame = item.latest_inventory(f, top=None if not item.manager else HEATMAP_TOP_STORES)
    if frame.empty:
        return Chart(empty=("No store-SKU rows", "No assortment rows match the selected filters and dates."))
    latest = frame["date"].max()
    pivot = frame.pivot_table(index="store_name", columns="product_name", values="inventory_observed",
                              aggfunc="first", dropna=False)
    status = frame.pivot_table(index="store_name", columns="product_name", values="inventory_data_status",
                               aggfunc="first").reindex(index=pivot.index, columns=pivot.columns)
    text = np.where(pivot.isna(), np.where(status.isna(), "", "Unknown"), pivot.round(0).astype("Int64").astype(str))
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[c.replace("Marvel ", "") for c in pivot.columns], y=list(pivot.index), text=text,
        texttemplate="%{text}", hovertemplate="%{y}<br>%{x}<br>Inventory: %{text}<extra></extra>",
        colorscale=[[0.0, CHART_COLORS[7]], [0.12, CHART_COLORS[3]], [0.35, "#f3eded"], [1.0, CHART_COLORS[0]]],
        colorbar=dict(title="On-hand units"), zmin=0, xgap=1, ygap=1))
    fig.update_xaxes(side="top", tickangle=-30, showgrid=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    fig.update_traces(hovertemplate=f"%{{y}}<br>%{{x}}<br>On hand {pd.Timestamp(latest):%b %d, %Y}: %{{text}}"
                                    "<extra></extra>")
    return Chart(fig, "", max(460, min(720, len(pivot) * 24 + 150)),
                 layout={"plot_bgcolor": "#e7e1e1",
                         "title": {"text": f"On hand on {pd.Timestamp(latest):%b %d, %Y}", "x": 0, "xanchor": "left",
                                   "y": 0.995, "yanchor": "top", "font": {"size": 12, "color": "#65595a"}}})


def chart_lost_sales(item, f, option, colors):
    """Heuristic exposure trend; not the shared missing-day estimator."""
    group_by = option or "region"
    frame = item.lost_sales_heuristic(f, group_by)
    if frame.empty:
        return Chart(empty=("No observed OOS days", "No observed zero-inventory days with an observed in-stock "
                                                    "baseline in the selected scope."))
    order = list(colors[group_by])
    fig = px.area(frame, x="week", y="lost_value", color="grp", color_discrete_map=colors[group_by],
                  category_orders={"grp": order},
                  labels={"lost_value": "Heuristic exposure (static list price)", "week": "",
                          "grp": group_by.capitalize()})
    fig.update_traces(line=dict(width=2))
    fig.update_yaxes(tickformat=",.0f")
    return Chart(fig, "", 440)


def chart_duration(item, f, option, colors):
    """Consecutive observed OOS days; a calendar gap or Unknown day ends an episode."""
    group_col = option or "channel"
    episodes = item.oos_episodes(f, group_col)
    if episodes.empty:
        return Chart(empty=("No observed OOS episodes", "No observed zero-inventory days in the selected period."))
    key = "channel" if group_col == "channel" else "tier"
    fig = px.box(episodes, x="grp", y="days", color="grp", points="outliers", color_discrete_map=colors[key],
                 category_orders={"grp": list(colors[key])},
                 labels={"days": "Consecutive observed OOS days per episode", "grp": ""})
    fig.update_layout(showlegend=False)
    return Chart(fig, "", 420)


def chart_compliance(item, f, option, colors):
    merged = item.execution_by_brand(f)
    if merged.empty:
        return Chart(empty=("No audits or MSL rows", "Nothing matches the selected filters."))
    merged["brand_key"] = merged["category"] + " · " + merged["brand"]
    melted = merged.melt(id_vars=["brand_key"], value_vars=["planogram_pct", "msl_availability_pct"],
                         var_name="Metric", value_name="Percentage")
    names = {"planogram_pct": "Planogram Compliance % (audited rows)",
             "msl_availability_pct": "MSL SKU-day Availability % (observed)"}
    melted["Metric"] = melted["Metric"].map(names)
    melted = melted.dropna(subset=["Percentage"])
    if melted.empty:
        return Chart(empty=("No audited rows or MSL SKUs", "Every group is N/A for the selected filters."))
    fig = px.bar(melted, y="brand_key", x="Percentage", color="Metric", barmode="group", orientation="h",
                 text_auto=".1f", labels={"Percentage": "Rate (%)", "brand_key": ""},
                 category_orders={"Metric": list(names.values())},
                 color_discrete_map={names["planogram_pct"]: CHART_COLORS[0], names["msl_availability_pct"]: CHART_COLORS[1]})
    fig.update_traces(textposition="outside", cliponaxis=False)
    for x, dash, text, anchor in ((85, "dash", "85% display target (team-set)", "left"),
                                  (62, "dot", "Brief: 62% of stores comply", "right")):
        fig.add_vline(x=x, line_dash=dash, line_color="#65595a", line_width=1.5)
        fig.add_annotation(x=x, y=1, yref="paper", yanchor="bottom", xanchor=anchor, text=text, showarrow=False,
                           xshift=4 if anchor == "left" else -4, font=dict(size=11, color="#4a3f40"))
    fig.update_xaxes(range=[0, 108], ticksuffix="%")
    return Chart(fig, "",
                 max(420, 80 + 44 * merged["brand_key"].nunique()),
                 layout={"legend": {"y": 1.07}, "margin": {"t": 76}})   # reference labels between legend and bars


def chart_bubble(item, f, option, colors):
    frame = item.store_allocation_sales(f)
    if frame.empty:
        return Chart(empty=("No audited stores with invoices", "Nothing matches the selected filters."))
    frame["bubble_size"] = frame["segment_tier"].map({"Tier 1": 26, "Tier 2": 16, "Tier 3": 10}).fillna(12).astype(float)
    fig = px.scatter(frame, x="allocation", y="net_sales", size="bubble_size", color="channel",
                     hover_name="store_name", color_discrete_map=colors["channel"],
                     category_orders={"channel": list(colors["channel"])},
                     hover_data={"segment_tier": True, "region": True, "visits": True, "allocation": ":.1f",
                                 "net_sales": ":,.0f", "bubble_size": False},
                     labels={"allocation": "Audited SKU shelf allocation % (per visit)",
                             "net_sales": "Invoiced net sales (reported, no currency)", "channel": "Channel"})
    fig.update_traces(marker=dict(line=dict(width=1.5, color="#ffffff"), opacity=0.85))
    low, high = frame["allocation"].min(), frame["allocation"].max()
    if high - low < 2:                 # all SKUs selected: ~100% by construction; keep readable ticks
        middle = (high + low) / 2
        fig.update_xaxes(range=[middle - 1, middle + 1])
    fig.update_xaxes(ticksuffix="%", tickformat=".1f")
    fig.update_yaxes(tickformat=",.0f")
    return Chart(fig, "", 460)


SCORE_AXES = ["Planogram Compliance %", "MSL Availability % (observed)", "Secondary Display %",
              "Audited SKU Allocation %", "In-Stock % (observed)"]


def chart_radar(item, f, option, colors):
    group_col = option or "region"
    frame = item.scorecard(f, group_col)
    if frame.empty:
        return Chart(empty=("No rows", "Nothing matches the selected filters."))
    key = {"region": "region", "segment_tier": "tier", "channel": "channel"}[group_col]
    fig = go.Figure()
    for _, row in frame.iterrows():
        values = [None if row[a] is None or pd.isna(row[a]) else float(row[a]) for a in SCORE_AXES]
        color = colors[key].get(row["grp"], CHART_NEUTRAL)
        fig.add_trace(go.Scatterpolar(r=values + values[:1], theta=SCORE_AXES + SCORE_AXES[:1], fill="toself",
                                      fillcolor=_rgba(color, 0.10), name=str(row["grp"]), connectgaps=False,
                                      line=dict(color=color, width=2)))
    fig.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)",
                                 radialaxis=dict(visible=True, range=[0, 100], gridcolor="#ece4e4", ticksuffix="%",
                                                 tickfont=dict(size=10, color="#65595a")),
                                 angularaxis=dict(gridcolor="#ece4e4", tickfont=dict(size=11, color="#211b1c"))))
    return Chart(fig, "", 480)


REFERENCE_COLORS = {ABOVE_CEILING: CHART_COLORS[7], WITHIN_CEILING: CHART_COLORS[0],
                    "No single source reference": CHART_NEUTRAL}


def chart_discount(item, f, option, colors):
    frame = item.trade_terms(f).dropna(subset=["trade_discount_pct", "margin_pct_static_cost"])
    if frame.empty:
        return Chart(empty=("No invoices", "No reported invoice lines in the selected scope and dates."))
    fig = px.scatter(frame, x="trade_discount_pct", y="margin_pct_static_cost", color="reference_status",
                     color_discrete_map=REFERENCE_COLORS, hover_name="store_name",
                     category_orders={"reference_status": list(REFERENCE_COLORS)},
                     hover_data={"channel": True, "category": True, "lines": True, "source_flagged_lines": True,
                                 "trade_discount_pct": ":.1f", "standard_discount_pct_source": ":.2f",
                                 "max_discount_pct_source": ":.2f", "discount_ceiling_pct": ":.2f",
                                 "margin_pct_static_cost": ":.1f", "reference_status": True},
                     labels={"trade_discount_pct": "Trade discount % (gross-value weighted)",
                             "margin_pct_static_cost": "Margin % at static product cost",
                             "standard_discount_pct_source": "Standard discount %",
                             "max_discount_pct_source": "Maximum additional (pts)",
                             "discount_ceiling_pct": "Standard + maximum additional %",
                             "reference_status": "Source reference"})
    fig.update_traces(marker=dict(size=9, line=dict(width=1, color="#ffffff"), opacity=0.85))
    refs = frame["discount_ceiling_pct"].dropna().unique()
    if len(refs) == 1:
        fig.add_vline(x=float(refs[0]), line_dash="dash", line_color="#65595a", line_width=1.5,
                      annotation_text=f"Standard + maximum additional {refs[0]:.1f}% (provisional)",
                      annotation_position="top right", annotation_font=dict(size=11, color="#4a3f40"))
    fig.update_xaxes(ticksuffix="%")
    fig.update_yaxes(ticksuffix="%")
    return Chart(fig, "", 460)


def chart_tradeoff(item, f, option, colors):
    frame = item.tradeoff_evidence(f)
    if frame.empty:
        return Chart(empty=("No evidence", "No audited rows with a single source trade-off reference."))
    names = ["Planogram compliant audit rows %", "Secondary display audit rows %"]
    melted = frame.melt(id_vars=["required_exec_tradeoff"], value_vars=names, var_name="Evidence",
                        value_name="Percentage").dropna(subset=["Percentage"])
    if melted.empty:
        return Chart(empty=("No evidence", "Every group is N/A for the selected filters."))
    fig = px.bar(melted, x="required_exec_tradeoff", y="Percentage", color="Evidence", barmode="group",
                 text_auto=".1f", category_orders={"Evidence": names},
                 labels={"required_exec_tradeoff": "Required trade-off (source text)",
                         "Percentage": "Share of audited SKU rows (%)"},
                 color_discrete_map={names[0]: CHART_COLORS[0], names[1]: CHART_COLORS[1]})
    fig.update_traces(textposition="outside", cliponaxis=False)
    labels = {t: wrap(t) for t in melted["required_exec_tradeoff"].unique()}
    fig.update_xaxes(tickangle=0, tickmode="array", tickvals=list(labels), ticktext=list(labels.values()))
    fig.update_yaxes(range=[0, 110], ticksuffix="%")
    return Chart(fig, "", 440)


GEO_SCALE = [[0.0, "#e3eefb"], [0.5, CHART_COLORS[0]], [1.0, "#174a8c"]]


def chart_geo(item, f, option, colors):
    frame = item.state_execution(f)
    if frame.empty:
        return Chart(empty=("No rows", "Nothing matches the selected filters."))
    column = "execution_index" if (option or "health_score") == "health_score" else "net_sales"
    for name in ("execution_index", "net_sales", "planogram_pct", "in_stock_pct"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    fig = px.choropleth(frame, locations="state", locationmode="USA-states", color=column, scope="usa",
                        hover_name="state",
                        hover_data={"execution_index": ":.1f", "net_sales": ":,.0f", "planogram_pct": ":.1f",
                                    "in_stock_pct": ":.1f", "store_count": True, "state": False},
                        color_continuous_scale=GEO_SCALE,
                        labels={"execution_index": "Execution index (display only)",
                                "net_sales": "Invoiced net sales (no currency)", "planogram_pct": "Planogram %",
                                "in_stock_pct": "In-stock % (observed)", "store_count": "Stores"})
    fig.update_traces(marker_line_color="#ffffff", marker_line_width=1)
    fig.update_layout(geo=dict(bgcolor="rgba(0,0,0,0)", showlakes=False, landcolor="#f3eded",
                               subunitcolor="#dfd4d4"),
                      coloraxis_colorbar=dict(title=None, thickness=10, orientation="h", y=-0.02, yanchor="top",
                                              len=0.6, x=0.5, xanchor="center"))
    return Chart(fig, "", 500)


def chart_sankey(item, f, option, colors):
    flow = item.flow_by_channel(f)
    if flow.empty:
        return Chart(empty=("No comparable flows", f"No complete {PERIOD_TYPE} periods with both flows known in the "
                                                   "selected range."))
    categories = sorted(flow["category"].unique().tolist())
    channels = sorted(flow["channel"].unique().tolist())
    out_label, gap_label = "Observed consumer sell-out", "Invoiced above sell-out (gap, not measured stock)"
    nodes = categories + channels + [out_label, gap_label]
    index = {n: i for i, n in enumerate(nodes)}
    by_channel = flow.groupby("channel")[["sell_in", "sell_out"]].sum()
    sources, targets, values, link_colors = [], [], [], []
    for row in flow.itertuples(index=False):
        if row.sell_in and row.sell_in > 0:
            sources.append(index[row.category]); targets.append(index[row.channel])
            values.append(float(row.sell_in)); link_colors.append(_rgba(CHART_COLORS[0], 0.22))
    for channel, row in by_channel.iterrows():
        if row["sell_out"] > 0:
            sources.append(index[channel]); targets.append(index[out_label])
            values.append(float(min(row["sell_out"], row["sell_in"]))); link_colors.append(_rgba(CHART_COLORS[2], 0.30))
        if row["sell_in"] - row["sell_out"] > 0:
            sources.append(index[channel]); targets.append(index[gap_label])
            values.append(float(row["sell_in"] - row["sell_out"])); link_colors.append(_rgba(CHART_COLORS[1], 0.30))
    node_colors = ["#65595a"] * len(categories) + [colors["channel"].get(c, CHART_NEUTRAL) for c in channels] + \
        [CHART_COLORS[2], CHART_COLORS[1]]
    fig = go.Figure(go.Sankey(node=dict(pad=18, thickness=16, line=dict(color="#ffffff", width=1), label=nodes,
                                        color=node_colors),
                              link=dict(source=sources, target=targets, value=values, color=link_colors)))
    return Chart(fig, "", 460)


# output id -> (builder, option input, regional manager only, accessible label)
CHARTS = {
    "m1_chart9_geo_map": (chart_geo, "m1_c9_metric", True, "Regional execution map by state"),
    "m1_chart10_sankey": (chart_sankey, None, True, "Sell-in vs sell-out flow diagram"),
    "m1_chart1_heatmap": (chart_heatmap, None, False, "Stock level heatmap by store and SKU"),
    "m1_chart2_lost_sales": (chart_lost_sales, "m1_c2_group", True, "Lost sales heuristic trend"),
    "m1_chart3_duration": (chart_duration, "m1_c3_group", True, "OOS duration distribution"),
    "m1_chart4_compliance": (chart_compliance, None, False, "Planogram and MSL availability by brand"),
    "m1_chart5_bubble": (chart_bubble, None, True, "Audited shelf allocation vs invoiced sales"),
    "m1_chart6_radar": (chart_radar, "m1_c6_group", True, "Execution scorecard radar"),
    "m1_chart7_discount": (chart_discount, None, False, "Discount vs margin scatter"),
    "m1_chart8_tradeoff": (chart_tradeoff, None, True, "Trade-off commitment evidence"),
}
LOCKED_TITLES = {"m1_chart9_geo_map": "The regional execution map", "m1_chart10_sankey": "The sell-in vs sell-out flow",
                 "m1_chart2_lost_sales": "The lost sales trend", "m1_chart3_duration": "The OOS duration distribution",
                 "m1_chart5_bubble": "The shelf allocation vs sales plot", "m1_chart6_radar": "The execution scorecard",
                 "m1_chart8_tradeoff": "The trade-off evidence"}


def render_chart(output_id, item, f, option, colors):
    """The chart's markup for one principal's data and filters (locked, empty or the figure)."""
    builder, _, manager_only, label = CHARTS[output_id]
    if manager_only and not item.manager:
        return locked(LOCKED_TITLES[output_id])
    if not f.skus:
        return state("No SKU selected", "Select at least one product SKU in the filters.")
    chart = builder(item, f, option, colors)
    if chart.empty is not None:
        return state(*chart.empty)
    element = output_id.replace("_", "-") + "-plot"
    return TagList(plot(chart.fig, element, label, chart.height, chart.layout),
                   tags.p(chart.note, class_="ws-muted m1-caveat") if chart.note else None)


# ------------------------------------------------------------------ KPI tiles

# Team display targets (not client-approved thresholds): (test, short text, line value(s)) per KPI.
KPI_TARGETS = {
    "msl": (lambda v: v >= 90, "≥ 90%", 90),
    "oos": (lambda v: v <= 5, "≤ 5%", 5),
    "gap": (lambda v: abs(v) <= 5, "within ±5%", (-5, 5)),
    "plano": (lambda v: v >= 80, "≥ 80%", 80),
}
KPI_TILES = (("msl", "MSL Compliance", "msl", "{:.1f}%", "up"), ("oos", "Reported OOS Rate", "oos", "{:.1f}%", "down"),
             ("gap", "Sell-In vs Sell-Out Gap", "gap_pct", "{:+.1f}%", "zero"),
             ("shelf", "Shelf Allocation", "allocation", "{:.1f}%", "up"),
             ("plano", "Planogram Compliance", "plano", "{:.1f}%", "up"))


BETTER_TEXT = {"up": "higher is better", "down": "lower is better", "zero": "closer to 0 is better"}


def _delta(value, prior, better, long=False):
    """The change vs the prior period of equal length, as a pill: the arrow is the direction of the number;
    the colour says whether that is better (green) or worse (red) for this KPI (``better``: "up", "down", or
    "zero" for a gap whose ideal is 0), so a fall in the OOS rate is green. The tooltip says which."""
    suffix = " vs prior period" if long else ""
    if value is None or pd.isna(value) or prior is None or pd.isna(prior):
        return kit.pill("No prior value" + ("" if not long else " to compare"), "outline")
    delta = round(value - prior, 1)
    if delta == 0:
        return kit.pill("No change" + suffix, "outline", title="Same as the previous period of equal length")
    text = f"{'▲' if delta > 0 else '▼'} {abs(delta):.1f} pts{suffix}"
    if better == "zero":
        closer = round(abs(value), 1) - round(abs(prior), 1)
        kind = "outline" if closer == 0 else ("ok" if closer < 0 else "bad")
    else:
        kind = "ok" if (delta > 0) == (better == "up") else "bad"
    verdict = {"ok": "Better", "bad": "Worse", "outline": "No better or worse"}[kind]
    return kit.pill(text, kind, title=f"{verdict} than the previous period of equal length ({BETTER_TEXT[better]})",
                    **{"data-trend": kind})


def _target(key, value, long=False):
    if key not in KPI_TARGETS or value is None or pd.isna(value):
        return None
    test, text, _ = KPI_TARGETS[key]
    meets = test(value)
    if long:
        return kit.pill(("Meets" if meets else "Misses") + f" team target {text}", "ok" if meets else "bad")
    return tags.span(tags.span(class_="m1-dot " + ("ok" if meets else "bad"), **{"aria-hidden": "true"}),
                     f"Target {text}", tags.span(" · met" if meets else " · missed", class_="m1-sr"),
                     class_="m1-kpi-target " + ("ok" if meets else "bad"), title="Team display target")


def kpi_tiles(c, p, selected="msl"):
    """Five compact KPI cards (value, change, target); the selected card's detail opens below the row."""
    tiles = []
    for key, label, field, pattern, better in KPI_TILES:
        value = c[field]
        on = key == selected
        tiles.append(tags.button(
            tags.span(label, class_="m1-kpi-label"),
            tags.span(_fmt(value, pattern), class_="m1-kpi-value"),
            tags.span(_delta(value, p[field], better), tags.span("vs prior period", class_="m1-kpi-vs"),
                      class_="m1-kpi-foot"),
            _target(key, value),
            type="button", class_="m1-kpi" + (" on" if on else ""), id=f"m1-kpi-{key}",
            onclick=f"Shiny.setInputValue('m1_kpi', '{key}', {{priority: 'event'}})",
            **{"data-key": key, "aria-pressed": "true" if on else "false",
               "aria-controls": "m1-kpi-detail"}))
    return tags.section(*tiles, class_="m1-kpis", id="m1-kpis",
                        **{"aria-label": "Business performance KPIs for the filtered stores; select one for its trend"})


def kpi_notes(key, c):
    """What the selected number rests on (in "How it's calculated", collapsed)."""
    gap_dates = f"{c['gap_first']:%b %d} – {c['gap_last']:%b %d, %Y}" if c.get("gap_first") else "no complete period"
    notes = {
        "msl": [f"Assessable coverage {_fmt(c['msl_coverage'])} of eligible store-days; "
                f"{c['msl_unknown'] or 0:,} store-days are Unknown and are not counted as failures."],
        "oos": [f"{c['oos_known'] or 0:,} store-SKU-days with known inventory; reporting coverage "
                f"{_fmt(c['coverage'])}. Unknown inventory is never treated as in stock."],
        "gap": [f"{_fmt(c['gap_units'], '{:+,.0f}')} units over {c['gap_periods']} complete {PERIOD_TYPE} periods "
                f"({gap_dates}); {c['gap_comparable_rows']:,} of {c['gap_rows']:,} rows comparable.",
                "Invoice-extract completeness is unconfirmed, so a missing invoice is not read as zero."
                if c["gap_completeness_unconfirmed"] else None],
        "shelf": [f"{c['visits']:,} audited category visits. Allocation is among the supplied SKUs of a category "
                  "(about 100% when every SKU is selected), not Marvel vs competitor shelf share; no target applies."],
        "plano": [f"{c['audited']:,} audited SKU rows."],
    }[key]
    if key in KPI_TARGETS:
        notes.append("The target line is a team display target, not a client-approved threshold.")
    return [n for n in notes if n]


KPI_READ = {
    "msl": "the share of store-days on which every must-stock product was on the shelf",
    "oos": "the share of product-days with zero stock; lower is better",
    "gap": "how far invoiced units ran ahead of (+) or behind (−) what consumers bought; 0 is ideal",
    "shelf": "the selected products' share of the audited shelf, among the client's own products in a category",
    "plano": "the share of audited products placed as the planogram says",
}


def trend_chart(key, frame, element_id):
    """The KPI per week (gap: per complete biweekly period) with its team target: a smooth line over a soft
    fill, no grid."""
    frame = frame[frame["value"].notna()] if frame is not None and len(frame) else frame
    if frame is None or not len(frame):
        return state("No trend for these filters", "No period in the range has a value for this KPI.")
    x = [pd.Timestamp(v).strftime("%Y-%m-%d") for v in frame["period"]]
    y = [float(v) for v in frame["value"]]
    lows, highs = [min(y)], [max(y)]
    target = KPI_TARGETS.get(key)
    lines = ()
    if target:
        lines = target[2] if isinstance(target[2], tuple) else (target[2],)
        lows.append(min(lines))
        highs.append(max(lines))
    pad = max(2.0, (max(highs) - min(lows)) * 0.15)
    low, high = min(lows) - pad, max(highs) + pad
    if key != "gap":
        low, high = max(0.0, low), min(100.0 + (5 if key == "shelf" else 0), high)
    color = CHART_COLORS[0]
    # A flat line at the bottom of the axis, so the soft fill ends there rather than at zero.
    fig = go.Figure(go.Scatter(x=x, y=[low] * len(y), mode="lines", line=dict(width=0), hoverinfo="skip",
                               showlegend=False))
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers", name=KPI_SPECS[key]["title"].replace(" %", ""), fill="tonexty",
        fillgradient=dict(type="vertical", colorscale=[[0, _rgba(color, 0.0)], [1, _rgba(color, 0.20)]]),
        line=dict(color=color, width=2.5, shape="spline", smoothing=0.6),
        marker=dict(size=7, color="#ffffff", line=dict(color=color, width=2)),
        hovertemplate=("Biweekly period from" if key == "gap" else "Week of") + " %{x|%b %d, %Y}<br>"
                      "<b>%{y:.1f}%</b><extra></extra>"))
    if key == "gap" and lines:
        fig.add_hrect(y0=lines[0], y1=lines[1], fillcolor="rgba(29,107,69,0.07)", line_width=0, layer="below")
    for value in lines:
        fig.add_hline(y=value, line=dict(color="#86121c", width=1.2, dash="dot"),
                      annotation=dict(text=f"Team target {target[1]}" if value == lines[-1] else "",
                                      font=dict(size=11, color="#86121c"), xanchor="right", x=1, yanchor="bottom"))
    layout = dict(showlegend=False, margin=dict(l=8, r=8, t=16, b=8), hovermode="x",
                  yaxis=dict(range=[low, high], ticksuffix="%", title=None, showgrid=False, zeroline=False,
                             showline=False, tickfont=dict(color="#8a7f80", size=11)),
                  xaxis=dict(tickformat="%b %d", title=None, showgrid=False, zeroline=False, showline=False,
                             tickfont=dict(color="#8a7f80", size=11), showspikes=True, spikemode="across",
                             spikethickness=1, spikedash="solid", spikecolor="#dfd4d4"))
    return plot(fig, element_id, f"{KPI_SPECS[key]['title']} trend", 300, layout)


def kpi_help(key, period):
    """How to read the selected KPI's trend (the "?" next to its title)."""
    lines = [f"The line is {KPI_READ[key]}, per {period}, for the stores, products and dates in the filters.",
             "Hover the chart for each " + period + "'s value."]
    if key in KPI_TARGETS:
        lines.append(f"The dotted line is the team's target ({KPI_TARGETS[key][1]}): "
                     + {"gap": "inside the green band is on target.", "oos": "points below it meet the target."}
                     .get(key, "points above it meet the target."))
    lines.append("On the cards, green means better than the previous period of the same length and red means "
                 "worse; the arrow only shows whether the number went up or down.")
    return kit.help_tip(KPI_SPECS[key]["title"].replace(" %", ""), tags.ul(*[tags.li(t) for t in lines]),
                        tags.p("Targets are set by the team for display; the client has not set them yet."
                               if key in KPI_TARGETS else "No target applies to this KPI.", class_="ws-help-data"),
                        id="m1-kpi-help")


def kpi_detail(key, c, p, trend, f):
    """Below the cards: the selected KPI's trend with target and (collapsed) how it is computed and what the
    number rests on."""
    spec = KPI_SPECS[key]
    _, _, field, pattern, better = next(t for t in KPI_TILES if t[0] == key)
    label = spec["title"].replace(" %", "")                  # the full name; the card uses a short one
    period = "complete biweekly period" if key == "gap" else "week"
    how = tags.details(tags.summary("How it's calculated"),
                       tags.dl(tags.dt("Definition"), tags.dd(spec["desc"]), tags.dt("Formula"),
                               tags.dd(spec["formula"]), tags.dt("Rules"), tags.dd(spec["logic"]),
                               tags.dt("This selection"), tags.dd(*[tags.div(n) for n in kpi_notes(key, c)],
                                                                  class_="m1-notes"),
                               tags.dt("Shared view"), tags.dd(spec["source"]), tags.dt("Components"),
                               tags.dd(spec["columns"])),
                       class_="ws-disclosure m1-how", id="m1-kpi-how")
    return tags.section(
        tags.div(tags.div(tags.div(tags.h2(label, class_="ws-h"), kpi_help(key, period), class_="ws-title-row"),
                          tags.p(f"{pd.Timestamp(f.start):%b %d} – {pd.Timestamp(f.end):%b %d, %Y}",
                                 class_="ws-muted m1-detail-range")),
                 tags.div(tags.span(_fmt(c[field], pattern), class_="m1-detail-value"),
                          _delta(c[field], p[field], better, long=True), _target(key, c[field], long=True),
                          class_="m1-detail-figures"),
                 class_="ws-card-head m1-detail-head"),
        trend_chart(key, trend, f"m1-trend-{key}"),
        how,
        class_="ws-card m1-kpi-detail", id="m1-kpi-detail", **{"aria-live": "polite"})


# ------------------------------------------------------------------ server

def module_1_server(input, output, session, context: dict, auth_state: dict):
    """Hosted entry point. ``context['runtime']`` is the pinned shared source (shared.runtime)."""
    runtime = context.get("runtime") if isinstance(context, dict) else None
    if runtime is None:
        return  # the legacy root app: the UI explains where the module runs; nothing is loaded
    module_label = context.get("module_label") or "Business Performance"
    active_module = auth_state.get("active_module") or (lambda: MODULE_KEY)
    data = reactive.value(None)
    load_state = reactive.value(("signed_out", ""))
    choice_info = reactive.value(None)
    synced = reactive.value(False)          # filters received from the browser after the per-session update
    generation = reactive.value(0)          # bumped on every identity change; late results are discarded
    page = reactive.value("overview")
    scope_summary = reactive.value(None)    # (stores, as_of) for the welcome line, read in the background

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return pid()

    navigate = auth_state.get("navigate")

    @reactive.effect
    @reactive.event(input.m1_open_copilot)
    def _open_copilot():
        """Top-bar Copilot button: host navigation only (no question or filter is passed along)."""
        if navigate is not None and principal() is not None:
            navigate("module_4")

    @reactive.extended_task
    async def load_task(pid, gen):
        def work():
            item = runtime.module1_pool().get(pid)
            return item, item.choices()
        try:
            item, choices = await asyncio.to_thread(work)
            return gen, pid, item, choices, None
        except PermissionError:
            return gen, pid, None, None, "Your account has no data scope in the shared publication."
        except Exception as error:  # sanitized: class name only
            reason = str(error) if type(error).__name__ in ("SourceUnavailable", "DataUnavailable") else \
                f"Shared data could not be loaded ({type(error).__name__})."
            return gen, pid, None, None, reason

    @reactive.effect
    async def _on_principal():
        pid = principal()
        with reactive.isolate():
            generation.set(generation() + 1)
            data.set(None)
            choice_info.set(None)
            synced.set(False)
            chosen_preset.set(DEFAULT_PRESET)
            scope_summary.set(None)
            load_state.set(("idle", "") if pid else ("signed_out", ""))
            page.set("overview")
            ui.update_navset("m1_pages", selected="overview")
        await session.send_custom_message("m1-clear", {})

    @reactive.effect
    def _load_on_first_visit():
        """The scoped copy is built when the module is first opened, not at sign-in."""
        pid, kind = principal(), load_state()[0]
        if pid and kind == "idle" and active_module() == MODULE_KEY:
            with reactive.isolate():
                load_state.set(("loading", "Loading your scoped shared data…"))
                load_task.invoke(pid, generation())
                scope_task.invoke(pid, generation())

    @reactive.extended_task
    async def scope_task(pid, gen):
        """The welcome line's store count and as-of date, read off the session (a slow connection never holds
        up the welcome itself). A failure just leaves the line without them; the load reports real problems."""
        def work():
            reader = runtime.reader()
            return reader.principal_scope(pid)["stores"], reader.record.get("as_of")
        try:
            return gen, pid, await asyncio.to_thread(work)
        except Exception:  # noqa: BLE001 - optional detail only
            return gen, pid, None

    @reactive.effect
    def _scope_read():
        if scope_task.status() != "success":
            return
        gen, pid, summary = scope_task.result()
        with reactive.isolate():
            if gen == generation() and pid == principal():
                scope_summary.set(summary)

    handled = {"result": None}

    @reactive.effect
    def _loaded():
        if load_task.status() != "success":
            return
        result = load_task.result()
        gen, pid, item, choices, reason = result
        with reactive.isolate():
            if result is handled["result"]:
                return
            handled["result"] = result
            if gen != generation() or pid != principal() or load_state()[0] != "loading":
                return  # a late result for a previous sign-in is discarded
            if item is None:
                load_state.set(("unavailable", reason))
                return
            data.set(item)
            choice_info.set(choices)
            load_state.set(("ready", ""))
            ui.update_checkbox_group("m1_channel", choices=choices["channels"], selected=choices["channels"])
            ui.update_checkbox_group("m1_region", choices=choices["regions"], selected=choices["regions"])
            ui.update_checkbox_group("m1_sku", choices=sku_choices(choices), selected=list(choices["products"]))
            ui.update_radio_buttons("m1_msl", selected="all")
            last, first = pd.Timestamp(choices["last_date"]), pd.Timestamp(choices["first_date"])
            start, end = preset_span(DEFAULT_PRESET, first, last)
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()),
                                 min=str(first.date()), max=str(last.date()))

    @reactive.effect
    def _filters_synced():
        info = choice_info()
        if info is None or synced.get():
            return
        dates = input.m1_dates()
        # The first values sent back after ui.update_* carry the scoped choices and dates.
        _, end = preset_span(DEFAULT_PRESET, info["first_date"], info["last_date"])
        if input.m1_sku() and dates and str(dates[1]) == str(end.date()):
            synced.set(True)

    @reactive.effect
    @reactive.event(input.m1_sku_all)
    def _select_all_skus():
        info = choice_info()
        if info:
            ui.update_checkbox_group("m1_sku", selected=msl_skus(info, input.m1_msl()))

    @reactive.effect
    @reactive.event(input.m1_msl)
    def _msl_narrows_skus():
        """MSL only / non-MSL only: the SKU list shows (and selects) just those products; All brings back all."""
        info = choice_info()
        if info:
            skus = msl_skus(info, input.m1_msl())
            ui.update_checkbox_group("m1_sku", choices=sku_choices(info, skus), selected=skus)

    @reactive.effect
    @reactive.event(input.m1_sku_none)
    def _deselect_all_skus():
        ui.update_checkbox_group("m1_sku", selected=[])

    chosen_preset = reactive.value(DEFAULT_PRESET)      # the preset last picked (several can cover the same weeks)

    @reactive.effect
    @reactive.event(input.m1_preset)
    def _apply_preset():
        info, key = choice_info(), input.m1_preset()
        if info and key in DATE_PRESETS:
            chosen_preset.set(key)
            start, end = preset_span(key, info["first_date"], info["last_date"])
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()))

    @reactive.effect
    @reactive.event(input.m1_reset)
    def _reset_filters():
        info = choice_info()
        if info:
            chosen_preset.set(DEFAULT_PRESET)
            ui.update_checkbox_group("m1_channel", selected=info["channels"])
            ui.update_checkbox_group("m1_region", selected=info["regions"])
            ui.update_checkbox_group("m1_sku", choices=sku_choices(info), selected=list(info["products"]))
            ui.update_radio_buttons("m1_msl", selected="all")
            start, end = preset_span(DEFAULT_PRESET, info["first_date"], info["last_date"])
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()))

    @reactive.effect
    @reactive.event(input.m1_go)
    async def _go():
        target = input.m1_go()
        if target in PAGES:
            page.set(target)
            ui.update_navset("m1_pages", selected=target)
            await session.send_custom_message("m1-scroll-top", {})

    # ---------------------------------------------------------- filters and scope
    @reactive.calc
    def span():
        info = choice_info()
        req(info and synced())
        dates = input.m1_dates()
        req(dates and dates[0] and dates[1])
        return snap(dates[0], dates[1], info["first_date"], info["last_date"])

    def make_filters(a, b):
        info = choice_info()
        channels = tuple(input.m1_channel()) if input.m1_channel() else tuple(info["channels"])
        regions = tuple(input.m1_region()) if input.m1_region() else tuple(info["regions"])
        skus = tuple(input.m1_sku()) if input.m1_sku() is not None else tuple(info["products"])
        msl = input.m1_msl() or "all"
        if msl in ("msl", "non"):                # the MSL pill narrows the selected SKUs to (non-)MSL products
            required = set(info.get("msl_products") or ())
            skus = tuple(s for s in skus if (s in required) == (msl == "msl"))
        return Filters(channels, regions, skus, set(skus) == set(info["products"]), a.date(), b.date())

    @reactive.calc
    def checked():
        """The principal's data after a fresh scope/publication check, or (None, reason)."""
        item = data()
        state_kind, reason = load_state()
        if item is None:
            return None, reason
        if synced():  # re-check whenever the filters change
            input.m1_dates(), input.m1_channel(), input.m1_region(), input.m1_sku(), input.m1_msl()
        try:
            item.check()
            return item, None
        except Exception as error:
            return None, ("Your account scope or the shared publication changed; sign out and sign in again "
                          f"({type(error).__name__}).")

    @reactive.calc
    def current():
        item, _ = checked()
        req(item)
        a, b = span()
        return item, make_filters(a, b)

    @reactive.calc
    def colors():
        info = choice_info()
        req(info)
        return {"channel": palette(info["channels"]), "region": palette(info["regions"]),
                "tier": palette(info["tiers"]), "category": palette(info["categories"])}

    # ---------------------------------------------------------- navigation and header
    @output(suspend_when_hidden=False)               # in the app sidebar, hidden while another module is open
    @render.ui
    def m1_nav():
        if principal() is None:
            return None
        return tags.nav(*[kit.nav_item(PAGE_ICONS[key], text, active=page() == key, onclick=f"m1Go('{key}')",
                                       **{"data-page": key}) for key, text in PAGES.items()],
                        class_="ws-subnav-items", **{"aria-label": f"{module_label} pages"})

    @render.ui
    def m1_crumb():
        return tags.nav(tags.span(module_label), tags.span("/", **{"aria-hidden": "true"}), tags.strong(PAGES[page()]),
                        class_="ws-crumb", **{"aria-label": "Breadcrumb"})

    @render.ui
    def m1_user_block():
        name, title = auth_state["name"](), auth_state["title"]()
        avatar = auth_state.get("avatar")
        return None if name is None else kit.user_block(name, title, id="m1-user",
                                                        avatar_src=avatar() if avatar else None)

    @output(suspend_when_hidden=False)
    @render.text
    def m1_ready():
        return "yes" if checked()[0] is not None else "no"

    @render.ui
    @safe
    def m1_page_head():
        if principal() is None:
            return None
        title, sub = PAGE_HEADS[page()]
        item, _ = checked()
        if item is None:  # the welcome screen stands in for the page head while the first load runs
            return None if load_state()[0] in ("idle", "loading") else kit.page_head(title, sub=sub)
        scope = item.scope
        role = "Regional manager view" if item.manager else "Field sales rep view"
        meta = [tags.span(tags.b(role), f" · {scope['stores']} stores in your demo scope"),
                tags.span("Data through ", tags.b(str(item.record.get("as_of")))),
                kit.pill("Reported data only", "outline")]
        return kit.page_head(title, *meta, sub=sub)

    @render.text
    def m1_welcome_detail():
        summary = scope_summary()
        return welcome_detail(*summary) if summary else welcome_detail()

    @render.ui
    def m1_status():
        kind, reason = load_state()
        item, check_reason = checked()
        if kind in ("signed_out",) or item is not None:
            return None
        if kind in ("idle", "loading"):
            with reactive.isolate():
                clock = input.m1_client_clock() if "m1_client_clock" in input else None
            return welcome(auth_state["name"](), clock, ui.output_text("m1_welcome_detail", inline=True))
        return kit.notice(tags.strong("Business Performance is unavailable. "), check_reason or reason or "",
                          kind="bad", role="alert", id="m1-unavailable")

    # ---------------------------------------------------------- filter pills
    def pill_value(text, changed, extra=None):
        return TagList(tags.span(text, class_="m1-pill-val ws-fpill-val" + (" set" if changed else "")),
                       tags.span(extra, class_="m1-pill-extra ws-fpill-extra") if extra else None)

    @render.ui
    def m1_pill_dates():
        info = choice_info()
        req(info)
        a, b = span()
        key = matching_preset((a, b), info["first_date"], info["last_date"], chosen_preset())
        dates = f"{a:%b %d} – {b:%b %d, %Y}"
        if key is None:
            return pill_value(dates, True)
        return pill_value(DATE_PRESETS[key], key != DEFAULT_PRESET, dates)

    @render.ui
    def m1_presets():
        info = choice_info()
        req(info)
        key = matching_preset(span(), info["first_date"], info["last_date"], chosen_preset())
        return tags.div(*[tags.button(label, type="button", class_="m1-preset" + (" on" if k == key else ""),
                                      onclick=f"Shiny.setInputValue('m1_preset', '{k}', {{priority: 'event'}})",
                                      **{"aria-pressed": "true" if k == key else "false"})
                          for k, label in DATE_PRESETS.items()], class_="m1-presets")

    @render.ui
    def m1_pill_channel():
        info = choice_info()
        req(info)
        return TagList(tags.span("Channel", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_channel(), {c: c for c in info["channels"]})))

    @render.ui
    def m1_pill_region():
        info = choice_info()
        req(info)
        return TagList(tags.span("Region", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_region(), {r: r for r in info["regions"]})))

    @render.ui
    def m1_pill_msl():
        req(choice_info())
        value = input.m1_msl() or "all"
        return TagList(tags.span("Products", class_="m1-pill-key ws-fpill-key"),
                       pill_value(MSL_PILL.get(value, MSL_PILL["all"]), value != "all"))

    @render.ui
    def m1_pill_sku():
        info = choice_info()
        req(info)
        names = info.get("product_names") or info["products"]
        shown = {s: names.get(s, s) for s in msl_skus(info, input.m1_msl())}
        return TagList(tags.span("SKU", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_sku(), shown, empty_means_all=False)))

    @render.ui
    def m1_sku_count():
        info = choice_info()
        req(info)
        shown = msl_skus(info, input.m1_msl())
        return tags.span(f"{len([s for s in input.m1_sku() or () if s in shown])} of {len(shown)} selected",
                         class_="ws-muted")

    @render.ui
    @safe
    def m1_filter_summary():
        info = choice_info()
        item, f = current()
        changed = (choice_label(input.m1_channel(), {c: c for c in info["channels"]})[1]
                   or choice_label(input.m1_region(), {r: r for r in info["regions"]})[1]
                   or choice_label(input.m1_sku(), {s: s for s in msl_skus(info, input.m1_msl())},
                                   empty_means_all=False)[1]
                   or (input.m1_msl() or "all") != "all"
                   or matching_preset(span(), info["first_date"], info["last_date"], chosen_preset()) != DEFAULT_PRESET)
        reset = tags.button("Reset filters", type="button", class_="m1-reset", id="m1-reset",
                            onclick="Shiny.setInputValue('m1_reset', Date.now(), {priority: 'event'})") \
            if changed else None
        return TagList(reset, tags.span("Showing ", tags.b(f"{item.store_count(f)} stores"), " · ",
                                        tags.b(f"{len(f.skus)} SKUs"), class_="m1-fnote"))

    # ---------------------------------------------------------- KPIs
    @reactive.calc
    def kpi_values():
        item, f = current()
        a, b = pd.Timestamp(f.start), pd.Timestamp(f.end)
        n = (b - a).days + 1
        prior = Filters(f.channels, f.regions, f.skus, f.all_skus, (a - pd.Timedelta(days=n)).date(),
                        (a - pd.Timedelta(days=1)).date())
        return item.kpis(f), item.kpis(prior)

    selected_kpi = reactive.value("msl")

    @reactive.effect
    @reactive.event(input.m1_kpi)
    def _select_kpi():
        if input.m1_kpi() in KPI_SPECS:
            selected_kpi.set(input.m1_kpi())

    @render.ui
    @safe
    def m1_kpis():
        if not current()[1].skus:
            return state("No SKU selected", "Select at least one product SKU, or another MSL option, in the filters.")
        return kpi_tiles(*kpi_values(), selected=selected_kpi())

    @reactive.calc
    def kpi_trend():
        item, f = current()
        return item.kpi_trend(f, selected_kpi())

    @render.ui
    @safe
    def m1_kpi_detail():
        item, f = current()
        if not f.skus:
            return None
        c, p = kpi_values()
        return kpi_detail(selected_kpi(), c, p, kpi_trend(), f)

    # ---------------------------------------------------------- charts
    for output_id, (_, option_id, _, _) in CHARTS.items():
        def bind(output_id=output_id, option_id=option_id):
            @output(id=output_id)
            @render.ui
            @safe
            def _chart():
                item, f = current()
                option = input[option_id]() if option_id else None
                return render_chart(output_id, item, f, option, colors())
        bind()
