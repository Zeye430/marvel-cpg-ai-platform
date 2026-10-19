"""
app.py
======
Marvel CPG AI Platform — Enterprise Commercial Execution Suite
Unified Application Shell orchestrating:
- Module 1: Commercial Execution Analytics Dashboard
- Module 2: Predictive Out-of-Stock Risk Engine (t+7 Days)
- Module 3: Commercial Negotiation & Guardrail Simulator
- Module 4: Commercial Copilot (Text-to-SQL & Grounded RAG)
"""

from pathlib import Path
from shiny import App, ui, reactive

# Shared Infrastructure
from shared.data import get_dataset, start_background_preload
from shared.auth import login_ui, auth_server

# Platform Modules
from modules.module_1 import module_1_ui, module_1_server
from modules.module_2 import module_2_ui, module_2_server
from modules.module_3 import module_3_ui, module_3_server
from modules.module_4 import module_4_ui, module_4_server

# Asynchronously start loading data so web server binds to $PORT immediately
start_background_preload()
dataset = get_dataset()

# Assets directory
WWW_DIR = Path(__file__).parent / "www"

app_ui = ui.page_fillable(
    # Hidden reactive auth state: 'logged_out' or 'logged_in', and user role: 'manager' or 'rep'
    ui.div(
        ui.input_text("auth_state", "", value="logged_out"),
        ui.input_text("user_role_state", "", value="manager"),
        style="display:none;"
    ),

    # Global head resources: Google Fonts and Light Silver Luxury Apple CSS & Shuffling JS
    ui.tags.head(
        ui.tags.link(rel="preconnect", href="https://fonts.googleapis.com"),
        ui.tags.link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin="anonymous"),
        ui.tags.link(
            href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap",
            rel="stylesheet"
        ),
        ui.tags.link(rel="stylesheet", href="styles.css"),
        ui.tags.script(src="app.js"),
    ),

    # 1. LOGIN SCREEN
    ui.panel_conditional(
        "input.auth_state == 'logged_out'",
        login_ui(logo_path="marvel_logo.jpg")
    ),

    # 2. AUTHENTICATED PLATFORM WORKSPACE
    ui.panel_conditional(
        "input.auth_state == 'logged_in'",
        ui.div(
            # Global Executive Header Navbar
            ui.tags.header(
                ui.div(
                    ui.div(
                        ui.HTML("""
                        <div class="brand-monogram">
                            <img src="marvel_logo.jpg" alt="Marvel CPG" />
                        </div>
                        """),
                        class_="brand-monogram-wrapper"
                    ),
                    ui.div(
                        ui.div(
                            ui.h1("MARVEL CPG", class_="brand-title"),
                            ui.span("COMMERCIAL AI 2.5", class_="version-tag"),
                            class_="brand-row"
                        ),
                        ui.p("Autonomous Commercial Execution Intelligence", class_="brand-tagline"),
                        class_="brand-info"
                    ),
                    class_="navbar-left"
                ),
                ui.div(
                    ui.div(
                        ui.span(class_="status-light pulse-dot"),
                        ui.span("LIVE WAREHOUSE CONNECTED", class_="status-text"),
                        class_="status-indicator"
                    ),
                    ui.div(
                        ui.span("REGION", class_="telemetry-label"),
                        ui.span("US-COMMERCIAL", class_="telemetry-val"),
                        class_="telemetry-chip d-none d-lg-inline-flex"
                    ),
                    ui.div(
                        ui.span("PLATFORM", class_="telemetry-label"),
                        ui.span("4 MODULES ACTIVE", class_="telemetry-val"),
                        class_="telemetry-chip d-none d-lg-inline-flex"
                    ),
                    ui.output_ui("nav_user_badge"),
                    ui.input_action_button("btn_logout", "Sign Out", class_="btn-luxury-logout ms-2"),
                    class_="navbar-right"
                ),
                class_="navbar executive-navbar"
            ),

            # Multi-Module Platform Navigation Tabs
            ui.div(
                ui.navset_tab(
                    ui.nav_panel(
                        "📊 Module 1: Execution Analytics",
                        module_1_ui(dataset),
                        value="tab_mod1"
                    ),
                    ui.nav_panel(
                        "🎯 Module 2: Predictive OOS Risk (t+7)",
                        module_2_ui(dataset),
                        value="tab_mod2"
                    ),
                    ui.nav_panel(
                        "💼 Module 3: Negotiation Simulator",
                        module_3_ui(dataset),
                        value="tab_mod3"
                    ),
                    ui.nav_panel(
                        "🤖 Module 4: Commercial Copilot (SQL & RAG)",
                        module_4_ui(dataset),
                        value="tab_mod4"
                    ),
                    id="platform_active_module"
                ),
                class_="platform-module-nav"
            ),
        )
    ),

    title="Marvel CPG | Commercial Execution Intelligence Platform"
)


def server(input, output, session):
    # Shared Authentication & Session Lifecycle
    auth_state = auth_server(input, output, session, dataset["stores"])

    # Register All 4 Platform Modules
    module_1_server(input, output, session, dataset, auth_state)
    module_2_server(input, output, session, dataset, auth_state)
    module_3_server(input, output, session, dataset, auth_state)
    module_4_server(input, output, session, dataset, auth_state)


app = App(app_ui, server, static_assets=str(WWW_DIR))
