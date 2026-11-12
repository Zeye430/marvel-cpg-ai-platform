"""
modules/module_3/ui.py
======================
Layout for Module 3: Negotiation Simulator, in the shared workspace design (shared/ui_kit).

Hosted in the workspace shell, the app sidebar lists its two pages (``pages_nav_ui``):

- Deal simulator (brief Scenario A): one store × SKU's negotiation context and three proposals side
  by side (additional discount on top of the standard and execution commitment), each checked against
  the guardrail and given a verdict: APPROVED, APPROVED WITH CONDITIONS, MANAGER REVIEW, REJECTED or
  CANNOT ASSESS.
- Price change (brief Scenario B): a list-price increase on a SKU or category, with the volume,
  revenue and gross-profit response from a price elasticity estimated on the user's stores.

The store, product and quantity (and the price change's target) are filter pills like Module 1's;
each chart's "?" explains how to read it. Every number is rendered by the server from the signed-in
user's scoped shared publication. The rules are an Owner proposal the client has not confirmed; the
execution uplift is a switchable assumption in the page.

Without the shared runtime (the legacy root ``app.py``) the module shows where it runs instead.
"""

from pathlib import Path
from typing import Any, Dict

from shiny import ui

from shared import ui_kit as kit

from .calc import RULES_VERSION, UPLIFT_SOURCES

_CSS = (Path(__file__).with_name("module3.css")).read_text()

PAGES = {"deal": "Deal simulator", "price": "Price change"}
PAGE_ICONS = {"deal": "scale", "price": "chart"}
SCENARIOS = ("a", "b", "c")

_SCRIPT = """
window.jQuery(document).on('shiny:connected', function () {
  if (window.__m3Handlers) { return; }
  window.__m3Handlers = true;
  // Sign-out / identity switch: drop previously rendered scoped content immediately.
  Shiny.addCustomMessageHandler('m3-clear', function (message) {
    document.querySelectorAll('#m3-root .shiny-html-output').forEach(function (e) { e.innerHTML = ''; });
  });
  Shiny.addCustomMessageHandler('m3-scroll-top', function (message) {
    setTimeout(function () { window.scrollTo({top: 0, behavior: 'instant'}); }, 0);
  });
});
function m3Go(page) { Shiny.setInputValue('m3_go', page, {priority: 'event'}); }
"""


def _scenario(key):
    letter = key.upper()
    return ui.div(
        ui.div(ui.tags.h3(f"Scenario {letter}", class_="m3-scn-title"), ui.output_ui(f"m3_verdict_{key}"),
               class_="m3-scn-head"),
        ui.div(ui.input_numeric(f"m3_add_{key}", "Additional discount above the standard (pts)", 0, min=0, max=40, step=0.5),
               ui.input_select(f"m3_commit_{key}", "Execution commitment offered", {"none": "None offered"}),
               class_="m3-scn-inputs"),
        ui.output_ui(f"m3_result_{key}"),
        class_="ws-card m3-scn", id=f"m3-scn-{key}")


def rules_panel():
    """The rules and assumptions in words (static; no data)."""
    return ui.tags.div(
        ui.tags.p(f"Rule version {RULES_VERSION}: the Owner's proposed policy, pending client confirmation and "
                  "confirmation of Sales authority. While the source guardrail is \"Requires review\", every verdict "
                  "is a provisional simulation: it does not authorise offering or agreeing a deal. Manager review is a "
                  "status shown here; nothing is sent or saved.", class_="ws-muted"),
        ui.tags.p("Total discount = standard discount + proposed additional discount (percentage points). The "
                  "account's recent invoiced discount is history only: it is not the standard and is not added to "
                  "it.", class_="ws-muted"),
        ui.tags.ul(ui.tags.li(ui.tags.b("APPROVED: "), "no additional discount (the standard only), with the gross "
                              "margin at or above the minimum."),
                   ui.tags.li(ui.tags.b("APPROVED WITH CONDITIONS: "), "an additional discount up to and including the "
                              "maximum, with the guardrail's execution commitment offered (any one of the listed "
                              "options) and the margin at or above the minimum."),
                   ui.tags.li(ui.tags.b("MANAGER REVIEW: "), "an additional discount above the maximum with the margin "
                              "still at or above the minimum. Distributors: any additional discount, because "
                              "distributor commitments are not confirmed."),
                   ui.tags.li(ui.tags.b("REJECTED: "), "a margin below the minimum, a total discount of 100% or more, "
                              "or an additional discount within the maximum without the commitment."),
                   ui.tags.li(ui.tags.b("CANNOT ASSESS: "), "a price, cost, standard, maximum, minimum or commitment "
                              "is missing, or the additional discount is empty or negative. Nothing is assumed in "
                              "its place."),
                   class_="m3-rule-list"),
        ui.tags.p("Margin = (net price − unit cost) / net price, with net price = list price × (1 − total discount). "
                  "Reference list price and cost are static; currency is not supplied. Execution uplift is an "
                  "assumption: the client's V3 note states +8% for a display, +5% for planogram compliance and +3% "
                  "per 10 points of shelf share, which the data does not show within a store; shelf share in the "
                  "audits is an allocation among Marvel products.", class_="ws-muted"),
        id="m3-rules")


def _deal_page():
    return ui.nav_panel(
        "deal",
        ui.div(
            kit.filter_pill("m3-pill-store", "Account (store)", kit.pill_face("Store", "m3_store", empty="Choose", narrows=False),
                            ui.input_selectize("m3_store", None, [], options={"placeholder": "Search stores…"}),
                            icon_name="home", **{"data-autoclose": ""}),
            kit.filter_pill("m3-pill-sku", "Product", kit.pill_face("Product", "m3_sku", empty="Choose", narrows=False),
                            ui.input_selectize("m3_sku", None, [], options={"placeholder": "Search products…"}),
                            **{"data-autoclose": ""}),
            kit.filter_pill("m3-pill-qty", "Order quantity", kit.pill_face("Quantity", "m3_qty", unit="units"),
                            ui.input_numeric("m3_qty", "Units in this order", 100, min=1, step=10)),
            class_="ws-fbar m3-fbar", role="search", **{"aria-label": "Deal"}),
        ui.output_ui("m3_context"),
        ui.output_ui("m3_banner"),
        ui.div(*[_scenario(k) for k in SCENARIOS], class_="m3-scenarios", id="m3-scenarios"),
        ui.div(ui.div(ui.div(ui.tags.h2("Margin by scenario", class_="ws-h"),
                             kit.help_tip("Margin by scenario",
                                          ui.tags.ul(ui.tags.li("One bar per proposal: the gross margin left after "
                                                                "its total discount."),
                                                     ui.tags.li("The dashed line is the guardrail's minimum margin; a "
                                                                "bar below it fails."),
                                                     ui.tags.li("The label on each bar is its verdict.")),
                                          ui.tags.p("Margin = (net price − unit cost) / net price, at the static "
                                                    "reference list price and cost.", class_="ws-help-data"),
                                          id="m3-chart-help"),
                             class_="ws-title-row"), class_="ws-card-head"),
               ui.output_ui("m3_chart"), class_="ws-card m3-chart-card"),
        ui.tags.details(
            ui.tags.summary("Assumptions and rules (pending client confirmation)"),
            ui.div(ui.input_radio_buttons("m3_uplift", "Execution uplift on volume", UPLIFT_SOURCES,
                                          selected="client"),
                   class_="m3-assume"),
            rules_panel(),
            class_="ws-disclosure m3-assumptions", id="m3-assumptions"))


def _price_page():
    return ui.nav_panel(
        "price",
        ui.div(
            kit.filter_pill("m3-pill-kind", "Apply the increase to", kit.pill_face("Apply to", "m3_b_kind", narrows=False),
                            ui.input_radio_buttons("m3_b_kind", None, {"category": "A category",
                                                                       "sku": "One product"}, selected="category"),
                            **{"data-autoclose": ""}),
            kit.filter_pill("m3-pill-target", "Category or product",
                            kit.pill_face("Target", "m3_b_target", empty="Choose", narrows=False),
                            ui.input_selectize("m3_b_target", None, []), **{"data-autoclose": ""}),
            class_="ws-fbar m3-fbar", role="search", **{"aria-label": "Price change target"}),
        ui.div(
            ui.div(ui.input_slider("m3_b_increase", "List-price increase", 0, 15, 5, step=0.5, post="%"),
                   ui.input_slider("m3_b_pass", "Passed on to the shelf price", 0, 100, 100, step=5, post="%"),
                   ui.div(ui.input_radio_buttons("m3_b_source", "Price elasticity",
                                                 {"estimated": "Estimated from your stores", "custom": "Custom"},
                                                 selected="estimated", inline=True),
                          ui.panel_conditional("input.m3_b_source === 'custom'",
                                               ui.input_numeric("m3_b_custom", None, -1.0, step=0.1, max=0)),
                          class_="m3-elast"),
                   class_="m3-pick m3-pick-b2"),
            class_="ws-card m3-pick-card"),
        ui.output_ui("m3_b_results"),
        ui.output_ui("m3_b_warnings"),
        ui.div(ui.div(ui.div(ui.tags.h2("Response to the price increase", class_="ws-h"),
                             kit.help_tip("Response to the price increase",
                                          ui.tags.ul(ui.tags.li("The lines show how revenue and gross profit change "
                                                                "for increases from 0% to 15%."),
                                                     ui.tags.li("The marker is the increase chosen above; the shaded "
                                                                "band is the range the elasticity could plausibly "
                                                                "take (about 95%)."),
                                                     ui.tags.li("Where a line crosses 0, the increase stops paying "
                                                                "off.")),
                                          ui.div(ui.output_ui("m3_b_notes"), class_="ws-help-data"),
                                          id="m3-b-chart-help"),
                             class_="ws-title-row"), class_="ws-card-head"),
               ui.output_ui("m3_b_chart"), class_="ws-card m3-chart-card"))


def pages_nav_ui():
    """Module 3's page navigation for the host's sidebar (rendered by the Module 3 server)."""
    return ui.output_ui("m3_nav")


def _not_hosted():
    return ui.div(
        kit.kit_dependency(),
        kit.card(ui.tags.h2("The Negotiation Simulator runs in the integrated workspace", class_="ws-h"),
                 ui.tags.p("It reads the shared, account-scoped business publication. Start the integrated app "
                           "(shiny run integrated_app.py; see docs/integrated-app.md). It no longer reads the "
                           "legacy CSV loader.", class_="ws-sub"),
                 id="m3-not-hosted"),
        class_="ws m3", id="m3-root")


def module_3_ui(dataset: Dict[str, Any] = None) -> ui.Tag:
    """Module 3 page. ``dataset`` is the host context; data never comes from it."""
    context = dataset if isinstance(dataset, dict) else {}
    if context.get("runtime") is None:
        return _not_hosted()
    actions = kit.topbar_actions(ui.output_ui("m3_user_block"),
                                 copilot_id="m3_open_copilot" if context.get("copilot_nav") else None)
    topbar = ui.div(ui.div(ui.output_ui("m3_crumb"), class_="ws-crumb-wrap"),
                    ui.div(*actions, class_="ws-topbar-actions"), class_="ws-topbar")
    return ui.div(
        kit.kit_dependency(),
        kit.plotly_dependency(),
        ui.tags.style(_CSS),
        ui.tags.script(_SCRIPT),
        topbar,
        ui.div(
            ui.output_ui("m3_page_head"),
            ui.output_ui("m3_status"),
            ui.div(ui.output_text("m3_ready"), class_="ws-hidden", id="m3-ready-flag"),
            ui.panel_conditional("output.m3_ready === 'yes'",
                                 ui.navset_hidden(_deal_page(), _price_page(), id="m3_pages", selected="deal")),
            class_="ws-content"),
        class_="ws ws-controls m3", id="m3-root")
