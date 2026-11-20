"""
modules/module_1/ui.py
======================
Layout for Module 1: Business Performance (Commercial Execution Analytics), in the shared
workspace design (shared/ui_kit: Module 2's design).

Hosted in the workspace shell, the app sidebar lists Module 1's pages (``pages_nav_ui``):
Overview (five KPI cards; the selected card's trend and, collapsed, what it measures and how it is
calculated open below them; then the store exception list) and one page per dashboard section:
Geography & flow (state/city map and sell-in vs sell-out), Store cards, Out-of-stock, Shelf execution
and Trade terms. The page head carries the Reported / Estimated data view switch. Every page has the portfolio
filters (date range, channel, region, MSL products, product SKU) as one row of filter pills (the
workspace's ``ws-fpill``): each pill shows its current value and opens its options on click. Every
chart has a "?" next to its title that explains how to read it and what the data is; there is no
small print under the charts. While the first load runs, the page shows a welcome greeting instead. Every number and chart is rendered by the server from the signed-in user's
scoped shared publication; nothing here is fixed.

Without the shared runtime (the legacy root ``app.py``) the module shows where it runs instead
of reading the legacy CSV loader.
"""

from pathlib import Path
from typing import Any, Dict

from htmltools import HTMLDependency
from shiny import ui

from shared import ui_kit as kit

_CSS = (Path(__file__).with_name("module1.css")).read_text()
ASSETS = Path(__file__).resolve().parent / "assets"
GEO_VERSION = "1.0"
# Served by the app itself so the state map never fetches map data from a CDN.
TOPOJSON_URL = f"lib/m1-geo-{GEO_VERSION}/"

PAGES = {"overview": "Overview", "geo": "Geography & flow", "stores": "Store cards", "oos": "Out-of-stock",
         "shelf": "Shelf execution", "trade": "Trade terms"}
PAGE_ICONS = {"overview": "home", "geo": "chart", "stores": "list", "oos": "pulse", "shelf": "shelf",
              "trade": "scale"}

_SCRIPT = """
window.jQuery(document).on('shiny:connected', function () {
  if (window.__m1Handlers) { return; }
  window.__m1Handlers = true;
  // Sign-out / identity switch: drop previously rendered scoped content immediately.
  Shiny.addCustomMessageHandler('m1-clear', function (message) {
    document.querySelectorAll('#m1-root .shiny-html-output').forEach(function (e) { e.innerHTML = ''; });
  });
  Shiny.addCustomMessageHandler('m1-scroll-top', function (message) {
    setTimeout(function () { window.scrollTo({top: 0, behavior: 'instant'}); }, 0);
  });
});
function m1Go(page) { Shiny.setInputValue('m1_go', page, {priority: 'event'}); }
// Store buttons in tables and cards: open the store card here, or the store's detail in Stockout Risk (which
// checks the account's access to that store itself). Only the store id is passed.
document.addEventListener('click', function (event) {
  var button = event.target.closest ? event.target.closest('#m1-root [data-m1-store]') : null;
  if (!button) { return; }
  var store = button.getAttribute('data-m1-store');
  if (button.tagName === 'TR' && event.target.closest('a, button, details')) { return; }
  if (button.getAttribute('data-m1-action') === 'risk') {
    Shiny.setInputValue('m2_open_store', store, {priority: 'event'});
    Shiny.setInputValue('m1_open_risk', store, {priority: 'event'});
  } else {
    Shiny.setInputValue('m1_open_store', store, {priority: 'event'});
  }
});
// The browser's local time, for the welcome greeting (the server's clock may be in another time zone).
window.jQuery(document).on('shiny:connected', function () {
  var now = new Date();
  Shiny.setInputValue('m1_client_clock', {hour: now.getHours(),
    day: now.toLocaleDateString('en-US', {weekday: 'long', month: 'long', day: 'numeric'})});
});
// SKU pill: type to filter the list; category headings between the groups.
function m1SkuSearch(box) {
  var term = box.value.trim().toLowerCase();
  document.querySelectorAll('#m1_sku .checkbox').forEach(function (row) {
    row.hidden = term !== '' && row.textContent.toLowerCase().indexOf(term) === -1;
  });
  m1SkuHeadings();
}
function m1SkuHeadings() {
  var list = document.querySelector('#m1_sku .shiny-options-group');
  if (!list) { return; }
  list.querySelectorAll('.m1-sku-group').forEach(function (h) { h.remove(); });
  var last = null;
  list.querySelectorAll('.checkbox').forEach(function (row) {
    var cat = row.querySelector('[data-category]');
    cat = cat ? cat.getAttribute('data-category') : '';
    if (!row.hidden && cat !== last) {
      var h = document.createElement('div');
      h.className = 'm1-sku-group'; h.textContent = cat; h.setAttribute('aria-hidden', 'true');
      row.parentNode.insertBefore(h, row);
      last = cat;
    }
  });
}
window.jQuery(document).on('shiny:updateinput', function (event) {
  if (event.target && event.target.id === 'm1_sku') { setTimeout(m1SkuHeadings, 0); }
});
// A ranking row is a link: Enter or Space opens its store card too.
document.addEventListener('keydown', function (event) {
  if (event.key !== 'Enter' && event.key !== ' ') { return; }
  var row = event.target.closest ? event.target.closest('#m1-root tr[data-m1-store]') : null;
  if (!row || row !== event.target) { return; }
  event.preventDefault();
  Shiny.setInputValue('m1_open_store', row.getAttribute('data-m1-store'), {priority: 'event'});
});
"""


def geo_dependency():
    """Plotly's US states topology (usa_110m.json), bundled; see assets/geo/README.md."""
    return HTMLDependency("m1-geo", GEO_VERSION, source={"subdir": str(ASSETS / "geo")}, all_files=True)


def _pill(key, title, *body, icon=None):
    """One filter pill: the face shows the current value (server-rendered); the options open below it."""
    return kit.filter_pill(f"m1-pill-{key}", title, ui.output_ui(f"m1_pill_{key}", inline=True), *body,
                           icon_name=icon, class_="m1-pill", pop_class="m1-pop")


def _filters():
    """The portfolio filters as one row of pills, kept in view while the page scrolls."""
    return ui.div(
        _pill("dates", "Date range",
              ui.output_ui("m1_presets"),
              ui.input_date_range("m1_dates", "Custom range (whole weeks, Monday to Sunday)", start="2026-06-30",
                                  end="2026-06-30"),
              icon="calendar"),
        _pill("channel", "Channel", ui.input_checkbox_group("m1_channel", None, []),
              ui.tags.p("No box ticked means all channels.", class_="ws-muted m1-pop-note")),
        _pill("region", "Region", ui.input_checkbox_group("m1_region", None, []),
              ui.tags.p("No box ticked means all regions.", class_="ws-muted m1-pop-note")),
        _pill("msl", "MSL products",
              ui.input_radio_buttons("m1_msl", None, {"all": "All products", "msl": "MSL products only",
                                                      "non": "Non-MSL products only"}, selected="all"),
              ui.tags.p("MSL = must-stock list. The SKU list then shows only these products.",
                        class_="ws-muted m1-pop-note")),
        _pill("sku", "Product SKU",
              ui.tags.input(type="search", class_="form-control m1-sku-search", placeholder="Search products",
                            oninput="m1SkuSearch(this)", **{"aria-label": "Search products"}),
              ui.div(ui.output_ui("m1_sku_count", inline=True),
                     ui.div(ui.input_action_link("m1_sku_all", "Select all"), ui.span("·", **{"aria-hidden": "true"}),
                            ui.input_action_link("m1_sku_none", "Clear"), class_="m1-sku-actions"),
                     class_="m1-sku-head"),
              ui.div(ui.input_checkbox_group("m1_sku", None, []), class_="m1-sku-list")),
        ui.output_ui("m1_filter_summary", class_="m1-fsum"),
        class_="m1-fbar", id="m1_filter_box", role="search", **{"aria-label": "Filters"})


def _chart_card(output_id, title, read, data, control=None, wide=True, before=None, extra=None):
    """One chart: its title with a "?" (how to read it; what the data is), an optional view control, the chart
    (``before`` / ``extra``: markup above / below it, such as summary figures or a table)."""
    tip = kit.help_tip(title, ui.tags.ul(*[ui.tags.li(line) for line in read]),
                       ui.tags.p(data, class_="ws-help-data"), id=f"{output_id}-help")
    return ui.div(
        ui.div(ui.div(ui.tags.h2(title, class_="ws-h"), tip, class_="ws-title-row"),
               control, class_="ws-card-head m1-chart-head"),
        before,
        ui.output_ui(output_id),
        extra,
        class_="ws-card m1-chart-card" + (" m1-wide" if wide else ""))


def _section_card(card_id, title, read, data, body):
    """A card of tables (not a chart), with the same "?" help as the charts."""
    tip = kit.help_tip(title, ui.tags.ul(*[ui.tags.li(line) for line in read]),
                       ui.tags.p(data, class_="ws-help-data"), id=f"{card_id}-help")
    return ui.div(ui.div(ui.div(ui.tags.h2(title, class_="ws-h"), tip, class_="ws-title-row"),
                         class_="ws-card-head m1-chart-head"),
                  body, class_="ws-card m1-section-card m1-wide", id=card_id)


def _radio(input_id, choices, selected):
    return ui.div(ui.input_radio_buttons(input_id, None, choices, selected=selected, inline=True),
                  class_="m1-toggle", role="group")


def _pages():
    overview = ui.nav_panel(
        "overview",
        ui.output_ui("m1_kpis"),
        ui.output_ui("m1_kpi_detail"),
        _section_card("m1-rank-card", "Store ranking",
                      ["Each store gets a composite score from the five KPIs: for every KPI, its percentile among the "
                       "stores in view (100 = best in view; for OOS lower is better, for the gap closer to 0 is "
                       "better), averaged with equal 20% weights set by the team.",
                       "“Best performing” and “Needs attention” show the ten highest and lowest scores; “Move” is the "
                       "change in rank since the previous period of equal length. Click a row to open its store card.",
                       "The five values sit next to the score, so you can see what drives it. Open the store card "
                       "for the trend and a radar comparison.",
                       "A store needs at least three of the five measures for a score. With every product selected, "
                       "shelf allocation is about 100% for all stores and does not change the ranking."],
                      "The score ranks stores against each other within your scope and filters; it is a team "
                      "weighting, not a client-approved target.",
                      ui.output_ui("m1_ranking")))

    geo = ui.nav_panel(
        "geo",
        _chart_card("m1_chart9_geo_map", "Regional and store map",
                    ["Each state is shaded by the measure chosen on the right; the colour bar shows the values.",
                     "Bubbles are cities with stores in your scope: bigger means more stores; red means at least one "
                     "store had a must-stock product at zero on the latest day.",
                     "Hover a state or bubble for its figures; click a bubble (or choose a city below) to list its "
                     "stores and open their store cards.",
                     "The state ranking under the map gives the exact values."],
                    "Bubbles sit on public U.S. Census city points: city-level approximations, not store addresses. "
                    "OOS rate and MSL compliance follow the page's data view; invoiced net sales are reported "
                    "invoices (no currency in the source).",
                    _radio("m1_c9_metric", {"oos": "OOS rate", "msl": "MSL compliance", "sales": "Invoiced net sales"},
                           "oos"),
                    extra=ui.div(ui.div(ui.input_select("m1_city_pick", "Stores of a city", {"": "Choose a city"}),
                                        class_="m1-city-pick"),
                                 ui.output_ui("m1_city_stores"), class_="m1-city")),
        _chart_card("m1_chart10_flow", "Sell-in vs sell-out",
                    ["Top: invoiced units (sell-in) and consumer units (sell-out) per complete biweekly period.",
                     "Bottom: the gap, sell-in minus sell-out, above or below zero. A missing point is a period "
                     "without comparable flows, never zero.",
                     "The table shows where the gap is and how many periods in a row it has kept the same sign.",
                     "A gap that stays positive is worth checking for channel stock building up or invoice timing; "
                     "one that stays negative for stock running down and the replenishment plan."],
                    "Complete biweekly periods where both flows are known. Sell-in is invoiced, not received, stock, "
                    "so the gap is a flow difference, not measured inventory. Invoice-extract completeness is "
                    "unconfirmed.",
                    before=ui.output_ui("m1_flow_summary"), extra=ui.output_ui("m1_flow_table")))
    stores = ui.nav_panel(
        "stores",
        ui.div(
            ui.div(ui.div(ui.tags.h2("Store card", class_="ws-h"),
                          kit.help_tip("Store card", ui.tags.ul(
                              ui.tags.li("The composite score and the store's rank among the stores in view; the "
                                         "move is since the previous period of equal length."),
                              ui.tags.li("Scorecard: each measure for the filtered dates, its line over the last 13 "
                                         "weeks, the change vs the previous period and whether it is improving or "
                                         "worsening (later half of the 13 weeks vs earlier half; under 1 point is "
                                         "stable)."),
                              ui.tags.li("“Better than X of N similar stores” compares with stores of the same "
                                         "channel and tier in your scope."),
                              ui.tags.li("The radar shows each measure as a percentile among the stores in view; add "
                                         "up to four stores in “Compare with” to overlay them."),
                              ui.tags.li("Managers see the store's field rep; click the name for the rep's card.")),
                              ui.tags.p("The score is a team weighting (equal 20% per KPI), not a client target.",
                                        class_="ws-help-data"),
                              id="m1-store-help"),
                          class_="ws-title-row"),
                   ui.div(ui.input_select("m1_store", "Store", {}),
                          ui.input_selectize("m1_compare", "Compare with (radar)", {}, multiple=True,
                                             options={"maxItems": 4, "placeholder": "Add up to 4 stores"}),
                          class_="m1-store-pick"),
                   class_="ws-card-head m1-chart-head"),
            class_="ws-card m1-chart-card m1-wide"),
        ui.output_ui("m1_store_card"))
    oos = ui.nav_panel(
        "oos",
        _chart_card("m1_chart1_heatmap", "Stock status by store and product",
                    ["Rows are stores, columns are products; each cell is the stock status on the latest day in your "
                     "date range, with the units on hand.",
                     "Red is zero stock, blue is in stock, grey is Unknown (not reported that day: not zero).",
                     "In the Estimated data view, lighter cells marked ~ carry an estimate.",
                     "Managers see the 25 stores with the most zero-stock products; the title says how many of all."],
                    "Daily store inventory for the must-stock products when any is selected, otherwise for the "
                    "selected products. Colour is the status, not a judgement of how much stock is enough."),
        _chart_card("m1_chart2_lost_sales", "Lost sales estimate trend (heuristic)",
                    ["Each band is the estimated value of sales lost to stockouts per week, by region or category.",
                     "A rising band means stockouts are costing more; compare bands to see where.",
                     "It is a rough estimate to prioritise, not measured lost revenue."],
                    "Days at zero stock × the product's usual daily sales in that store × list price (no currency in "
                    "the source). Days without a usual-sales baseline are left out, not estimated; promotions, "
                    "weekdays and substitution are not modelled.",
                    _radio("m1_c2_group", {"region": "By region", "category": "By category"}, "region")),
        _chart_card("m1_chart3_duration", "How long stockouts last",
                    ["Each box shows how many days in a row products stayed out of stock, by channel or tier.",
                     "The line in the box is the typical length; the box covers the middle half of stockouts.",
                     "Dots are unusually long stockouts worth a closer look."],
                    "Consecutive days at zero stock per store and product. A missing or Unknown day ends a stockout, "
                    "and the date range cuts episodes at its edges, so lengths are observed, not complete outages.",
                    _radio("m1_c3_group", {"channel": "By channel", "segment_tier": "By tier"}, "channel")))
    shelf = ui.nav_panel(
        "shelf",
        _chart_card("m1_chart4_compliance", "Planogram and MSL availability by brand",
                    ["Two bars per brand: planogram compliance of audited SKU rows, and must-stock availability over "
                     "SKU-days with known stock.",
                     "The two bars have different denominators, so they are compared within a measure, not with "
                     "each other. Hover a bar for how many rows it rests on.",
                     "A missing bar means no data, not zero."],
                    "Audited planogram rows from store visits, and daily availability of must-stock products. No "
                    "client-approved target exists, so no pass line is drawn."))
    trade = ui.nav_panel(
        "trade",
        _chart_card("m1_chart7_discount", "Discount vs margin",
                    ["Each dot is a store and category: further right is a deeper trade discount, higher is a "
                     "better margin.",
                     "Red dots are above the standard discount plus the maximum additional discount, the range "
                     "that needs manager review; the dashed line marks that total when one applies.",
                     "Dots far right and low give away the most margin. To test a deal, use the Negotiation Simulator."],
                    "Reported invoices, discount weighted by gross value (it already includes the standard "
                    "discount), margin at static product cost. The reference is standard + maximum additional from "
                    "the client's guardrail table under the Owner's proposed rules, which the client has not "
                    "confirmed: it is not an approved rule."),
        _chart_card("m1_chart8_tradeoff", "Trade-off commitment evidence",
                    ["Bars group stores by the execution commitment their terms require.",
                     "Each pair shows how often audits found the planogram met and a secondary display in place.",
                     "Low bars are a lead to check with the store, not proof that a commitment was broken."],
                    "Audit evidence only: no approved rule turns these shares into Met or Breach, so no commitment "
                    "is shown as verified."))
    return ui.navset_hidden(overview, geo, stores, oos, shelf, trade, id="m1_pages", selected="overview")


def pages_nav_ui():
    """Module 1's page navigation for the host's sidebar (rendered by the Module 1 server)."""
    return ui.output_ui("m1_nav")


def _not_hosted():
    return ui.div(
        kit.kit_dependency(),
        kit.card(ui.tags.h2("Business Performance runs in the integrated workspace", class_="ws-h"),
                 ui.tags.p("This dashboard reads the shared, account-scoped business publication. Start the "
                           "integrated app (shiny run integrated_app.py; see docs/integrated-app.md). It no longer "
                           "reads the legacy CSV loader.", class_="ws-sub"),
                 id="m1-not-hosted"),
        class_="ws m1", id="m1-root")


def module_1_ui(dataset: Dict[str, Any] = None) -> ui.Tag:
    """Module 1 page. ``dataset`` is the host context; data never comes from it."""
    context = dataset if isinstance(dataset, dict) else {}
    if context.get("runtime") is None:
        return _not_hosted()
    actions = kit.topbar_actions(ui.output_ui("m1_user_block"),
                                 copilot_id="m1_open_copilot" if context.get("copilot_nav") else None)
    topbar = ui.div(ui.div(ui.output_ui("m1_crumb"), class_="ws-crumb-wrap"),
                    ui.div(*actions, class_="ws-topbar-actions"), class_="ws-topbar")
    return ui.div(
        kit.kit_dependency(),
        kit.plotly_dependency(),
        geo_dependency(),
        ui.tags.style(_CSS),
        ui.tags.script(_SCRIPT),
        topbar,
        ui.div(
            ui.output_ui("m1_page_head"),
            ui.output_ui("m1_status"),
            ui.div(ui.output_text("m1_ready"), class_="ws-hidden", id="m1-ready-flag"),
            ui.panel_conditional("output.m1_ready === 'yes'", _filters(), _pages()),
            class_="ws-content"),
        class_="ws ws-controls m1", id="m1-root")
