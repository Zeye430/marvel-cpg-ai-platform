"""Standalone local Module 2 demo: serving candidate only, demo identities only.

Run: python -m modules.module_2 demo --serving-root <isolated candidate root>
(or MODULE2_SERVING_ROOT=<root> shiny run modules/module_2/demo_app.py).

Sign-in buttons select fixed *demo* principals on the server; the browser never
supplies a principal ID or role. This is a local review aid, not production
authentication. It never imports shared.data or reads raw CSVs. With a serving
root it is fully offline (local DuckDB). Only an explicit
SHARED_DATA_BACKEND=postgres (and no serving root) makes it read the shared
PostgreSQL release configured by SHARED_DATA_DATABASE_URL.
"""

import os
from pathlib import Path

from htmltools import tags
from shiny import App, reactive, render, ui

from .server import configured_service, register
from .service import Principal
from .ui import LOGO, font_dependency, module_2_ui

DEMO_ACCOUNTS = {
    "m2_demo_rep_a": ("demo:rep-a", "Sales Rep A (demo)", "business"),
    "m2_demo_rep_b": ("demo:rep-b", "Sales Rep B (demo)", "business"),
    "m2_demo_manager": ("demo:manager", "Manager, all stores (demo)", "business"),
    # Separate entry; reuses the demo manager's data permission (not production authentication).
    "m2_demo_ops": ("demo:manager", "ML Operations (demo)", "ops"),
}


def app_ui(request=None):
    buttons = [ui.input_action_button(key, f"Sign in as {label}", class_="m2-btn" + (" primary" if i == 0 else ""))
               for i, (key, (_, label, workspace)) in enumerate(DEMO_ACCOUNTS.items()) if workspace == "business"]
    ops = ui.input_action_button("m2_demo_ops", "Open ML Operations (demo)", class_="m2-btn")
    return ui.page_fluid(
        ui.busy_indicators.use(),
        ui.tags.title("Module 2 · OOS risk (local demo)"),
        ui.tags.meta(name="viewport", content="width=device-width, initial-scale=1"),
        font_dependency(),
        ui.div(ui.output_text("m2_demo_signed_in"), style="display:none", id="m2-demo-flag"),
        ui.panel_conditional(
            "output.m2_demo_signed_in !== 'yes'",
            ui.div(ui.div(
                ui.div(LOGO, ui.div(tags.strong("Marvel CPG"), tags.span("Module 2 · Stockout risk"),
                                    class_="m2-logo-text"), class_="m2-logo"),
                tags.h1("Module 2 local demo", class_="m2-title"),
                tags.p("Choose a demo identity. These are generated demo assignments (round-robin stores to Reps A–F; "
                       "manager has all stores), not client territories or production sign-in.", class_="m2-sub"),
                ui.div(*buttons, class_="m2-roles"),
                tags.p("Model quality reports are a separate, read-only operations page.", class_="m2-sub",
                       style="margin-top:16px"),
                ui.div(ops, class_="m2-roles"), class_="m2-card m2-login", id="m2-login"), class_="m2")),
        ui.panel_conditional("output.m2_demo_signed_in === 'yes'", module_2_ui({})),
        style="padding:0;max-width:100%;overflow-x:clip")


def make_server(service, reason):
    def server(input, output, session):
        principal = reactive.value(None)
        label = reactive.value("")
        workspace = reactive.value("business")

        for key, (principal_id, text, space) in DEMO_ACCOUNTS.items():
            def bind(key=key, principal_id=principal_id, text=text, space=space):
                @reactive.effect
                @reactive.event(getattr(input, key))
                def _sign_in():
                    principal.set(Principal(principal_id, True, True, text))
                    label.set(text)
                    workspace.set(space)
            bind()

        def sign_out():
            """The demo's one server-side sign-out path (used by the Module 2 sign-out control)."""
            principal.set(None)
            label.set("")
            workspace.set("business")

        @output(suspend_when_hidden=False)
        @render.text
        def m2_demo_signed_in():
            return "yes" if principal() else "no"

        register(input, output, session, lambda: principal(), service, reason, workspace_fn=lambda: workspace(),
                 logout_fn=sign_out, identity_fn=lambda: (label(), None))
    return server


def build_app(serving_root=None):
    """A given ``serving_root`` always uses the offline DuckDB backend. Without it the
    backend follows SHARED_DATA_BACKEND (default duckdb); PostgreSQL is used only when
    that variable is explicitly set to ``postgres``."""
    if serving_root:
        os.environ["MODULE2_SERVING_ROOT"] = str(Path(serving_root).resolve())
    service, reason = configured_service("duckdb" if serving_root else None)
    return App(app_ui, make_server(service, reason))


def run(serving_root, *, host="127.0.0.1", port=8765):
    from shiny import run_app
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("The demo binds to loopback only")
    run_app(build_app(serving_root), host=host, port=port, launch_browser=False)


app = build_app()
