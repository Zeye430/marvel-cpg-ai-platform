"""
modules/module_3/ui.py
======================
UI layout for Module 3: Commercial Negotiation Simulator.
"""

from typing import Dict, Any
from shiny import ui
from shinywidgets import output_widget


def module_3_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Renders the Commercial Negotiation Simulator interface."""
    CH = dataset["channels"]
    SKU_CHOICES = dataset["sku_choices"]
    prods = dataset["prods"]

    categories = sorted(prods["category"].unique().tolist())

    return ui.div(
        ui.div(
            # Hero Card
            ui.div(
                ui.div(
                    ui.div(
                        ui.HTML("""
                        <svg class="header-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="12" y1="1" x2="12" y2="23"/>
                            <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
                        </svg>
                        """),
                        ui.h2("Commercial Negotiation Simulator", class_="section-title"),
                        class_="header-title-box"
                    ),
                    ui.div(
                        ui.span("POLICY GUARDRAIL ENGINE", class_="badge-telemetry-live"),
                        class_="header-actions"
                    ),
                    class_="card-header-row mb-2"
                ),
                ui.p(
                    "Simulate trade terms, test discount proposals against contractual guardrails (dim_guardrails), "
                    "and calculate margin realization before agreeing to retailer concessions.",
                    class_="text-muted small mb-3"
                ),
                class_="sleek-card dashboard-hero-card mb-4"
            ),

            # Simulator Grid: Left Controls, Right Outcomes
            ui.div(
                # Left Column: Scenario Parameters
                ui.div(
                    ui.div(
                        ui.h5("Proposed Deal Terms", class_="fw-bold mb-3", style="color:#1d1d1f;"),

                        ui.div(
                            ui.input_select("m3_channel", "Retail Account Channel:", CH, selected="Modern Trade"),
                            class_="mb-3"
                        ),

                        ui.div(
                            ui.input_select("m3_category", "Product Category:", categories, selected="Surface Cleaners"),
                            class_="mb-3"
                        ),

                        ui.div(
                            ui.input_select("m3_sku", "Target SKU:", SKU_CHOICES, selected=list(prods.sku_id)[0]),
                            class_="mb-3"
                        ),

                        ui.div(
                            ui.input_numeric("m3_volume", "Order Quantity (Units):", value=5000, min=100, max=500000, step=500),
                            class_="mb-3"
                        ),

                        ui.div(
                            ui.input_slider("m3_discount", "Proposed Discount %:", min=0.0, max=35.0, value=12.0, step=0.5, post="%"),
                            class_="mb-4"
                        ),

                        ui.div(
                            ui.span("Required Retailer Execution Commitments:", class_="control-label fw-bold mb-2 d-block", style="font-size:0.82rem;"),
                            ui.input_checkbox("m3_tradeoff_shelf", "Retailer Commits to +15% Shelf Share (Frontage)", value=True),
                            ui.input_checkbox("m3_tradeoff_endcap", "Retailer Commits to Secondary Feature Display (Endcap)", value=False),
                            class_="p-3 mb-3",
                            style="background:rgba(89,210,254,0.06);border:1px solid rgba(89,210,254,0.2);border-radius:12px;"
                        ),

                        class_="sleek-card p-4"
                    ),
                    class_="col-12 col-lg-5"
                ),

                # Right Column: Financial Impact & Policy Verification
                ui.div(
                    ui.div(
                        # Guardrail Approval Status Banner
                        ui.output_ui("m3_approval_banner"),

                        # Financial Metric Cards
                        ui.div(
                            ui.output_ui("m3_financial_metrics"),
                            class_="mb-4"
                        ),

                        # Scenario Comparison Chart
                        ui.div(
                            ui.div(
                                ui.span("Deal Economics: Base List vs Proposed Invoiced Realization", class_="chart-card-title"),
                                class_="chart-header-row mb-2"
                            ),
                            output_widget("chart_m3_waterfall"),
                            class_="sleek-card chart-sleek-card"
                        ),

                        class_="d-flex flex-column gap-3"
                    ),
                    class_="col-12 col-lg-7"
                ),

                class_="row g-4 mb-4"
            ),

            class_="app-container"
        )
    )
