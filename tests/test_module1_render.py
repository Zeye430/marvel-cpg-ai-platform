"""Module 1 rendering on the hand-built SYNTHETIC fixture of tests/test_module1_shared.py.

Every chart and the KPI tiles render for the manager and the rep, for representative,
empty and no-SKU filters, without a NaN/Infinity value reaching the browser (the cause of
the legacy session crash). Rep views lock the manager-only charts. No client data, cloud
access or model is used.
"""

import ast
import json
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import plotly.graph_objects as go

from modules.module_1 import server as m1
from modules.module_1.data import Filters, Module1Pool
from tests.test_module1_shared import D0, build_fixture, reader_for

ROOT = Path(__file__).resolve().parents[1]
FIGURE = re.compile(r"var f=(\{.*?\});var el=", re.S)


def figures(markup):
    """Every figure payload embedded in a rendered chart, parsed strictly (NaN/Infinity raise)."""
    return [json.loads(m, parse_constant=m1._reject) for m in FIGURE.findall(markup)]


class Module1RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic.duckdb"
        build_fixture(cls.path)
        cls.pool = Module1Pool(reader_for(cls.path))
        cls.manager, cls.rep = cls.pool.get("mgr"), cls.pool.get("rep")
        info = cls.manager.choices()
        cls.colors = {"channel": m1.palette(info["channels"]), "region": m1.palette(info["regions"]),
                      "tier": m1.palette(info["tiers"]), "category": m1.palette(info["categories"])}

    @classmethod
    def tearDownClass(cls):
        cls.manager.close()
        cls.rep.close()
        cls.temp.cleanup()

    def filters(self, skus=("P1", "P2", "P3"), channels=("Modern Trade", "Traditional Trade"),
                regions=("Northeast", "West"), start=D0, end=D0 + timedelta(27)):
        return Filters(tuple(channels), tuple(regions), tuple(skus), set(skus) == {"P1", "P2", "P3"}, start, end)

    def render(self, output_id, item, f, option=None):
        return str(m1.render_chart(output_id, item, f, option, self.colors))

    def test_every_chart_renders_strict_json_for_the_manager(self):
        options = {"m1_chart9_geo_map": ("health_score", "net_sales"), "m1_chart2_lost_sales": ("region", "category"),
                   "m1_chart3_duration": ("channel", "segment_tier"),
                   "m1_chart6_radar": ("region", "segment_tier", "channel")}
        drawn = 0
        for output_id in m1.CHARTS:
            for option in options.get(output_id, (None,)):
                with self.subTest(chart=output_id, option=option):
                    markup = self.render(output_id, self.manager, self.filters(), option)
                    self.assertNotIn("Regional manager access required", markup)
                    self.assertNotIn("could not load", markup)
                    parsed = figures(markup)
                    for fig in parsed:
                        self.assertTrue(fig["data"], output_id)
                    drawn += bool(parsed)
        self.assertGreaterEqual(drawn, 12)          # all ten chart types draw on representative data

    def test_unknown_values_become_null_not_nan(self):
        markup = self.render("m1_chart1_heatmap", self.manager, self.filters())
        (fig,) = figures(markup)
        self.assertIn(None, [v for row in fig["data"][0]["z"] for v in row])   # Unknown stays a gap
        self.assertIn("Unknown", json.dumps(fig["data"][0]["text"]))
        self.assertNotIn("NaN", markup)
        payload = m1.figure_json(go.Figure(go.Bar(x=["a", "b"], y=[float("nan"), float("inf")])))
        self.assertEqual(json.loads(payload)["data"][0]["y"], [None, None])
        with self.assertRaises(m1.NonFiniteChart):
            json.loads('{"y": [NaN]}', parse_constant=m1._reject)

    def test_rep_sees_three_charts_and_locked_manager_views(self):
        for output_id, (_, _, manager_only, _) in m1.CHARTS.items():
            with self.subTest(chart=output_id):
                markup = self.render(output_id, self.rep, self.filters())
                self.assertEqual("Regional manager access required" in markup, manager_only)
                if manager_only:
                    self.assertEqual(figures(markup), [])          # nothing of the chart is computed or sent
        self.assertEqual(sorted(k for k, v in m1.CHARTS.items() if not v[2]),
                         ["m1_chart1_heatmap", "m1_chart4_compliance", "m1_chart7_discount"])

    def test_empty_and_no_sku_filters_give_messages_not_errors(self):
        empty, none = self.filters(channels=("Distributor",)), self.filters(skus=())
        for output_id in m1.CHARTS:
            with self.subTest(chart=output_id):
                for f in (empty, none):
                    markup = self.render(output_id, self.manager, f)
                    self.assertIn("ws-state", markup)
                    self.assertNotIn("could not load", markup)
                    self.assertEqual(figures(markup), [])
        self.assertIn("No SKU selected", self.render("m1_chart4_compliance", self.manager, none))

    def test_kpi_cards_are_compact_and_show_na_without_a_verdict(self):
        f = self.filters()
        tiles = str(m1.kpi_tiles(self.manager.kpis(f), self.manager.kpis(self.filters(channels=("Distributor",))),
                                 selected="oos"))
        self.assertEqual(len(re.findall(r'class="m1-kpi(?: on)?"', tiles)), 5)
        self.assertEqual(tiles.count('aria-pressed="true"'), 1)          # the selected card
        self.assertIn('id="m1-kpi-oos"', tiles)
        self.assertIn("No prior value", tiles)
        self.assertIn("Target ≥ 90%", tiles)
        for small_print in ("How it's calculated", "not market share", "invoice", "coverage"):
            self.assertNotIn(small_print, tiles)                          # moved to the detail below the cards
        empty = self.manager.kpis(self.filters(channels=("Distributor",)))
        blank = str(m1.kpi_tiles(empty, empty))
        self.assertEqual(blank.count(">N/A<"), 5)
        self.assertNotIn("Target", blank)               # no target verdict on N/A

    def test_kpi_detail_has_the_trend_with_target_notes_and_calculation(self):
        f = self.filters()
        c, p = self.manager.kpis(f), self.manager.kpis(self.filters(channels=("Distributor",)))
        expected = {"msl": "team display target", "oos": "Unknown inventory is never treated as in stock",
                    "gap": "Invoice-extract completeness is unconfirmed", "shelf": "not Marvel vs competitor shelf share",
                    "plano": "audited SKU rows"}
        for key, note in expected.items():
            with self.subTest(key=key):
                trend = self.manager.kpi_trend(f, key)
                markup = str(m1.kpi_detail(key, c, p, trend, f))
                self.assertIn(note, markup)
                self.assertIn("How it's calculated", markup)
                self.assertIn(m1.KPI_SPECS[key]["formula"], markup)
                if trend["value"].notna().any():
                    (fig,) = figures(markup)                          # strict JSON: no NaN reaches the browser
                    shapes = [s for s in fig["layout"].get("shapes", []) if s.get("type") == "line"]
                    self.assertEqual(bool(shapes), key in m1.KPI_TARGETS)   # the target line, where one applies
                else:
                    self.assertIn("No trend for these filters", markup)

    def test_kpi_trend_adds_up_to_the_card_value(self):
        info = self.manager.choices()
        partial = tuple(list(info["products"])[:1])                   # one SKU: unequal visits per week
        cases = (self.filters(), self.filters(skus=partial))
        fields = (("msl", "msl", 100), ("oos", "oos", 100), ("gap", "gap_pct", 100), ("plano", "plano", 100),
                  ("shelf", "allocation", 1))
        for f in cases:
            c = self.manager.kpis(f)
            for key, field, scale in fields:
                with self.subTest(key=key, skus=len(f.skus)):
                    trend = self.manager.kpi_trend(f, key)
                    den = trend["denominator"].sum()
                    total = None if not den else scale * trend["numerator"].sum() / den
                    if c[field] is None:
                        self.assertIsNone(total)
                    else:
                        self.assertAlmostEqual(total, c[field], places=6)

    def test_msl_option_narrows_the_selected_skus(self):
        info = self.manager.choices()
        self.assertTrue(set(info["msl_products"]) <= set(info["products"]))
        self.assertTrue(info["msl_products"])

    def test_colours_follow_the_entity_not_the_filter(self):
        palette = m1.palette(["Distributor", "Modern Trade", "Traditional Trade"])
        self.assertEqual(len(set(palette.values())), 3)
        one = self.render("m1_chart3_duration", self.manager, self.filters(channels=("Traditional Trade",)), "channel")
        both = self.render("m1_chart3_duration", self.manager, self.filters(), "channel")
        color = self.colors["channel"]["Traditional Trade"]
        for markup in (one, both):
            traces = [t for fig in figures(markup) for t in fig["data"] if t.get("name") == "Traditional Trade"]
            self.assertTrue(traces)
            self.assertEqual({t["marker"]["color"] for t in traces}, {color})

    def test_module_uses_only_the_shared_source_and_local_assets(self):
        imports = set()
        for name in ("server.py", "ui.py", "data.py", "__init__.py"):
            tree = ast.parse((ROOT / "modules" / "module_1" / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                elif isinstance(node, ast.Import):
                    imports.update(a.name for a in node.names)
        for legacy in ("shared.data", "shared.auth", "shinywidgets"):
            self.assertNotIn(legacy, imports)
        geo = ROOT / "modules" / "module_1" / "assets" / "geo" / "usa_110m.json"
        self.assertEqual(json.loads(geo.read_text())["type"], "Topology")
        markup = self.render("m1_chart9_geo_map", self.manager, self.filters())
        self.assertIn('"topojsonURL": "lib/m1-geo-1.0/"', markup)
        self.assertNotIn("cdn.plot.ly", markup)


class FilterPillAndWelcomeTests(unittest.TestCase):
    """Pure helpers behind the filter pills and the welcome screen (no data)."""

    def test_greeting_follows_the_browsers_hour(self):
        for hour, text in ((None, "Welcome"), (4, "Working late"), (5, "Good morning"), (11, "Good morning"),
                           (12, "Good afternoon"), (16, "Good afternoon"), (17, "Good evening"),
                           (22, "Good evening"), (23, "Working late")):
            self.assertEqual(m1.greeting(hour), text, hour)
        self.assertEqual(m1.display_name("Sales Rep A (demo)"), "Sales Rep A")

    def test_welcome_names_the_user_and_what_is_loading(self):
        text = str(m1.welcome("Manager (demo)", {"hour": 9, "day": "Thursday, November 5"},
                              m1.welcome_detail(221, "2026-06-30")))
        for part in ("Good morning, Manager", "Thursday, November 5", "for 221 stores", "data through 2026-06-30",
                     'role="progressbar"'):
            self.assertIn(part, text)
        bare = str(m1.welcome(None))                       # no clock, name or scope yet: still a sensible line
        self.assertIn("Welcome", bare)
        self.assertIn("Getting your business performance ready.", bare)

    def test_change_colour_says_better_or_worse_and_the_gap_aims_at_zero(self):
        kind = lambda pill: re.search(r'ws-pill (\w+)', str(pill)).group(1)
        self.assertEqual(kind(m1._delta(50.0, 45.0, "up")), "ok")
        self.assertEqual(kind(m1._delta(6.0, 5.0, "down")), "bad")
        self.assertEqual(kind(m1._delta(-3.0, -5.0, "zero")), "ok")       # up, closer to 0: better
        self.assertEqual(kind(m1._delta(-5.0, -3.0, "zero")), "bad")      # down, further from 0: worse
        self.assertEqual(kind(m1._delta(4.0, 2.0, "zero")), "bad")
        self.assertEqual(kind(m1._delta(2.0, 4.0, "zero")), "ok")
        self.assertEqual(kind(m1._delta(3.0, -3.0, "zero")), "outline")   # as far from 0 as before: neutral
        self.assertIn("▲ 2.0 pts", str(m1._delta(-3.0, -5.0, "zero")))    # the arrow is the number's direction

    def test_welcome_reads_nothing_and_gets_the_scope_line_later(self):
        self.assertEqual(m1.welcome_detail(), "Getting your business performance ready.")
        self.assertEqual(m1.welcome_detail(37, "2026-06-30"),
                         "Getting your business performance ready for 37 stores, with data through 2026-06-30.")
        self.assertIn("Getting your business performance ready.", str(m1.welcome("Sales Rep A (demo)", {"hour": 20})))

    def test_pill_values_say_all_one_name_or_how_many(self):
        names = {"a": "Alpha", "b": "Beta", "c": "Gamma"}
        self.assertEqual(m1.choice_label(["a", "b", "c"], names), ("All", False))
        self.assertEqual(m1.choice_label([], names), ("All", False))          # channel/region: none = all
        self.assertEqual(m1.choice_label(["b"], names), ("Beta", True))
        self.assertEqual(m1.choice_label(["a", "c"], names), ("2 of 3", True))
        self.assertEqual(m1.choice_label(["a", "b", "c"], names, empty_means_all=False), ("All 3", False))
        self.assertEqual(m1.choice_label([], names, empty_means_all=False), ("None", True))   # SKU: none = none

    def test_date_presets_are_whole_weeks_inside_the_data(self):
        first, last = "2025-01-01", "2026-06-30"                # 2026-06-30 is a Tuesday
        ts = m1.pd.Timestamp
        self.assertEqual(m1.preset_span("1w", first, last), (ts("2026-06-22"), ts("2026-06-28")))   # last full week
        self.assertEqual(m1.preset_span("4w", first, last), (ts("2026-06-01"), ts("2026-06-28")))
        self.assertEqual(m1.preset_span("mtd", first, last), (ts("2026-06-01"), ts(last)))
        self.assertEqual(m1.preset_span("qtd", first, last), (ts("2026-03-30"), ts(last)))       # from Monday
        self.assertEqual(m1.preset_span("ytd", first, last), (ts("2025-12-29"), ts(last)))
        self.assertEqual(m1.preset_span("all", first, last), (ts(first), ts(last)))
        for key in m1.DATE_PRESETS:
            with self.subTest(key=key):
                start, end = m1.preset_span(key, first, last)
                self.assertTrue(start.dayofweek == 0 or start == ts(first))
                self.assertLessEqual(end, ts(last))                              # never past the data
                self.assertEqual(m1.matching_preset((start, end), first, last, chosen=key), key)
        self.assertEqual(m1.DEFAULT_PRESET, "4w")
        self.assertNotIn("13w", m1.DATE_PRESETS)
        custom = m1.snap("2026-03-03", "2026-03-20", first, last)
        self.assertIsNone(m1.matching_preset(custom, first, last))
        self.assertIsNone(m1.matching_preset(custom, first, last, chosen="qtd"))         # edited: custom
        # Data ending on a Sunday: the last week is the one that ends that day.
        self.assertEqual(m1.preset_span("1w", first, "2026-06-28"), (ts("2026-06-22"), ts("2026-06-28")))

    def test_msl_pill_narrows_the_sku_list(self):
        info = {"products": {"a": "A", "b": "B", "c": "C"}, "msl_products": ["a", "c"],
                "product_names": {"a": "Alpha", "b": "Beta", "c": "Gamma"}, "product_categories": {}}
        self.assertEqual(m1.msl_skus(info, "all"), ["a", "b", "c"])
        self.assertEqual(m1.msl_skus(info, "msl"), ["a", "c"])
        self.assertEqual(m1.msl_skus(info, "non"), ["b"])
        self.assertEqual(list(m1.sku_choices(info, ["a", "c"])), ["a", "c"])
        names = {s: s for s in m1.msl_skus(info, "msl")}
        self.assertEqual(m1.choice_label(["a", "c"], names, empty_means_all=False), ("All 2", False))

    def test_change_colour_says_better_or_worse_for_that_kpi(self):
        up = str(m1._delta(52.0, 50.0, "up"))
        self.assertIn('data-trend="ok"', up)
        self.assertIn("higher is better", up)
        oos_up = str(m1._delta(7.0, 5.0, "down"))                # OOS went up: worse, red
        self.assertIn('data-trend="bad"', oos_up)
        self.assertIn("▲", oos_up)
        self.assertIn("lower is better", oos_up)
        oos_down = str(m1._delta(4.0, 5.0, "down"))              # OOS went down: better, green
        self.assertIn('data-trend="ok"', oos_down)
        self.assertIn("▼", oos_down)
        gap = str(m1._delta(-2.0, 4.0, "zero"))                  # closer to zero: better although it fell
        self.assertIn('data-trend="ok"', gap)


if __name__ == "__main__":
    unittest.main()
