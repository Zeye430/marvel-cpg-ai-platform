"""
modules/module_4/ui.py
======================
UI layout for Module 4: Commercial Copilot (Text-to-SQL + RAG).
Every answer is produced by the server from the signed-in user's scoped data or
from the permitted knowledge-base documents; nothing here is a fixed answer.
"""

from pathlib import Path
from typing import Any, Dict

from shiny import ui

from modules.module_2.ui import plotly_dependency  # same dependency name: plotly.js loads once

_CSS = (Path(__file__).with_name("module4.css")).read_text()

# The brief's example questions, offered as one-click starters.
DATA_PROMPTS = [
    "Which are the 5 SKUs with the highest out-of-stock rate in Traditional Trade this week?",
    "Which Modern Trade stores have Sell-in above Sell-out over the last 4 weeks?",
    "What is the average shelf share for the Laundry category in the Southeast region?",
]
KNOWLEDGE_PROMPTS = [
    "What argument do I give a client who refuses to take the new degreaser line?",
    "Which SKU can I substitute for Marvel Power Clean Lemon 1L if it's out of stock?",
    "What discount can I offer without manager approval?",
]
PROMPTS = DATA_PROMPTS + KNOWLEDGE_PROMPTS
SHORT = ["📉 Top OOS SKUs · Traditional Trade", "📦 Sell-in > Sell-out · Modern Trade", "🧺 Laundry shelf share · Southeast",
         "💬 Degreaser objection", "🔁 Substitute for Power Clean Lemon", "🛡️ Discount without approval"]


def module_4_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Renders the copilot: status, starter questions, the question box and the conversation."""
    def chips(start, prompts):
        return [ui.input_action_button(f"m4_ex_{start + i}", SHORT[start + i], class_="btn-quick-demo",
                                       title=prompt) for i, prompt in enumerate(prompts)]

    return ui.div(
        plotly_dependency(),
        ui.tags.style(_CSS),
        ui.div(
            ui.div(
                ui.div(
                    ui.div(
                        ui.HTML("""<svg class="header-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                            stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>"""),
                        ui.h2("Commercial Copilot (Text-to-SQL & RAG)", class_="section-title"),
                        class_="header-title-box",
                    ),
                    ui.div(ui.span("LOCAL MODEL · NLQ + RAG", class_="badge-telemetry-live"), class_="header-actions"),
                    class_="card-header-row mb-2",
                ),
                ui.p("Ask about sales, stock-outs, shelf execution or deliveries to get an answer with the SQL, a chart "
                     "and the table behind it, limited to the stores you can see. Ask about products, substitutes, "
                     "objections or policies to get an answer cited from Marvel's authorized documents.",
                     class_="text-muted small mb-2"),
                ui.output_ui("m4_status"),
                ui.div(ui.span("Data questions", class_="tray-label"), *chips(0, DATA_PROMPTS), class_="m4-tray"),
                ui.div(ui.span("Knowledge", class_="tray-label"), *chips(3, KNOWLEDGE_PROMPTS), class_="m4-tray mb-3"),
                ui.div(
                    ui.input_text_area("m4_user_query", None, rows=2, width="100%",
                                       placeholder="Ask a question about sales, stock-outs, shelf share, deliveries, "
                                                   "products or commercial policies…"),
                    ui.input_task_button("m4_btn_ask", "Ask Copilot ↵", label_busy="Thinking…",
                                         class_="btn-luxury-primary"),
                    ui.input_action_button("m4_btn_clear", "Clear", class_="btn-quick-demo"),
                    class_="m4-ask",
                ),
                class_="sleek-card dashboard-hero-card mb-4",
            ),
            ui.output_ui("m4_conversation"),
            class_="app-container",
        ),
        id="m4-root",
    )
