"""Integrated application shell: demo sign-in, module navigation and one shared source.

``build_app(specs, runtime)`` hosts any list of module specs (UI factory + server
function) in one Shiny app, in the shared workspace design (shared/ui_kit: Module 2's
design). One app sidebar lists every module by its functional name (MODULE_CATALOG);
modules not in the build keep their place but cannot be opened, and the active module's
own pages (``ModuleSpec.pages_nav``) are listed under it. Each module receives:

* ``context``: ``{"runtime": SharedRuntime, **host options, "hosted": True,
  "module_label": <its name>}``. Module services are bound to that runtime's single
  pinned reader, so every module shows the same publication / release and the
  principal's account scope from it.
* ``auth_state``: per session. ``authenticated``, ``principal_id``, ``role``, ``name``
  and ``title`` are reactive; ``is_demo`` is always true; ``logout()`` ends the session
  identity; ``navigate(key)`` switches the visible module (no data is passed along);
  ``active_module()`` (reactive) is the visible module's key, so a module can load on first visit.

DEMO ONLY: the sign-in buttons select fixed demo principals on the server. The
browser only reports which button was pressed, never a principal, role or store.
No password is checked; this is not production authentication. Store access comes
from the account scope configured for that principal in the shared data.

The shell never imports module business logic beyond the given specs and never
loads raw CSVs or legacy tables; an unavailable source is shown, not replaced.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from htmltools import HTML, tags
from shiny import App, reactive, render, ui

from shared import ui_kit as kit

SETUP_HINT = ("Configure one shared source: SHARED_DATA_PUBLICATION or SHARED_DATA_ARTIFACT_DIR for a local "
              "verified publication, or SHARED_DATA_BACKEND=postgres with SHARED_DATA_DATABASE_URL. "
              "See docs/integrated-app.md.")


@dataclass(frozen=True)
class ModuleSpec:
    key: str                                  # e.g. "module_2"; also the navigation value
    label: str                                # navigation text (the module's functional name)
    caption: str                              # e.g. "Module 2"
    ui: Callable[[dict], object]              # context -> UI
    server: Callable                          # (input, output, session, context, auth_state)
    pages_nav: Optional[Callable[[], object]] = None   # the module's own page links, shown under it in the sidebar


@dataclass(frozen=True)
class ModuleInfo:
    key: str
    number: int
    name: str
    description: str
    icon: str


# Every module of the workspace, named by what it does. A module without a spec in this build keeps
# its place in the sidebar but cannot be opened (its code is not imported).
MODULE_CATALOG = (
    ModuleInfo("module_1", 1, "Business Performance", "Sales, distribution and shelf KPIs for the business", "bars"),
    ModuleInfo("module_2", 2, "Stockout Risk", "Predicted stockouts and which stores to visit first", "shelf"),
    ModuleInfo("module_3", 3, "Negotiation Simulator", "Test deal terms against commercial guardrails", "scale"),
    ModuleInfo("module_4", 4, "Commercial Copilot", "Ask questions about your data and Marvel's documents", "spark"),
)
MODULE_NAMES = {m.key: m.name for m in MODULE_CATALOG}


@dataclass(frozen=True)
class DemoAccount:
    key: str
    principal_id: str
    role: str                                 # "Regional Manager" or "Field Rep" (module document access)
    name: str
    title: str


DEMO_ACCOUNTS = (
    DemoAccount("manager", "demo:manager", "Regional Manager", "Manager (demo)", "Regional manager · demo identity"),
    DemoAccount("rep_a", "demo:rep-a", "Field Rep", "Sales Rep A (demo)", "Field sales rep · demo identity"),
    DemoAccount("rep_b", "demo:rep-b", "Field Rep", "Sales Rep B (demo)", "Field sales rep · demo identity"),
)

_CSS = """
body { background: var(--ws-canvas); margin: 0; }
.ws .ws-shell-alert { margin: 16px 24px 0; }
@media (max-width: 640px) { .ws .ws-shell-alert { margin: 12px 16px 0; } }
"""

_SCRIPT = """
window.jQuery(document).on('shiny:connected', function () {
  if (window.__wsHandlers) { return; }
  window.__wsHandlers = true;
  // The active module: highlighted in the sidebar, and only its own pages are listed under it.
  Shiny.addCustomMessageHandler('ws-active', function (message) {
    document.querySelectorAll('#shell-nav button[data-module]').forEach(function (item) {
      if (item.getAttribute('aria-disabled') === 'true') { return; }
      var on = item.dataset.module === message.key;
      item.classList.toggle('active', on);
      if (on) { item.setAttribute('aria-current', 'page'); } else { item.removeAttribute('aria-current'); }
    });
    document.querySelectorAll('#shell-nav .ws-subnav[data-module]').forEach(function (box) {
      box.hidden = box.dataset.module !== message.key;
    });
  });
});
function wsOpenModule(key) { Shiny.setInputValue('shell_nav', key, {priority: 'event'}); }
// Phones: the sidebar is an overlay; choosing a destination closes it.
document.addEventListener('click', function (event) {
  var item = event.target.closest && event.target.closest('#shell_sidebar .ws-nav-item');
  if (!item || item.getAttribute('aria-disabled') === 'true' || !window.matchMedia('(max-width: 575.98px)').matches) { return; }
  var layout = item.closest('.bslib-sidebar-layout');
  var toggle = layout && layout.querySelector(':scope > .collapse-toggle');
  if (toggle && !layout.classList.contains('sidebar-collapsed')) { setTimeout(function () { toggle.click(); }, 0); }
});
"""


def _brand(subtitle="Commercial workspace"):
    return tags.div(kit.logo(), tags.div(tags.strong("Marvel CPG"), tags.span(subtitle), class_="ws-logo-text"),
                    class_="ws-logo")


def _catalog(specs):
    """Catalogue order, then any host-specific module not in the catalogue."""
    known = {m.key for m in MODULE_CATALOG}
    extra = [ModuleInfo(s.key, 0, s.label, s.caption, "list") for s in specs if s.key not in known]
    return list(MODULE_CATALOG) + extra


def _module_nav(specs):
    present = {s.key: s for s in specs}
    items = [tags.p("Modules", class_="ws-nav-label")]
    for info in _catalog(specs):
        spec = present.get(info.key)
        caption = f"Module {info.number}" if info.number else None
        if spec is None:
            items.append(kit.nav_item(info.icon, info.name, caption=caption, disabled=True, note="Soon",
                                      title=f"{info.description}. Not available in this build yet.",
                                      **{"data-module": info.key,
                                         "aria-label": f"{info.name}, {caption}: not available in this build yet"}))
            continue
        first = spec.key == specs[0].key
        items.append(kit.nav_item(info.icon, spec.label, caption=spec.caption, active=first, title=info.description,
                                  onclick=f"wsOpenModule('{spec.key}')", **{"data-module": spec.key}))
        if spec.pages_nav is not None:
            items.append(tags.div(spec.pages_nav(), class_="ws-subnav", hidden=None if first else "hidden",
                                  **{"data-module": spec.key}))
    return tags.nav(*items, class_="ws-nav-group", id="shell-nav", **{"aria-label": "Modules"})


def _sidebar(specs):
    other = tags.div(
        tags.p("Other", class_="ws-nav-label"),
        kit.nav_item("help", "Help", disabled=True, note="Soon", **{"aria-label": "Help — coming soon"}),
        kit.nav_item("settings", "Settings", disabled=True, note="Soon", **{"aria-label": "Settings — coming soon"}),
        class_="ws-nav-group")
    foot = tags.div(ui.output_ui("shell_source"), ui.output_ui("shell_identity"),
                    ui.input_action_button("shell_signout", HTML(f"{kit.icon('logout')}<span>Sign out</span>"),
                                           class_="ws-signout"),
                    class_="ws-sidebar-foot")
    return ui.sidebar(_brand(), _module_nav(specs), other, foot, id="shell_sidebar", open="desktop", width=248,
                      class_="ws-sidebar ws-controls", title=None)


def _app_ui(specs, contexts, title):
    accounts = [ui.input_action_button(f"shell_signin_{a.key}", f"Sign in as {a.name}",
                                       class_="ws-btn" + (" primary" if i == 0 else ""))
                for i, a in enumerate(DEMO_ACCOUNTS)]
    present = {s.key for s in specs}
    modules = tags.ul(*[tags.li(tags.strong(info.name), tags.span(
        f"Module {info.number} · {info.description}" if info.number else info.description,
        " (not in this build yet)" if info.key not in present else ""))
        for info in _catalog(specs)], class_="ws-module-list")
    login = tags.div(
        _brand(),
        tags.h1("Sign in", class_="ws-title"),
        tags.p("Demo sign-in only: choose a demo identity. No password is checked and this is not production "
               "authentication. Store access comes from the account scope configured for that identity in the "
               "shared data.", class_="ws-sub"),
        tags.div(*accounts, class_="ws-roles"),
        tags.p("Modules", class_="ws-label"),
        modules,
        class_="ws-card ws-login ws-controls", id="shell-login")
    panels = [ui.nav_panel(s.key, tags.div(s.ui(contexts[s.key]), id=f"shell-panel-{s.key}")) for s in specs]
    main = tags.div(ui.output_ui("shell_alert"), ui.navset_hidden(*panels, id="shell_module", selected=specs[0].key),
                    class_="ws-main")
    workspace = tags.div(ui.layout_sidebar(_sidebar(specs), main, fillable=False, border=False, border_radius=False),
                         class_="ws-app", id="shell-app")
    return ui.page_fluid(
        ui.tags.head(ui.tags.title(title), ui.tags.meta(name="viewport", content="width=device-width, initial-scale=1"),
                     ui.tags.style(_CSS), ui.tags.script(_SCRIPT)),
        kit.kit_dependency(),
        ui.busy_indicators.use(),
        tags.div(ui.output_text("shell_signed_in"), class_="ws-hidden", id="shell-flag"),
        ui.panel_conditional("output.shell_signed_in !== 'yes'", login),
        ui.panel_conditional("output.shell_signed_in === 'yes'", workspace),
        class_="ws", style="padding:0;max-width:100%;overflow-x:clip")


def build_app(specs, runtime, *, title="Marvel CPG · Commercial workspace (demo)", host_options=None):
    """One Shiny app hosting ``specs`` on the single shared ``runtime``."""
    specs = tuple(specs)
    if not specs or len({s.key for s in specs}) != len(specs):
        raise ValueError("Provide module specs with distinct keys")
    keys = {s.key for s in specs}
    context = {"runtime": runtime, **(host_options or {})}
    # Per module: hosted in this shell (its sidebar is the app sidebar) and its functional name.
    contexts = {s.key: {**context, "hosted": True, "module_label": s.label} for s in specs}

    def server(input, output, session):
        account = reactive.value(None)
        active = reactive.value(specs[0].key)

        @reactive.calc
        def signed_in():
            return account() is not None

        def navigate(key):
            if key in keys and account() is not None:
                active.set(key)
                ui.update_navset("shell_module", selected=key)

        def logout():
            account.set(None)
            active.set(specs[0].key)
            ui.update_navset("shell_module", selected=specs[0].key)

        copilot_request = reactive.value(None)          # (question, sequence) from a module to the copilot

        auth_state = {
            "copilot_request": copilot_request if "module_4" in keys else None,
            "authenticated": signed_in,
            "principal_id": reactive.calc(lambda: account().principal_id if account() else None),
            "role": reactive.calc(lambda: account().role if account() else None),
            "name": reactive.calc(lambda: account().name if account() else None),
            "title": reactive.calc(lambda: account().title if account() else None),
            "is_demo": lambda: True,
            "logout": logout,
            "navigate": navigate,
            "active_module": lambda: active(),
        }

        for demo in DEMO_ACCOUNTS:
            def bind(demo=demo):
                @reactive.effect
                @reactive.event(input[f"shell_signin_{demo.key}"])
                def _sign_in():
                    account.set(demo)
                    active.set(specs[0].key)
                    ui.update_navset("shell_module", selected=specs[0].key)
            bind()

        @reactive.effect
        @reactive.event(input.shell_signout)
        def _sign_out():
            logout()

        @reactive.effect
        @reactive.event(input.shell_nav)
        def _nav():
            navigate(input.shell_nav())

        @reactive.effect
        async def _sidebar_state():
            """Sidebar highlight and page links follow the active module (also after each sign-in)."""
            key, _ = active(), account()
            await session.send_custom_message("ws-active", {"key": key})

        @output(suspend_when_hidden=False)
        @render.text
        def shell_signed_in():
            return "yes" if signed_in() else "no"

        @reactive.calc
        def source():
            account()                                   # re-check on every sign-in (the runtime retries)
            return runtime.status() if signed_in() else None

        @output(suspend_when_hidden=False)              # in the sidebar, closed by default on phones
        @render.ui
        def shell_source():
            status = source()
            if status is None:
                return None
            if not status["ready"]:
                return tags.div(tags.span(class_="ws-dot bad", **{"aria-hidden": "true"}),
                                tags.span("Shared data unavailable"), class_="ws-source", id="shell-source",
                                title=status["reason"], **{"data-ready": "no"})
            ident = status["identity"]
            kind = "Shared PostgreSQL" if ident.get("backend") == "postgres" else "Local publication"
            release = ident.get("release_id") or ident.get("artifact_id")
            return tags.div(tags.span(class_="ws-dot ok", **{"aria-hidden": "true"}),
                            tags.span(f"{kind} · data through {ident.get('as_of')}"), class_="ws-source",
                            id="shell-source", title=f"Source {release}",
                            **{"data-ready": "yes", "data-as-of": str(ident.get("as_of")), "data-release": str(release)})

        @render.ui
        def shell_alert():
            status = source()
            if status is None or status["ready"]:
                return None
            return tags.div(tags.strong(status["reason"] + ". "), SETUP_HINT,
                            " Questions about documents still work in the copilot where possible.",
                            class_="ws-banner bad ws-shell-alert", role="alert", id="shell-alert")

        @output(suspend_when_hidden=False)              # in the sidebar, closed by default on phones
        @render.ui
        def shell_identity():
            demo = account()
            if demo is None:
                return None
            return kit.user_block(demo.name, demo.title, class_="ws-user-card", id="shell-identity",
                                  **{"data-principal": demo.principal_id})

        for spec in specs:
            spec.server(input, output, session, contexts[spec.key], auth_state)

    return App(_app_ui(specs, contexts, title), server)


__all__ = ["DEMO_ACCOUNTS", "MODULE_CATALOG", "MODULE_NAMES", "ModuleSpec", "build_app", "SETUP_HINT"]
