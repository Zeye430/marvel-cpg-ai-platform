"""
modules/module_2/ui.py
======================
Module 2 layout: a modular workspace inside the Module 2 area (it does not replace the app's
own navigation). A collapsible Module 2 sidebar switches Module 2 pages (Overview, Store
ranking, Analytics; or Model health / Monitoring in the separate ML Operations demo entry) and
holds Help, Settings, the user card and Sign out. The top bar has store search, placeholder
buttons (Copilot, messages, notifications, calendar: visible, no behaviour) and the user.
Every number is rendered by the server from the published serving data; nothing here is fixed.
"""

from pathlib import Path
from typing import Any, Dict

from htmltools import HTML, HTMLDependency
from shiny import ui

from .service import VIEW_LABELS

_CSS = (Path(__file__).with_name("module2.css")).read_text()
_ASSETS = Path(__file__).with_name("assets")

_SORT_NAMES = {"score": "Priority score", "sales_at_risk": "Sales at risk", "shelf_risk": "Shelf risk",
               "msl_risk": "MSL risk", "urgency": "Urgency"}
# One control holds key and direction together, so a change is a single atomic input update.
SORT_CHOICES = {**{f"{k}:{d}": f"{name} — {'high to low' if d == 'desc' else 'low to high'}"
                   for k, name in _SORT_NAMES.items() for d in ("desc", "asc")},
                "rank:asc": "Overall rank — 1 first", "rank:desc": "Overall rank — last first"}
ROW_CHOICES = {"10": "10 rows", "50": "50 rows", "100": "100 rows"}
_SKU_SORT_NAMES = {"sales": "Sales at risk", "risk": "Stockout risk", "oos_days": "OOS days · 28",
                   "units_short": "Units short", "inventory": "Inventory", "status": "Status now", "sku": "SKU"}
SKU_SORT_CHOICES = {f"{k}:{d}": f"{name} — {'A to Z' if k == 'sku' and d == 'asc' else 'Z to A' if k == 'sku' else ('high to low' if d == 'desc' else 'low to high')}"
                    for k, name in _SKU_SORT_NAMES.items() for d in ("desc", "asc")}

# Simple line icons (drawn for this module; 24x24, stroke = currentColor).
_ICON_PATHS = {
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "chart": '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-8"/>',
    "pulse": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    "gauge": '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/>'
            '<circle cx="12" cy="17.5" r=".6"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3'
                'l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "logout": '<path d="M14 4h5v16h-5"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "spark": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8'
             'L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "bell": '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "back": '<path d="M15 6l-6 6 6 6"/>',
}


def icon(name, size=18):
    return HTML(f'<svg class="m2-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
                f'aria-hidden="true" focusable="false">{_ICON_PATHS[name]}</svg>')


# Original placeholder mark for Marvel CPG (not a third-party logo): a red tile with a white
# shelf-and-peak monogram. To be replaced by the client's brand assets.
LOGO = HTML(
    '<svg class="m2-logo-mark" width="32" height="32" viewBox="0 0 32 32" role="img" '
    'aria-label="Marvel CPG placeholder mark">'
    '<rect width="32" height="32" rx="8" fill="#b41824"/>'
    '<path d="M8 21V11.5l4.6 5.2L16 12l3.4 4.7 4.6-5.2V21" fill="none" stroke="#fff" stroke-width="2.6" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M8 24.5h16" stroke="#fff" stroke-width="1.8" stroke-linecap="round" opacity=".7"/></svg>')


def plotly_dependency():
    """Serve plotly.min.js from the installed plotly package (no CDN)."""
    import plotly
    source = Path(plotly.__file__).parent / "package_data"
    return HTMLDependency("m2-plotly", plotly.__version__, source={"subdir": str(source)},
                          script={"src": "plotly.min.js"})


def font_dependency():
    """Inter 4.1 (SIL OFL 1.1), bundled locally; registered under a Module 2-only family name."""
    return HTMLDependency("m2-inter", "4.1", source={"subdir": str(_ASSETS / "fonts")},
                          stylesheet={"href": "inter.css"})


_SCRIPT = """
// Sign-out / identity switch: drop previously rendered scoped content immediately.
window.jQuery(document).on('shiny:connected', function () {
  if (window.__m2Handlers) { return; }
  window.__m2Handlers = true;
  Shiny.addCustomMessageHandler('m2-clear', function (message) {
    document.querySelectorAll('#m2-root .shiny-html-output').forEach(function (e) { e.innerHTML = ''; });
  });
  // A store detail opens at its top; back restores the previous position once the page is visible.
  Shiny.addCustomMessageHandler('m2-scroll-top', function (message) {
    setTimeout(function () { window.scrollTo({top: 0, behavior: 'instant'}); }, 0);
  });
  Shiny.addCustomMessageHandler('m2-scroll-to', function (message) {
    var target = document.getElementById(message.id);
    if (target) { setTimeout(function () { target.scrollIntoView({block: 'start', behavior: 'instant'}); }, 60); }
  });
  Shiny.addCustomMessageHandler('m2-restore-scroll', function (message) {
    var tries = 0, target = message && message.page;
    (function restore() {
      var pane = document.querySelector('#m2-root .tab-pane[data-value="' + target + '"]');
      if ((!pane || !pane.classList.contains('active')) && tries++ < 40) { setTimeout(restore, 50); return; }
      requestAnimationFrame(function () { window.scrollTo({top: window.__m2Scroll || 0, behavior: 'instant'}); });
    })();
  });
});
function m2Go(page) { Shiny.setInputValue('m2_go', page, {priority: 'event'}); }
function m2Sort(key) { Shiny.setInputValue('m2_sort_click', key, {priority: 'event'}); }
function m2SkuSort(key) { Shiny.setInputValue('m2_sku_sort_click', key, {priority: 'event'}); }
function m2OpenSku(sku) { Shiny.setInputValue('m2_open_sku', sku, {priority: 'event'}); }
function m2OpenStore(el) {
  window.__m2Scroll = window.scrollY;
  Shiny.setInputValue('m2_open_store', el.dataset.store, {priority: 'event'});
}
// Store links are the keyboard route; a pointer click anywhere on the row is a shortcut,
// except while the user is selecting text.
document.addEventListener('click', function (event) {
  var link = event.target.closest && event.target.closest('a.m2-store-link');
  if (link) { event.preventDefault(); m2OpenStore(link); return; }
  var row = event.target.closest && event.target.closest('tr[data-open-store]');
  if (!row || event.target.closest('a, button, input, select, label')) { return; }
  if (String(window.getSelection && window.getSelection()).length) { return; }
  var inner = row.querySelector('a.m2-store-link');
  if (inner) { m2OpenStore(inner); }
});
// Placeholder controls announce "Coming soon" and do nothing.
document.addEventListener('click', function (event) {
  if (event.target.closest && event.target.closest('[aria-disabled="true"]')) { event.preventDefault(); }
}, true);
// Outputs inside a collapsed disclosure are suspended; opening it tells Shiny they are now visible.
document.addEventListener('toggle', function (event) {
  var box = event.target;
  if (box.classList && box.classList.contains('m2-disclosure') && box.open) {
    window.jQuery(box).trigger('shown');
    window.dispatchEvent(new Event('resize'));
  }
}, true);
document.addEventListener('DOMContentLoaded', function () {
  var box = document.getElementById('m2_filter_box');
  if (box && window.matchMedia('(max-width: 640px)').matches) { box.removeAttribute('open'); }
});
"""


def _placeholder(name, label, text=None):
    """Visible, keyboard-reachable, announced as not available; no handler is attached."""
    return ui.tags.button(icon(name), ui.tags.span(text, class_="m2-ph-text") if text else None,
                          ui.tags.span("Soon", class_="m2-soon") if text else None,
                          type="button", class_="m2-icon-btn" + (" m2-copilot" if text else ""),
                          title=f"{label} — coming soon", **{"aria-disabled": "true",
                                                             "aria-label": f"{label} — coming soon"})


def _sidebar():
    return ui.sidebar(
        ui.div(LOGO, ui.div(ui.tags.strong("Marvel CPG"), ui.tags.span("Module 2 · Stockout risk"),
                            class_="m2-logo-text"), class_="m2-logo"),
        ui.output_ui("m2_nav"),
        ui.div(
            ui.tags.p("Other", class_="m2-nav-label"),
            ui.tags.button(icon("help"), ui.tags.span("Help"), ui.tags.span("Soon", class_="m2-soon"),
                           type="button", class_="m2-nav-item", **{"aria-disabled": "true",
                                                                    "aria-label": "Help — coming soon"}),
            ui.tags.button(icon("settings"), ui.tags.span("Settings"), ui.tags.span("Soon", class_="m2-soon"),
                           type="button", class_="m2-nav-item", **{"aria-disabled": "true",
                                                                    "aria-label": "Settings — coming soon"}),
            class_="m2-nav-group"),
        ui.div(ui.output_ui("m2_user_card"),
               ui.input_action_button("m2_signout", HTML(f"{icon('logout')}<span>Sign out</span>"),
                                      class_="m2-nav-item m2-signout"),
               class_="m2-sidebar-foot"),
        id="m2_sidebar", open="desktop", width=248, class_="m2-sidebar", title=None)


def _topbar():
    return ui.div(
        ui.div(ui.output_ui("m2_crumb"), class_="m2-crumb-wrap"),
        ui.div(ui.span(icon("search", 16), class_="m2-search-icon"),
               ui.input_text("m2_f_search", None, placeholder="Search stores by ID or name"),
               class_="m2-search", role="search"),
        ui.div(_placeholder("spark", "Copilot", "Copilot"), _placeholder("mail", "Messages"),
               _placeholder("bell", "Notifications"), _placeholder("calendar", "Calendar"),
               ui.span(class_="m2-topbar-sep", **{"aria-hidden": "true"}),
               ui.output_ui("m2_user_block"), class_="m2-topbar-actions"),
        class_="m2-topbar")


def _filters():
    return ui.tags.details(
        ui.tags.summary("Filters"),
        ui.div(
            ui.input_select("m2_f_channel", "Channel", {"": "All channels"}),
            ui.input_select("m2_f_region", "Region", {"": "All regions"}),
            ui.input_select("m2_f_tier", "Tier", {"": "All tiers"}),
            ui.input_select("m2_f_urgency", "Urgency", {"": "All urgency", "urgent": "Urgent", "high": "High",
                                                        "monitor": "Monitor"}),
            class_="m2-filters"),
        id="m2_filter_box", class_="m2-filter-box m2-card-flat", open="open")


def _business_pages():
    overview = ui.nav_panel(
        "overview",
        ui.output_ui("m2_kpis"),
        ui.div(ui.output_ui("m2_focus"), ui.output_ui("m2_overview_oos"), class_="m2-grid m2-grid-2"),
        ui.output_ui("m2_top10"),
        ui.output_ui("m2_regions"))
    ranking = ui.nav_panel(
        "ranking",
        ui.div(ui.div(ui.input_radio_buttons("m2_view", "View", VIEW_LABELS, selected="all", inline=True),
                      class_="m2-views", role="group"),
               ui.div(ui.output_ui("m2_count"),
                      ui.div(ui.input_select("m2_sort", "Sort by", SORT_CHOICES, selected="score:desc"),
                             ui.input_select("m2_rows", "Rows", ROW_CHOICES, selected="10"),
                             class_="m2-sortbar"),
                      class_="m2-listhead"),
               ui.output_ui("m2_priority_table"),
               ui.div(ui.input_action_button("m2_prev", "Previous", class_="m2-btn"),
                      ui.output_ui("m2_page_label"),
                      ui.input_action_button("m2_next", "Next", class_="m2-btn"),
                      class_="m2-pager"),
               ui.output_ui("m2_rank_help"),
               class_="m2-card"),
        ui.tags.details(ui.tags.summary("Supporting distribution: highest SKU stockout risk per store"),
                        ui.output_ui("m2_risk_chart"), class_="m2-disclosure", id="m2-risk-box"))
    analytics = ui.nav_panel(
        "analytics",
        ui.div(
            ui.div(ui.input_radio_buttons("m2_oos_measure", "Measure",
                                          {"current": "Out of stock now", "28d": "Past 28 days"},
                                          selected="current", inline=True),
                   ui.input_radio_buttons("m2_oos_msl", "MSL", {"all": "All", "MSL": "MSL", "Non-MSL": "Non-MSL",
                                                                "Unknown": "Unknown"}, selected="all", inline=True),
                   class_="m2-analytics-controls"),
            ui.output_ui("m2_oos_note"),
            class_="m2-card m2-card-tight"),
        ui.div(ui.output_ui("m2_oos_chart"), ui.output_ui("m2_oos_donut"), class_="m2-grid m2-grid-21"),
        ui.output_ui("m2_oos_daily"),
        ui.div(ui.div(ui.input_select("m2_oos_sku", "Stores affected by SKU", {"": "Select a SKU"}),
                      class_="m2-inline-control"),
               ui.output_ui("m2_oos_stores"), class_="m2-card", id="m2-oos-drill"))
    detail = ui.nav_panel(
        "detail",
        ui.input_action_button("m2_back", ui.TagList(icon("back", 16), ui.output_text("m2_back_label", inline=True)),
                               class_="m2-btn m2-back"),
        ui.output_ui("m2_detail_head"),
        ui.output_ui("m2_detail_stats"),
        ui.output_ui("m2_detail_actions"),
        ui.navset_underline(
            ui.nav_panel("SKUs", ui.div(
                ui.div(ui.input_select("m2_sku_filter", "Show", {"all": "All SKUs", "msl": "MSL only",
                                                                 "oos": "Out of stock now", "alert": "Model alert",
                                                                 "unestimated": "Not estimated"}),
                       ui.input_text("m2_sku_search", "Search SKU", placeholder="SKU or product"),
                       ui.input_select("m2_sku_sort", "Sort by", SKU_SORT_CHOICES, selected="sales:desc"),
                       ui.input_select("m2_sku_rows", "Rows", ROW_CHOICES, selected="10"),
                       class_="m2-sortbar m2-sku-controls"),
                ui.output_ui("m2_sku_table"),
                ui.div(ui.input_action_button("m2_sku_prev", "Previous", class_="m2-btn"),
                       ui.output_ui("m2_sku_page_label"),
                       ui.input_action_button("m2_sku_next", "Next", class_="m2-btn"), class_="m2-pager"),
                class_="m2-card"), value="skus"),
            ui.nav_panel("Inventory trend", ui.div(
                ui.div(ui.input_selectize("m2_trend_skus", "SKUs (up to 5)", {}, multiple=True,
                                          options={"maxItems": 5}), class_="m2-inline-control"),
                ui.output_ui("m2_trend_chart"), class_="m2-card"), value="trend"),
            ui.nav_panel("Out-of-stock SKUs", ui.div(ui.output_ui("m2_detail_oos"), class_="m2-card"), value="oos"),
            ui.nav_panel("Shelf execution", ui.div(ui.output_ui("m2_shelf"), class_="m2-card"), value="shelf"),
            ui.nav_panel("Estimate quality", ui.div(ui.output_ui("m2_quality"), class_="m2-card"), value="quality"),
            id="m2_detail_tabs", selected="skus"))
    return ui.navset_hidden(overview, ranking, analytics, detail, id="m2_pages", selected="overview")


def _operations():
    return ui.div(
        ui.output_ui("m2_ops_header"),
        ui.navset_hidden(
            ui.nav_panel("health", ui.output_ui("m2_health")),
            ui.nav_panel("monitoring", ui.output_ui("m2_monitoring")),
            id="m2_ops_tabs", selected="health"),
        class_="m2-ops", id="m2-ops-root")


def module_2_ui(dataset: Dict[str, Any] = None) -> ui.Tag:
    """Module 2 tab. ``dataset`` is accepted for app compatibility and not used."""
    main = ui.div(
        _topbar(),
        ui.div(
            ui.output_ui("m2_page_head"),
            ui.output_ui("m2_banners"),
            ui.panel_conditional("output.m2_mode === 'business' && output.m2_page !== 'detail'", _filters()),
            ui.panel_conditional("output.m2_mode === 'business'", _business_pages()),
            ui.panel_conditional("output.m2_mode === 'ops'", _operations()),
            class_="m2-content"),
        class_="m2-main")
    return ui.div(
        plotly_dependency(),
        font_dependency(),
        ui.tags.style(_CSS),
        ui.tags.script(_SCRIPT),
        ui.div(ui.output_text("m2_mode"), ui.output_text("m2_page"), class_="m2-hidden", id="m2-mode-flag"),
        ui.panel_conditional("output.m2_mode === 'none'", ui.output_ui("m2_signed_out")),
        ui.panel_conditional("output.m2_mode !== 'none'",
                             ui.layout_sidebar(_sidebar(), main, fillable=False, border=False, border_radius=False,
                                               class_="m2-shell")),
        class_="m2", id="m2-root")
