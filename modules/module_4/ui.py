"""
modules/module_4/ui.py
======================
UI layout for Module 4: Commercial Copilot (Text-to-SQL + RAG), in the shared workspace
design (shared/ui_kit: Module 2's design).

Layout: a conversation column (oldest first) with the composer fixed at the bottom, a home
screen with suggested questions while the conversation is empty, and a "Data scope" rail with
the source, scope, prediction release, language model and document access. Every answer is
produced by the server from the signed-in user's scoped data or the permitted knowledge-base
documents; nothing here is a fixed answer.
"""

from pathlib import Path
from typing import Any, Dict

from shiny import ui

from shared import ui_kit as kit

_CSS = (Path(__file__).with_name("module4.css")).read_text()

# One-click starter questions (the brief's examples plus Module 2's published predictions).
SUGGESTIONS = (
    ("Your data", "chart", (
        ("Top out-of-stock SKUs · Traditional Trade",
         "Which are the 5 SKUs with the highest out-of-stock rate in Traditional Trade this week?"),
        ("Sell-in above sell-out · Modern Trade",
         "Which Modern Trade stores have Sell-in above Sell-out over the last 4 weeks?"),
        ("Laundry shelf share · Southeast",
         "What is the average shelf share for the Laundry category in the Southeast region?"),
    )),
    ("Stockout predictions", "pulse", (
        ("Stockout alerts in my stores", "Which SKUs in my stores have a stockout alert?"),
        ("Visit priority ranking", "Show the visit priority ranking of my stores"),
    )),
    ("Marvel documents", "doc", (
        ("Degreaser objection", "What argument do I give a client who refuses to take the new degreaser line?"),
        ("Substitute for Power Clean Lemon 1L",
         "Which SKU can I substitute for Marvel Power Clean Lemon 1L if it's out of stock?"),
        ("Discount without approval", "What discount can I offer without manager approval?"),
    )),
)
SHORT = [title for _, _, items in SUGGESTIONS for title, _ in items]
PROMPTS = [prompt for _, _, items in SUGGESTIONS for _, prompt in items]

_SCRIPT = """
(function () {
  if (window.__m4Ui) { return; }
  window.__m4Ui = true;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  // Enter sends, Shift+Enter adds a line.
  document.addEventListener('keydown', function (event) {
    var box = event.target;
    if (!box || box.id !== 'm4_user_query' || event.key !== 'Enter' || event.shiftKey || event.isComposing) { return; }
    event.preventDefault();
    var send = document.getElementById('m4_btn_ask');
    if (!box.value.trim() || !send || send.disabled) { return; }
    Shiny.setInputValue('m4_user_query', box.value);
    setTimeout(function () { send.click(); }, 0);
  });
  document.addEventListener('click', function (event) {
    // Chart | Table | SQL tabs inside one answer.
    var tab = event.target.closest && event.target.closest('#m4-root [data-m4-tab]');
    if (tab) {
      var card = tab.closest('.m4-a');
      card.querySelectorAll('[data-m4-tab]').forEach(function (t) {
        var on = t === tab;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
      });
      card.querySelectorAll('[data-m4-panel]').forEach(function (p) { p.hidden = p.dataset.m4Panel !== tab.dataset.m4Tab; });
      if (tab.dataset.m4Tab === 'chart') { window.dispatchEvent(new Event('resize')); }
      return;
    }
    // Suggested rephrasings fill the question box (they are not sent automatically).
    var fill = event.target.closest && event.target.closest('#m4-root [data-m4-fill]');
    if (fill) {
      var box = document.getElementById('m4_user_query');
      box.value = fill.dataset.m4Fill;
      box.dispatchEvent(new Event('input', {bubbles: true}));
      box.focus();
    }
  });
  // Arrow keys move between the tabs of one answer.
  document.addEventListener('keydown', function (event) {
    var tab = event.target.closest && event.target.closest('#m4-root [data-m4-tab]');
    if (!tab || (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft')) { return; }
    var tabs = Array.prototype.slice.call(tab.parentNode.querySelectorAll('[data-m4-tab]'));
    var next = tabs[(tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    next.focus(); next.click();
  });
  // A new question or answer scrolls into view; the box clears after sending.
  window.jQuery(document).on('shiny:value', function (event) {
    if (event.name !== 'm4_conversation') { return; }
    setTimeout(function () {
      var turns = document.querySelectorAll('#m4_conversation .m4-turn');
      var last = turns[turns.length - 1];
      if (last) { last.scrollIntoView({block: 'start', behavior: reduce.matches ? 'auto' : 'smooth'}); }
    }, 30);
  });
  // The data-scope rail is a side panel on wide screens and a collapsed panel on narrow ones.
  function rail() {
    var box = document.getElementById('m4-rail');
    if (box) { box.open = window.matchMedia('(min-width: 1180px)').matches; }
  }
  window.jQuery(document).on('shiny:connected', rail);
  window.matchMedia('(min-width: 1180px)').addEventListener('change', rail);
})();
"""


def _suggestions():
    groups, n = [], 0
    for title, icon_name, items in SUGGESTIONS:
        cards = []
        for short, prompt in items:
            cards.append(ui.input_action_button(
                f"m4_ex_{n}", ui.TagList(ui.tags.span(short, class_="m4-card-title"),
                                         ui.tags.span(prompt, class_="m4-card-text")),
                class_="m4-suggestion", title=prompt))
            n += 1
        groups.append(ui.div(ui.div(kit.icon(icon_name, 16), ui.span(title), class_="m4-group-title"),
                             ui.div(*cards, class_="m4-cards"), class_="m4-group"))
    return groups


def module_4_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Renders the copilot: top bar, conversation (or home), composer and the data-scope rail."""
    label = (dataset or {}).get("module_label") or "Commercial Copilot"
    home = ui.div(
        ui.div(kit.icon("spark", 22), class_="m4-home-mark"),
        ui.h1("What would you like to know?", class_="ws-title m4-home-title"),
        ui.p("Ask about your stores' sales, stock, shelf execution and deliveries, Module 2's stockout predictions, "
             "or Marvel's product and policy documents. Data answers come with a chart, the table and the SQL behind "
             "them; document answers cite their sources.", class_="ws-sub m4-home-sub"),
        ui.div(*_suggestions(), class_="m4-groups"),
        class_="m4-home", id="m4-home")
    composer = ui.div(
        ui.div(
            ui.input_text_area("m4_user_query", None, rows=1, width="100%",
                               placeholder="Ask about your stores' data or Marvel's documents…"),
            ui.div(ui.input_action_button("m4_btn_clear", "Clear", class_="ws-btn m4-clear",
                                          title="Clear the conversation"),
                   ui.input_task_button("m4_btn_ask", ui.TagList(kit.icon("send", 16), ui.span("Ask")),
                                        label_busy="Thinking…", class_="ws-btn primary m4-send"),
                   class_="m4-composer-actions"),
            class_="m4-composer"),
        ui.p("Enter to send · Shift+Enter for a new line · Answers use only the stores and documents you can access.",
             class_="m4-composer-hint"),
        class_="m4-composer-wrap")
    rail = ui.tags.details(ui.tags.summary(kit.icon("database", 16), ui.span("Data scope")),
                           ui.output_ui("m4_status"), class_="m4-rail", id="m4-rail", open="open")
    return ui.div(
        kit.kit_dependency(),
        kit.plotly_dependency(),
        ui.tags.style(_CSS),
        ui.tags.script(_SCRIPT),
        kit.topbar((label, "Ask"), ui.output_ui("m4_user_block")),
        ui.div(
            ui.div(
                ui.div(ui.output_text("m4_has_turns"), class_="ws-hidden"),
                ui.panel_conditional("output.m4_has_turns !== 'yes'", home),
                ui.output_ui("m4_conversation"),
                composer,
                class_="m4-main"),
            rail,
            class_="m4-layout"),
        class_="ws ws-controls m4",
        id="m4-root",
    )
