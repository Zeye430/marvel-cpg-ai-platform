"""
modules/module_1/charts.py
==========================
Module 1 chart builders (pure: scoped data + filters -> Chart) and the Plotly plumbing.

Charts are rendered like Module 2's (Plotly JSON + the workspace's local plotly.js). Every figure is
checked for strict JSON first: a NaN or infinite value can never reach the browser and close the
session. Values follow the page's data view (Reported, or the explicit Estimated view); Unknown stays
Unknown, and heuristics or unapproved rules are labelled as such. No chart draws a team target or the
brief's 62% background figure as a pass mark.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from htmltools import TagList, tags
from plotly.subplots import make_subplots
from shiny import ui

from shared import ui_kit as kit
from shared.chart_palette import CHART_COLORS, CHART_NEUTRAL

from .data import ABOVE_CEILING, PERIOD_TYPE, WITHIN_CEILING, aggregate
from .ui import TOPOJSON_URL

HEATMAP_TOP_STORES = 25
CITY_FILE = Path(__file__).with_name("reference") / "city_centroids.csv"
ZERO, STOCKED = CHART_COLORS[7], CHART_COLORS[0]
UNKNOWN_FILL = "#d9d2d2"


# ------------------------------------------------------------------ plumbing

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


def plot(fig, element_id, label, height, layout=None, click_input=None):
    """One validated Plotly figure as HTML for the workspace's local plotly.js (``layout``: chart overrides).
    ``click_input``: a Shiny input that receives the first ``customdata`` value of a clicked point."""
    chart_layout(fig, height)
    if layout:
        fig.update_layout(**layout)
    payload = figure_json(fig)
    config = json.dumps({"displayModeBar": False, "responsive": True, "topojsonURL": TOPOJSON_URL})
    click = ""
    if click_input:
        click = (f"el.on('plotly_click',function(d){{var p=d&&d.points&&d.points[0];"
                 f"if(p&&p.customdata){{var v=Array.isArray(p.customdata)?p.customdata[0]:p.customdata;"
                 f"Shiny.setInputValue('{click_input}',v,{{priority:'event'}});}}}});")
    script = (f"(function(){{var f={payload};var el=document.getElementById('{element_id}');"
              f"if(el&&window.Plotly){{Plotly.newPlot(el,f.data,f.layout,{config}).then(function(){{{click}}});}}}})();")
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
    before: object = None        # markup above the figure (e.g. summary figures)
    after: object = None         # markup below the figure (e.g. the ranking table)
    click_input: str = None


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


def fmt(value, pattern="{:.1f}%"):
    return "N/A" if value is None or pd.isna(value) else pattern.format(value)


def fdate(value, pattern="%b %d, %Y"):
    return "—" if value is None or pd.isna(value) else f"{pd.Timestamp(value):{pattern}}"


def state(title, text, kind="info"):
    return tags.div(tags.strong(title), tags.div(text), class_=f"ws-state m1-state {kind}", role="status")


def store_labels(frame, name="store_name", key="store_id"):
    """Store display names; a name used by two stores gets its id, so different stores never merge."""
    counts = frame.groupby(name)[key].nunique()
    return {k: (n if counts.get(n, 0) <= 1 else f"{n} ({k})")
            for k, n in frame[[key, name]].drop_duplicates().itertuples(index=False)}


def table(head, rows, table_id=None, caption=None, class_=""):
    return tags.div(tags.table(tags.caption(caption, class_="m1-sr") if caption else None,
                               tags.thead(tags.tr(*[tags.th(h, scope="col") for h in head])),
                               tags.tbody(*rows), class_=f"ws-table m1-table {class_}".strip(), id=table_id),
                    class_="ws-table-wrap m1-table-wrap")


# ------------------------------------------------------------------ chart builders (pure: data -> Chart)

STATUS_CODES = {"zero": 0, "zero_est": 1, "unknown": 2, "stocked_est": 3, "stocked": 4}
STATUS_TEXT = {0: "Zero stock", 1: "Zero stock (estimated)", 2: "Unknown (not reported)",
               3: "In stock (estimated)", 4: "In stock"}


def _status_code(inventory, status):
    if inventory is None or pd.isna(inventory):
        return STATUS_CODES["unknown"]
    estimated = status == "Estimated"
    if inventory == 0:
        return STATUS_CODES["zero_est" if estimated else "zero"]
    return STATUS_CODES["stocked_est" if estimated else "stocked"]


def chart_heatmap(item, f, option, colors):
    """Stock status per store and product on the latest day: zero, in stock or Unknown (never zero), with
    estimated cells marked in the Estimated view. Colour is the status, not a stock-level judgement."""
    frame = item.latest_inventory(f, top=None if not item.manager else HEATMAP_TOP_STORES)
    if frame.empty:
        return Chart(empty=("No store-SKU rows", "No assortment rows match the selected filters and dates."))
    latest = frame["date"].max()
    labels = store_labels(frame)
    frame = frame.assign(store=frame["store_id"].map(labels),
                         code=[_status_code(i, s) for i, s in zip(frame["inventory"], frame["inventory_data_status"])])
    frame["text"] = [("Unknown" if c == 2 else (f"~{i:.0f}" if c in (1, 3) else f"{i:.0f}"))
                     for i, c in zip(frame["inventory"], frame["code"])]
    codes = frame.pivot_table(index="store", columns="product_name", values="code", aggfunc="first")
    text = frame.pivot_table(index="store", columns="product_name", values="text", aggfunc="first")
    text = text.reindex(index=codes.index, columns=codes.columns)
    hover = np.vectorize(lambda c: "" if pd.isna(c) else STATUS_TEXT[int(c)])(codes.values.astype(float))
    scale = [[0.0, ZERO], [0.2, ZERO], [0.2, _rgba(ZERO, 0.45)], [0.4, _rgba(ZERO, 0.45)],
             [0.4, UNKNOWN_FILL], [0.6, UNKNOWN_FILL], [0.6, _rgba(STOCKED, 0.35)], [0.8, _rgba(STOCKED, 0.35)],
             [0.8, _rgba(STOCKED, 0.75)], [1.0, _rgba(STOCKED, 0.75)]]
    fig = go.Figure(go.Heatmap(
        z=codes.values, x=[c.replace("Marvel ", "") for c in codes.columns], y=list(codes.index),
        text=text.fillna("").values, customdata=hover, texttemplate="%{text}", zmin=-0.5, zmax=4.5,
        colorscale=scale, showscale=False, xgap=1, ygap=1,
        hovertemplate=f"%{{y}}<br>%{{x}}<br>%{{customdata}} on {pd.Timestamp(latest):%b %d, %Y}: %{{text}} units"
                      "<extra></extra>"))
    fig.update_xaxes(side="top", tickangle=-30, showgrid=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    msl = bool(frame["is_msl"].fillna(False).all())
    shown, total = frame["store_id"].nunique(), frame.attrs.get("stores_total", frame["store_id"].nunique())
    which = "Must-stock (MSL) products" if msl else "Selected products (no must-stock product selected)"
    count = (f"{shown} of {total} stores with the most zero-stock products" if shown < total else f"{shown} stores")
    legend = tags.div(*[tags.span(tags.span(class_="m1-swatch", style=f"background:{c}"), t, class_="m1-key")
                        for c, t in ((ZERO, "Zero stock"), (UNKNOWN_FILL, "Unknown (not reported)"),
                                     (_rgba(STOCKED, 0.75), "In stock"))
                        + (((_rgba(ZERO, 0.45), "~ estimated zero"), (_rgba(STOCKED, 0.35), "~ estimated in stock"))
                           if item.estimated else ())],
                      class_="m1-legend")
    return Chart(fig, "", max(460, min(720, len(codes) * 24 + 150)),
                 layout={"plot_bgcolor": "#efeaea",
                         "title": {"text": f"{which} on {pd.Timestamp(latest):%b %d, %Y} · {count}", "x": 0,
                                   "xanchor": "left", "y": 0.995, "yanchor": "top",
                                   "font": {"size": 12, "color": "#65595a"}}},
                 after=legend)


def chart_lost_sales(item, f, option, colors):
    """Heuristic exposure trend; not the shared missing-day estimator."""
    group_by = option or "region"
    frame = item.lost_sales_heuristic(f, group_by)
    if frame.empty:
        return Chart(empty=("No zero-stock days", "No zero-stock days with an in-stock baseline in the selected "
                                                  "scope."))
    order = list(colors[group_by])
    fig = px.area(frame, x="week", y="lost_value", color="grp", color_discrete_map=colors[group_by],
                  category_orders={"grp": order},
                  labels={"lost_value": "Heuristic exposure (static list price)", "week": "",
                          "grp": group_by.capitalize()})
    fig.update_traces(line=dict(width=2))
    fig.update_yaxes(tickformat=",.0f")
    return Chart(fig, "", 440)


def chart_duration(item, f, option, colors):
    """Consecutive zero-stock days; a calendar gap or Unknown day ends an episode."""
    group_col = option or "channel"
    episodes = item.oos_episodes(f, group_col)
    if episodes.empty:
        return Chart(empty=("No stockout episodes", "No zero-stock days in the selected period."))
    key = "channel" if group_col == "channel" else "tier"
    fig = px.box(episodes, x="grp", y="days", color="grp", points="outliers", color_discrete_map=colors[key],
                 category_orders={"grp": list(colors[key])},
                 labels={"days": "Consecutive zero-stock days per episode", "grp": ""})
    fig.update_layout(showlegend=False)
    return Chart(fig, "", 420)


def chart_compliance(item, f, option, colors):
    """Planogram compliance (audited SKU rows) and must-stock availability (SKU-days) per brand. Two
    different denominators, so no common pass line is drawn."""
    merged = item.execution_by_brand(f)
    if merged.empty:
        return Chart(empty=("No audits or MSL rows", "Nothing matches the selected filters."))
    merged["brand_key"] = merged["category"] + " · " + merged["brand"]
    names = {"planogram_pct": "Planogram compliance % (audited SKU rows)",
             "msl_availability_pct": "Must-stock availability % (SKU-days with known stock)"}
    dens = {"planogram_pct": "audited", "msl_availability_pct": "known"}
    rows = []
    for metric, label in names.items():
        for r in merged.itertuples(index=False):
            value = getattr(r, metric)
            if value is None or pd.isna(value):
                continue
            rows.append({"brand_key": r.brand_key, "Metric": label, "Percentage": float(value),
                         "base": int(getattr(r, dens[metric]) or 0)})
    melted = pd.DataFrame(rows)
    if melted.empty:
        return Chart(empty=("No audited rows or MSL SKUs", "Every group is N/A for the selected filters."))
    fig = px.bar(melted, y="brand_key", x="Percentage", color="Metric", barmode="group", orientation="h",
                 text_auto=".1f", labels={"Percentage": "Rate (%)", "brand_key": ""}, custom_data=["base"],
                 category_orders={"Metric": list(names.values())},
                 color_discrete_map={names["planogram_pct"]: CHART_COLORS[0],
                                     names["msl_availability_pct"]: CHART_COLORS[1]})
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}<br>%{fullData.name}: %{x:.1f}%<br>Based on %{customdata[0]:,} rows"
                                    "<extra></extra>")
    fig.update_xaxes(range=[0, 108], ticksuffix="%")
    return Chart(fig, "", max(420, 80 + 44 * merged["brand_key"].nunique()))


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


# ---------------------------------------------------------------- map

# One sequential hue for every state measure (darker = higher); red is kept for the city bubbles that flag a
# must-stock product at zero, so the two layers never blend.
STATE_SCALE = ["#e3eefb", STOCKED, "#174a8c"]
GEO_METRICS = {"oos": ("oos_pct", "OOS rate %", "Darker = a larger share of product-days at zero stock", STATE_SCALE),
               "msl": ("msl_pct", "MSL compliance %", "Darker = more store-days with every must-stock product",
                       STATE_SCALE),
               "sales": ("net_sales", "Invoiced net sales", "Darker = more invoiced net sales (no currency)",
                         STATE_SCALE)}


def city_points():
    """The public city reference points (see reference/README.md); missing file: no points, not an error."""
    if not CITY_FILE.exists():
        return pd.DataFrame(columns=["city", "state", "latitude", "longitude"])
    return pd.read_csv(CITY_FILE, dtype={"geoid": str})[["city", "state", "latitude", "longitude"]]


def city_key(city, state_code):
    return f"{city}|{state_code}"


def city_frame(stores):
    """Store metrics summed to city + state, joined to the reference points (cities without one: no point)."""
    cities = aggregate(stores, ["city", "state"])
    if not len(cities):
        return cities
    cities = cities.merge(city_points(), on=["city", "state"], how="left")
    cities["key"] = [city_key(c, s) for c, s in zip(cities["city"], cities["state"])]
    return cities


def _bubble_group(row):
    if row["stores_with_msl_zero"] > 0:
        return "flag"
    if pd.isna(row["msl_required_latest"]) or not row["msl_required_latest"] or row["msl_unknown_latest"]:
        return "unknown"
    return "clear"


def chart_geo(item, f, option, colors, stores=None):
    """States shaded by one metric of the scoped stores, plus one bubble per city (an approximate city point,
    not a store location); a click on a bubble lists that city's stores."""
    stores = item.store_metrics(f) if stores is None else stores
    if stores.empty:
        return Chart(empty=("No stores", "No stores in your scope match the selected filters."))
    metric = option if option in GEO_METRICS else "oos"
    column, label, reading, scale = GEO_METRICS[metric]
    states = aggregate(stores, ["state"])
    states[column] = pd.to_numeric(states[column], errors="coerce")
    custom = np.stack([states["stores"], states["oos_pct"].astype(float), states["msl_pct"].astype(float),
                       states["coverage_pct"].astype(float), states["net_sales"].astype(float)], axis=-1)
    fig = go.Figure(go.Choropleth(
        locations=states["state"], z=states[column], locationmode="USA-states", colorscale=scale,
        marker_line_color="#ffffff", marker_line_width=1, customdata=custom, name="States",
        colorbar=dict(title=None, thickness=10, orientation="h", y=-0.02, yanchor="top", len=0.5, x=0.5,
                      xanchor="center", ticksuffix="%" if metric != "sales" else ""),
        hovertemplate="<b>%{location}</b><br>Stores: %{customdata[0]:,}<br>OOS rate: %{customdata[1]:.1f}%"
                      "<br>MSL compliance: %{customdata[2]:.1f}%<br>Known stock coverage: %{customdata[3]:.1f}%"
                      "<br>Invoiced net sales: %{customdata[4]:,.0f}<extra></extra>"))
    cities = city_frame(stores)
    placed = cities.dropna(subset=["latitude", "longitude"])
    groups = {"flag": ("A store with a must-stock product at zero", ZERO),
              "clear": ("No must-stock product at zero", "#ffffff"),
              "unknown": ("Must-stock status unknown or not applicable", CHART_NEUTRAL)}
    if len(placed):
        placed = placed.assign(group=placed.apply(_bubble_group, axis=1))
        biggest = max(1, int(placed["stores"].max()))
        for group, (name, color) in groups.items():
            part = placed[placed["group"] == group]
            if not len(part):
                continue
            cd = np.stack([part["key"], part["city"], part["stores"], part["stores_with_msl_zero"],
                           part["oos_pct"].astype(float), part["msl_pct"].astype(float),
                           part["net_sales"].astype(float)], axis=-1)
            fig.add_trace(go.Scattergeo(
                lat=part["latitude"], lon=part["longitude"], mode="markers", name=name, customdata=cd,
                marker=dict(size=[8 + 14 * (n / biggest) ** 0.5 for n in part["stores"]], color=color, opacity=0.95,
                            line=dict(width=1.5, color="#211b1c" if group == "clear" else "#ffffff")),
                hovertemplate="<b>%{customdata[1]}</b> (city-level point)<br>Stores: %{customdata[2]}"
                              "<br>With a must-stock product at zero: %{customdata[3]}<br>OOS rate: "
                              "%{customdata[4]:.1f}%<br>MSL compliance: %{customdata[5]:.1f}%<br>Invoiced net sales: "
                              "%{customdata[6]:,.0f}<br>Click to list the stores<extra></extra>"))
    fig.update_layout(geo=dict(scope="usa", bgcolor="rgba(0,0,0,0)", showlakes=False, landcolor="#f3eded",
                               subunitcolor="#dfd4d4"))
    # Worst first for OOS (high) and MSL (low); largest first for sales.
    ranked = states.sort_values(column, ascending=metric == "msl", na_position="last")
    pattern = "{:,.0f}" if metric == "sales" else "{:.1f}%"
    rows = [tags.tr(tags.td(r.state), tags.td(f"{int(r.stores):,}"), tags.td(fmt(getattr(r, column), pattern)),
                    tags.td(fmt(r.coverage_pct)), tags.td(f"{int(r.stores_with_msl_zero):,}"))
            for r in ranked.itertuples(index=False)]
    unplaced = int(cities["latitude"].isna().sum()) if len(cities) else 0
    after = TagList(
        tags.p(f"Shading: {label}. {reading}. Grey or blank states have no stores in your scope or no value. "
               "Bubbles are city-level points from the U.S. Census place file, not store addresses; size is the "
               "number of stores." + (f" {unplaced} cities have no reference point and are only in the lists."
                                      if unplaced else ""), class_="ws-muted m1-map-note"),
        tags.details(tags.summary(f"State ranking by {label}"),
                     table(["State", "Stores", label, "Known stock coverage", "Stores with an MSL product at zero"],
                           rows, table_id="m1-state-table", caption=f"States by {label}"),
                     class_="ws-disclosure m1-rank", id="m1-state-rank"))
    return Chart(fig, "", 520, layout={"legend": {"y": 1.0}, "margin": {"t": 30}}, after=after,
                 click_input="m1_city")


# ---------------------------------------------------------------- sell-in vs sell-out

def chart_flow(item, f, option, colors):
    """Comparable sell-in and sell-out units per complete biweekly period, and their signed gap (zero
    baseline; a period without comparable flows is a break, never zero). Replaces the Sankey, which hid
    negative gaps."""
    trend = item.flow_trend(f)
    if trend.empty or trend["sell_out"].notna().sum() == 0:
        return Chart(empty=("No comparable flows", f"No complete {PERIOD_TYPE} periods with both flows known in the "
                                                   "selected range. Choose a longer date range."))
    x = [pd.Timestamp(v).strftime("%Y-%m-%d") for v in trend["period_start"]]
    ends = [fdate(v, "%b %d") for v in trend["period_end"]]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38], vertical_spacing=0.08)
    for column, name, color in (("sell_in", "Sell-in (invoiced units)", CHART_COLORS[0]),
                                ("sell_out", "Sell-out (consumer units)", CHART_COLORS[1])):
        fig.add_trace(go.Scatter(x=x, y=trend[column].astype(float), name=name, mode="lines+markers",
                                 connectgaps=False, line=dict(color=color, width=2), marker=dict(size=8),
                                 customdata=ends, hovertemplate=f"{name}<br>%{{x|%b %d}} – %{{customdata}}: "
                                                                "%{y:,.0f}<extra></extra>"), row=1, col=1)
    gap = trend["gap_units"].astype(float)
    pct = [None if v is None or pd.isna(v) else float(v) for v in trend["gap_pct"]]
    fig.add_trace(go.Bar(x=x, y=gap, name="Gap (sell-in − sell-out)", showlegend=True,
                         marker_color=[CHART_COLORS[0] if v >= 0 else CHART_COLORS[1] for v in gap.fillna(0)],
                         customdata=np.stack([ends, [fmt(p, "{:+.1f}%") for p in pct]], axis=-1),
                         hovertemplate="Gap %{x|%b %d} – %{customdata[0]}: %{y:+,.0f} units (%{customdata[1]})"
                                       "<extra></extra>"), row=2, col=1)
    fig.update_yaxes(title_text="Units", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(title_text="Gap units", tickformat="+,.0f", zeroline=True, zerolinecolor="#65595a",
                     zerolinewidth=1, row=2, col=1)
    fig.update_xaxes(tickformat="%b %d", row=2, col=1)
    periods = int(trend["sell_out"].notna().sum())
    hint = kit.notice(f"Only {periods} complete biweekly period{'s' if periods != 1 else ''} in this date range. "
                      "Choose “Quarter to date” or “Year to date” in the date filter to see how long a gap has lasted.",
                      kind="info", id="m1-flow-hint") if periods < 3 else None
    return Chart(fig, "", 480, layout={"hovermode": "x unified", "bargap": 0.35}, before=hint)


# output id -> (builder, option input, regional manager only, accessible label)
CHARTS = {
    "m1_chart9_geo_map": (chart_geo, "m1_c9_metric", False, "Map of states and store cities"),
    "m1_chart10_flow": (chart_flow, None, False, "Sell-in and sell-out units per biweekly period with the gap"),
    "m1_chart1_heatmap": (chart_heatmap, None, False, "Stock status by store and product"),
    "m1_chart2_lost_sales": (chart_lost_sales, "m1_c2_group", True, "Lost sales heuristic trend"),
    "m1_chart3_duration": (chart_duration, "m1_c3_group", True, "OOS duration distribution"),
    "m1_chart4_compliance": (chart_compliance, None, False, "Planogram and MSL availability by brand"),
    "m1_chart7_discount": (chart_discount, None, False, "Discount vs margin scatter"),
    "m1_chart8_tradeoff": (chart_tradeoff, None, True, "Trade-off commitment evidence"),
}
LOCKED_TITLES = {"m1_chart2_lost_sales": "The lost sales trend", "m1_chart3_duration": "The OOS duration distribution",
                 "m1_chart8_tradeoff": "The trade-off evidence"}


def locked(title):
    return tags.div(tags.strong("Regional manager access required"),
                    tags.span(f"{title} is part of the regional manager view. Field sales reps see their own stores "
                              "on every other page."),
                    class_="ws-state m1-locked", role="note")


def render_chart(output_id, item, f, option, colors, **extra):
    """The chart's markup for one principal's data and filters (locked, empty or the figure)."""
    builder, _, manager_only, label = CHARTS[output_id]
    if manager_only and not item.manager:
        return locked(LOCKED_TITLES[output_id])
    if not f.skus:
        return state("No SKU selected", "Select at least one product SKU in the filters.")
    chart = builder(item, f, option, colors, **extra)
    if chart.empty is not None:
        return state(*chart.empty)
    element = output_id.replace("_", "-") + "-plot"
    return TagList(chart.before, plot(chart.fig, element, label, chart.height, chart.layout, chart.click_input),
                   chart.after, tags.p(chart.note, class_="ws-muted m1-caveat") if chart.note else None)
