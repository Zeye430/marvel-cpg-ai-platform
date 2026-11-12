"""
modules/module_3/calc.py
========================
Pure calculations for Module 3 (no data access): deal economics and the guardrail verdict for
Scenario A, and the price response for Scenario B.

The guardrail rules (``RULES_VERSION``) are the Owner's provisional policy, pending client confirmation
and confirmation of Sales authority:

- the channel × category **standard discount** is the starting allowance; the total discount on the
  invoice is ``standard + proposed additional`` (percentage points; the account's recent invoiced
  discount is history, never the standard and never added to it);
- no additional discount needs no extra commitment; a positive additional discount up to and including
  the maximum needs the guardrail's execution commitment; above the maximum, with the margin still at or
  above the minimum, it needs manager review (a status, not a saved workflow); a margin below the
  minimum is rejected;
- distributors: their execution commitments are not confirmed, so any additional discount goes to
  manager review.

Margin = (list price × (1 − total/100) − unit cost) / net price. A verdict is a guardrail check result,
not an approval or an offer; while the source policy is not confirmed every verdict is a provisional
simulation. Unknown inputs (price, cost, standard, maximum, minimum, commitment text, or an invalid
proposal) give CANNOT ASSESS; they are never replaced by zero.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

RULES_VERSION = "m3-rules.v1-provisional"
# Source policy states under which a verdict may be described as within the rep's authority.
# The shared negotiation context currently publishes only "Requires review".
CONFIRMED_POLICY = frozenset({"Approved"})

APPROVED = "APPROVED"
CONDITIONS = "APPROVED WITH CONDITIONS"
REVIEW = "MANAGER REVIEW"
REJECTED = "REJECTED"
NOT_ASSESSED = "CANNOT ASSESS"
TOLERANCE = 1e-9

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
    standard: float | None             # standard_discount_pct_source: the starting allowance (points)
    cap: float | None                  # max_discount_pct_source: maximum additional discount (points)
    floor: float | None                # min_target_margin_source (gross margin on net price, %)
    tradeoff: str = ""
    policy_status: str | None = None   # the source guardrail's status ("Requires review" today)

    @property
    def distributor(self):
        return self.channel == "Distributor"

    @property
    def provisional(self):
        """True unless the source policy is confirmed: verdicts are then simulations, not authority."""
        return self.policy_status not in CONFIRMED_POLICY

    @property
    def ceiling(self):
        """The highest total discount without manager review (standard + maximum additional)."""
        return None if self.standard is None or self.cap is None else self.standard + self.cap


def unit_economics(list_price, unit_cost, discount_pct):
    """Net unit price, gross margin % on net price and unit gross profit; None where undefined."""
    if list_price is None or unit_cost is None or discount_pct is None:
        return None, None, None
    net = list_price * (1 - discount_pct / 100)
    if net <= 0:
        return net, None, net - unit_cost
    return net, 100 * (net - unit_cost) / net, net - unit_cost


def _additional(value):
    """A proposed additional discount in points, or None when it is missing, not a number or negative."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def scenario(deal, additional, commitment=None, quantity=None, uplift_source="client"):
    """One proposal: economics, the guardrail checks, the verdict and its reason.

    ``additional``: the proposed discount on top of the standard, in percentage points (missing or
    negative: cannot assess). ``commitment``: a Commitment key the account offers, or None.
    ``quantity``: order units for money amounts (None: per unit only)."""
    if uplift_source not in UPLIFT_SOURCES:
        raise ValueError("Unknown rule option")
    extra = _additional(additional)
    total = None if extra is None or deal.standard is None else deal.standard + extra
    net, margin, unit_gp = unit_economics(deal.list_price, deal.unit_cost, total)
    _, _, base_gp = unit_economics(deal.list_price, deal.unit_cost, deal.standard)
    options = {} if deal.distributor else {c.key: c for c in commitments(deal.tradeoff)}
    offered = options.get(commitment)
    result = {"additional": extra, "standard": deal.standard, "total": total, "net": net, "margin": margin,
              "unit_gp": unit_gp, "commitment": offered,
              "cap_ok": None if deal.cap is None or extra is None else extra <= deal.cap + TOLERANCE,
              "floor_ok": None if margin is None or deal.floor is None else margin >= deal.floor - TOLERANCE,
              "provisional": deal.provisional, "notes": []}
    # Money for the order, and whether the commitment's assumed volume uplift pays for the extra discount
    # (compared with the same order at the standard discount).
    uplift = (offered.uplift or 0.0) if (offered and uplift_source == "client") else 0.0
    result["uplift"] = uplift
    result["breakeven"] = None
    if extra is not None and unit_gp is not None and base_gp is not None and unit_gp > 0:
        result["breakeven"] = max(0.0, base_gp / unit_gp - 1) if extra > 0 else 0.0
    result["covers"] = None if result["breakeven"] is None else uplift >= result["breakeven"] - 1e-12
    if quantity:
        q = float(quantity)
        result.update(revenue=None if net is None else q * net,
                      discount_cost=None if deal.list_price is None or total is None else q * deal.list_price * total / 100,
                      gross_profit=None if unit_gp is None else q * unit_gp,
                      gross_profit_with_uplift=None if unit_gp is None else q * (1 + uplift) * unit_gp)
    result["verdict"], result["reason"] = verdict(deal, result, offered)
    return result


PROVISIONAL = (" Provisional simulation: the policy and Sales authority await client confirmation, so this "
               "result does not authorise offering or agreeing anything.")


def verdict(deal, result, offered):
    """(verdict, reason) under RULES_VERSION. Missing inputs are never a pass; while the source policy is not
    confirmed the reason says the result is provisional and gives no permission to agree a deal."""
    suffix = PROVISIONAL if deal.provisional else ""
    extra = result["additional"]
    if extra is None:
        return NOT_ASSESSED, "Enter an additional discount of 0 or more percentage points." + suffix
    if deal.standard is None:
        return NOT_ASSESSED, ("The standard discount for this channel and category is not in the shared data, so the "
                              "total discount cannot be worked out." + suffix)
    if result["net"] is not None and result["net"] <= 0:
        return REJECTED, f"A total discount of {result['total']:.2f}% leaves no net price." + suffix
    if deal.floor is None or result["margin"] is None:
        return NOT_ASSESSED, "A price, cost or guardrail value is missing, so this proposal cannot be checked." + suffix
    if not result["floor_ok"]:
        return REJECTED, (f"Gross margin {result['margin']:.1f}% is below the {deal.floor:.2f}% minimum." + suffix)
    if extra <= TOLERANCE:
        return APPROVED, (f"Standard discount only ({deal.standard:.2f}%): no extra commitment is needed and the "
                          "margin is at or above the minimum." + suffix)
    if deal.cap is None:
        return NOT_ASSESSED, "The maximum additional discount is missing, so this proposal cannot be checked." + suffix
    if deal.distributor:
        return REVIEW, (f"An additional {extra:.2f} pts for a distributor goes to manager review: distributor "
                        "commitments are not confirmed. Do not confirm it to the account." + suffix)
    if not result["cap_ok"]:
        return REVIEW, (f"An additional {extra:.2f} pts is above the {deal.cap:.2f} pts maximum, with the margin at "
                        "or above the minimum: it needs manager review. Do not confirm it to the account." + suffix)
    if not commitments(deal.tradeoff):
        return NOT_ASSESSED, "The guardrail's required commitment is missing, so this proposal cannot be checked." + suffix
    if offered is not None:
        return CONDITIONS, (f"An additional {extra:.2f} pts is within the {deal.cap:.2f} pts maximum on condition the "
                            f"account delivers the commitment ({offered.label})." + suffix)
    return REJECTED, (f"An additional {extra:.2f} pts needs the required commitment "
                      f"({deal.tradeoff}), and none is offered." + suffix)


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
