"""
modules/module_4/charts.py
==========================
Picks a chart for a query result from the shape of the table:

- a date/week/month column + numbers      -> line chart (one line per measure, or per
                                             category when there is one measure)
- a label column + one measure            -> bar chart (horizontal when long)
- a label column + comparable measures     -> grouped bars (e.g. sell-in vs sell-out)
- a single row                            -> no chart (the answer is a headline figure)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

PALETTE = ["#0071E3", "#FF9500", "#AF52DE", "#34C759", "#FF3B30", "#5AC8FA"]
LABEL_PREFERENCE = ["product_name", "store_name", "promo_name", "region", "channel", "category", "segment_tier",
                    "state", "city", "brand", "delivery_status", "sku_id", "store_id"]
RATE_HINTS = ("pct", "rate", "share", "percent", "compliance")
COUNT_HINTS = ("known", "days", "count", "audited", "rows", "n_", "number")
MAX_BARS = 25


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


def measures(numbers: list[str]) -> list[str]:
    """Measures worth plotting: rates first; supporting counts only when nothing else exists."""
    rates = [c for c in numbers if any(h in c.lower() for h in RATE_HINTS)]
    if rates:
        return rates[:1]
    main = [c for c in numbers if not any(h in c.lower() for h in COUNT_HINTS) and "gap" not in c.lower()]
    return (main or numbers)[:3]


def auto_chart(frame: pd.DataFrame | None, title: str = ""):
    if frame is None or len(frame) < 2:
        return None
    import plotly.graph_objects as go

    kinds = classify(frame)
    ys = measures(kinds["numbers"])
    if not ys:
        return None
    fig = go.Figure()
    if kinds["dates"]:
        x = kinds["dates"][0]
        data = frame.sort_values(x)
        groups = [c for c in kinds["labels"] if 1 < data[c].nunique() <= 8]
        if len(ys) == 1 and groups:
            for i, (name, part) in enumerate(data.groupby(groups[0], sort=True)):
                fig.add_trace(go.Scatter(x=part[x], y=part[ys[0]], mode="lines+markers", name=str(name),
                                         line=dict(color=PALETTE[i % len(PALETTE)], width=2.5)))
        else:
            for i, y in enumerate(ys):
                fig.add_trace(go.Scatter(x=data[x], y=data[y], mode="lines+markers", name=y.replace("_", " "),
                                         line=dict(color=PALETTE[i % len(PALETTE)], width=2.5)))
        fig.update_xaxes(title=x.replace("_", " "))
        fig.update_yaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None)
    else:
        if not kinds["labels"]:
            return None
        label = next((c for c in LABEL_PREFERENCE if c in kinds["labels"]), kinds["labels"][0])
        data = frame.head(MAX_BARS)
        names = data[label].astype(str)
        horizontal = len(data) > 8 or names.str.len().max() > 14
        for i, y in enumerate(ys):
            values = data[y]
            bar = (go.Bar(y=names, x=values, orientation="h") if horizontal else go.Bar(x=names, y=values))
            bar.update(name=y.replace("_", " "), marker_color=PALETTE[i % len(PALETTE)],
                       text=[f"{v:,.1f}" if isinstance(v, float) else f"{v:,}" for v in values],
                       textposition="outside", cliponaxis=False)
            fig.add_trace(bar)
        if horizontal:
            fig.update_yaxes(autorange="reversed")
            fig.update_xaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None)
        else:
            fig.update_yaxes(title=ys[0].replace("_", " ") if len(ys) == 1 else None)
        fig.update_layout(barmode="group")
        if len(frame) > MAX_BARS:
            title = (title + " " if title else "") + f"(first {MAX_BARS} of {len(frame)})"
    fig.update_layout(template="plotly_white", title=dict(text=title, font=dict(size=13)) if title else None,
                      margin=dict(l=10, r=30, t=40 if title else 16, b=40), height=340,
                      showlegend=len(fig.data) > 1, legend=dict(orientation="h", y=-0.2),
                      font=dict(family="Plus Jakarta Sans, -apple-system, Segoe UI, sans-serif", size=12),
                      paper_bgcolor="rgba(0,0,0,0)")
    return fig
