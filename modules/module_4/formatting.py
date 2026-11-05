"""
modules/module_4/formatting.py
==============================
One way to show a result value, used for the table, the chart labels and the result the
language model summarizes, so all three agree.

Probabilities (predicted stockout probability, alert threshold, risk index: 0-1) are shown as
percentages with a bound for the extremes, so a small nonzero probability never reads as zero
(0.004 -> "0.4%", 0.0004 -> "<0.1%", 0 -> "0%"). Other small nonzero numbers keep two significant
digits instead of rounding to 0.00. Missing values are "—" (unknown, not zero).
"""

from __future__ import annotations

import math
import re

MISSING = "—"
PROBABILITY_COLUMN = re.compile(r"probabilit|threshold|risk_index", re.I)


def is_probability(column: str) -> bool:
    return bool(PROBABILITY_COLUMN.search(str(column)))


def _missing(value) -> bool:
    import pandas as pd
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):                 # not a scalar
        return False


def probability(value) -> str:
    if _missing(value):
        return MISSING
    value = float(value)
    if value == 0:
        return "0%"
    if 0 < value < 0.001:
        return "<0.1%"
    if 0.999 < value < 1:
        return ">99.9%"
    return f"{100 * value:.1f}%"


def number(value) -> str:
    if _missing(value):
        return MISSING
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value != 0 and abs(value) < 0.01:
            return f"{value:.2g}"                       # 0.004 -> "0.004", never "0.00"
        return f"{value:,.2f}"
    if hasattr(value, "normalize") and hasattr(value, "date") and value == value.normalize():
        return value.date().isoformat()                 # a date stored as a midnight timestamp
    return str(value)


def value(column: str, raw) -> str:
    if is_probability(column) and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return probability(raw)
    return number(raw)


def display_frame(frame):
    """A copy of ``frame`` with every value rendered as text by ``value`` (``tolist`` gives Python scalars)."""
    shown = frame.copy()
    for column in shown.columns:
        values = shown[column].tolist()
        known = [v for v in values if isinstance(v, float) and not _missing(v)]
        if known and not is_probability(column) and all(v.is_integer() for v in known):
            # Whole numbers stored as floats because a value is blank (a rank column): 1, not 1.00.
            values = [int(v) if isinstance(v, float) and not _missing(v) else v for v in values]
        shown[column] = [value(column, v) for v in values]
    return shown
