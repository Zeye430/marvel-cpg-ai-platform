"""
modules/module_2/ui.py
======================
Module 2 layout: today's store priorities, store/SKU detail, and (for managers)
model health and monitoring. Every number is rendered by the server from the
published serving candidate; nothing here is a fixed metric or random value.
"""

from pathlib import Path
from typing import Any, Dict

from htmltools import HTMLDependency
from shiny import ui

_CSS = (Path(__file__).with_name("module2.css")).read_text()


def plotly_dependency():
    """Serve plotly.min.js from the installed plotly package (no CDN)."""
    import plotly
    source = Path(plotly.__file__).parent / "package_data"
    return HTMLDependency("m2-plotly", plotly.__version__, source={"subdir": str(source)},
                          script={"src": "plotly.min.js"})


_FILTER_SCRIPT = """
// Sign-out: drop previously rendered scoped data from the page immediately.
// Registered once the Shiny client is initialized (before session messages arrive).
window.jQuery(document).on('shiny:connected', function () {
  if (window.__m2ClearHandler) { return; }
  window.__m2ClearHandler = true;
  Shiny.addCustomMessageHandler('m2-clear', function (message) {
    document.querySelectorAll('#m2-root .shiny-html-output').forEach(function (e) { e.innerHTML = ''; });
  });
});
document.addEventListener('DOMContentLoaded', function () {
  var box = document.getElementById('m2_filter_box');
  if (box && window.matchMedia('(max-width: 640px)').matches) { box.removeAttribute('open'); }
});
"""


def _filters():
    return ui.tags.details(
        ui.tags.summary("Filters"),
        ui.div(
            ui.input_select("m2_f_channel", "Channel", {"": "All channels"}),
            ui.input_select("m2_f_region", "Region", {"": "All regions"}),
            ui.input_select("m2_f_tier", "Tier", {"": "All tiers"}),
            ui.input_text("m2_f_search", "Store search", placeholder="Store ID or name"),
            ui.div(
                ui.input_checkbox("m2_f_msl", "Observed MSL gap only"),
                ui.input_checkbox("m2_f_alert", "Predicted alerts only"),
                ui.input_checkbox("m2_f_unknown", "Unknown inputs only"),
                class_="m2-checks"),
            class_="m2-filters"),
        id="m2_filter_box", class_="m2-card m2-filter-box", open="open")


def module_2_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Module 2 tab. ``dataset`` is accepted for app compatibility and not used."""
    return ui.div(
        plotly_dependency(),
        ui.tags.style(_CSS),
        ui.tags.script(_FILTER_SCRIPT),
        ui.output_ui("m2_header"),
        ui.navset_pill(
            ui.nav_panel(
                "Today's priorities",
                ui.div(
                    _filters(),
                    ui.output_ui("m2_tiles"),
                    ui.div(
                        ui.output_ui("m2_count"),
                        ui.download_button("m2_export", "Export visit list (CSV)", class_="m2-btn"),
                        class_="m2-actions"),
                    ui.output_ui("m2_priority_table"),
                    ui.div(
                        ui.input_action_button("m2_prev", "Previous", class_="m2-btn"),
                        ui.input_action_button("m2_next", "Next", class_="m2-btn"),
                        ui.output_ui("m2_page_label"),
                        class_="m2-pager"),
                    ui.div(
                        ui.div(ui.h2("Store risk index distribution", class_="m2-h"),
                               ui.output_ui("m2_risk_chart"), class_="m2-card"),
                        ui.div(ui.h2("Data follow-up", class_="m2-h"), ui.output_ui("m2_followups"),
                               class_="m2-card"),
                        class_="m2-two"),
                    style="padding-top:12px"),
                value="priorities"),
            ui.nav_panel(
                "Store detail",
                ui.div(
                    ui.div(
                        ui.input_action_button("m2_back", "← Back to priorities", class_="m2-btn"),
                        ui.input_select("m2_store", "Store", {"": "Select a store"}),
                        class_="m2-actions"),
                    ui.output_ui("m2_detail"),
                    ui.div(
                        ui.h2("Reported inventory history (last 28 days)", class_="m2-h"),
                        ui.input_select("m2_detail_sku", "SKU", {"": "Select a SKU"}),
                        ui.output_ui("m2_history_chart"),
                        class_="m2-card"),
                    style="padding-top:12px"),
                value="detail"),
            ui.nav_panel("Model health", ui.div(ui.output_ui("m2_health"), style="padding-top:12px"), value="health"),
            ui.nav_panel("Monitoring", ui.div(ui.output_ui("m2_monitoring"), style="padding-top:12px"),
                         value="monitoring"),
            id="m2_tabs", selected="priorities"),
        class_="m2", id="m2-root")
