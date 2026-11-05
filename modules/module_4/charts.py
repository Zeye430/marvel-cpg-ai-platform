"""
modules/module_4/charts.py
==========================
Picks a chart for a query result from the shape of the table:

- a date/week/month column + numbers      -> line chart (one line per measure, or per
                                             category when there is one measure)
- a label column + one measure            -> bar chart (horizontal when long); a label that repeats
                                             is joined with the next label column (product · store),
                                             and rows that still cannot be told apart get no chart
- a label column + comparable measures     -> grouped bars (e.g. sell-in vs sell-out); measures on a
                                             different scale (>10x) stay in the table (one axis only)
- a single row                            -> no chart (the answer is a headline figure)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from shared.ui_kit import CHART_COLORS, FONT_STACK

from .formatting import is_probability, value as format_value

PALETTE = CHART_COLORS                       # shared multi-hue chart palette, fixed order (never cycled)
LABEL_PREFERENCE = ["product_name", "store_name", "promo_name", "region", "channel", "category", "segment_tier",
                    "state", "city", "brand", "delivery_status", "sku_id", "store_id"]
RATE_HINTS = ("pct", "rate", "share", "percent", "compliance")
COUNT_HINTS = ("known", "days", "count", "audited", "rows", "n_", "number")
ORDINAL_HINTS = ("rank",)  # positions, not quantities: never drawn as bar lengths
MAX_BARS = 25
MAX_LABEL = 30          # longer bar labels are shortened in the middle on the axis; the hover shows them in full


def _is_date(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna().head(5)
    return len(sample) > 0 and all(isinstance(v, (dt.date, dt.datetime)) for v in sample)


def classify(frame: pd.DataFrame) -> dict:
    dates = [c for c in frame.columns if _is_date(frame[c])]
    numbers = [c for c in frame.columns if c not in dates and pd.api.types.is_numeric_dtype(frame[c])
               and not pd.api.types.is_bool_dtype(frame[c])]
    labels = [c for c in frame.columns if c not in dates and c not in numbers]
    return {"dates": dates, "numbers": numbers, "labels": labels}


def _is_count(column: str) -> bool:
    name = column.lower()
    # "n_" only as a prefix (n_stores), so "sell_in_units" is not mistaken for a count.
    return name.startswith("n_") or any(h in name for h in COUNT_HINTS if h != "n_")


def measures(numbers: list[str]) -> list[str]:
    """Measures worth plotting: rates first; supporting counts only when nothing else exists."""
    numbers = [c for c in numbers if not any(h in c.lower() for h in ORDINAL_HINTS)]
    rates = [c for c in numbers if any(h in c.lower() for h in RATE_HINTS)]
    if rates:
        return rates[:1]
    main = [c for c in numbers if not _is_count(c) and "gap" not in c.lower()]
    return (main or numbers)[:3]


def comparable(frame: pd.DataFrame, ys: list[str]) -> list[str]:
    """One axis only: measures more than 10x smaller than the largest are left to the table."""
    if len(ys) < 2:
        return ys
    peak = {y: pd.to_numeric(frame[y], errors="coerce").abs().max() for y in ys}
    top = max((v for v in peak.values() if pd.notna(v)), default=0)
    return [y for y in ys if top == 0 or (pd.notna(peak[y]) and peak[y] * 10 >= top)]


def _bar_labels(frame: pd.DataFrame, labels: list[str]) -> pd.Series | None:
    """One distinct label per row: the preferred label column, joined with the next ones when it repeats
    (e.g. the same product in several stores); None when rows cannot be told apart."""
    ordered = [c for c in LABEL_PREFERENCE if c in labels] + [c for c in labels if c not in LABEL_PREFERENCE]
    if not ordered:
        return None
    names = frame[ordered[0]].astype(str)
    for column in ordered[1:3]:
        if not names.duplicated().any():
            break
        names = names + " · " + frame[column].astype(str)
    return None if names.duplicated().any() else names


def auto_chart(frame: pd.DataFrame | None, title: str = ""):
    if frame is None or len(frame) < 2:
        return None
    import plotly.graph_objects as go

    kinds = classify(frame)
    ys = comparable(frame, measures(kinds["numbers"]))
    if not ys:
        return None
    fig = go.Figure()
    height = 340
    if kinds["dates"]:
        x = kinds["dates"][0]
        data = frame.sort_values(x)
        groups = [c for c in kinds["labels"] if 1 < data[c].nunique() <= 8]
        if len(ys) == 1 and groups:
            for i, (name, part) in enumerate(data.groupby(groups[0], sort=True)):
                fig.add_trace(go.Scatter(x=part[x], y=part[ys[0]], mode="lines+markers", name=str(name),
                                         line=dict(color=PALETTE[i], width=2), marker=dict(size=7)))
        else:
            for i, y in enumerate(ys):
                fig.add_trace(go.Scatter(x=data[x], y=data[y], mode="lines+markers", name=y.replace("_", " "),
                                         line=dict(color=PALETTE[i], width=2), marker=dict(size=7)))
        fig.update_xaxes(title=x.replace("_", " "))
        fig.update_yaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None)
    else:
        if not kinds["labels"]:
            return None
        names = _bar_labels(frame, kinds["labels"])
        if names is None:                    # one bar per row needs a distinct label; never stack rows
            return None
        data = frame.head(MAX_BARS)
        full = names.head(MAX_BARS)
        names = full.map(lambda n: n if len(n) <= MAX_LABEL else n[:13] + "…" + n[-(MAX_LABEL - 14):])
        if names.duplicated().any():          # shortening must not merge two rows into one bar
            names = full
        horizontal = len(data) > 8 or names.str.len().max() > 14
        for i, y in enumerate(ys):
            values = data[y]
            bar = (go.Bar(y=names, x=values, orientation="h") if horizontal else go.Bar(x=names, y=values))
            labelled = len(ys) == 1 and len(data) <= 12           # direct labels only when they stay readable
            bar.update(name=y.replace("_", " "), marker=dict(color=PALETTE[i], cornerradius=4),
                       text=[format_value(y, v) for v in values.tolist()] if labelled else None,
                       textposition="outside", cliponaxis=False,
                       hovertext=[f"{n}: {format_value(y, v)}" for n, v in zip(full.tolist(), values.tolist())],
                       hoverinfo="text")
            fig.add_trace(bar)
        percent = ".1%" if all(is_probability(y) for y in ys) else None   # probabilities on a % axis
        if horizontal:
            per_bar = 20 + 12 * (len(ys) - 1)                    # room for each row's bars
            height = max(280, min(900, per_bar * len(data) + 130))
            fig.update_yaxes(autorange="reversed")
            fig.update_xaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None, tickformat=percent)
        else:
            fig.update_yaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None, tickformat=percent)
        fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.08)
        if len(frame) > MAX_BARS:
            title = (title + " " if title else "") + f"(first {MAX_BARS} of {len(frame)})"
    fig.update_layout(template="plotly_white", title=dict(text=title, font=dict(size=13)) if title else None,
                      margin=dict(l=10, r=30, t=(40 if title else 16) + (28 if len(fig.data) > 1 else 0), b=40),
                      height=height,
                      showlegend=len(fig.data) > 1,
                      legend=dict(orientation="h", x=0, y=1, yanchor="bottom", xanchor="left"),
                      font=dict(family=FONT_STACK, size=12, color="#211b1c"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      hovermode="x unified" if kinds["dates"] else "closest",
                      hoverlabel=dict(bgcolor="#ffffff", bordercolor="#dfd4d4", font=dict(family=FONT_STACK)))
    fig.update_xaxes(gridcolor="#ece4e4", linecolor="#dfd4d4", zeroline=False, tickfont=dict(color="#65595a"))
    fig.update_yaxes(gridcolor="#ece4e4", linecolor="#dfd4d4", zeroline=False, tickfont=dict(color="#65595a"))
    return fig
