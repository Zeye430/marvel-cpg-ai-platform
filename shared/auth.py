"""
shared/auth.py
==============
Role-based authentication, user profiles, session state, and luxury login view.
Supports Regional Commercial Director (Elena Rostova) and 6 Field Sales Representatives.
"""

from typing import Dict, Any, Optional
from shiny import ui, reactive, render
from shared.data import REP_PROFILES, MANAGER_PROFILE


def login_ui(logo_path: str = "marvel_logo.jpg") -> ui.Tag:
    """Renders the executive brand login screen in Apple Pro silver light theme."""
    rep_choices = {
        rep_id: f"{prof['name']} — {prof['region']} ({rep_id})"
        for rep_id, prof in REP_PROFILES.items()
    }
    return ui.div(
        ui.div(
            ui.div(
                # Theme Branded Monogram Crest
                ui.div(
                    ui.HTML(f"""
                    <div class="brand-crest">
                        <img src="{logo_path}" alt="Marvel CPG" />
                    </div>
                    """),
                    class_="text-center"
                ),
                ui.h1("MARVEL CPG", class_="brand-title text-center"),
                ui.div("COMMERCIAL EXECUTION INTELLIGENCE", class_="brand-subtitle text-center"),

                # Role Selection Tabs (segmented switch)
                ui.div(
                    ui.input_radio_buttons(
                        "login_role_tab",
                        None,
                        {"manager": "👔  Regional Manager", "rep": "📱  Field Representative"},
                        selected="manager",
                        inline=True
                    ),
                    class_="role-tab-container"
                ),

                # Panel for Regional Manager Login
                ui.panel_conditional(
                    "input.login_role_tab == 'manager'",
                    ui.div(
                        ui.div(
                            ui.div("ER", class_="profile-avatar"),
                            ui.div(
                                ui.div("Elena Rostova", class_="fw-bold fs-6", style="color:#1d1d1f;"),
                                ui.div("Director of Commercial Operations & Execution", class_="small", style="color:#6e6e73;"),
                                ui.div("Executive Scope: All 221 Stores · 4 Regions · 23 SKUs", class_="small", style="color:#4a8fe7;font-weight:600;"),
                            ),
                            class_="d-flex align-items-center gap-3 profile-preview-card"
                        ),
                        ui.div(
                            ui.span("Enterprise SSO Identity", class_="luxury-input-label"),
                            ui.input_text("login_email", None, value="elena.rostova@marvelcpg.com"),
                            class_="mb-3"
                        ),
                        ui.div(
                            ui.span("Security Access Key", class_="luxury-input-label"),
                            ui.input_password("login_password", None, value="••••••••••••"),
                            class_="mb-4"
                        ),
                        ui.input_action_button(
                            "btn_login_manager",
                            "Enter Executive Command Suite →",
                            class_="btn-luxury-primary"
                        ),
                    )
                ),

                # Panel for Field Representative Login
                ui.panel_conditional(
                    "input.login_role_tab == 'rep'",
                    ui.div(
                        ui.div(
                            ui.span("Representative Account", class_="luxury-input-label"),
                            ui.input_select(
                                "login_rep_choice",
                                None,
                                rep_choices,
                                selected="Rep A"
                            ),
                            class_="mb-3"
                        ),
                        ui.div(
                            ui.div("FR", class_="profile-avatar rep"),
                            ui.div(
                                ui.output_ui("login_rep_preview_details")
                            ),
                            class_="d-flex align-items-center gap-3 profile-preview-card mb-3"
                        ),
                        ui.div(
                            ui.span("Territory Access PIN", class_="luxury-input-label"),
                            ui.input_password("login_rep_pin", None, value="••••"),
                            class_="mb-4"
                        ),
                        ui.input_action_button(
                            "btn_login_rep",
                            "Access Territory Field Suite →",
                            class_="btn-luxury-primary"
                        ),
                    )
                ),

                # Quick 1-Click Access Row for fast evaluation
                ui.div(
                    ui.div(
                        ui.hr(style="border-color:rgba(0,0,0,0.08);margin:24px 0 16px 0;"),
                        ui.div("QUICK DEMO ONE-CLICK SIGN IN", class_="text-center mb-2",
                               style="font-size:0.68rem;letter-spacing:0.12em;font-weight:700;color:#86868b;"),
                        ui.div(
                            ui.input_action_button("quick_mgr", "⚡ Manager View (All Stores)", class_="btn-quick-demo flex-fill"),
                            ui.input_action_button("quick_rep", "⚡ Field Rep (Alex Mercer · Rep A)", class_="btn-quick-demo flex-fill"),
                            class_="d-flex gap-2"
                        )
                    )
                ),

                # Security Footnote
                ui.div(
                    ui.HTML("""
                    <div class="mt-4 pt-2 text-center" style="font-size:0.7rem;color:#86868b;">
                        <span>🔒 SOC-2 Type II Certified · 256-bit TLS · Live Marvel CPG Warehouse</span>
                    </div>
                    """)
                ),

                class_="luxury-login-card"
            ),
            class_="luxury-login-wrapper"
        ),
        class_="luxury-login-backdrop"
    )


def auth_server(input, output, session, stores_df) -> Dict[str, Any]:
    """Manages login events, reactive user profile state, and session controls."""
    active_role = reactive.value("Regional Manager")
    active_rep = reactive.value(None)
    active_name = reactive.value(MANAGER_PROFILE["name"])
    active_title = reactive.value(MANAGER_PROFILE["title"])
    # Server-side session identity for data scoping (demo identities only; not
    # production authentication). Signed out until a login handler runs.
    principal_id = reactive.value(None)
    authenticated = reactive.value(False)
    is_demo = reactive.value(True)

    def _sign_in(principal):
        principal_id.set(principal)
        authenticated.set(True)

    @reactive.effect
    @reactive.event(input.btn_login_manager)
    def _do_login_mgr():
        active_role.set("Regional Manager")
        active_rep.set(None)
        active_name.set(MANAGER_PROFILE["name"])
        active_title.set(MANAGER_PROFILE["title"])
        _sign_in("demo:manager")
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="manager")

    @reactive.effect
    @reactive.event(input.btn_login_rep)
    def _do_login_rep():
        rep_id = input.login_rep_choice() if input.login_rep_choice() else "Rep A"
        prof = REP_PROFILES.get(rep_id, REP_PROFILES["Rep A"])
        active_role.set("Field Rep")
        active_rep.set(rep_id)
        active_name.set(prof["name"])
        active_title.set(f"{prof['title']} ({prof['region']})")
        _sign_in("demo:rep-" + (active_rep() or "Rep A").split()[-1].lower())
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="rep")

    @reactive.effect
    @reactive.event(input.quick_mgr)
    def _quick_mgr():
        active_role.set("Regional Manager")
        active_rep.set(None)
        active_name.set(MANAGER_PROFILE["name"])
        active_title.set(MANAGER_PROFILE["title"])
        _sign_in("demo:manager")
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="manager")

    @reactive.effect
    @reactive.event(input.quick_rep)
    def _quick_rep():
        prof = REP_PROFILES["Rep A"]
        active_role.set("Field Rep")
        active_rep.set("Rep A")
        active_name.set(prof["name"])
        active_title.set(f"{prof['title']} ({prof['region']})")
        _sign_in("demo:rep-" + (active_rep() or "Rep A").split()[-1].lower())
        ui.update_text("auth_state", value="logged_in")
        ui.update_text("user_role_state", value="rep")

    @reactive.effect
    @reactive.event(input.btn_logout)
    def _do_logout():
        principal_id.set(None)
        authenticated.set(False)
        ui.update_text("auth_state", value="logged_out")

    @render.ui
    def login_rep_preview_details():
        rep_id = input.login_rep_choice() if input.login_rep_choice() else "Rep A"
        prof = REP_PROFILES.get(rep_id, REP_PROFILES["Rep A"])
        rep_store_count = (stores_df["rep_id"] == rep_id).sum() if stores_df is not None else 0
        return ui.div(
            ui.div(prof["name"], class_="fw-bold fs-6", style="color:#1d1d1f;"),
            ui.div(f"{prof['title']} · {prof['region']}", class_="small", style="color:#6e6e73;"),
            ui.div(f"Territory Scope: {rep_store_count} Assigned Stores · {prof['badge']}",
                   class_="small", style="color:#0b594b;font-weight:600;")
        )

    @render.ui
    def nav_user_badge():
        is_rep = active_role() == "Field Rep"
        role_label = f"Field Representative · {active_rep()}" if is_rep else "Regional Manager"
        badge_style = "background:rgba(115,251,211,0.22);border:1px solid rgba(115,251,211,0.6);color:#064e43;" if is_rep else "background:rgba(74,143,231,0.15);border:1px solid rgba(74,143,231,0.4);color:#2466b8;"
        return ui.div(
            ui.div(
                ui.span("👤", style="font-size:0.95rem;"),
                ui.div(
                    ui.div(active_name(), class_="fw-bold", style="font-size:0.84rem;line-height:1.2;color:#1d1d1f;"),
                    ui.div(active_title(), class_="text-muted", style="font-size:0.72rem;line-height:1.1;color:#6e6e73;"),
                ),
                class_="d-flex align-items-center gap-2"
            ),
            ui.span(role_label, class_="badge rounded-pill ms-2", style=f"{badge_style}font-size:0.72rem;padding:4px 10px;"),
            class_="user-session-chip"
        )

    return {
        "role": active_role,
        "rep": active_rep,
        "name": active_name,
        "title": active_title,
        "principal_id": principal_id,
        "authenticated": authenticated,
        "is_demo": is_demo,
    }
