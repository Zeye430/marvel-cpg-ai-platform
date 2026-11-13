"""
modules/module_1/panels.py
==========================
Module 1 HTML panels (pure: values -> markup): the data-view toggle, the five KPI cards and the selected
card's detail, the store exception list, the store card, the sell-in/sell-out breakdown and the stores of a
map city.

Rules kept here: no colour or verdict comes from a team target (the reference line is opt-in and labelled as
team-set); the brief's shelf share is reported as not available rather than replaced by the own-SKU
allocation; Unknown is never zero; every rate states what it is a share of; peer comparisons use only
stores in the viewer's own scope; actions describe what to check, not a cause.
"""

import pandas as pd
import plotly.graph_objects as go
from htmltools import TagList, tags
from shiny import ui

from shared import ui_kit as kit
from shared.chart_palette import CHART_COLORS

from .charts import _rgba, fdate, fmt, plot, state, table
from .data import PERIOD_TYPE, SCORE_MIN_MEASURES, aggregate, trend_direction

PEERS_MIN = 4             # fewer comparable stores in scope: no peer comparison
FLOW_ROWS = 15

# ------------------------------------------------------------------ KPI definitions

KPI_SPECS = {
    "msl": {
        "title": "MSL Compliance %",
        "measures": "Whether every product on the must-stock list (MSL) had stock in the store.",
        "use": "Managers: find regions, channels and stores where must-stock products go missing. Reps: see "
               "which of your stores miss a must-stock product before the next visit.",
        "formula": "100 × compliant store-days ÷ assessable store-days",
        "logic": "Noncompliant if any applicable MSL product is known at zero; Compliant if all are known above "
                 "zero; otherwise Unknown (not a failure). A store with no applicable MSL product is N/A. Over "
                 "several days this is a share of store-days, not of distinct stores.",
        "example": "Of 100 assessable store-days, 80 had every must-stock product in stock: 80%. If another 20 "
                   "store-days were Unknown, coverage is 100 of 120 and those 20 count neither way. "
                   "(Illustrative numbers.)",
        "limits": "Stock above zero stands in for availability; it does not prove the product is on the shelf. "
                  "Whether the MSL differs by channel or tier is still to be confirmed with the client.",
        "next": "The store ranking and store cards below, and the stock status heatmap on "
                "Out-of-stock.",
        "source": "store_day (all SKUs) or store_sku_daily (selected SKUs) · shared_kpi.v1",
        "columns": "msl_compliant_store_count, msl_assessable_store_count, msl_eligible_store_count",
    },
    "oos": {
        "title": "Reported OOS Rate %",
        "title_estimated": "OOS Rate %",
        "measures": "How often a product had zero stock, among product-days whose stock is known.",
        "use": "Find the products, stores, channels and regions where stockouts happen most often. Frequent "
               "stockouts suggest missed sales; the rate itself is not a lost-sales amount.",
        "formula": "100 × zero-stock store-SKU-days ÷ store-SKU-days with known stock",
        "logic": "Unknown stock is left out of the denominator and shown as coverage; it is never treated as in "
                 "stock. Numerators and denominators are summed first, then divided.",
        "example": "1,000 known store-SKU-days with 50 at zero: 5%. Another 200 Unknown days are reported as "
                   "coverage, never as in stock. (Illustrative numbers.)",
        "limits": "A frequency of product-day records, not the share of affected stores, and not when in the day "
                  "the stockout happened.",
        "next": "The store ranking and store cards; the heatmap and stockout duration on Out-of-stock.",
        "source": "store_day (all SKUs) or store_sku_daily (selected SKUs) · shared_kpi.v1",
        "columns": "oos_sku_count, known_inventory_sku_count, observed_inventory_sku_count, expected_sku_count",
    },
    "gap": {
        "title": "Sell-in vs Sell-out Gap %",
        "measures": "Whether units invoiced to the channel (sell-in) match the units consumers bought (sell-out).",
        "use": "A gap that stays positive is worth checking for stock building up in the channel; one that stays "
               "negative for replenishment falling behind consumer demand.",
        "formula": f"100 × (sell-in − sell-out units) ÷ sell-out units, over complete comparable {PERIOD_TYPE}ly "
                   "periods",
        "logic": "Only complete periods fully inside the date range and rows where both flows are known. A missing "
                 "invoice is not zero while invoice-extract completeness is unconfirmed. Zero sell-out gives N/A.",
        "example": "Sell-out 100 units and sell-in 120: +20%. Sell-in 80: −20%. (Illustrative numbers.)",
        "limits": "A flow difference, not measured stock, revenue or profit; a single period can reflect delivery "
                  "timing. Sell-in is invoiced, not received, stock.",
        "next": "Geography & flow › Sell-in vs sell-out: each period, how long a gap has lasted and where.",
        "source": f"commercial_period · period_type={PERIOD_TYPE}, alignment_basis=event_date · shared_kpi.v1",
        "columns": "comparable_gap_units, comparable_sell_out_units, flow_is_comparable, period_is_complete",
    },
    "shelf": {
        "title": "Audited SKU Shelf Allocation %",
        "measures": "The selected products' share of the shelf among the client's own products, in audited "
                    "category visits.",
        "use": "Compare how much of the client's shelf space the selected products get, by store, region or "
               "channel. Select a product group to see its share; with every product selected it is about 100%.",
        "formula": "Sum the selected SKUs' allocation in each audited category visit, then average across those "
                   "visits",
        "logic": "Supplied SKUs of a category sum to about 100% by construction. The competitor shelf universe is "
                 "not in the data, so this is not Marvel's share against other brands; no target applies.",
        "example": "In one Laundry visit the two selected products hold 60% and 15% of the client's Laundry shelf: "
                   "75% for that visit. (Illustrative numbers.)",
        "limits": "Audited visits only, observed in both data views. Brand-vs-competitor shelf share needs "
                  "competitor shelf measurements from the client.",
        "next": "Store cards (shelf allocation by store) and the Shelf execution page.",
        "source": "execution_visits · scope_label = Individual audited SKU · shared_kpi.v1",
        "columns": "shelf_allocation_pct per store × audit date × category visit",
    },
    "plano": {
        "title": "Planogram Compliance %",
        "measures": "Whether audited products were placed as the planogram specifies.",
        "use": "Find the stores, brands and products whose shelf layout needs correcting.",
        "formula": "100 × compliant audited SKU rows ÷ audited SKU rows",
        "logic": "Actual audit rows only (individual audited SKU scope, so overlapping scopes are not added up); a "
                 "day without an audit is not noncompliance.",
        "example": "75 of 100 audited SKU rows compliant: 75%. That does not mean 75% of stores are fully "
                   "compliant. (Illustrative numbers.)",
        "limits": "Stores audited more often weigh more. Audits are observed facts in both data views; the "
                  "Estimated view does not fill them in.",
        "next": "Shelf execution page and the store cards.",
        "source": "execution_visits · scope_label = Individual audited SKU · shared_kpi.v1",
        "columns": "compliant_audit_row_count, audited_row_count",
    },
}

# Team display references (not client-approved thresholds), drawn only when the viewer asks for them.
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
KPI_READ = {
    "msl": "the share of assessable store-days on which every must-stock product had stock",
    "oos": "the share of known product-days with zero stock; lower is better",
    "gap": "how far invoiced units ran ahead of (+) or behind (−) what consumers bought; 0 is balanced",
    "shelf": "the selected products' share of the audited shelf, among the client's own products",
    "plano": "the share of audited products placed as the planogram says",
}


def kpi_title(key, estimated=False):
    spec = KPI_SPECS[key]
    return (spec.get("title_estimated") if estimated else None) or spec["title"]


def tile_label(key, label, estimated=False):
    return "OOS Rate" if key == "oos" and estimated else label


# ------------------------------------------------------------------ data view toggle

TOGGLE_HELP = {"reported": "Reported values only; unreported days stay Unknown.",
               "estimated": "Observed values first; short reporting gaps carry an estimate; the rest stays Unknown."}

def mode_toggle(mode, load_kind="idle", reason="", info=None):
    """Two-state data view switch next to the page title, with its status (loading / unavailable / estimator)."""
    buttons = [tags.button(text, type="button", title=(TOGGLE_HELP[key]), class_="m1-mode-btn" + (" on" if key == mode else ""),
                           id=f"m1-mode-{key}", **{"aria-pressed": "true" if key == mode else "false",
                                                    "data-mode": key},
                           onclick=f"Shiny.setInputValue('m1_mode', '{key}', {{priority: 'event'}})")
               for key, text in (("reported", "Reported data only"), ("estimated", "Estimated data view"))]
    status = None
    if load_kind == "loading":
        status = tags.span("Loading the estimated view…", class_="m1-mode-status", role="status")
    elif load_kind == "unavailable":
        status = tags.span(f"Estimated view unavailable: {reason}", class_="m1-mode-status bad", role="alert")

    return tags.span(tags.span(*buttons, class_="m1-mode", role="group", **{"aria-label": "Data view"}), status,
                     class_="m1-mode-wrap")


# ------------------------------------------------------------------ KPI cards and detail

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


def kpi_tiles(c, p, selected="msl", estimated=False):
    """Five compact KPI cards (value and change vs the prior period); the selected card's detail opens below.
    Shelf Share is not available in the data and says so."""
    tiles = []
    for key, label, field, pattern, better in KPI_TILES:
        on = key == selected
        if field is None:
            value = tags.span("N/A", class_="m1-kpi-value")
            foot = tags.span(kit.pill("Needs competitor shelf data", "outline"), class_="m1-kpi-foot")
        else:
            value = tags.span(fmt(c[field], pattern), class_="m1-kpi-value")
            foot = tags.span(_delta(c[field], p[field], better), tags.span("vs prior period", class_="m1-kpi-vs"),
                             class_="m1-kpi-foot")
        tiles.append(tags.button(
            tags.span(tile_label(key, label, estimated), class_="m1-kpi-label"), value, foot,
            type="button", class_="m1-kpi" + (" on" if on else ""), id=f"m1-kpi-{key}",
            onclick=f"Shiny.setInputValue('m1_kpi', '{key}', {{priority: 'event'}})",
            **{"data-key": key, "aria-pressed": "true" if on else "false", "aria-controls": "m1-kpi-detail"}))
    return tags.section(*tiles, class_="m1-kpis", id="m1-kpis",
                        **{"aria-label": "Business performance KPIs for the filtered stores; select one for its trend"})


def kpi_notes(key, c, estimated=False):
    """What the selected number rests on (in "How it's calculated", collapsed)."""
    gap_dates = f"{c['gap_first']:%b %d} – {c['gap_last']:%b %d, %Y}" if c.get("gap_first") else "no complete period"
    share = (f" {fmt(c.get('estimated_share'))} of the known store-SKU-days are estimated values."
             if estimated else "")
    notes = {
        "msl": [f"{c.get('msl_compliant') or 0:,} compliant of {c.get('msl_assessable') or 0:,} assessable "
                f"store-days; assessable coverage {fmt(c['msl_coverage'])} of eligible store-days. "
                f"{c['msl_unknown'] or 0:,} store-days are Unknown and are not counted as failures." + share],
        "oos": [f"{c['oos_count'] or 0:,} zero-stock of {c['oos_known'] or 0:,} store-SKU-days with known stock; "
                f"known stock coverage {fmt(c.get('known_coverage'))} (reported {fmt(c['coverage'])}). Unknown stock "
                "is never treated as in stock." + share],
        "gap": [f"Sell-in {fmt(c.get('sell_in'), '{:,.0f}')} and sell-out {fmt(c.get('sell_out'), '{:,.0f}')} units; "
                f"gap {fmt(c['gap_units'], '{:+,.0f}')} units over {c['gap_periods']} complete {PERIOD_TYPE}ly periods "
                f"({gap_dates}); {c['gap_comparable_rows']:,} of {c['gap_rows']:,} rows comparable.",
                "Invoice-extract completeness is unconfirmed, so a missing invoice is not read as zero."
                if c["gap_completeness_unconfirmed"] else None],
        "shelf": [f"{c['visits']:,} audited category visits. Allocation is among the client's own products in a "
                  "category (about 100% when every product is selected), not Marvel vs competitor shelf share."],
        "plano": [f"{c.get('plano_compliant') or 0:,} compliant of {c['audited']:,} audited SKU rows."],
    }[key]
    return [n for n in notes if n]


def trend_chart(key, frame, element_id, show_targets=False):
    """The KPI per week (gap: per complete biweekly period): a smooth line over a soft fill, no grid. The
    team reference line is drawn only when asked for."""
    frame = frame[frame["value"].notna()] if frame is not None and len(frame) else frame
    if frame is None or not len(frame):
        return state("No trend for these filters", "No period in the range has a value for this KPI.")
    x = [pd.Timestamp(v).strftime("%Y-%m-%d") for v in frame["period"]]
    y = [float(v) for v in frame["value"]]
    lows, highs = [min(y)], [max(y)]
    target = KPI_TARGETS.get(key) if show_targets else None
    lines = ()
    if target:
        lines = target[2] if isinstance(target[2], tuple) else (target[2],)
        lows.append(min(lines))
        highs.append(max(lines))
    if key == "gap":
        lows.append(0.0)
        highs.append(0.0)
    pad = max(2.0, (max(highs) - min(lows)) * 0.15)
    low, high = min(lows) - pad, max(highs) + pad
    if key != "gap":
        low, high = max(0.0, low), min(100.0 + (5 if key == "shelf" else 0), high)
    color = CHART_COLORS[0]
    fig = go.Figure(go.Scatter(x=x, y=[low] * len(y), mode="lines", line=dict(width=0), hoverinfo="skip",
                               showlegend=False))
    name = "Own-SKU shelf allocation" if key == "shelf" else KPI_SPECS[key]["title"].replace(" %", "")
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers", name=name, fill="tonexty",
        fillgradient=dict(type="vertical", colorscale=[[0, _rgba(color, 0.0)], [1, _rgba(color, 0.20)]]),
        line=dict(color=color, width=2.5, shape="spline", smoothing=0.6),
        marker=dict(size=7, color="#ffffff", line=dict(color=color, width=2)),
        hovertemplate=("Biweekly period from" if key == "gap" else "Week of") + " %{x|%b %d, %Y}<br>"
                      "<b>%{y:.1f}%</b><extra></extra>"))
    if key == "gap":
        fig.add_hline(y=0, line=dict(color="#65595a", width=1))
    for value in lines:
        fig.add_hline(y=value, line=dict(color="#65595a", width=1.2, dash="dot"),
                      annotation=dict(text=f"Team reference {target[1]} (not client-approved)"
                                      if value == lines[-1] else "", font=dict(size=11, color="#65595a"),
                                      xanchor="right", x=1, yanchor="bottom"))
    layout = dict(showlegend=False, margin=dict(l=8, r=8, t=16, b=8), hovermode="x",
                  yaxis=dict(range=[low, high], ticksuffix="%", title=None, showgrid=False, zeroline=False,
                             showline=False, tickfont=dict(color="#8a7f80", size=11)),
                  xaxis=dict(tickformat="%b %d", title=None, showgrid=False, zeroline=False, showline=False,
                             tickfont=dict(color="#8a7f80", size=11), showspikes=True, spikemode="across",
                             spikethickness=1, spikedash="solid", spikecolor="#dfd4d4"))
    return plot(fig, element_id, f"{name} trend", 300, layout)


def kpi_help(key, period, estimated=False):
    """How to read the selected KPI's trend (the "?" next to its title)."""
    lines = [f"The line is {KPI_READ[key]}, per {period}, for the stores, products and dates in the filters.",
             "Hover the chart for each " + period + "'s value."]
    if key in KPI_TARGETS:
        lines.append("“Show team reference” draws the team's display reference as a dotted line. The client has "
                     "not set targets, so it is not a pass mark and nothing is coloured by it.")
    lines.append("On the cards, green means better than the previous period of the same length and red means "
                 "worse; the arrow only shows whether the number went up or down.")
    if estimated and key in ("msl", "oos", "gap"):
        lines.append("Estimated data view: observed values first, short reporting gaps carry an estimate, the "
                     "rest stays Unknown.")
    return kit.help_tip(kpi_title(key, estimated).replace(" %", ""), tags.ul(*[tags.li(t) for t in lines]),
                        tags.p(KPI_SPECS[key]["measures"], class_="ws-help-data"), id="m1-kpi-help")


def kpi_detail(key, c, p, trend, f, estimated=False, show_targets=False):
    """Below the cards: the selected KPI's trend and (collapsed) what it measures, how it is calculated, an
    example, its limits and where to look next."""
    spec = KPI_SPECS[key]
    _, _, field, pattern, better = next(t for t in KPI_TILES if t[0] == key)
    label = kpi_title(key, estimated).replace(" %", "")
    period = "complete biweekly period" if key == "gap" else "week"
    rows = [("What it measures", spec["measures"]), ("How to use it", spec["use"]), ("Formula", spec["formula"]),
            ("Rules", spec["logic"]), ("Example", spec["example"]), ("Limits", spec["limits"]),
            ("Look next", spec["next"])]
    how = tags.details(
        tags.summary("What it measures and how it's calculated"),
        tags.dl(*[part for title, text in rows for part in (tags.dt(title), tags.dd(text))],
                tags.dt("This selection"), tags.dd(*[tags.div(n) for n in kpi_notes(key, c, estimated)],
                                                   class_="m1-notes"),
                tags.dt("Data view"), tags.dd("Estimated — observed first, estimates in short gaps, "
                                              "Unknown kept" if estimated and key in ("msl", "oos", "gap")
                                              else "Reported" if key in ("msl", "oos", "gap")
                                              else "Audits are observed in both views"),
                tags.dt("Shared view"), tags.dd(spec["source"]), tags.dt("Components"), tags.dd(spec["columns"])),
        class_="ws-disclosure m1-how", id="m1-kpi-how")
    if field is None:
        figures = tags.div(tags.span("N/A", class_="m1-detail-value"),
                           kit.pill("Not available: no competitor shelf data", "outline"), class_="m1-detail-figures")
    else:
        figures = tags.div(tags.span(fmt(c[field], pattern), class_="m1-detail-value"),
                           _delta(c[field], p[field], better, long=True), class_="m1-detail-figures")
    toggle = None
    if key in KPI_TARGETS:
        toggle = tags.button("Hide team reference" if show_targets else "Show team reference", type="button",
                             class_="m1-link-btn", id="m1-targets", **{"aria-pressed": "true" if show_targets else "false"},
                             onclick="Shiny.setInputValue('m1_targets', Date.now(), {priority: 'event'})")
    sub = None
    return tags.section(
        tags.div(tags.div(tags.div(tags.h2(label, class_="ws-h"), kpi_help(key, period, estimated),
                                   class_="ws-title-row"),
                          tags.p(f"{pd.Timestamp(f.start):%b %d} – {pd.Timestamp(f.end):%b %d, %Y}",
                                 class_="ws-muted m1-detail-range"),
                          tags.p(sub, class_="ws-muted m1-detail-range") if sub else None),
                 figures, class_="ws-card-head m1-detail-head"),
        trend_chart(key, trend, f"m1-trend-{key}", show_targets),
        tags.div(toggle, class_="m1-detail-tools") if toggle else None,
        how,
        class_="ws-card m1-kpi-detail", id="m1-kpi-detail", **{"aria-live": "polite"})


# ------------------------------------------------------------------ store actions

def store_actions(store_id, stockout_nav=False, card=True):
    """Buttons that open the store card here, or the store's detail in Stockout Risk (which re-checks access)."""
    card = tags.button("Store card", type="button", class_="m1-act", **{"data-m1-store": store_id,
                                                                       "data-m1-action": "card"}) if card else None
    risk = tags.button("Stockout risk", type="button", class_="m1-act", **{"data-m1-store": store_id,
                                                                          "data-m1-action": "risk"}) \
        if stockout_nav else None
    return tags.span(card, risk, class_="m1-acts")


def _count(value):
    return "Unknown" if value is None or pd.isna(value) else f"{int(value):,}"


# ------------------------------------------------------------------ store card

CARD_METRICS = (
    ("msl_pct", "MSL compliance", "up", "msl", lambda r: f"{_count(r.get('msl_compliant'))} of "
     f"{_count(r.get('msl_assessable'))} assessable store-days"),
    ("oos_pct", "OOS rate", "down", "oos", lambda r: f"{_count(r.get('oos'))} zero-stock of {_count(r.get('known'))} "
     "known store-SKU-days"),
    ("gap_pct", "Sell-in vs sell-out gap", "zero", "gap", lambda r: f"{fmt(r.get('gap_units'), '{:+,.0f}')} units over "
     f"{_count(r.get('gap_periods'))} complete {PERIOD_TYPE}ly periods"),
    ("alloc_pct", "Shelf allocation", "up", "alloc", lambda r: f"Own-SKU share of the client's shelf over "
     f"{_count(r.get('visits'))} audited category visits"),
    ("plano_pct", "Planogram compliance", "up", "plano", lambda r: f"{_count(r.get('compliant'))} of "
     f"{_count(r.get('audited'))} audited SKU rows · last audit {fdate(r.get('last_audit'), '%b %d')}"),
)
VERDICT_KIND = {"Improving": "ok", "Worsening": "bad", "Stable": "outline"}
RADAR_AXES = {"msl_pct": "MSL", "oos_pct": "OOS", "gap_pct": "Gap", "alloc_pct": "Shelf", "plano_pct": "Planogram"}


def peer_position(value, peers, better):
    """(better than, compared) among peer values; None when there are too few peers or no value."""
    peers = [float(v) for v in peers if v is not None and not pd.isna(v)]
    if value is None or pd.isna(value) or len(peers) < PEERS_MIN:
        return None
    if better == "up":
        wins = sum(v < value for v in peers)
    elif better == "down":
        wins = sum(v > value for v in peers)
    else:
        wins = sum(abs(v) > abs(value) for v in peers)
    return wins, len(peers)


def rep_contacts(reps):
    """The field reps assigned to the store (manager view): the name opens a small card with the rep's photo and a
    message button (a placeholder until messaging exists). No contact data leaves the app."""
    if not reps:
        return tags.span("No field rep assigned", class_="ws-muted m1-reps", id="m1-card-reps")
    people = []
    for rep in reps:
        people.append(tags.details(
            tags.summary(kit.avatar(rep["name"], rep.get("avatar"), 22), tags.span(rep["name"]), class_="m1-rep-face",
                         title=f"{rep['name']} — field sales rep"),
            tags.div(kit.avatar(rep["name"], rep.get("avatar"), 64),
                     tags.div(tags.strong(rep["name"]), tags.div(rep.get("title") or "Field sales rep",
                                                                 class_="ws-muted")),
                     tags.button(kit.icon("chat", 16), "Message", tags.span("Soon", class_="ws-soon"), type="button",
                                 class_="m1-msg-btn", title="Messaging — coming soon",
                                 **{"aria-disabled": "true", "aria-label": f"Message {rep['name']} — coming soon"}),
                     class_="ws-help-pop m1-rep-pop"),
            class_="ws-help m1-rep"))
    return tags.span(tags.span("Field rep", class_="m1-reps-k"), *people, class_="m1-reps", id="m1-card-reps")


def score_block(row, prior_rank=None, total=None):
    """The composite score, the rank among the stores in view and the move since the previous period."""
    score, rank = row.get("score"), row.get("rank")
    if score is None or pd.isna(score):
        return tags.div(tags.div("—", class_="m1-score-v"),
                        tags.div(f"No score: needs {SCORE_MIN_MEASURES} of the 5 measures", class_="ws-muted"),
                        class_="m1-score", id="m1-card-score")
    move = None
    if prior_rank is not None and not pd.isna(prior_rank) and rank is not None and not pd.isna(rank):
        delta = int(prior_rank) - int(rank)
        move = kit.pill(f"{'▲' if delta > 0 else '▼'} {abs(delta)} vs prior period" if delta else "No change",
                        "ok" if delta > 0 else "bad" if delta < 0 else "outline",
                        title="Places moved in the ranking since the previous period of equal length")
    return tags.div(tags.div(tags.span(f"{score:.0f}", class_="m1-score-v"), tags.span("/100", class_="m1-score-of")),
                    tags.div(f"Rank {int(rank)} of {total}" if total else f"Rank {int(rank)}", class_="m1-score-rank"),
                    move, class_="m1-score", id="m1-card-score")


def sparkline(trend, metric, label, width=150, height=34):
    """A small inline line of the last 13 weeks (the gap per biweekly period) with its latest point; at least a
    10-point vertical span, so tiny wobbles are not drawn as a trend. No value: an empty line."""
    part = trend[(trend["metric"] == metric) & trend["value"].notna()].sort_values("period") \
        if trend is not None and len(trend) else None
    if part is None or len(part) < 2:
        return tags.span("No weekly data", class_="ws-muted m1-spark-empty")
    values = [float(v) for v in part["value"]]
    low, high = min(values), max(values)
    if metric == "gap":
        low, high = min(low, 0.0), max(high, 0.0)
    if high - low < 10:
        mid = (high + low) / 2
        low, high = mid - 5, mid + 5
    pad = 3

    def xy(i, v):
        x = pad + (width - 2 * pad) * i / (len(values) - 1)
        y = pad + (height - 2 * pad) * (1 - (v - low) / (high - low))
        return round(x, 1), round(y, 1)
    points = [xy(i, v) for i, v in enumerate(values)]
    zero = ""
    if metric == "gap":
        _, zy = xy(0, 0.0)
        zero = f'<line x1="0" x2="{width}" y1="{zy}" y2="{zy}" stroke="#d8cfcf" stroke-width="1"/>'
    path = " ".join(f"{x},{y}" for x, y in points)
    lx, ly = points[-1]
    first, last = pd.Timestamp(part["period"].iloc[0]), pd.Timestamp(part["period"].iloc[-1])
    svg = (f'<svg class="m1-spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="{label}: {len(values)} points from {first:%b %d} to {last:%b %d}, latest {values[-1]:.1f}%">'
           f'{zero}<polyline points="{path}" fill="none" stroke="{CHART_COLORS[0]}" stroke-width="1.8" '
           f'stroke-linejoin="round" stroke-linecap="round"/>'
           f'<circle cx="{lx}" cy="{ly}" r="2.8" fill="{CHART_COLORS[0]}"/></svg>')
    return ui.HTML(svg)


def card_metric_rows(row, stores, prior_row=None, trend=None):
    """One row per measure: name, value, the 13-week line, the change vs the prior period and the direction; what
    the value rests on and the comparison with similar stores sit underneath in small print."""
    peers = stores[(stores["channel"] == row["channel"]) & (stores["segment_tier"] == row["segment_tier"])
                   & (stores["store_id"] != row["store_id"])]
    rows = []
    for column, label, better, metric, base in CARD_METRICS:
        value = row.get(column)
        position = peer_position(value, peers[column].tolist(), better)
        compare = (f"better than {position[0]} of {position[1]} similar stores" if position else
                   "too few similar stores to compare")
        pattern = "{:+.1f}%" if better == "zero" else "{:.1f}%"
        change = _delta(value, prior_row.get(column) if prior_row is not None else None, better)
        direction = trend_direction(trend, metric, better) if trend is not None else None
        verdict = kit.pill(direction["verdict"], VERDICT_KIND[direction["verdict"]],
                           title=f"Last 13 weeks: later half {fmt(direction['late'], pattern)} vs earlier half "
                                 f"{fmt(direction['early'], pattern)}", **{"data-verdict": direction["verdict"]}) \
            if direction else kit.pill("Too little history", "outline", title="Fewer than four periods with a value")
        rows.append(tags.div(
            tags.div(tags.div(label, class_="m1-sc-name"),
                     tags.div(f"{base(row)} · {compare} ({BETTER_TEXT[better]})", class_="m1-sc-base"),
                     class_="m1-sc-label"),
            tags.div(fmt(value, pattern), class_="m1-sc-value"),
            tags.div(sparkline(trend, metric, label), class_="m1-sc-spark"),
            tags.div(change, verdict, class_="m1-sc-status"),
            class_="m1-sc-row", **{"data-metric": column}))
    return peers, rows


def store_radar(scored, store_id, compare=(), element_id="m1-store-radar"):
    """The five measures as percentiles among the stores in view (100 = best in view, so every axis reads the
    same way), for the store and up to four stores to compare. Hover shows the actual values."""
    keep = [store_id] + [c for c in compare if c != store_id][:4]
    rows = scored[scored["store_id"].isin(keep)].set_index("store_id")
    if store_id not in rows.index:
        return None
    axes = [RADAR_AXES[c] for c, *_ in CARD_METRICS]          # short names fit on a phone; hover has the full one
    names = [label for _, label, _, _, _ in CARD_METRICS]
    fig = go.Figure()
    for i, sid in enumerate(keep):
        if sid not in rows.index:
            continue
        r = rows.loc[sid]
        values = [None if pd.isna(r.get(f"p_{c}")) else float(r.get(f"p_{c}")) for c, *_ in CARD_METRICS]
        actual = [f"{n}: " + fmt(r.get(c), "{:+.1f}%" if b == "zero" else "{:.1f}%")
                  for n, (c, _, b, _, _) in zip(names, CARD_METRICS)]
        color = CHART_COLORS[i] if i < len(CHART_COLORS) else "#a8a29e"
        fig.add_trace(go.Scatterpolar(r=values + values[:1], theta=axes + axes[:1], name=r["store_name"],
                                      customdata=actual + actual[:1], fill="toself" if i == 0 else "none",
                                      fillcolor=_rgba(color, 0.12), line=dict(color=color, width=2.5 if i == 0 else 2),
                                      connectgaps=False,
                                      hovertemplate="%{customdata} (percentile %{r:.0f})<extra>%{fullData.name}"
                                                    "</extra>"))
    fig.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)",
                                 radialaxis=dict(range=[0, 100], tickvals=[50, 100], gridcolor="#efe8e8",
                                                 tickfont=dict(size=9, color="#8a7f80")),
                                 angularaxis=dict(gridcolor="#efe8e8", tickfont=dict(size=11, color="#4a3f40"))))
    return plot(fig, element_id, "Store profile radar", 340,
                {"margin": dict(l=40, r=40, t=24, b=24), "legend": {"orientation": "h", "y": -0.08, "yanchor": "top"}})


def store_card(row, scored, skus=None, latest=None, stockout_nav=False, estimated=False, prior=None, trend=None,
               reps=None, compare=(), manager=False):
    """One store's execution profile ("player card"): who and where, the composite score and rank, a scorecard of
    the five measures (value, 13-week line, change, direction), a radar to compare stores, and the products to
    check. Small print explains what each value rests on; nothing else competes with the numbers."""
    prior_row = None
    if prior is not None and len(prior):
        match = prior[prior["store_id"] == row["store_id"]]
        prior_row = match.iloc[0].to_dict() if len(match) else None
    peers, rows = card_metric_rows(row, scored, prior_row, trend)
    window = ""
    if trend is not None and trend.attrs.get("start"):
        window = f"{fdate(trend.attrs['start'], '%b %d')} – {fdate(trend.attrs['end'], '%b %d, %Y')}"
    issues_block = tags.p("No products to check.", class_="ws-muted")
    count = 0
    if skus is not None and len(skus):
        issues = skus[(skus["inventory"].isna()) | (skus["inventory"] == 0) | (skus["inventory_data_status"] == "Estimated")]
        count = len(issues)
        if count:
            items = [tags.li(tags.span(r.product_name, class_="m1-chk-name"),
                             tags.span("MSL", class_="m1-chk-tag") if r.is_msl else None,
                             tags.span("Not reported" if pd.isna(r.inventory) else
                                       (f"~{r.inventory:,.0f} est." if r.inventory_data_status == "Estimated"
                                        else f"{r.inventory:,.0f} on hand"), class_="m1-chk-stock"),
                             class_="m1-chk")
                     for r in issues.itertuples(index=False)]
            issues_block = TagList(tags.ul(*items, class_="m1-chk-list", id="m1-card-skus"),
                                   tags.p(f"{len(skus) - count} other selected products had stock above zero.",
                                          class_="ws-muted m1-small") if len(skus) > count else None)
        else:
            issues_block = tags.p("Every selected product had stock above zero.", class_="ws-muted")
    meta = " · ".join(str(m) for m in (f"{row['channel']} · {row['segment_tier']}", f"{row['city']}, {row['state']}",
                                       row["store_id"]))
    total = int(scored["rank"].notna().sum())
    return tags.article(
        tags.header(
            tags.div(tags.h2(row["store_name"], class_="m1-card-title"), tags.p(meta, class_="m1-card-meta"),
                     tags.div(rep_contacts(reps) if manager else None,
                              store_actions(row["store_id"], stockout_nav, card=False) if stockout_nav else None,
                              class_="m1-card-who")),
            score_block(row, prior_row.get("rank") if prior_row else None, total),
            class_="m1-card-header"),
        tags.section(
            tags.div(tags.h3("Scorecard", class_="m1-sec-h"),
                     tags.span(f"Lines: last 13 weeks ({window})" if window else "", class_="ws-muted m1-small"),
                     class_="m1-sec-head"),
            tags.div(*rows, class_="m1-sc"),
            tags.p(f"Last report {fdate(row.get('last_report'))} · last delivery receipt "
                   f"{fdate(row.get('last_delivery'))} · {len(peers)} similar stores (same channel and tier) in your "
                   "scope" + (" · values include estimates" if estimated else ""), class_="ws-muted m1-small"),
            class_="m1-card-sec"),
        tags.div(
            tags.section(tags.div(tags.h3("Profile", class_="m1-sec-h"),
                                  tags.span("Percentile among the stores in view · 100 = best", class_="ws-muted m1-small"),
                                  class_="m1-sec-head"),
                         store_radar(scored, row["store_id"], compare), class_="m1-card-sec"),
            tags.section(tags.div(tags.h3(f"Products to check ({count})", class_="m1-sec-h"),
                                  tags.span(f"on {fdate(latest)}", class_="ws-muted m1-small"), class_="m1-sec-head"),
                         issues_block, class_="m1-card-sec"),
            class_="m1-card-cols"),
        class_="ws-card m1-store-card", id="m1-store-card", **{"data-store": row["store_id"]})


# ------------------------------------------------------------------ store ranking (overview)

def ranking_panel(scored, prior=None, side="top", rows_shown=10):
    """Stores ranked by the composite score, best or weakest first; the five measures beside it, so the score
    is never shown without what it is made of."""
    if scored is None or scored.empty or scored["score"].notna().sum() == 0:
        return state("No ranking", "No store has enough measures for a score with these filters.")
    ranked = scored[scored["score"].notna()].sort_values("rank", ascending=side == "top")
    prior_rank = dict(zip(prior["store_id"], prior["rank"])) if prior is not None and len(prior) else {}
    total = len(ranked)
    switch = tags.div(*[tags.button(text, type="button", class_="m1-seg" + (" on" if key == side else ""),
                                    **{"aria-pressed": "true" if key == side else "false"},
                                    onclick=f"Shiny.setInputValue('m1_rank_side', '{key}', {{priority: 'event'}})")
                        for key, text in (("top", "Best performing"), ("bottom", "Needs attention"))],
                      class_="m1-segs", role="group", **{"aria-label": "Show the best or the weakest stores"})
    rows = []
    for r in ranked.head(rows_shown).to_dict("records"):
        before = prior_rank.get(r["store_id"])
        move = "—"
        if before is not None and not pd.isna(before):
            delta = int(before) - int(r["rank"])
            move = f"{'▲' if delta > 0 else '▼'} {abs(delta)}" if delta else "="
        rows.append(tags.tr(
            tags.td(tags.b(f"{int(r['rank'])}")),
            tags.td(tags.b(r["store_name"]), tags.div(f"{r['city']}, {r['state']} · {r['store_id']}", class_="ws-muted"),
                    tags.div(f"{r['channel']} · {r['segment_tier']}", class_="ws-muted")),
            tags.td(tags.span(f"{r['score']:.0f}", class_="m1-rank-score"),
                    tags.span(f" · {int(r['measures'])} of 5 measures", class_="ws-muted")
                    if r["measures"] < 5 else None),
            tags.td(move, title="Places moved since the previous period of equal length"),
            tags.td(fmt(r["msl_pct"])), tags.td(fmt(r["oos_pct"])), tags.td(fmt(r["gap_pct"], "{:+.1f}%")),
            tags.td(fmt(r["alloc_pct"])), tags.td(fmt(r["plano_pct"])),
            class_="m1-row-link", tabindex="0", role="link", title=f"Open the store card of {r['store_name']}",
            **{"data-store": r["store_id"], "data-m1-store": r["store_id"], "data-m1-action": "card",
               "aria-label": f"Rank {int(r['rank'])}, {r['store_name']}, score {r['score']:.0f}: open the store card"}))
    lead = tags.p(f"{total} stores scored. Score = the average of each store's percentile on the five KPIs among "
                  "the stores in view (equal 20% weights, set by the team; 100 = best in view). Click a store to open "
                  "its card.",
                  class_="m1-exc-lead")
    return TagList(tags.div(lead, switch, class_="m1-exc-head"),
                   table(["#", "Store", "Score", "Move", "MSL", "OOS", "Gap", "Shelf alloc.", "Planogram"], rows,
                         table_id="m1-rank-table", caption="Store ranking by composite score"))


# ------------------------------------------------------------------ sell-in vs sell-out: summary and breakdown

def flow_summary(c):
    """The selected range's comparable totals above the flow chart (gap % is N/A without sell-out)."""
    dates = f"{c['gap_first']:%b %d} – {c['gap_last']:%b %d, %Y}" if c.get("gap_first") else "no complete period"
    items = [("Sell-in (invoiced)", fmt(c.get("sell_in"), "{:,.0f}") + " units"),
             ("Sell-out (consumers)", fmt(c.get("sell_out"), "{:,.0f}") + " units"),
             ("Gap", fmt(c.get("gap_pct"), "{:+.1f}%")), ("Gap units", fmt(c.get("gap_units"), "{:+,.0f}")),
             ("Complete periods", f"{c.get('gap_periods', 0)} · {dates}")]
    return tags.div(*[tags.div(tags.span(k, class_="m1-sum-k"), tags.span(v, class_="m1-sum-v"), class_="m1-sum")
                      for k, v in items], class_="m1-sums", id="m1-flow-summary")


def run_text(run):
    """▲ = sell-in ahead of consumers; ▼ = consumers ahead of sell-in."""
    if not run:
        return "—"
    end = pd.Timestamp(run["last"]) + pd.Timedelta(days=13)
    arrow = "▲" if run["direction"] == "+" else "▼"
    return tags.span(f"{arrow} {run['length']} period{'s' if run['length'] != 1 else ''}", tags.br(),
                     tags.span(f"{fdate(run['first'], '%b %d')} – {fdate(end, '%b %d')}", class_="ws-muted"),
                     title="sell-in ahead of consumers" if arrow == "▲" else "consumers ahead of sell-in")


def flow_table(frame, dimension, show_all=False, manager=True):
    """Where the gap is and how long it has lasted: current run (ending in the last period), the longest run each
    way, OOS rate and latest delivery receipt. Ordered by the current run's length, then the size of the gap."""
    dims = [("channel", "Channels"), ("region", "Regions"), ("store", "Stores"), ("sku", "Products")]
    if not manager:
        dims = [d for d in dims if d[0] in ("store", "sku")]
    switch = tags.div(*[tags.button(text, type="button", class_="m1-seg" + (" on" if key == dimension else ""),
                                    **{"aria-pressed": "true" if key == dimension else "false"},
                                    onclick=f"Shiny.setInputValue('m1_flow_dim', '{key}', {{priority: 'event'}})")
                        for key, text in dims], class_="m1-segs", role="group", **{"aria-label": "Break down by"})
    if frame is None or frame.empty:
        return TagList(switch, state("No comparable flows", "No complete comparable period in the selected range."))
    order = frame.assign(_len=[(r or {}).get("length", 0) for r in frame["current"]],
                         _gap=frame["gap_pct"].astype(float).abs().fillna(-1))
    order = order.sort_values(["_len", "_gap"], ascending=False)
    rows = [tags.tr(tags.td(tags.b(r["label"])), tags.td(fmt(r["sell_in"], "{:,.0f}")),
                    tags.td(fmt(r["sell_out"], "{:,.0f}")), tags.td(fmt(r["gap_units"], "{:+,.0f}")),
                    tags.td(fmt(r["gap_pct"], "{:+.1f}%")), tags.td(run_text(r["current"])),
                    tags.td(run_text(r["longest_positive"])), tags.td(run_text(r["longest_negative"])),
                    tags.td(fmt(r["oos_pct"])), tags.td(fdate(r.get("last_delivery"), "%b %d")))
            for r in order.to_dict("records")]
    shown = rows if show_all or len(rows) <= FLOW_ROWS else rows[:FLOW_ROWS]
    more = tags.button(f"Show all {len(rows)}" if not show_all else f"Show the first {FLOW_ROWS}", type="button",
                       class_="m1-link-btn", id="m1-flow-more",
                       onclick="Shiny.setInputValue('m1_flow_more', Date.now(), {priority: 'event'})") \
        if len(rows) > FLOW_ROWS else None
    return TagList(
        tags.div(tags.h3("Where the gap is and how long it has lasted", class_="m1-card-h"), switch,
                 class_="m1-exc-head"),
        table(["", "Sell-in", "Sell-out", "Gap units", "Gap %", "Current run", "Longest ▲ run", "Longest ▼ run",
               "OOS rate", "Last delivery receipt"], shown, table_id="m1-flow-table",
              caption="Sell-in vs sell-out by group"),
        tags.div(more, class_="m1-detail-tools") if more else None,
        tags.p("▲ = sell-in ahead of consumers; ▼ = consumers ahead of sell-in. A run counts consecutive complete "
               "periods with the same sign; zero or a period without comparable flows ends it. ▲ is worth checking for channel stock building up or invoice timing; ▼ for stock "
               "running down and the replenishment plan. Both are leads to verify, not proven stockouts or "
               "overstock. Sell-in is invoiced, not received, stock.", class_="ws-muted m1-map-note"))


# ------------------------------------------------------------------ map: stores of a city

def city_stores(stores, key, stockout_nav=False):
    """The stores of the clicked (or chosen) city, with their store cards; ids, not names, identify stores."""
    if not key:
        return tags.p("Click a city bubble on the map, or choose a city above, to list its stores.",
                      class_="ws-muted", id="m1-city-empty")
    city, _, state_code = key.partition("|")
    part = stores[(stores["city"] == city) & (stores["state"] == state_code)]
    if part.empty:
        return tags.p("No stores in your scope for that city with these filters.", class_="ws-muted")
    rows = [tags.tr(tags.td(tags.b(r["store_name"]), tags.div(r["store_id"], class_="ws-muted")),
                    tags.td(f"{r['channel']} · {r['segment_tier']}"),
                    tags.td(_count(r["msl_zero"]) if r.get("msl_required_latest") else "N/A"),
                    tags.td(fmt(r["oos_pct"])), tags.td(fmt(r["msl_pct"])), tags.td(fmt(r["plano_pct"])),
                    tags.td(fmt(r["net_sales"], "{:,.0f}")), tags.td(fdate(r.get("last_report"), "%b %d")),
                    tags.td(store_actions(r["store_id"], stockout_nav)))
            for r in part.sort_values(["store_name", "store_id"]).to_dict("records")]
    return TagList(tags.h3(f"{city}, {state_code}: {len(part)} store{'s' if len(part) != 1 else ''}",
                           class_="m1-card-h"),
                   table(["Store", "Channel · tier", "MSL products at zero", "OOS rate", "MSL compliance",
                          "Planogram", "Invoiced net sales", "Last report", ""], rows, table_id="m1-city-table",
                         caption=f"Stores in {city}, {state_code}"))
