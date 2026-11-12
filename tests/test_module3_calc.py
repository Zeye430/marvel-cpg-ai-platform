"""Module 3 pure calculations: deal economics, the guardrail verdict and the price response.

No data access. The worked examples use the delivered guardrail revision with a standard discount
(Modern Trade Surface Cleaners: standard 9.64%, maximum additional 5.03 pts, minimum margin 22.01%,
commitment "Achieve 100% MSL compliance + planogram adherence") on Marvel Power Clean Lemon 1L
(list $4.50, cost $2.10), under the Owner's provisional rules (calc.RULES_VERSION).
"""

import math
import unittest

from modules.module_3 import calc
from modules.module_3.server import deal_from, default_proposals

TRADEOFFS = ("Secure +10% shelf share or place 1 secondary display",
             "Secure +15% shelf share or place 1 secondary display",
             "Commit to featured endcap placement for 4 weeks",
             "Achieve 100% MSL compliance + planogram adherence")
STANDARD, CAP, FLOOR = 9.64, 5.03, 22.01


def lemon(standard=STANDARD, cap=CAP, floor=FLOOR, channel="Modern Trade", tradeoff=TRADEOFFS[3], status=None):
    return calc.Deal(channel, 4.50, 2.10, standard, cap, floor, tradeoff, status)


def floor_total(floor, list_price=4.50, unit_cost=2.10):
    """The total discount (%) at which the margin equals ``floor`` exactly."""
    return 100 * (1 - unit_cost / (list_price * (1 - floor / 100)))


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
    def verdict(self, deal, additional, commitment=None):
        return calc.scenario(deal, additional, commitment)["verdict"]

    def test_total_is_standard_plus_additional(self):
        r = calc.scenario(lemon(), 3.0, "planogram")
        self.assertAlmostEqual(r["total"], 12.64, places=9)
        net, margin, _ = calc.unit_economics(4.50, 2.10, 12.64)
        self.assertAlmostEqual(r["net"], net, places=12)
        self.assertAlmostEqual(r["margin"], margin, places=12)
        self.assertAlmostEqual(r["margin"], 100 * (4.5 * (1 - 0.1264) - 2.1) / (4.5 * (1 - 0.1264)), places=9)

    def test_rule_matrix(self):
        cases = [  # (additional, commitment, verdict)
            (0, None, calc.APPROVED),                   # exact zero: the standard only, no commitment needed
            (0, "planogram", calc.APPROVED),
            (0.5, None, calc.REJECTED),                 # positive additional needs the commitment
            (0.5, "planogram", calc.CONDITIONS),
            (CAP, "planogram", calc.CONDITIONS),        # exact cap is inside
            (CAP, None, calc.REJECTED),
            (CAP + 0.01, None, calc.REVIEW),            # above the cap, margin still fine: manager review
            (CAP + 0.01, "planogram", calc.REVIEW),
            (20, None, calc.REVIEW),
            (40, "planogram", calc.REJECTED),           # below the minimum margin
            (100 - STANDARD, None, calc.REJECTED),      # 100% total: no net price
            (150, None, calc.REJECTED),
        ]
        for additional, commitment, expected in cases:
            with self.subTest(additional=additional, commitment=commitment):
                self.assertEqual(self.verdict(lemon(), additional, commitment), expected)
        self.assertIn("no extra commitment", calc.scenario(lemon(), 0)["reason"])
        self.assertIn("needs the required commitment", calc.scenario(lemon(), 1)["reason"])
        self.assertIn("manager review", calc.scenario(lemon(), 6)["reason"])
        self.assertIn("below the 22.01% minimum", calc.scenario(lemon(), 40)["reason"])
        self.assertIn("no net price", calc.scenario(lemon(), 100 - STANDARD)["reason"])

    def test_exact_floor_passes_and_just_below_fails(self):
        additional = floor_total(FLOOR) - STANDARD                      # about 30.5 pts, above the cap
        at = calc.scenario(lemon(), additional)
        self.assertAlmostEqual(at["margin"], FLOOR, places=9)
        self.assertEqual(at["verdict"], calc.REVIEW)
        self.assertEqual(self.verdict(lemon(), additional + 1e-6), calc.REJECTED)
        within = lemon(floor=calc.unit_economics(4.5, 2.1, STANDARD + 2.0)[1])   # floor met exactly within the cap
        self.assertEqual(self.verdict(within, 2.0, "planogram"), calc.CONDITIONS)
        self.assertEqual(self.verdict(lemon(floor=within.floor + 1e-6), 2.0, "planogram"), calc.REJECTED)
        self.assertEqual(self.verdict(lemon(floor=calc.unit_economics(4.5, 2.1, STANDARD)[1]), 0), calc.APPROVED)

    def test_missing_or_invalid_inputs_are_never_a_pass(self):
        for deal in (lemon(standard=None), calc.Deal("Modern Trade", None, 2.1, STANDARD, CAP, FLOOR, TRADEOFFS[3]),
                     calc.Deal("Modern Trade", 4.5, None, STANDARD, CAP, FLOOR, TRADEOFFS[3]), lemon(floor=None)):
            for additional in (0, 2):
                with self.subTest(deal=deal, additional=additional):
                    r = calc.scenario(deal, additional, "planogram", quantity=100)
                    self.assertEqual(r["verdict"], calc.NOT_ASSESSED)
        for value in (None, -0.5, -10, float("nan"), float("inf"), "x", True):
            with self.subTest(additional=value):
                r = calc.scenario(lemon(), value, "planogram", quantity=100)
                self.assertEqual(r["verdict"], calc.NOT_ASSESSED)
                for key in ("total", "net", "margin", "breakeven", "revenue", "gross_profit"):
                    self.assertIsNone(r[key], key)                           # nothing invented, no zero filled in
        self.assertEqual(self.verdict(lemon(cap=None), 1, "planogram"), calc.NOT_ASSESSED)
        self.assertEqual(self.verdict(lemon(cap=None), 0), calc.APPROVED)    # the cap only matters above 0
        self.assertEqual(self.verdict(lemon(tradeoff=""), 1, "planogram"), calc.NOT_ASSESSED)
        self.assertEqual(self.verdict(lemon(tradeoff=""), 6), calc.REVIEW)
        self.assertIn("standard discount", calc.scenario(lemon(standard=None), 0)["reason"])

    def test_zero_standard_is_a_value_not_a_missing_one(self):
        zero = lemon(standard=0.0)
        r = calc.scenario(zero, 0)
        self.assertEqual((r["total"], r["verdict"]), (0.0, calc.APPROVED))
        self.assertEqual(self.verdict(zero, CAP, "planogram"), calc.CONDITIONS)

    def test_recent_invoiced_discount_is_never_the_standard(self):
        context = {"channel": "Modern Trade", "reference_list_price": 4.5, "reference_unit_cost": 2.1,
                   "weighted_trade_discount_pct_28d": 12.0, "standard_discount_pct_source": STANDARD,
                   "max_discount_pct_source": CAP, "min_target_margin_source": FLOOR,
                   "required_exec_tradeoff": TRADEOFFS[3], "policy_status": "Requires review"}
        deal = deal_from(context)
        self.assertEqual(deal.standard, STANDARD)
        self.assertAlmostEqual(calc.scenario(deal, 1.0, "planogram")["total"], STANDARD + 1.0, places=9)
        old = {k: v for k, v in context.items() if k != "standard_discount_pct_source"}     # earlier release
        r = calc.scenario(deal_from(old), 0)
        self.assertEqual((r["total"], r["verdict"]), (None, calc.NOT_ASSESSED))

    def test_distributor_additional_discount_goes_to_manager_review(self):
        deal = lemon(standard=11.31, cap=6.99, floor=19.06, channel="Distributor",
                     tradeoff="Secure +10% shelf share or place 1 secondary display")
        self.assertEqual(self.verdict(deal, 0), calc.APPROVED)
        for additional in (0.5, 6.99, 7.0, 20):
            for commitment in (None, "shelf", "display"):
                with self.subTest(additional=additional, commitment=commitment):
                    r = calc.scenario(deal, additional, commitment)
                    self.assertEqual(r["verdict"], calc.REVIEW)
                    self.assertIsNone(r["commitment"])                  # no shelf commitment for wholesale
                    self.assertIn("distributor commitments are not confirmed", r["reason"])
        self.assertEqual(self.verdict(deal, 60), calc.REJECTED)         # below the minimum still rejected
        self.assertEqual(self.verdict(deal, 100 - 11.31), calc.REJECTED)

    def test_distributor_card_offers_manager_review_not_a_shelf_commitment(self):
        from modules.module_3.server import context_panel
        deal = lemon(standard=11.31, cap=6.99, floor=19.06, channel="Distributor", tradeoff=TRADEOFFS[0])
        text = str(context_panel({"channel": "Distributor", "category": "Laundry"}, deal))
        self.assertIn("manager review", text)
        self.assertNotIn("shelf share or place", text)
        retail = str(context_panel({"channel": "Modern Trade", "category": "Laundry"}, lemon(tradeoff=TRADEOFFS[0])))
        self.assertIn("shelf share or place", retail)
        self.assertIn("9.64%", retail)                                  # the standard is shown
        self.assertIn("14.67%", retail)                                 # standard + maximum additional

    def test_unconfirmed_policy_gives_no_permission_to_agree(self):
        pending = lemon(status="Requires review")
        self.assertTrue(pending.provisional)
        for additional, commitment in ((0, None), (2, "planogram"), (2, None), (6, None), (40, "planogram")):
            r = calc.scenario(pending, additional, commitment)
            self.assertTrue(r["provisional"])
            self.assertIn("does not authorise offering or agreeing anything", r["reason"])
            self.assertNotIn("within the rep's authority", r["reason"])
        self.assertTrue(lemon().provisional)                             # status missing
        confirmed = lemon(status="Approved")
        ok = calc.scenario(confirmed, 0)
        self.assertEqual((ok["verdict"], ok["provisional"]), (calc.APPROVED, False))
        self.assertNotIn("Provisional", ok["reason"])
        above = calc.scenario(confirmed, 6)
        self.assertIn("Do not confirm it to the account", above["reason"])  # review is never permission

    def test_unknown_rule_option_is_refused(self):
        with self.assertRaises(ValueError):
            calc.scenario(lemon(), 1, uplift_source="guess")
        with self.assertRaises(TypeError):
            calc.scenario(lemon(), 1, basis="total")                    # the cap basis is no longer a choice


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
        deal = lemon(tradeoff=TRADEOFFS[0])
        display = calc.scenario(deal, 2.0, "display", quantity=100)
        base = 4.5 * (1 - 0.0964) - 2.1                                # gross profit at the standard discount
        unit = 4.5 * (1 - 0.1164) - 2.1
        self.assertAlmostEqual(display["breakeven"], base / unit - 1, places=9)    # about +5.0%
        self.assertTrue(display["covers"])                                         # +8% display
        shelf = calc.scenario(deal, 2.0, "shelf")
        self.assertFalse(shelf["covers"])                                          # +3% per 10 pts
        measured = calc.scenario(deal, 2.0, "display", uplift_source="measured")
        self.assertEqual(measured["uplift"], 0.0)
        self.assertFalse(measured["covers"])
        self.assertEqual(calc.scenario(deal, 0)["breakeven"], 0.0)
        self.assertAlmostEqual(display["gross_profit"], 100 * unit, places=9)
        self.assertAlmostEqual(display["discount_cost"], 100 * 4.5 * 0.1164, places=9)

    def test_starting_proposals_show_the_rules(self):
        for tradeoff in TRADEOFFS:
            deal = lemon(tradeoff=tradeoff)
            verdicts = [calc.scenario(deal, a, None if c == "none" else c)["verdict"] for a, c in default_proposals(deal)]
            self.assertEqual(verdicts, [calc.APPROVED, calc.CONDITIONS, calc.REVIEW], tradeoff)
        for cap in (0.4, 3.15, 6.99):
            proposals = default_proposals(lemon(cap=cap))
            self.assertLessEqual(proposals[1][0], cap)
            self.assertGreater(proposals[2][0], cap)
        distributor = lemon(channel="Distributor", cap=6.99)
        verdicts = [calc.scenario(distributor, a, None if c == "none" else c)["verdict"]
                    for a, c in default_proposals(distributor)]
        self.assertEqual(verdicts, [calc.APPROVED, calc.REVIEW, calc.REVIEW])


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
