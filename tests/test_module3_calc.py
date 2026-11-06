"""Module 3 pure calculations: deal economics, the guardrail verdict and the price response.

No data access. The worked examples are the pricing policy's (knowledge base MCPG-05, which uses the
V3 guardrail table): Marvel Power Clean Lemon 1L, list $4.50, cost $2.10, Modern Trade Surface Cleaners
cap 12.80%, minimum margin 26.62%.
"""

import math
import unittest

from modules.module_3 import calc
from modules.module_3.server import default_proposals

TRADEOFFS = ("Secure +10% shelf share or place 1 secondary display",
             "Secure +15% shelf share or place 1 secondary display",
             "Commit to featured endcap placement for 4 weeks",
             "Achieve 100% MSL compliance + planogram adherence")


def lemon(current=0.0, channel="Modern Trade", tradeoff=TRADEOFFS[0]):
    return calc.Deal(channel, 4.50, 2.10, current, 12.80, 26.62, tradeoff)


class EconomicsTests(unittest.TestCase):
    def test_policy_worked_examples(self):
        for discount, net, margin in ((0, 4.50, 53.3), (10, 4.05, 48.1), (12.80, 3.92, 46.5), (15, 3.82, 45.1)):
            n, m, _ = calc.unit_economics(4.50, 2.10, discount)
            self.assertAlmostEqual(n, net, places=2)
            self.assertAlmostEqual(m, margin, places=1)

    def test_missing_or_zero_price_is_not_a_number(self):
        self.assertEqual(calc.unit_economics(None, 2.1, 10), (None, None, None))
        net, margin, _ = calc.unit_economics(4.5, 2.1, 100)
        self.assertEqual((net, margin), (0.0, None))


class VerdictTests(unittest.TestCase):
    def test_policy_examples_give_the_three_verdicts(self):
        self.assertEqual(calc.scenario(lemon(), 10)["verdict"], calc.APPROVED)
        self.assertEqual(calc.scenario(lemon(), 15, "shelf")["verdict"], calc.CONDITIONS)
        self.assertEqual(calc.scenario(lemon(), 15, "display")["verdict"], calc.CONDITIONS)
        rejected = calc.scenario(lemon(), 15)
        self.assertEqual(rejected["verdict"], calc.REJECTED)
        self.assertIn("without the required commitment", rejected["reason"])

    def test_cap_is_inclusive_and_compares_the_total_discount_by_default(self):
        self.assertEqual(calc.scenario(lemon(current=10.8), 2.0)["verdict"], calc.APPROVED)     # 12.80 = cap
        over = calc.scenario(lemon(current=12.0), 1.0)
        self.assertEqual((over["total"], over["verdict"]), (13.0, calc.REJECTED))
        # The other reading (the brief's additional discount): only the 1 point is compared.
        self.assertEqual(calc.scenario(lemon(current=12.0), 1.0, basis="additional")["verdict"], calc.APPROVED)

    def test_margin_below_the_minimum_is_rejected_even_with_a_commitment(self):
        deep = calc.scenario(lemon(), 40, "display")
        self.assertLess(deep["margin"], 26.62)
        self.assertEqual(deep["verdict"], calc.REJECTED)
        self.assertIn("senior approval", deep["reason"])

    def test_distributor_above_the_cap_goes_to_manager_review_without_a_shelf_commitment(self):
        deal = calc.Deal("Distributor", 4.50, 2.10, 14.0, 16.34, 17.41, TRADEOFFS[2])
        self.assertEqual(calc.scenario(deal, 2.0)["verdict"], calc.APPROVED)
        above = calc.scenario(deal, 4.0)
        self.assertEqual(above["verdict"], calc.CONDITIONS)
        self.assertIn("manager review", above["reason"])

    def test_distributor_card_offers_manager_review_not_a_shelf_commitment(self):
        from modules.module_3.server import context_panel
        deal = calc.Deal("Distributor", 4.50, 2.10, 14.0, 16.34, 17.41, TRADEOFFS[0])
        text = str(context_panel({"channel": "Distributor", "category": "Laundry"}, deal))
        self.assertIn("manager review", text)
        self.assertNotIn("shelf share or place", text)
        retail = str(context_panel({"channel": "Modern Trade", "category": "Laundry"}, lemon()))
        self.assertIn("shelf share or place", retail)

    def test_a_discount_that_leaves_no_net_price_is_rejected_not_missing(self):
        for additional in (100, 150):
            out = calc.scenario(lemon(), additional)
            self.assertEqual(out["verdict"], calc.REJECTED)
            self.assertIn("no net price", out["reason"])

    def test_missing_inputs_are_never_a_pass(self):
        for deal in (calc.Deal("Modern Trade", None, 2.1, 0.0, 12.8, 26.62),
                     calc.Deal("Modern Trade", 4.5, 2.1, 0.0, None, 26.62),
                     calc.Deal("Modern Trade", 4.5, 2.1, 0.0, 12.8, None)):
            self.assertEqual(calc.scenario(deal, 5)["verdict"], calc.NOT_ASSESSED)

    def test_unknown_current_discount_is_disclosed_and_not_guessed(self):
        result = calc.scenario(lemon(current=None), 5.0)
        self.assertEqual((result["current"], result["total"], result["compared"]), (None, 5.0, 5.0))
        self.assertIn("current discount is unknown", result["notes"][0])

    def test_unknown_rule_option_is_refused(self):
        with self.assertRaises(ValueError):
            calc.scenario(lemon(), 1, basis="net")
        with self.assertRaises(ValueError):
            calc.scenario(lemon(), 1, uplift_source="guess")


class CommitmentAndPaybackTests(unittest.TestCase):
    def test_every_supplied_tradeoff_maps_to_commitments_with_the_client_uplift(self):
        got = {text: [(c.key, round(c.uplift, 3), c.weeks) for c in calc.commitments(text)] for text in TRADEOFFS}
        self.assertEqual(got[TRADEOFFS[0]], [("shelf", 0.03, None), ("display", 0.08, None)])
        self.assertEqual(got[TRADEOFFS[1]], [("shelf", 0.045, None), ("display", 0.08, None)])
        self.assertEqual(got[TRADEOFFS[2]], [("endcap", 0.08, 4)])
        self.assertEqual(got[TRADEOFFS[3]], [("planogram", 0.05, None)])
        self.assertEqual([c.key for c in calc.commitments("Something new")], ["required"])
        self.assertEqual(calc.commitments(""), [])

    def test_break_even_volume_and_whether_the_uplift_covers_it(self):
        deal = lemon(current=12.7)
        display = calc.scenario(deal, 2.0, "display", quantity=100)
        self.assertAlmostEqual(display["breakeven"], 1.8285 / 1.7385 - 1, places=6)   # about +5.2%
        self.assertTrue(display["covers"])                                            # +8% display
        shelf = calc.scenario(deal, 2.0, "shelf")
        self.assertFalse(shelf["covers"])                                             # +3% per 10 pts
        measured = calc.scenario(deal, 2.0, "display", uplift_source="measured")
        self.assertEqual(measured["uplift"], 0.0)
        self.assertFalse(measured["covers"])
        self.assertAlmostEqual(display["gross_profit"], 100 * (4.5 * (1 - 0.147) - 2.1), places=6)
        self.assertAlmostEqual(display["discount_cost"], 100 * 4.5 * 0.147, places=6)

    def test_starting_proposals_show_all_three_verdicts(self):
        for current in (0.0, 6.0, None):
            deal = lemon(current=current)
            verdicts = [calc.scenario(deal, a, None if c == "none" else c)["verdict"] for a, c in default_proposals(deal)]
            self.assertEqual(verdicts, [calc.APPROVED, calc.REJECTED, calc.CONDITIONS], current)
        over = lemon(current=13.0)                         # already above the cap: honest, not hidden
        verdicts = [calc.scenario(over, a, None if c == "none" else c)["verdict"] for a, c in default_proposals(over)]
        self.assertEqual(verdicts, [calc.REJECTED, calc.REJECTED, calc.CONDITIONS])


class PriceResponseTests(unittest.TestCase):
    def test_unit_elastic_demand_keeps_revenue_flat(self):
        r = calc.price_response(5, -1.0, 100, unit_net=4.5 * 0.873, unit_cost=2.1)
        self.assertAlmostEqual(r["volume"], 1 / 1.05 - 1, places=9)
        self.assertAlmostEqual(r["revenue"], 0.0, places=9)
        self.assertAlmostEqual(r["profit"], (4.5 * 0.873 * 1.05 - 2.1) / 1.05 / (4.5 * 0.873 - 2.1) - 1, places=9)

    def test_pass_through_and_zero_elasticity(self):
        self.assertEqual(calc.price_response(5, -1.0, 0)["volume"], 0.0)              # shelf price unchanged
        self.assertAlmostEqual(calc.price_response(10, 0.0)["revenue"], 0.10, places=9)
        self.assertIsNone(calc.price_response(5, -1.0)["profit"])                       # no unit economics given

    def test_break_even_elasticity(self):
        self.assertAlmostEqual(calc.breakeven_elasticity(5), -1.0, places=9)
        self.assertAlmostEqual(calc.breakeven_elasticity(10, 50), -math.log(1.1) / math.log(1.05), places=9)
        self.assertIsNone(calc.breakeven_elasticity(0))
        self.assertIsNone(calc.breakeven_elasticity(5, 0))


if __name__ == "__main__":
    unittest.main()
