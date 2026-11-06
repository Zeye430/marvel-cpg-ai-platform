"""
modules/module_3/calc.py
========================
Pure calculations for Module 3 (no data access): deal economics and the guardrail verdict for
Scenario A, and the price response for Scenario B.

The guardrail rules are a proposal pending client confirmation (``RULES_VERSION``): which discount the
cap applies to and the execution uplift are switchable assumptions, never silent defaults. A verdict
is a guardrail check result, not an approval: a deal above the cap goes to manager review.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

RULES_VERSION = "m3-rules.v0-proposed"

APPROVED = "APPROVED"
CONDITIONS = "APPROVED WITH CONDITIONS"
REJECTED = "REJECTED"
NOT_ASSESSED = "CANNOT ASSESS"

# Which discount the cap is compared with (the brief's input is an additional discount; the pricing
# policy (MCPG-05) compares the total discount on the invoice). The client has not confirmed either.
CAP_BASIS = {"total": "Total discount (current 28-day discount + additional)",
             "additional": "Additional discount only"}

# Execution uplift on volume. The client's V3 data note states these effects; within a store over time
# they are not measurable in the data (about 0%), so both are offered and labelled.
UPLIFT_SOURCES = {"client": "Client V3 data note: display +8%, planogram +5%, +3% per 10 pts shelf share",
                  "measured": "Measured in the data within a store: about 0%"}
UPLIFT = {"display": 0.08, "planogram": 0.05, "shelf_per_10pts": 0.03}


@dataclass(frozen=True)
class Commitment:
    key: str
    label: str
    uplift: float | None      # client-stated volume uplift while the commitment holds (None: no figure)
    weeks: int | None = None  # a time-limited commitment (the uplift applies for these weeks only)


def commitments(tradeoff):
    """The execution commitments that satisfy a guardrail's required trade-off text (any one of them)."""
    text = (tradeoff or "").strip()
    found = []
    shelf = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*%\s*shelf share", text, re.I)
    if shelf:
        points = float(shelf.group(1))
        found.append(Commitment("shelf", f"+{points:g} pts shelf share", UPLIFT["shelf_per_10pts"] * points / 10))
    endcap = re.search(r"endcap[^0-9]*(\d+)\s*weeks?", text, re.I)
    if endcap:
        weeks = int(endcap.group(1))
        found.append(Commitment("endcap", f"Featured endcap for {weeks} weeks", UPLIFT["display"], weeks))
    elif re.search(r"secondary display|endcap", text, re.I):
        found.append(Commitment("display", "1 secondary display", UPLIFT["display"]))
    if re.search(r"planogram", text, re.I):
        label = "100% MSL compliance + planogram adherence" if re.search(r"MSL", text, re.I) \
            else "Planogram adherence"
        found.append(Commitment("planogram", label, UPLIFT["planogram"]))
    if not found and text:
        found.append(Commitment("required", text, None))
    return found


@dataclass(frozen=True)
class Deal:
    """One store × SKU's reference prices and guardrail (from the shared negotiation context)."""
    channel: str
    list_price: float | None
    unit_cost: float | None
    current_discount: float | None     # weighted 28-day trade discount %, None when no recent invoice
    cap: float | None                  # max_discount_pct_source
    floor: float | None                # min_target_margin_source (gross margin on net price, %)
    tradeoff: str = ""

    @property
    def distributor(self):
        return self.channel == "Distributor"


def unit_economics(list_price, unit_cost, discount_pct):
    """Net unit price, gross margin % on net price and unit gross profit; None where undefined."""
    if list_price is None or unit_cost is None or discount_pct is None:
        return None, None, None
    net = list_price * (1 - discount_pct / 100)
    if net <= 0:
        return net, None, net - unit_cost
    return net, 100 * (net - unit_cost) / net, net - unit_cost


def scenario(deal, additional, commitment=None, quantity=None, basis="total", uplift_source="client"):
    """One proposal: economics, the three guardrail checks, the verdict and its reason.

    ``commitment``: a Commitment key the account offers, or None. ``quantity``: order units for money
    amounts (None: per unit only)."""
    if basis not in CAP_BASIS or uplift_source not in UPLIFT_SOURCES:
        raise ValueError("Unknown rule option")
    additional = max(0.0, float(additional or 0))
    current_known = deal.current_discount is not None
    current = deal.current_discount if current_known else 0.0
    total = current + additional
    net, margin, unit_gp = unit_economics(deal.list_price, deal.unit_cost, total)
    _, _, base_gp = unit_economics(deal.list_price, deal.unit_cost, current)
    compared = total if basis == "total" else additional
    options = {c.key: c for c in commitments(deal.tradeoff)}
    offered = options.get(commitment)
    result = {"additional": additional, "current": deal.current_discount, "total": total, "compared": compared,
              "net": net, "margin": margin, "unit_gp": unit_gp, "commitment": offered,
              "cap_ok": None if deal.cap is None else compared <= deal.cap + 1e-9,
              "floor_ok": None if margin is None or deal.floor is None else margin >= deal.floor - 1e-9,
              "notes": []}
    if not current_known and basis == "total":
        result["notes"].append("No invoice in the last 28 days: the current discount is unknown, so the cap is "
                               "compared with the additional discount only.")
        result["compared"] = compared = additional
        result["cap_ok"] = None if deal.cap is None else compared <= deal.cap + 1e-9
    # Money for the order, and whether the commitment's assumed volume uplift pays for the extra discount.
    uplift = (offered.uplift or 0.0) if (offered and uplift_source == "client") else 0.0
    result["uplift"] = uplift
    result["breakeven"] = None
    if unit_gp is not None and base_gp is not None and unit_gp > 0:
        result["breakeven"] = max(0.0, base_gp / unit_gp - 1) if additional > 0 else 0.0
    result["covers"] = None if result["breakeven"] is None else uplift >= result["breakeven"] - 1e-12
    if quantity:
        q = float(quantity)
        result.update(revenue=None if net is None else q * net,
                      discount_cost=None if deal.list_price is None else q * deal.list_price * total / 100,
                      gross_profit=None if unit_gp is None else q * unit_gp,
                      gross_profit_with_uplift=None if unit_gp is None else q * (1 + uplift) * unit_gp)
    result["verdict"], result["reason"] = verdict(deal, result, offered)
    return result


def verdict(deal, result, offered):
    """(verdict, reason) under RULES_VERSION. Missing inputs are never a pass."""
    if deal.cap is None or deal.floor is None or result["margin"] is None:
        return NOT_ASSESSED, "A price, cost or guardrail value is missing, so this proposal cannot be checked."
    if not result["floor_ok"]:
        return REJECTED, (f"Gross margin {result['margin']:.1f}% is below the {deal.floor:.2f}% minimum; "
                          "this needs senior approval and is not offered.")
    if result["cap_ok"]:
        return APPROVED, (f"Within the {deal.cap:.2f}% cap and above the margin minimum: the rep can agree it "
                          "(rules pending client confirmation).")
    if deal.distributor:
        return CONDITIONS, (f"Above the {deal.cap:.2f}% cap: distributor proposals above the cap go to manager "
                            "review (volume thresholds were not supplied). Do not confirm it to the account yet.")
    if offered is not None:
        return CONDITIONS, (f"Above the {deal.cap:.2f}% cap with the required commitment ({offered.label}): "
                            "prepare it for manager review; do not confirm it to the account yet.")
    return REJECTED, (f"Above the {deal.cap:.2f}% cap without the required commitment "
                      f"({deal.tradeoff or 'not supplied'}).")


# ---------------------------------------------------------------- Scenario B: price change

def price_response(increase_pct, elasticity, pass_through_pct=100.0, unit_net=None, unit_cost=None):
    """Volume, revenue and gross-profit change (fractions) for a list-price increase.

    The shelf price moves by increase × pass-through; volume follows a constant elasticity on that
    change; Marvel's revenue per unit rises by the full increase (the trade discount % is unchanged)."""
    p = increase_pct / 100
    shelf = p * pass_through_pct / 100
    if 1 + shelf <= 0:
        raise ValueError("Price change out of range")
    volume = (1 + shelf) ** elasticity - 1
    revenue = (1 + p) * (1 + volume) - 1
    profit = None
    if unit_net is not None and unit_cost is not None and unit_net - unit_cost > 0:
        old = unit_net - unit_cost
        new = unit_net * (1 + p) - unit_cost
        profit = new * (1 + volume) / old - 1
    return {"volume": volume, "revenue": revenue, "profit": profit}


def breakeven_elasticity(increase_pct, pass_through_pct=100.0):
    """The elasticity at which the increase leaves revenue unchanged (more negative: revenue falls)."""
    p = increase_pct / 100
    shelf = p * pass_through_pct / 100
    if p <= 0 or shelf <= 0:
        return None
    return -math.log(1 + p) / math.log(1 + shelf)
