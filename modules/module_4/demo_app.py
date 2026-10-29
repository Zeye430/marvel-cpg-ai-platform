"""Standalone local Module 4 demo: the copilot tab only, demo identities only.

Run from the repository root:
    uv run --extra app --extra module4 shiny run modules.module_4.demo_app:app --port 8766

Sign-in buttons select fixed *demo* principals on the server; the browser never
supplies a principal ID or role. This is a local review and presentation aid, not
production authentication. It never imports shared.data or reads raw CSVs: data
comes from the accepted business publication, answers from the local model.
"""

from pathlib import Path

from shiny import App, reactive, render, ui

from modules.module_4.server import module_4_server
from modules.module_4.ui import module_4_ui

WWW = Path(__file__).resolve().parents[2] / "www"
DEMO_ACCOUNTS = {
    "m4_demo_manager": ("demo:manager", "Regional Manager", "Regional Manager · all stores (demo)"),
    "m4_demo_rep_a": ("demo:rep-a", "Field Rep", "Field Rep A · assigned stores (demo)"),
    "m4_demo_rep_b": ("demo:rep-b", "Field Rep", "Field Rep B · assigned stores (demo)"),
}

app_ui = ui.page_fluid(
    ui.tags.head(ui.tags.link(rel="stylesheet", href="styles.css"),
                 ui.tags.title("Module 4 · Commercial Copilot (local demo)")),
    ui.div(
        ui.div(ui.span("Demo sign-in:", class_="tray-label"),
               *[ui.input_action_button(key, label, class_="btn-quick-demo")
                 for key, (_, _, label) in DEMO_ACCOUNTS.items()],
               ui.input_action_button("m4_demo_logout", "Sign out", class_="btn-quick-demo"),
               ui.output_text("m4_demo_who", inline=True),
               class_="d-flex flex-wrap align-items-center gap-2 p-3"),
        module_4_ui({}),
        style="max-width:1280px;margin:0 auto;",
    ),
)


def server(input, output, session):
    principal = reactive.value(None)
    authenticated = reactive.value(False)
    role = reactive.value("Field Rep")
    label = reactive.value("Signed out")
    auth_state = {"principal_id": principal, "authenticated": authenticated, "role": role,
                  "is_demo": lambda: True}

    for key, (principal_id, role_name, text) in DEMO_ACCOUNTS.items():
        def make(key=key, principal_id=principal_id, role_name=role_name, text=text):
            @reactive.effect
            @reactive.event(input[key])
            def _sign_in():
                role.set(role_name)
                label.set(f"Signed in: {text}")
                principal.set(principal_id)
                authenticated.set(True)
        make()

    @reactive.effect
    @reactive.event(input.m4_demo_logout)
    def _sign_out():
        principal.set(None)
        authenticated.set(False)
        label.set("Signed out")

    @render.text
    def m4_demo_who():
        return label()

    module_4_server(input, output, session, {}, auth_state)


app = App(app_ui, server, static_assets=str(WWW))
