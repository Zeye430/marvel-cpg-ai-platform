"""Module 1 rendering on the hand-built SYNTHETIC fixture of tests/test_module1_shared.py.

Every chart and the KPI tiles render for the manager and the rep, for representative,
empty and no-SKU filters, without a NaN/Infinity value reaching the browser (the cause of
the legacy session crash). Rep views lock the manager-only charts. No client data, cloud
access or model is used.
"""

import ast
import html
import json
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import plotly.graph_objects as go

from modules.module_1 import charts, panels, server as m1
from modules.module_1.data import (Filters, Module1Pool, aggregate, composite_scores, streaks,
                                   trend_direction)
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
        options = {"m1_chart9_geo_map": ("oos", "msl", "sales"), "m1_chart2_lost_sales": ("region", "category"),
                   "m1_chart3_duration": ("channel", "segment_tier")}
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
        self.assertGreaterEqual(drawn, 12)          # all eight chart types draw on representative data

    def test_unknown_values_become_null_not_nan(self):
        markup = self.render("m1_chart1_heatmap", self.manager, self.filters(end=D0 + timedelta(12)))
        (fig,) = figures(markup)
        codes = [v for row in fig["data"][0]["z"] for v in row]
        self.assertIn(m1.STATUS_CODES["unknown"], codes)                       # Unknown is its own status
        self.assertIn("Unknown", json.dumps(fig["data"][0]["text"]))
        self.assertIn("Unknown (not reported)", json.dumps(fig["data"][0]["customdata"]))
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
                         ["m1_chart10_flow", "m1_chart1_heatmap", "m1_chart4_compliance", "m1_chart7_discount",
                          "m1_chart9_geo_map"])                    # the rep's own stores on the map and flows

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
        self.assertNotIn("Target", tiles)                                 # no team-target verdict on the cards
        self.assertIn("Reported OOS Rate", tiles)
        self.assertIn("Shelf Allocation", tiles)                          # the own-SKU allocation tile
        self.assertEqual(tiles.count(">N/A<"), 0)
        for small_print in ("How it's calculated", "not market share", "invoice", "coverage"):
            self.assertNotIn(small_print, tiles)                          # moved to the detail below the cards
        empty = self.manager.kpis(self.filters(channels=("Distributor",)))
        blank = str(m1.kpi_tiles(empty, empty))
        self.assertEqual(blank.count(">N/A<"), 5)
        estimated = str(m1.kpi_tiles(self.manager.kpis(f), empty, estimated=True))
        self.assertIn(">OOS Rate<", estimated)                            # the view switch names the basis

    def test_kpi_detail_has_the_trend_with_target_notes_and_calculation(self):
        f = self.filters()
        c, p = self.manager.kpis(f), self.manager.kpis(self.filters(channels=("Distributor",)))
        expected = {"msl": "are not counted as failures", "oos": "Unknown stock is never treated as in stock",
                    "gap": "Invoice-extract completeness is unconfirmed", "shelf": "not Marvel vs competitor shelf share",
                    "plano": "audited SKU rows"}
        for key, note in expected.items():
            for show in (False, True):
                with self.subTest(key=key, show_targets=show):
                    trend = self.manager.kpi_trend(f, key)
                    markup = str(m1.kpi_detail(key, c, p, trend, f, show_targets=show))
                    self.assertIn(note, markup)
                    self.assertIn("What it measures and how it", markup)
                    for part in ("measures", "formula", "example", "limits", "next"):
                        self.assertIn(html.escape(m1.KPI_SPECS[key][part], quote=False), markup, part)
                    self.assertNotIn("Meets", markup)
                    self.assertNotIn("Misses", markup)
                    if trend["value"].notna().any():
                        (fig,) = figures(markup)                      # strict JSON: no NaN reaches the browser
                        dotted = [s for s in fig["layout"].get("shapes", []) if s.get("type") == "line"
                                  and (s.get("line") or {}).get("dash") == "dot"]
                        # the team reference only on request, and labelled as not client-approved
                        self.assertEqual(bool(dotted), show and key in m1.KPI_TARGETS)
                        if dotted:
                            self.assertIn("not client-approved", json.dumps(fig["layout"]))
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
        for name in ("server.py", "ui.py", "data.py", "charts.py", "panels.py", "__init__.py"):
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


class Module1RevisionTests(unittest.TestCase):
    """The revised dashboard: Estimated data view, store-level metrics, exceptions, store cards, the sell-in vs
    sell-out runs and the city map (SYNTHETIC fixture only)."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic.duckdb"
        build_fixture(cls.path)
        cls.pool = Module1Pool(reader_for(cls.path))
        cls.manager, cls.rep = cls.pool.get("mgr"), cls.pool.get("rep")
        cls.estimated = cls.pool.get("mgr", "estimated")
        info = cls.manager.choices()
        cls.colors = {"channel": m1.palette(info["channels"]), "region": m1.palette(info["regions"]),
                      "tier": m1.palette(info["tiers"]), "category": m1.palette(info["categories"])}

    @classmethod
    def tearDownClass(cls):
        for item in (cls.manager, cls.rep, cls.estimated):
            item.close()
        cls.temp.cleanup()

    def filters(self, skus=("P1", "P2", "P3"), channels=("Modern Trade", "Traditional Trade"),
                regions=("Northeast", "West"), start=D0, end=D0 + timedelta(27)):
        return Filters(tuple(channels), tuple(regions), tuple(skus), set(skus) == {"P1", "P2", "P3"}, start, end)

    # ---------------------------------------------------------------- Estimated data view
    def test_estimated_view_fills_short_gaps_keeps_unknown_and_both_views_coexist(self):
        f = self.filters()
        reported, estimated = self.manager.kpis(f), self.estimated.kpis(f)
        self.assertEqual(reported["estimated"], 0)
        self.assertGreater(estimated["estimated"], 0)
        self.assertEqual(estimated["oos_known"], reported["oos_known"] + estimated["estimated"])
        self.assertLess(estimated["oos_known"], estimated["expected"])     # S2/P3 day 0 stays Unknown
        self.assertAlmostEqual(estimated["estimated_share"], 100 * estimated["estimated"] / estimated["oos_known"])
        for key in ("audited", "plano", "visits"):                         # audits are observed in both views
            self.assertEqual(reported[key], estimated[key], key)
        recomputed = self.estimated.kpis(Filters(f.channels, f.regions, f.skus, False, f.start, f.end))
        for key in ("oos", "msl", "msl_unknown", "oos_known", "estimated"):
            self.assertEqual(estimated[key], recomputed[key], key)        # store_day and SKU-day paths agree
        self.assertEqual(self.estimated.estimate_info()["version"], "synthetic-estimator.v0")
        self.assertIsNone(self.manager.estimate_info())
        self.assertFalse(self.manager.closed)                              # the Reported view is kept
        self.assertIs(self.pool.get("mgr"), self.manager)
        self.assertIs(self.pool.get("mgr", "estimated"), self.estimated)

    def test_estimated_view_needs_a_publication_that_declares_it(self):
        from data_platform.access import DataUnavailable
        pool = Module1Pool(reader_for(self.path, estimates=False))
        with self.assertRaises(DataUnavailable):
            pool.get("mgr", "estimated")
        with self.assertRaises(ValueError):
            pool.get("mgr", "guessed")

    def test_estimated_heatmap_marks_estimated_cells(self):
        markup = str(m1.render_chart("m1_chart1_heatmap", self.estimated,
                                     self.filters(("P3",), end=D0 + timedelta(12)), None, self.colors))
        (fig,) = figures(markup)
        codes = {v for row in fig["data"][0]["z"] for v in row if v is not None}
        self.assertTrue(codes & {m1.STATUS_CODES["zero_est"], m1.STATUS_CODES["stocked_est"]})
        self.assertIn("estimated", markup)

    # ---------------------------------------------------------------- store level
    def test_store_metrics_add_up_to_the_kpis_and_respect_scope(self):
        for skus in (("P1", "P2", "P3"), ("P1",)):
            f = self.filters(skus)
            with self.subTest(skus=skus):
                stores = self.manager.store_metrics(f)
                a = self.manager.availability_components(f)
                for column, key in (("oos", "oos"), ("known", "known"), ("expected", "expected"),
                                    ("msl_compliant", "msl_compliant"), ("msl_assessable", "msl_assessable")):
                    self.assertEqual(int(stores[column].sum()), a[key], column)
                total = aggregate(stores.assign(all="all"), ["all"]).iloc[0]
                self.assertAlmostEqual(total["oos_pct"], self.manager.kpis(f)["oos"])
        rep = self.rep.store_metrics(self.filters())
        self.assertEqual(rep["store_id"].tolist(), ["S1"])

    def test_streaks_break_on_zero_and_missing_periods(self):
        p = [D0 + timedelta(14 * i) for i in range(6)]
        run = streaks(p, [5, 3, 0, -2, -1, -4])
        self.assertEqual(run["current"]["direction"], "-")
        self.assertEqual(run["current"]["length"], 3)
        self.assertEqual(run["current"]["first"], p[3])
        self.assertEqual(run["longest_positive"]["length"], 2)
        missing = streaks(p, [5, None, 4, 4, 4, None])
        self.assertIsNone(missing["current"])                              # the last period has no comparable flow
        self.assertEqual(missing["longest_positive"]["length"], 3)         # a missing period is never filled
        self.assertIsNone(missing["longest_negative"])
        self.assertIsNone(streaks([], [])["current"])

    def test_flow_shows_negative_gaps_instead_of_truncating_them(self):
        f = self.filters(("P1",), channels=("Traditional Trade",))
        trend = self.manager.flow_trend(f)
        self.assertEqual(trend["gap_units"].dropna().tolist(), [-20.0])
        markup = str(m1.render_chart("m1_chart10_flow", self.manager, f, None, self.colors))
        (fig,) = figures(markup)
        bars = [t for t in fig["data"] if t["type"] == "bar"]
        self.assertEqual(bars[0]["y"], [-20.0])
        breakdown = self.manager.flow_breakdown(self.filters(("P1", "P2")), "store")
        s2 = breakdown[breakdown["key"] == "S2"].iloc[0]
        self.assertEqual(s2["current"]["direction"], "-")
        self.assertIn("consumers ahead of sell-in", str(panels.run_text(s2["current"])))
        table = str(panels.flow_table(breakdown, "store"))
        self.assertIn("Longest ▼ run", table)
        self.assertIn("not proven stockouts", table)

    def test_store_card_compares_only_with_enough_similar_stores_in_scope(self):
        f = self.filters()
        stores = self.manager.store_metrics(f)
        scored = composite_scores(stores)
        row = scored[scored["store_id"] == "S1"].iloc[0].to_dict()
        trend = self.manager.store_trend(f, "S1")
        card = str(panels.store_card(row, scored, self.manager.store_skus(f, "S1"), row["latest_date"],
                                     prior=composite_scores(self.manager.store_metrics(m1.prior_filters(f))),
                                     trend=trend, reps=[{"name": "Alex Mercer", "title": "Field sales rep",
                                                         "avatar": None}],
                                     compare=["S2"], manager=True))
        self.assertIn("too few similar stores", card)                     # no channel-and-tier peers in the fixture
        for label in ("MSL compliance", "OOS rate", "Planogram compliance", "Sell-in vs sell-out gap",
                      "Shelf allocation", "/100", "Rank 1 of 2" if row["rank"] == 1 else "Rank 2 of 2",
                      "Scorecard", "Profile", "Products to check", "Alex Mercer", "Message"):
            self.assertIn(label, card)
        self.assertNotIn("mailto:", card)                                  # the rep card has no contact address
        self.assertIn("Messaging — coming soon", card)
        self.assertGreaterEqual(card.count('class="m1-spark"'), 3)         # 13-week lines in the scorecard
        radar = figures(card)
        polar = [t for fig in radar for t in fig["data"] if t["type"] == "scatterpolar"]
        self.assertEqual(len(polar), 2)                                    # the store and one compared store
        rep_view = str(panels.store_card(row, scored, None, row["latest_date"], reps=None, manager=False))
        self.assertNotIn("Field rep", rep_view)                            # reps see no rep directory
        self.assertEqual(panels.peer_position(50, [10, 20, 60, 70], "up"), (2, 4))
        self.assertEqual(panels.peer_position(5, [10, 20, 1, 7], "down"), (3, 4))
        self.assertEqual(panels.peer_position(-2, [5, -1, 10, -8], "zero"), (3, 4))
        self.assertIsNone(panels.peer_position(50, [10, 20, None], "up"))

    def test_map_draws_city_points_with_a_click_and_no_synthetic_index(self):
        points = pd.DataFrame({"city": ["City A"], "state": ["NY"], "latitude": [42.0], "longitude": [-74.0]})
        with patch.object(charts, "city_points", return_value=points):
            markup = str(m1.render_chart("m1_chart9_geo_map", self.manager, self.filters(), "oos", self.colors))
        (fig,) = figures(markup)
        bubbles = [t for t in fig["data"] if t["type"] == "scattergeo"]
        self.assertEqual([t["customdata"][0][0] for t in bubbles], ["City A|NY"])
        self.assertIn("plotly_click", markup)
        self.assertIn("m1_city", markup)
        self.assertIn("1 cities have no reference point", markup)          # City B: listed, not placed
        self.assertIn("not store addresses", markup)
        self.assertNotIn("Execution index", markup)
        listing = str(panels.city_stores(self.manager.store_metrics(self.filters()), "City B|CA"))
        self.assertIn("SYNTHETIC Store 2", listing)

    def test_city_reference_covers_every_store_city(self):
        reference = pd.read_csv(charts.CITY_FILE)
        self.assertFalse(reference.duplicated(["city", "state"]).any())
        self.assertTrue(reference["latitude"].between(18, 72).all())
        self.assertTrue(reference["longitude"].between(-170, -60).all())
        stores = ROOT / "data" / "raw" / "dim_stores.csv"
        if not stores.exists():
            self.skipTest("local store dimension not present")
        cities = pd.read_csv(stores)[["city", "state"]].drop_duplicates()
        missing = cities.merge(reference, on=["city", "state"], how="left", indicator=True)
        self.assertEqual(missing[missing["_merge"] == "left_only"].shape[0], 0)

    def test_no_team_target_or_brief_background_figure_as_a_pass_mark(self):
        text = "".join((ROOT / "modules" / "module_1" / n).read_text() for n in ("charts.py", "panels.py", "ui.py"))
        for phrase in ("Brief: 62%", "62% of stores comply", "85% display target", "Meets team target",
                       "Execution index", "execution_index"):
            self.assertNotIn(phrase, text)

    def test_composite_score_percentiles_directions_ties_and_minimum(self):
        frame = pd.DataFrame({"store_id": ["A", "B", "C", "D"],
                              "msl_pct": [90.0, 80.0, 70.0, None], "oos_pct": [1.0, 2.0, 3.0, 4.0],
                              "gap_pct": [-1.0, 5.0, -10.0, 2.0], "alloc_pct": [100.0, 100.0, 100.0, 100.0],
                              "plano_pct": [95.0, 85.0, 75.0, None]})
        out = composite_scores(frame).set_index("store_id")
        self.assertAlmostEqual(out.loc["A", "p_oos_pct"], 100.0)           # lowest OOS is best
        self.assertAlmostEqual(out.loc["A", "p_gap_pct"], 100.0)           # closest to zero is best
        self.assertAlmostEqual(out.loc["C", "p_gap_pct"], 0.0)
        self.assertTrue((out["p_alloc_pct"] == 50.0).all())                # ties share a percentile
        self.assertEqual(out.loc["D", "measures"], 3)                      # two measures missing, still scored
        self.assertEqual(out.loc["A", "rank"], 1)
        self.assertEqual(list(out.sort_values("rank").index), ["A", "B", "D", "C"])
        sparse = composite_scores(frame.assign(oos_pct=None, gap_pct=None)).set_index("store_id")
        self.assertTrue(pd.isna(sparse.loc["D", "score"]))                 # fewer than three measures: no score
        self.assertTrue(pd.isna(sparse.loc["D", "rank"]))
        noisy = composite_scores(frame.assign(alloc_pct=[99.993, 100.0, 100.007, 100.0]))
        self.assertEqual(noisy["p_alloc_pct"].nunique(), 1)                # float noise does not rank stores

    def test_trend_direction_compares_halves_of_summed_components(self):
        p = [D0 + timedelta(7 * i) for i in range(6)]
        rows = [{"metric": "oos", "period": d, "numerator": n, "denominator": 100, "value": n}
                for d, n in zip(p, [10, 12, 11, 5, 4, 3])]
        trend = pd.DataFrame(rows)
        oos = trend_direction(trend, "oos", "down")
        self.assertEqual(oos["verdict"], "Improving")                      # 11% -> 4%: lower is better
        self.assertAlmostEqual(oos["early"], 11.0)
        self.assertEqual(trend_direction(trend.assign(metric="msl"), "msl", "up")["verdict"], "Worsening")
        flat = trend.assign(numerator=50)
        self.assertEqual(trend_direction(flat, "oos", "down")["verdict"], "Stable")
        self.assertIsNone(trend_direction(trend.head(3), "oos", "down"))   # too short to tell
        gap = trend.assign(metric="gap", numerator=[-10, -9, -8, 2, 1, 0])
        self.assertEqual(trend_direction(gap, "gap", "zero")["verdict"], "Improving")

    def test_ranking_panel_shows_score_with_its_measures_and_rank_moves(self):
        f = self.filters()
        scored = composite_scores(self.manager.store_metrics(f))
        prior = scored.assign(rank=scored["rank"].map({1: 2, 2: 1}))
        top = str(panels.ranking_panel(scored, prior, "top"))
        for text in ("Best performing", "Needs attention", "equal 20% weights", "Shelf alloc.", "▲ 1", "▼ 1"):
            self.assertIn(text, top)
        self.assertNotIn('class="m1-act"', top)                                   # no buttons: the row opens the card
        self.assertEqual(top.count('data-m1-action="card"'), 2)
        self.assertEqual(top.count('tabindex="0"'), 2)                      # rows are reachable by keyboard
        first = re.findall(r'data-store="(S\d)"', top)
        bottom = re.findall(r'data-store="(S\d)"', str(panels.ranking_panel(scored, None, "bottom")))
        self.assertEqual(first, list(reversed(bottom)))
        rep = composite_scores(self.rep.store_metrics(f))
        self.assertEqual(rep["store_id"].tolist(), ["S1"])                 # a rep ranks only their own stores

    def test_store_trend_covers_a_fixed_thirteen_weeks(self):
        f = self.filters(start=D0 + timedelta(21), end=D0 + timedelta(27))
        trend = self.manager.store_trend(f, "S1")
        self.assertEqual(set(trend["metric"]) - {"msl", "oos", "plano", "alloc", "gap"}, set())
        self.assertEqual(trend.attrs["start"], (D0 + timedelta(27) - timedelta(7 * 13 - 1)))
        oos = trend[trend["metric"] == "oos"]
        self.assertEqual(int(oos["denominator"].sum()),
                         self.manager.availability_components(
                             Filters(("Modern Trade",), ("Northeast",), f.skus, True, D0, D0 + timedelta(27)))["known"])

    def test_estimated_view_is_not_labelled_as_a_candidate_in_the_ui(self):
        text = "".join((ROOT / "modules" / "module_1" / n).read_text() for n in ("panels.py", "ui.py", "charts.py"))
        self.assertNotIn("Candidate estimates", text)
        self.assertNotIn("not an approved default", text)
        toggle = str(panels.mode_toggle("estimated", "ready", "", {"version": "v1"}))
        self.assertNotIn("Estimator", toggle)                              # no estimator label in the page head
        self.assertNotIn("andidate", toggle)


if __name__ == "__main__":
    unittest.main()
