"""
modules/module_1/ui.py
======================
UI layout for Module 1: Commercial Execution Analytics Dashboard.
Features:
- Left panel: Workflow card with portfolio filters (Date range, channels, regions, SKUs)
- Right panel: Commercial Execution Intelligence Hero Card, 5 KPI Tiles, and 4 Visual Sections.
"""

from typing import Dict, Any
from shiny import ui
from shinywidgets import output_widget
from shared.data import DEFAULT_START, DEFAULT_END


def module_1_ui(dataset: Dict[str, Any]) -> ui.Tag:
    """Renders the complete Module 1 workspace layout."""
    CH = dataset["channels"]
    REG = dataset["regions"]
    SKU_CHOICES = dataset["sku_choices"]
    prods = dataset["prods"]
    MIN_DATE = dataset["min_date"]
    MAX_DATE = dataset["max_date"]

    return ui.div(
        # Subtle Ambient Backdrop
        ui.div(class_="ambient-glow ambient-glow-1"),
        ui.div(class_="ambient-glow ambient-glow-2"),
        ui.div(class_="ambient-grid"),

        ui.div(
ui.tags.main(
    # -----------------------------------------------------
    # 1. WORKFLOW CARD -> PORTFOLIO FILTERS (.panel-left)
    # -----------------------------------------------------
    ui.tags.section(
        ui.div(
            # Card Header Row
            ui.div(
                ui.div(
                    ui.HTML("""
                    <svg class="header-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="4" y1="21" x2="4" y2="14"/>
                        <line x1="4" y1="10" x2="4" y2="3"/>
                        <line x1="12" y1="21" x2="12" y2="12"/>
                        <line x1="12" y1="8" x2="12" y2="3"/>
                        <line x1="20" y1="21" x2="20" y2="16"/>
                        <line x1="20" y1="12" x2="20" y2="3"/>
                        <line x1="1" y1="14" x2="7" y2="14"/>
                        <line x1="9" y1="8" x2="15" y2="8"/>
                        <line x1="17" y1="16" x2="23" y2="16"/>
                    </svg>
                    """),
                    ui.h2("PORTFOLIO FILTERS", class_="section-title"),
                    class_="header-title-box"
                ),
                ui.div(
                    ui.span("LIVE SCOPE", class_="live-clock-label"),
                    class_="live-clock-badge"
                ),
                class_="card-header-row"
            ),

            # Active Scope Badge
            ui.output_ui("sidebar_scope_badge"),
            ui.hr(class_="filter-divider"),

            # Date Range Selector
            ui.div(
                ui.input_date_range(
                    "dates",
                    "Date range (snaps to full weeks)",
                    start=DEFAULT_START,
                    end=DEFAULT_END,
                    min=MIN_DATE,
                    max=MAX_DATE
                ),
                class_="filter-group"
            ),

            # Channel Selector
            ui.div(
                ui.input_checkbox_group("channel", "Channel", CH, selected=CH),
                class_="filter-group"
            ),

            # Region Selector
            ui.div(
                ui.input_selectize("region", "Region", REG, selected=REG, multiple=True),
                class_="filter-group"
            ),

            # SKU Selector with Quick Action Buttons
            ui.div(
                ui.div(
                    ui.span("Product SKU (23 Available)", class_="control-label fw-bold", style="font-size:0.8rem;color:#1d1d1f;"),
                    ui.div(
                        ui.input_action_button("btn_sku_all", "Select All", class_="btn-sku-action"),
                        ui.input_action_button("btn_sku_none", "Deselect All", class_="btn-sku-action"),
                        class_="d-flex gap-1"
                    ),
                    class_="d-flex align-items-center justify-content-between mb-1"
                ),
                ui.input_selectize(
                    "sku",
                    None,
                    SKU_CHOICES,
                    selected=list(prods.sku_id),
                    multiple=True,
                    options={
                        "placeholder": "Type to search & select products...",
                        "plugins": ["remove_button"],
                        "closeAfterSelect": False
                    }
                ),
                class_="filter-group mb-2"
            ),

            ui.hr(class_="filter-divider"),

            # KPI Definitions Accordion
            ui.accordion(
                ui.accordion_panel(
                    "KPI Definitions & Formulas",
                    ui.tags.small(
                        ui.tags.ul(
                            ui.tags.li(
                                ui.tags.b("MSL Compliance %: "),
                                "COUNT(DISTINCT CASE WHEN non_compliant_stores = 0 THEN store_id END) / COUNT(DISTINCT store_id) * 100",
                                ui.tags.br(),
                                ui.tags.span("Source: fact_sell_out (or fact_inventory), dim_products · Target ≥ 90%", class_="text-muted", style="font-size:0.68rem;")
                            ),
                            ui.tags.li(
                                ui.tags.b("OOS Rate %: "),
                                "SUM(CASE WHEN is_out_of_stock = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
                                ui.tags.br(),
                                ui.tags.span("Source: fact_sell_out (or fact_inventory) · Target ≤ 5%", class_="text-muted", style="font-size:0.68rem;")
                            ),
                            ui.tags.li(
                                ui.tags.b("Sell-in vs Sell-out Gap %: "),
                                "(SUM(units_sell_in) - SUM(units_sold_daily)) / SUM(units_sold_daily) * 100",
                                ui.tags.br(),
                                ui.tags.span("Source: fact_sell_in, fact_sell_out · Target within ±5%", class_="text-muted", style="font-size:0.68rem;")
                            ),
                            ui.tags.li(
                                ui.tags.b("Shelf Share %: "),
                                "AVG(shelf_share_pct) grouped by category, channel, and region",
                                ui.tags.br(),
                                ui.tags.span("Source: fact_execution_audits · Target ≥ 30%", class_="text-muted", style="font-size:0.68rem;")
                            ),
                            ui.tags.li(
                                ui.tags.b("Planogram Compliance %: "),
                                "SUM(CASE WHEN planogram_compliance = TRUE THEN 1 ELSE 0 END) / COUNT(*) * 100",
                                ui.tags.br(),
                                ui.tags.span("Source: fact_execution_audits · Target ≥ 80%", class_="text-muted", style="font-size:0.68rem;")
                            ),
                            style="padding-left:14px;margin-bottom:0;"
                        )
                    )
                ),
                open=False,
                class_="sidebar-accordion"
            ),

            ui.div(
                ui.tags.small("Source: Marvel Enterprise Data Warehouse (dim_products, dim_stores, fact_execution_audits, fact_sell_in, fact_sell_out)"),
                class_="mt-3 text-muted",
                style="font-size:0.7rem;line-height:1.4;"
            ),
            class_="sleek-card workflow-card"
        ),
        class_="panel-left"
    ),

    # -----------------------------------------------------
    # 2. VOICE CHAT CARD -> DASHBOARD (KEEP GOING DOWN)
    # -----------------------------------------------------
    ui.tags.section(
        # Dashboard Top Hero Card (Header, Preset Chips, Scope, & 5-Metric KPI Row)
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML("""
                    <svg class="header-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
                        <line x1="8" y1="21" x2="16" y2="21"/>
                        <line x1="12" y1="17" x2="12" y2="21"/>
                    </svg>
                    """),
                    ui.h2("Commercial Execution Intelligence", class_="section-title"),
                    class_="header-title-box"
                ),
                ui.div(
                    ui.span("LIVE TELEMETRY STREAM", class_="badge-telemetry-live"),
                    class_="header-actions"
                ),
                class_="card-header-row mb-2"
            ),

            # Presets Tray (modeled from Amirlan prompt-chips-tray)
            ui.div(
                ui.span("Quick Focus:", class_="tray-label"),
                ui.span("All Portfolio Channels", class_="prompt-chip chip-active"),
                ui.span("Modern Trade Hypermarkets", class_="prompt-chip"),
                ui.span("Traditional Trade Bodegas", class_="prompt-chip"),
                ui.span("Mandatory MSL Priority", class_="prompt-chip"),
                ui.span("High OOS Risk", class_="prompt-chip chip-alert"),
                ui.span("Contractual Guardrails", class_="prompt-chip"),
                class_="prompt-chips-tray"
            ),

            # Live Context Scope Bar
            ui.output_ui("ctx"),

            # 5-Metric Horizontal KPI Grid
            ui.output_ui("kpi_row"),

            class_="sleek-card dashboard-hero-card"
        ),

        # ==================== 1. COMMERCIAL STRATEGY & GEOSPATIAL ====================
        ui.div(
            ui.div(
                ui.span("COMMERCIAL STRATEGY & GEOSPATIAL EXECUTION", class_="section-badge badge-geo"),
                ui.h5("Geospatial Territory Health & Multi-Stage Inventory Velocity", class_="section-heading"),
                ui.p("Target Users: Regional Manager & Commercial Operations · Allocates field rep routes and synchronizes channel Sell-In with store Sell-Out.", class_="section-subheading"),
                class_="section-header-block"
            ),
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    # Regional Execution Map: Full-width sleek-card
                    ui.div(
                        ui.div(
                            ui.div(
                                ui.span("Regional Execution vs Revenue Performance Map (Choropleth)", class_="chart-card-title"),
                                ui.div(
                                    ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                    "Combines state-level invoiced sales from fact_sell_in, planogram adherence from fact_execution_audits, and store in-stock rates from fact_sell_out into a composite commercial health score across US territory states.",
                                    class_="chart-logic-desc"
                                )
                            ),
                            ui.input_radio_buttons("c9_metric", None, {"health_score": "Health Score", "net_sales": "Net Sales ($)"}, selected="health_score", inline=True),
                            class_="chart-header-row"
                        ),
                        output_widget("chart9_geo_map"),
                        class_="sleek-card chart-sleek-card mb-4"
                    ),
                    # Inventory Velocity Sankey: Full-width sleek-card
                    ui.div(
                        ui.div(
                            ui.div(
                                ui.span("Sell-In vs Sell-Out Inventory Velocity (Sankey Flow Diagram)", class_="chart-card-title"),
                                ui.div(
                                    ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                    "Visualizes multi-echelon supply flow from product category to trade channels using fact_sell_in invoiced volume and tracks throughput into consumer offtake versus channel holding buffer using fact_sell_out daily units.",
                                    class_="chart-logic-desc"
                                ),
                                class_="d-flex flex-column"
                            ),
                            class_="chart-header-row"
                        ),
                        output_widget("chart10_sankey_velocity"),
                        class_="sleek-card chart-sleek-card mb-4"
                    )
                )
            ),
            ui.panel_conditional(
                "input.user_role_state == 'rep'",
                ui.div(
                    ui.HTML("""
                    <div style="width:52px;height:52px;border-radius:16px;background:rgba(255,59,48,0.08);display:inline-flex;align-items:center;justify-content:center;margin-bottom:14px;border:1px solid rgba(255,59,48,0.2);">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                    </div>
                    """),
                    ui.h5("Regional Manager Access Required", class_="fw-bold mb-2", style="color:#1d1d1f;"),
                    ui.div(
                        "Commercial Strategy & Geospatial Execution (Regional Execution Choropleth Map & Multi-Stage Inventory Velocity Sankey) is restricted to Regional Commercial Directors and Supply Chain Operations. Field Sales Representatives have route access scoped to their assigned territory.",
                        class_="text-muted small mb-3",
                        style="max-width:540px;margin:0 auto;line-height:1.5;"
                    ),
                    ui.span("Active Account Role: Field Sales Representative", class_="badge bg-light text-secondary border px-3 py-2 rounded-pill"),
                    class_="sleek-card text-center p-5 mb-4"
                )
            ),
            class_="dashboard-section"
        ),

        # ==================== 2. OOS & SUPPLY CHAIN INSIGHTS ====================
        ui.div(
            ui.div(
                ui.span("OUT-OF-STOCK (OOS) & SUPPLY CHAIN INSIGHTS", class_="section-badge badge-oos"),
                ui.h5("Real-Time Store Inventory Radar & Lost Sales Mitigation", class_="section-heading"),
                ui.p("Target Users: Field Sales Representative & Regional Operations · Tracks mandatory MSL items, highlights impending stockouts, and quantifies commercial loss.", class_="section-subheading"),
                class_="section-header-block"
            ),
            # Stock Level & OOS Heatmap: Full-width sleek-card
            ui.div(
                ui.div(
                    ui.div(
                        ui.span("Stock Level & OOS Hazard Heatmap (Mandatory MSL Items · Red = Active OOS · Amber = Impending Stockout Hazard)", class_="chart-card-title"),
                        ui.div(
                            ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                            "Built from fact_sell_out daily on-hand store inventory levels across mandatory MSL items from dim_products, mapping zero inventory units to high-priority red out-of-stock hazard alerts and low buffers (1–15 units) to amber warnings on the latest date.",
                            class_="chart-logic-desc"
                        )
                    ),
                    ui.span("Target: Field Sales Rep Pre-Visit Radar", class_="badge bg-light text-secondary border"),
                    class_="chart-header-row"
                ),
                output_widget("chart1_oos_hazard"),
                class_="sleek-card chart-sleek-card mb-4"
            ),
            # Lost Sales Trend: Full-width sleek-card
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Estimated Lost Sales Trend & Cumulative Loss (Stacked Area Chart)", class_="chart-card-title"),
                            ui.div(
                                ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                "Created by joining fact_sell_out and dim_products, aggregating weekly lost revenue by multiplying active out-of-stock store-SKU days by their in-stock average daily unit baseline and list price.",
                                class_="chart-logic-desc"
                            )
                        ),
                        ui.input_radio_buttons("c2_group", None, {"region": "By Region", "category": "By Category"}, selected="region", inline=True),
                        class_="chart-header-row"
                    ),
                    output_widget("chart2_lost_sales"),
                    class_="sleek-card chart-sleek-card mb-4"
                )
            ),
            # OOS Duration: Full-width sleek-card
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("OOS Duration Distribution (Boxplot)", class_="chart-card-title"),
                            ui.div(
                                ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                "Calculated from chronological store-SKU-date observations in fact_sell_out, measuring consecutive days in out-of-stock state to distinguish transient single-day spikes from persistent replenishment delays across channels and tiers.",
                                class_="chart-logic-desc"
                            )
                        ),
                        ui.input_radio_buttons("c3_group", None, {"channel": "By Channel", "segment_tier": "By Tier"}, selected="channel", inline=True),
                        class_="chart-header-row"
                    ),
                    output_widget("chart3_oos_duration"),
                    class_="sleek-card chart-sleek-card mb-4"
                )
            ),
            ui.panel_conditional(
                "input.user_role_state == 'rep'",
                ui.div(
                    ui.HTML("""
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <span style="font-size:0.95rem;">🔒</span>
                        <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Macro Financial & Replenishment Analytics Restricted</span>
                    </div>
                    """),
                    ui.div(
                        "Estimated Lost Sales Trend & OOS Duration Distribution are restricted to Regional Management & Operations oversight. Field Sales Representatives have operational access to the store-level Stock Level & OOS Hazard Radar above.",
                        class_="small text-muted"
                    ),
                    class_="sleek-card p-3 mb-4"
                )
            ),
            class_="dashboard-section"
        ),

        # ==================== 3. SHELF EXECUTION & COMPLIANCE ====================
        ui.div(
            ui.div(
                ui.span("SHELF EXECUTION & COMPLIANCE OPTIMIZATION", class_="section-badge badge-shelf"),
                ui.h5("Planogram, MSL Availability & Shelf Real Estate Health", class_="section-heading"),
                ui.p("Target Users: Field Sales Rep & Regional Manager · Converts store audit compliance into revenue-generating shelf real estate.", class_="section-subheading"),
                class_="section-header-block"
            ),
            # Planogram & MSL Compliance: Full-width sleek-card
            ui.div(
                ui.div(
                    ui.div(
                        ui.span("Planogram & MSL Compliance Gauge Matrix (Target Benchmark ≥ 85% vs Historical Baseline 62%)", class_="chart-card-title"),
                        ui.div(
                            ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                            "Combines planogram compliance adherence rates from fact_execution_audits with in-stock MSL product availability from fact_sell_out, grouped by product category and brand against the 85% target benchmark.",
                            class_="chart-logic-desc"
                        )
                    ),
                    ui.span("Actionable Store-Fix Checklist", class_="badge bg-light text-secondary border"),
                    class_="chart-header-row"
                ),
                output_widget("chart4_compliance_gauge"),
                class_="sleek-card chart-sleek-card mb-4"
            ),
            # Shelf Share Bubble: Full-width sleek-card
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Actual Shelf Share vs. Target Bubble Plot", class_="chart-card-title"),
                            ui.div(
                                ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                "Plots store-level average category shelf share from fact_execution_audits against total invoiced net sales from fact_sell_in, sizing bubbles by commercial segment tier from dim_stores to highlight revenue conversion vs. the 30% shelf target.",
                                class_="chart-logic-desc"
                            )
                        ),
                        class_="chart-header-row"
                    ),
                    output_widget("chart5_shelf_bubble"),
                    class_="sleek-card chart-sleek-card mb-4"
                )
            ),
            # Execution Scorecard Radar: Full-width sleek-card
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Multi-Dimensional Execution Scorecard (Radar Chart)", class_="chart-card-title"),
                            ui.div(
                                ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                "Synthesizes 5 key execution dimensions across fact_execution_audits and fact_sell_out (planogram adherence, MSL availability, secondary displays, shelf share %, and general in-stock rate) grouped by territory region or channel.",
                                class_="chart-logic-desc"
                            )
                        ),
                        ui.input_radio_buttons("c6_group", None, {"region": "By Region", "segment_tier": "By Tier", "channel": "By Channel"}, selected="region", inline=True),
                        class_="chart-header-row"
                    ),
                    output_widget("chart6_radar_scorecard"),
                    class_="sleek-card chart-sleek-card mb-4"
                )
            ),
            ui.panel_conditional(
                "input.user_role_state == 'rep'",
                ui.div(
                    ui.HTML("""
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <span style="font-size:0.95rem;">🔒</span>
                        <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Territory-Wide Shelf Share & Multi-KPI Scorecard Restricted</span>
                    </div>
                    """),
                    ui.div(
                        "Actual Shelf Share vs. Target Bubble Plot & Multi-Dimensional Radar Scorecard are restricted to Regional Management oversight. Field Sales Representatives have store audit access via the Planogram & MSL Compliance Checklist above.",
                        class_="small text-muted"
                    ),
                    class_="sleek-card p-3 mb-4"
                )
            ),
            class_="dashboard-section"
        ),

        # ==================== 4. TRADE TERMS & NEGOTIATIONS ====================
        ui.div(
            ui.div(
                ui.span("TRADE TERMS & COMMERCIAL GUARDRAILS", class_="section-badge badge-trade"),
                ui.h5("Real-Time Negotiation Boundaries & Concession Enforcement", class_="section-heading"),
                ui.p("Target Users: Field Sales Rep & Regional Manager · Eliminates data-free negotiations, flags policy-breaching discounts, and verifies execution concessions.", class_="section-subheading"),
                class_="section-header-block"
            ),
            # Discount vs Margin: Full-width sleek-card
            ui.div(
                ui.div(
                    ui.div(
                        ui.span("Effective Discount vs Margin Realization Scatter Plot with Guardrails", class_="chart-card-title"),
                        ui.div(
                            ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                            "Integrates weekly transaction revenue and cost from fact_sell_in with dim_guardrails commercial caps, comparing effective invoice discount percentage against gross margin to flag policy-breaching transactions requiring managerial override.",
                            class_="chart-logic-desc"
                        )
                    ),
                    class_="chart-header-row"
                ),
                output_widget("chart7_discount_margin"),
                class_="sleek-card chart-sleek-card mb-4"
            ),
            # Trade-off Compliance: Full-width sleek-card
            ui.panel_conditional(
                "input.user_role_state == 'manager'",
                ui.div(
                    ui.div(
                        ui.div(
                            ui.span("Trade-off Execution Commitment Compliance (Grouped Bar Chart)", class_="chart-card-title"),
                            ui.div(
                                ui.span("Data & Logic: ", class_="fw-semibold text-dark"),
                                "Evaluates customer concession fulfillment by comparing audit observations in fact_execution_audits (shelf share %, secondary displays, planogram rates) against required retailer trade-off commitments defined in dim_guardrails.",
                                class_="chart-logic-desc"
                            )
                        ),
                        class_="chart-header-row"
                    ),
                    output_widget("chart8_tradeoff_compliance"),
                    class_="sleek-card chart-sleek-card mb-4"
                )
            ),
            ui.panel_conditional(
                "input.user_role_state == 'rep'",
                ui.div(
                    ui.HTML("""
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <span style="font-size:0.95rem;">🔒</span>
                        <span class="fw-bold" style="font-size:0.82rem;color:#1d1d1f;">Contractual Concession Oversight Restricted</span>
                    </div>
                    """),
                    ui.div(
                        "Trade-off Execution Commitment Compliance evaluates contractual concessions and required trade-offs across regional accounts and is restricted to Regional Management. Field Sales Reps have real-time negotiation boundary guidance via the Effective Discount vs Margin Realization chart above.",
                        class_="small text-muted"
                    ),
                    class_="sleek-card p-3 mb-4"
                )
            ),
            class_="dashboard-section"
        ),
        class_="panel-right"
    ),
    class_="workspace-grid"
),
            class_="app-container"
        )
    )
