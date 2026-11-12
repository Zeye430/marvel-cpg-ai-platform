"""
modules/module_1/ui.py
======================
Layout for Module 1: Business Performance (Commercial Execution Analytics), in the shared
workspace design (shared/ui_kit: Module 2's design).

Hosted in the workspace shell, the app sidebar lists Module 1's pages (``pages_nav_ui``):
Overview (five KPI cards; the selected card's trend with its target and, collapsed, how it is
calculated open below them) and one page per dashboard section:
Geography & flow, Out-of-stock, Shelf execution and Trade terms. Every page has the portfolio
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

PAGES = {"overview": "Overview", "geo": "Geography & flow", "oos": "Out-of-stock", "shelf": "Shelf execution",
         "trade": "Trade terms"}
PAGE_ICONS = {"overview": "home", "geo": "chart", "oos": "pulse", "shelf": "shelf", "trade": "scale"}

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


def _chart_card(output_id, title, read, data, control=None, wide=True):
    """One chart: its title with a "?" (how to read it; what the data is), an optional view control, the chart."""
    tip = kit.help_tip(title, ui.tags.ul(*[ui.tags.li(line) for line in read]),
                       ui.tags.p(data, class_="ws-help-data"), id=f"{output_id}-help")
    return ui.div(
        ui.div(ui.div(ui.tags.h2(title, class_="ws-h"), tip, class_="ws-title-row"),
               control, class_="ws-card-head m1-chart-head"),
        ui.output_ui(output_id),
        class_="ws-card m1-chart-card" + (" m1-wide" if wide else ""))


def _radio(input_id, choices, selected):
    return ui.div(ui.input_radio_buttons(input_id, None, choices, selected=selected, inline=True),
                  class_="m1-toggle", role="group")


def _pages():
    overview = ui.nav_panel(
        "overview",
        ui.output_ui("m1_kpis"),
        ui.output_ui("m1_kpi_detail"))
    geo = ui.nav_panel(
        "geo",
        _chart_card("m1_chart9_geo_map", "Regional execution vs revenue performance map",
                    ["Each state is shaded by the measure chosen on the right: darker means higher.",
                     "Hover a state for its sales, planogram compliance, in-stock rate and store count.",
                     "Compare the two views to find states that sell well but execute poorly, or the reverse.",
                     "Blank states have no stores in your scope."],
                    "Invoiced net sales (no currency in the source), audited planogram compliance and observed "
                    "in-stock rate. The execution index is an equal-weight average of the last two, for display; it "
                    "is not a shared KPI.",
                    _radio("m1_c9_metric", {"health_score": "Execution index", "net_sales": "Invoiced net sales"},
                           "health_score")),
        _chart_card("m1_chart10_sankey", "Sell-in vs sell-out flow",
                    ["Read left to right: invoiced units flow from each category to its channels.",
                     "Each channel then splits into what consumers bought (sell-out) and what was invoiced on top "
                     "of that (the gap).",
                     "A wide gap band means stock is being invoiced faster than it sells."],
                    "Complete biweekly periods where both flows are known. Invoiced units are not received stock, so "
                    "the gap is a flow difference, not measured inventory. Where sell-out exceeds sell-in, the "
                    "flow is shown up to sell-in."))
    oos = ui.nav_panel(
        "oos",
        _chart_card("m1_chart1_heatmap", "Stock level heatmap (mandatory MSL SKUs)",
                    ["Rows are stores, columns are must-stock products; each cell is the units on hand on the "
                     "latest day in your date range.",
                     "Red is zero stock, amber is a low buffer, blue is well stocked.",
                     "Grey cells marked Unknown were not reported that day: they are not zero stock.",
                     "Managers see the 25 stores with the most zero-stock products."],
                    "Reported daily store inventory for products on the must-stock list."),
        _chart_card("m1_chart2_lost_sales", "Lost sales estimate trend (heuristic)",
                    ["Each band is the estimated value of sales lost to stockouts per week, by region or category.",
                     "A rising band means stockouts are costing more; compare bands to see where.",
                     "It is a rough estimate to prioritise, not measured lost revenue."],
                    "Days observed at zero stock × the product's usual daily sales in that store × list price "
                    "(no currency in the source). Days without a usual-sales baseline are left out, not estimated.",
                    _radio("m1_c2_group", {"region": "By region", "category": "By category"}, "region")),
        _chart_card("m1_chart3_duration", "How long stockouts last",
                    ["Each box shows how many days in a row products stayed out of stock, by channel or tier.",
                     "The line in the box is the typical length; the box covers the middle half of stockouts.",
                     "Dots are unusually long stockouts worth a closer look."],
                    "Consecutive days observed at zero stock per store and product. A missing or Unknown day ends a "
                    "stockout, so gaps in reporting are never counted as continuous outage.",
                    _radio("m1_c3_group", {"channel": "By channel", "segment_tier": "By tier"}, "channel")))
    shelf = ui.nav_panel(
        "shelf",
        _chart_card("m1_chart4_compliance", "Planogram and MSL availability by brand",
                    ["Two bars per brand: planogram compliance and must-stock availability.",
                     "The dashed line is the team's 85% display target; the dotted line is the brief's 62% "
                     "starting point.",
                     "Brands left of the dashed line need attention. A missing bar means no data, not zero."],
                    "Audited planogram rows from store visits, and observed daily availability of must-stock "
                    "products. The 85% line is a team display target, not a client-approved threshold."),
        _chart_card("m1_chart5_bubble", "Shelf allocation vs invoiced sales",
                    ["Each bubble is a store: further right means more shelf allocated to the selected products, "
                     "higher means more sales.",
                     "Bigger bubbles are higher-tier stores; colour is the channel.",
                     "Stores high and to the left sell well on little shelf. It shows an association, not cause."],
                    "Shelf allocation per audited visit, among the client's own products in a category (not share "
                    "versus competitors), and invoiced net sales. No shelf-share target applies."),
        _chart_card("m1_chart6_radar", "Execution scorecard",
                    ["Each shape is a region, tier or channel scored on five execution measures (0–100%).",
                     "A bigger, rounder shape is stronger all-round; a dent shows the weak measure.",
                     "A gap in a shape means no data for that measure."],
                    "Planogram compliance and secondary displays from audits, shelf allocation, and observed "
                    "must-stock and overall in-stock rates.",
                    _radio("m1_c6_group", {"region": "By region", "segment_tier": "By tier", "channel": "By channel"},
                           "region")))
    trade = ui.nav_panel(
        "trade",
        _chart_card("m1_chart7_discount", "Discount vs margin",
                    ["Each dot is a store and category: further right is a deeper trade discount, higher is a "
                     "better margin.",
                     "Red dots are above the standard discount plus the maximum additional discount, the range "
                     "that needs manager review; the dashed line marks that total when one applies.",
                     "Dots far right and low give away the most margin."],
                    "Reported invoices, discount weighted by gross value (it already includes the standard "
                    "discount), margin at static product cost. The reference is standard + maximum additional from "
                    "the client's guardrail table under the Owner's proposed rules, which the client has not "
                    "confirmed: it is not an approved rule."),
        _chart_card("m1_chart8_tradeoff", "Trade-off commitment evidence",
                    ["Bars group stores by the execution commitment their terms require.",
                     "Each pair shows how often audits found the planogram met and a secondary display in place.",
                     "Low bars suggest commitments that are not being delivered."],
                    "Audit evidence only: no approved rule turns these shares into Met or Breach, so no commitment "
                    "is shown as verified."))
    return ui.navset_hidden(overview, geo, oos, shelf, trade, id="m1_pages", selected="overview")


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
