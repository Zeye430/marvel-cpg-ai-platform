"""
modules/module_1/ui.py
======================
Layout for Module 1: Business Performance (Commercial Execution Analytics), in the shared
workspace design (shared/ui_kit: Module 2's design).

Hosted in the workspace shell, the app sidebar lists Module 1's pages (``pages_nav_ui``):
Overview (five KPI cards; the selected card's trend with its target, notes and calculation open
below them) and one page per dashboard section:
Geography & flow, Out-of-stock, Shelf execution and Trade terms. Every page has the portfolio
filters (date range, channel, region, MSL products, product SKU) as one row of filter pills: each pill shows its
current value and opens its options on click. While the first load runs, the page shows a welcome
greeting instead. Every number and chart is rendered by the server from the signed-in user's
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
// Filter pills: one open at a time; a click outside or Escape closes it.
document.addEventListener('toggle', function (event) {
  var pill = event.target;
  if (!pill.classList || !pill.classList.contains('m1-pill') || !pill.open) { return; }
  document.querySelectorAll('#m1-root details.m1-pill[open]').forEach(function (other) {
    if (other !== pill) { other.removeAttribute('open'); }
  });
  var search = pill.querySelector('.m1-sku-search');
  if (search) { search.focus(); }
}, true);
document.addEventListener('click', function (event) {
  document.querySelectorAll('#m1-root details.m1-pill[open]').forEach(function (pill) {
    if (!pill.contains(event.target)) { pill.removeAttribute('open'); }
  });
});
document.addEventListener('keydown', function (event) {
  if (event.key !== 'Escape') { return; }
  document.querySelectorAll('#m1-root details.m1-pill[open]').forEach(function (pill) {
    pill.removeAttribute('open'); pill.querySelector('summary').focus();
  });
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
    """One filter pill: the summary shows the current value (server-rendered); the options open below it."""
    return ui.tags.details(
        ui.tags.summary(kit.icon(icon, 15) if icon else None, ui.output_ui(f"m1_pill_{key}", inline=True),
                        ui.span("▾", class_="m1-caret", **{"aria-hidden": "true"}),
                        class_="m1-pill-face", **{"aria-label": f"{title} filter"}),
        ui.div(ui.tags.h3(title, class_="m1-pop-title"), *body, class_="m1-pop", role="group",
               **{"aria-label": title}),
        class_="m1-pill", id=f"m1-pill-{key}")


def _filters():
    """The portfolio filters as one row of pills, kept in view while the page scrolls."""
    return ui.div(
        _pill("dates", "Date range",
              ui.output_ui("m1_presets"),
              ui.input_date_range("m1_dates", "Custom range (snaps to full weeks)", start="2026-06-30",
                                  end="2026-06-30"),
              icon="calendar"),
        _pill("channel", "Channel", ui.input_checkbox_group("m1_channel", None, [])),
        _pill("region", "Region", ui.input_checkbox_group("m1_region", None, [])),
        _pill("msl", "MSL products",
              ui.input_radio_buttons("m1_msl", None, {"all": "All products", "msl": "MSL products only",
                                                      "non": "Non-MSL products only"}, selected="all"),
              ui.tags.p("MSL = must-stock list (is_msl). Narrows the selected SKUs.", class_="ws-muted m1-pop-note")),
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


def _chart_card(output_id, title, logic, control=None, wide=True):
    """One chart: title, the data and logic behind it, an optional view control, the chart."""
    return ui.div(
        ui.div(ui.div(ui.tags.h2(title, class_="ws-h"), ui.tags.p(logic, class_="ws-muted m1-logic")),
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
                    "State-level invoiced net sales (sell_in_events), audited planogram compliance (execution_visits) "
                    "and observed in-stock rate (store_sku_daily). The execution index is an equal-weight display "
                    "index, not a shared KPI.",
                    _radio("m1_c9_metric", {"health_score": "Execution index", "net_sales": "Invoiced net sales"},
                           "health_score")),
        _chart_card("m1_chart10_sankey", "Sell-in vs sell-out flow",
                    "Comparable biweekly flows from commercial_period: invoiced units from category to channel, then "
                    "observed consumer sell-out versus invoiced units above sell-out. Invoice units are not received "
                    "stock, so the excess is a flow gap, not a measured inventory buffer."))
    oos = ui.nav_panel(
        "oos",
        _chart_card("m1_chart1_heatmap", "Stock level and OOS hazard heatmap (mandatory MSL SKUs)",
                    "Reported store_sku_daily inventory for mandatory MSL SKUs on the latest date in range. Zero is "
                    "red, low buffers amber; a store-SKU without a report that day stays Unknown and is never shown "
                    "as zero."),
        _chart_card("m1_chart2_lost_sales", "Lost sales heuristic trend",
                    "Heuristic only: observed zero-stock days × the store-SKU's observed in-stock average daily units "
                    "× static list price. This is not the shared missing-day estimator and not measured lost revenue.",
                    _radio("m1_c2_group", {"region": "By region", "category": "By category"}, "region")),
        _chart_card("m1_chart3_duration", "OOS duration distribution",
                    "Consecutive observed zero-stock calendar days per store-SKU (store_sku_daily). Any missing or "
                    "Unknown day ends an episode, so gaps are never counted as continuous outage.",
                    _radio("m1_c3_group", {"channel": "By channel", "segment_tier": "By tier"}, "channel")))
    shelf = ui.nav_panel(
        "shelf",
        _chart_card("m1_chart4_compliance", "Planogram and MSL availability by brand",
                    "Planogram compliance from audited SKU rows (execution_visits) and observed MSL SKU-day "
                    "availability (store_sku_daily) by category and brand. Missing bars are N/A, not zero. Reference "
                    "lines: 85% team display target and the brief's 62% baseline."),
        _chart_card("m1_chart5_bubble", "Audited shelf allocation vs invoiced sales",
                    "Store-level audited SKU shelf allocation per visit (execution_visits) against invoiced net sales "
                    "(sell_in_events), sized by segment tier. Allocation is within supplied category SKUs, not "
                    "competitor shelf share, so no target line is drawn."),
        _chart_card("m1_chart6_radar", "Multi-dimensional execution scorecard",
                    "Five shared components by region, tier or channel: planogram and secondary-display shares of "
                    "audited SKU rows, audited SKU allocation, and observed MSL and overall in-stock rates. Gaps are "
                    "N/A, not zero.",
                    _radio("m1_c6_group", {"region": "By region", "segment_tier": "By tier", "channel": "By channel"},
                           "region")))
    trade = ui.nav_panel(
        "trade",
        _chart_card("m1_chart7_discount", "Effective discount vs margin realization with guardrails",
                    "Reported invoices (sell_in_events) by store and category: gross-value-weighted trade discount "
                    "versus margin at static product cost, compared with the source discount reference from "
                    "negotiation_context. Source rules are Requires review, not approvals."),
        _chart_card("m1_chart8_tradeoff", "Trade-off commitment evidence",
                    "Audit evidence (planogram and secondary-display shares of audited SKU rows) grouped by the source "
                    "required trade-off text. No approved rule maps evidence to Met/Breach, so no commitment is shown "
                    "as verified."))
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
