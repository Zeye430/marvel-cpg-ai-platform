"""
modules/module_4/ui.py
======================
UI layout for Module 4: Commercial Copilot.
"""

from typing import Dict, Any
from shiny import ui


def module_4_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Renders the AI Copilot & Text-to-SQL Assistant interface."""
    return ui.div(
        ui.div(
            # Hero Card
            ui.div(
                ui.div(
                    ui.div(
                        ui.HTML("""
                        <svg class="header-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                        </svg>
                        """),
                        ui.h2("Commercial AI Copilot (Text-to-SQL & RAG)", class_="section-title"),
                        class_="header-title-box"
                    ),
                    ui.div(
                        ui.span("NATURAL LANGUAGE ANALYTICS & RAG", class_="badge-telemetry-live"),
                        class_="header-actions"
                    ),
                    class_="card-header-row mb-2"
                ),
                ui.p(
                    "Ask commercial performance questions in plain English. The Copilot translates questions into executable "
                    "SQL over the Marvel warehouse schema and grounds policy answers with document citations.",
                    class_="text-muted small mb-3"
                ),

                # Suggested Prompts Tray
                ui.div(
                    ui.span("Suggested Prompts:", class_="tray-label me-2"),
                    ui.input_action_button("m4_prompt_1", "📍 Top 5 High-OOS Stores in Northeast", class_="btn-quick-demo me-2 mb-2"),
                    ui.input_action_button("m4_prompt_2", "🛡️ Guardrail Rules for Modern Trade", class_="btn-quick-demo me-2 mb-2"),
                    ui.input_action_button("m4_prompt_3", "📦 Weekly Invoiced vs Sell-Out Units", class_="btn-quick-demo me-2 mb-2"),
                    ui.input_action_button("m4_prompt_4", "📜 Planogram Concession Penalty Policy", class_="btn-quick-demo mb-2"),
                    class_="mb-3 d-flex flex-wrap align-items-center"
                ),

                # Prompt Input Bar
                ui.div(
                    ui.input_text("m4_user_query", None, value="Which 5 stores had the highest OOS rate in the Northeast?", placeholder="Ask a question about sales, stockouts, promotions, or commercial policies..."),
                    ui.input_action_button("m4_btn_ask", "Query Copilot ↵", class_="btn-luxury-primary ms-2"),
                    class_="d-flex mb-2"
                ),

                class_="sleek-card dashboard-hero-card mb-4"
            ),

            # Output Grid: Response Answer & Generated SQL
            ui.div(
                # Natural Language Answer & Document Citations
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Assistant Response & Commercial Grounding", class_="chart-card-title"),
                            class_="chart-header-row mb-3"
                        ),
                        ui.output_ui("m4_copilot_response"),
                        class_="sleek-card p-4 h-100"
                    ),
                    class_="col-12 col-lg-6"
                ),

                # SQL Translation & Live Result Preview
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Generated SQL & Data Execution", class_="chart-card-title"),
                            class_="chart-header-row mb-3"
                        ),
                        ui.output_ui("m4_sql_preview"),
                        ui.hr(style="border-color:rgba(0,0,0,0.08);margin:16px 0;"),
                        ui.output_table("m4_query_results"),
                        class_="sleek-card p-4 h-100"
                    ),
                    class_="col-12 col-lg-6"
                ),

                class_="row g-4 mb-4"
            ),

            class_="app-container"
        )
    )
