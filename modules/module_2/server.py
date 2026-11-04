"""
modules/module_2/server.py
==========================
Module 2 server logic. Reads only the published Module 2 serving candidate via
the scoped service; the principal comes from server-side session state. No
training, labels, raw CSVs, random scores or fixed metrics.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

from htmltools import TagList, tags
from shiny import reactive, render, ui

from data_platform.access import DataUnavailable
from shared.chart_palette import CHART_COLORS, CHART_NEUTRAL

from .contracts import Module2Error
from . import business_priority as bp
from . import service as service_module
from .service import VIEW_LABELS, Module2Service, PermissionDenied, Principal, resolve_published

TARGETS = {"precision": 0.75, "recall": 0.70, "roc_auc": 0.80}
BUCKET_CLASS = {1: "b1", 2: "b2", 3: "b3", 4: "b4"}


# ------------------------------------------------------------------ formatting

def fmt_pct(value, digits=1):
    return "N/A" if value is None else f"{100 * value:.{digits}f}%"


def fmt_num(value, digits=3):
    return "N/A" if value is None else f"{value:.{digits}f}"


def fmt_int(value):
    return "N/A" if value is None else f"{int(value):,}"


def state(title, text, kind="info"):
    return tags.div(tags.span(title, class_="m2-state-title"), text, class_=f"m2-state m2-state-{kind}",
                    role="status")


def plot(fig, element_id, post_script=None):
    from shared.ui_kit import FONT_STACK          # imported on use: the server module stays importable alone
    fig.update_layout(template="plotly_white", margin=dict(l=48, r=16, t=24, b=44), height=300,
                      font=dict(family=FONT_STACK, size=12,
                                color="#211b1c"),
                      paper_bgcolor="rgba(0,0,0,0)")
    html = fig.to_html(full_html=False, include_plotlyjs=False, div_id=element_id, post_script=post_script,
                       config={"displayModeBar": False, "responsive": True})
    return ui.HTML(f'<div class="m2-chart" role="img" aria-label="{element_id}">{html}</div>')


def safe(function):
    """Unexpected failures render a sanitized error state, never a stack trace."""
    import functools

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - deliberate UI boundary
            if type(error).__name__ in ("SilentException", "SilentCancelOutputException"):
                raise
            return state("Something went wrong", f"This section could not load ({type(error).__name__}). "
                         "Try again; if it persists, check the published candidate.", "bad")
    return wrapper


def gate_badge(meets, undefined=False):
    if meets is None:
        return tags.span("Not evaluated", class_="m2-badge na")
    if undefined:
        return tags.span("Undefined", class_="m2-badge na")
    return tags.span("Meets targets" if meets else "Below target", class_="m2-badge " + ("pass" if meets else "fail"))


# ------------------------------------------------------------------ service wiring

def configured_service(backend=None):
    """Pin one serving release for the app session.

    ``backend`` (default: SHARED_DATA_BACKEND, else ``duckdb``) is chosen explicitly:
    ``duckdb`` pins the published candidate named by MODULE2_SERVING_ROOT;
    ``postgres`` opens the shared PostgreSQL reader from SHARED_DATA_DATABASE_URL
    (+ SHARED_DATA_SSLROOTCERT, optional SHARED_DATA_RELEASE_ID). A failure is an
    explicit unavailable state; there is never a fallback to another backend.
    """
    backend = backend or os.environ.get("SHARED_DATA_BACKEND") or "duckdb"
    latest = os.environ.get("MODULE2_LATEST_SOURCE_DATE")
    if backend == "postgres":
        try:
            from data_platform.readers import open_configured_reader
            reader = open_configured_reader(environ={**os.environ, "SHARED_DATA_BACKEND": "postgres"})
            return Module2Service(reader=reader,
                                  latest_source_as_of=date.fromisoformat(latest) if latest else None), None
        except Exception as error:  # sanitized: class name only, never connection details
            return None, f"Shared PostgreSQL serving unavailable: {type(error).__name__}"
    if backend != "duckdb":
        return None, "Unsupported SHARED_DATA_BACKEND (use duckdb or postgres)."
    root = os.environ.get("MODULE2_SERVING_ROOT")
    if not root:
        return None, "No Module 2 serving candidate is configured (set MODULE2_SERVING_ROOT)."
    try:
        return Module2Service(resolve_published(Path(root)),
                              latest_source_as_of=date.fromisoformat(latest) if latest else None), None
    except Exception as error:  # surfaced as an explicit unavailable state
        return None, f"Module 2 serving candidate unavailable: {type(error).__name__}"


def module_2_server(input, output, session, dataset: dict, auth_state: dict):
    """App entry point.

    In the integrated app ``dataset['runtime']`` (shared.runtime.SharedRuntime) supplies one
    service bound to the shared source used by every module; a module-specific source that
    differs is shown unavailable. Without a runtime, the standalone configuration is used.
    ``auth_state['workspace']`` (optional) selects the business workspace or the separate
    ML Operations entry; the business workspace is the default. ``auth_state['navigate']``
    (optional) lets the top-bar Copilot button open the host's copilot module. With
    ``dataset['hosted']`` the host's sidebar shows Module 2's pages (``m2_nav``) and its
    breadcrumb uses ``dataset['module_label']``.
    """
    runtime = dataset.get("runtime") if isinstance(dataset, dict) else None
    service, reason = runtime.module2_service() if runtime is not None else configured_service()
    navigate = auth_state.get("navigate")

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return Principal(pid(), True, bool(auth_state.get("is_demo", lambda: True)()))

    workspace = auth_state.get("workspace")
    name, title = auth_state.get("name"), auth_state.get("title")
    register(input, output, session, principal, service, reason,
             workspace_fn=(lambda: workspace() or "business") if workspace else None,
             logout_fn=auth_state.get("logout"),                 # the root app's own server-side logout
             identity_fn=(lambda: (name(), title())) if name and title else None,
             open_copilot_fn=(lambda: navigate("module_4")) if navigate else None,
             hosted=bool(dataset.get("hosted")) if isinstance(dataset, dict) else False,
             module_label=(dataset.get("module_label") if isinstance(dataset, dict) else None) or "Module 2")


ROLE_TEXT = {"sales": "Sales", "manager": "Manager"}
RANK_HELP = ("Overall rank orders the stores you can see by a proposed business-priority score (0-100): 80% "
             "estimated Marvel sales at risk (its position among your stores), 10% shelf risk and 10% MSL risk. "
             "Stores whose sales at risk cannot be estimated have no score and are listed last. Sorting by a column "
             "changes the order of the list, not the ranks. Ties keep the stored visit order. Weights and cut-offs "
             "are proposals awaiting business approval; this is a work priority, not a route.")
SORT_LABELS = {"score": "Priority score", "sales_at_risk": "Sales at risk", "shelf_risk": "Shelf risk",
               "msl_risk": "MSL risk", "urgency": "Urgency", "rank": "Overall rank"}


def _usd(value, status=None):
    if value is None:
        return "Unavailable"
    text = f"${value:,.0f}"
    return ("≥ " + text) if status in ("Partial", "Lower bound") else text


def _score(value):
    return "—" if value is None else f"{value:.0f}"


def _urgency_tag(level):
    label = bp.URGENCY_LABELS.get(level, "Unavailable")
    mark = bp.URGENCY_MARKS.get(level, "?")
    return tags.span(tags.span(mark, class_="m2-urg-mark", **{"aria-hidden": "true"}), " ", label,
                     class_=f"m2-urg m2-urg-{label.lower()}")


def _dash(value):
    return "—" if value is None else str(value)


def _window(as_of):
    if not as_of:
        return None, None
    start = date.fromisoformat(str(as_of)) + timedelta(days=1)
    return start, start + timedelta(days=6)


def _day(d):
    return f"{d:%b} {d.day}, {d.year}"


# Data colours come from the shared chart palette; red/white is the frame, not the data colour.
MSL_COLORS = {"MSL": CHART_COLORS[0], "Non-MSL": CHART_COLORS[1], "Unknown": CHART_NEUTRAL}
SKU_COLORS = list(CHART_COLORS[:5])
BUSINESS_PAGES = {"overview": "Overview", "ranking": "Store ranking", "analytics": "Analytics"}
OPS_PAGES = {"health": "Model health", "monitoring": "Monitoring"}
NAV_ICONS = {"overview": "home", "ranking": "list", "analytics": "chart", "health": "gauge", "monitoring": "pulse"}
ROLE_TITLES = {"sales": "Sales Representative", "manager": "Regional Manager"}
SKU_SORTS = {"sku": "SKU", "status": "Status now", "inventory": "Inventory", "oos_days": "OOS days · 28",
             "risk": "Stockout risk · 7 days", "units_short": "Units short", "sales": "Sales at risk"}


def icon(name, size=18):
    from .ui import icon as _icon                 # imported on use: the server module stays importable alone
    return _icon(name, size)


def kit_nav_item(icon_name, label, active=False, **attrs):
    from shared.ui_kit import nav_item            # imported on use, like icon()
    return nav_item(icon_name, label, active=active, **attrs)


def _category(text):
    """Risk category as separate lines (current state, predicted risk and shelf concern stay distinct)."""
    parts = [p for p in (text or "").split(" + ") if p] or ["—"]
    kinds = {"Current MSL stockout": "now", "Current stockout": "now", "Future stockout risk": "future",
             "Shelf execution": "shelf", "Verify data": "verify"}
    return tags.ul(*[tags.li(p, class_=f"m2-cat-{kinds.get(p, 'other')}") for p in parts], class_="m2-cat")


def _initials(name):
    parts = [p for p in "".join(c if c.isalnum() or c == " " else " " for c in (name or "")).split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def _store_link(store_id, name):
    return tags.a(name or store_id, href=f"#store-{store_id}", class_="m2-store-link", **{"data-store": store_id})


def _kpi(label, value, note, key, icon_name, tone=""):
    return tags.div(tags.div(icon(icon_name, 18), class_=f"m2-kpi-icon {tone}"),
                    tags.div(tags.div(value, class_="m2-kpi-value"), tags.div(label, class_="m2-kpi-label"),
                             tags.div(note, class_="m2-kpi-note"), class_="m2-kpi-body"),
                    class_="m2-kpi", **{"data-key": key})


def register(input, output, session, principal_fn, service, unavailable_reason=None, workspace_fn=None,
             logout_fn=None, identity_fn=None, open_copilot_fn=None, hosted=False, module_label="Module 2"):
    page = reactive.value(0)
    sku_page = reactive.value(0)
    previous = reactive.value(None)
    selected_store = reactive.value(None)
    current_page = reactive.value("overview")
    return_page = reactive.value("overview")
    workspace_fn = workspace_fn or (lambda: "business")

    @reactive.calc
    def who():
        return principal_fn()

    @reactive.calc
    def mode():
        return "none" if who() is None else ("ops" if workspace_fn() == "ops" else "business")

    @output(suspend_when_hidden=False)
    @render.text
    def m2_mode():
        return mode()

    @output(suspend_when_hidden=False)
    @render.text
    def m2_page():
        return current_page()

    def current_view():
        value = input.m2_view()
        return value if value in VIEW_LABELS else "all"

    def store_filters():
        return {"channel": input.m2_f_channel() or None, "region": input.m2_f_region() or None,
                "segment_tier": input.m2_f_tier() or None, "search": input.m2_f_search() or None,
                "urgency": input.m2_f_urgency() or None}

    def filters():
        return {**store_filters(), "view": current_view()}

    def current_sort():
        key, _, direction = (input.m2_sort() or "score:desc").partition(":")
        if key not in bp.SORT_KEYS or direction not in ("asc", "desc"):
            return "score", "desc"
        return key, direction

    def rows_per_page(name="m2_rows"):
        value = getattr(input, name)()
        return int(value) if value in ("10", "50", "100") else 10

    @reactive.calc
    def scope():
        p = who()
        if service is None or p is None:
            return None
        try:
            return service.reader.principal_scope(p.principal_id)
        except (PermissionError, DataUnavailable):
            return {"roles": [], "stores": 0, "full_population": False, "manager_full_scope": False}

    def role():
        roles = (scope() or {}).get("roles") or []
        return roles[0] if len(roles) == 1 else None

    @reactive.calc
    def priorities():
        p = who()
        if service is None:
            return {"status": "Unavailable", "message": unavailable_reason}
        if p is None or mode() != "business":
            return {"status": "Signed_out"}
        size = rows_per_page()
        try:
            return service.get_priorities(p, filters(), cursor=str(page() * size), page_size=size,
                                          sort=current_sort()).to_dict()
        except (Module2Error, PermissionDenied) as error:
            return {"status": "Error", "message": str(error)}

    @reactive.calc
    def overview():
        """Default business order (score, high to low) after the store filters; first 10 stores."""
        p = who()
        if service is None:
            return {"status": "Unavailable", "message": unavailable_reason}
        if p is None or mode() != "business":
            return {"status": "Signed_out"}
        try:
            return service.get_priorities(p, {**store_filters(), "view": "all"}, page_size=10).to_dict()
        except (Module2Error, PermissionDenied) as error:
            return {"status": "Error", "message": str(error)}

    def oos(measure):
        p = who()
        if service is None or p is None or mode() != "business":
            return None
        try:
            return service.get_oos_analytics(p, store_filters(), measure).to_dict()
        except Module2Error:
            raise
        except Exception as error:  # noqa: BLE001 - sanitized: class name only
            return {"status": "Unavailable", "message": f"Out-of-stock analytics could not load ({type(error).__name__})"}

    @reactive.calc
    def oos_current():
        return oos("current")

    @reactive.calc
    def oos_selected():
        measure = input.m2_oos_measure() if input.m2_oos_measure() in ("current", "28d") else "current"
        return oos_current() if measure == "current" else oos("28d")    # 28 days only on request

    def go(target):
        if mode() == "ops":
            if target in OPS_PAGES:
                current_page.set(target)
                ui.update_navset("m2_ops_tabs", selected=target)
            return
        if target in BUSINESS_PAGES:
            current_page.set(target)
            ui.update_navset("m2_pages", selected=target)

    @reactive.effect
    def _session_change():
        p, ws = who(), mode()
        with reactive.isolate():                               # Value.set compares by identity
            old = previous.get()
        key = (p.principal_id, ws) if p else None
        if old and old != key and service is not None:
            service.clear(old[0])                              # logout/role switch drops cached data
        previous.set(key)
        page.set(0)
        sku_page.set(0)
        selected_store.set(None)
        current_page.set("health" if ws == "ops" else "overview")
        ui.update_navset("m2_pages", selected="overview")
        ui.update_navset("m2_ops_tabs", selected="health")
        ui.update_radio_buttons("m2_view", selected="all")
        ui.update_radio_buttons("m2_oos_measure", selected="current")
        ui.update_radio_buttons("m2_oos_msl", selected="all")
        ui.update_text("m2_f_search", value="")
        ui.update_select("m2_f_urgency", selected="")
        ui.update_select("m2_sort", selected="score:desc")
        ui.update_select("m2_rows", selected="10")
        rows = []
        if p is not None and ws == "business" and service is not None:
            try:
                response = service.get_priorities(p, {"view": "all"}, page_size=200)
                rows = list(response.rows)
                cursor = response.next_cursor
                while cursor:
                    more = service.get_priorities(p, {"view": "all"}, cursor=cursor, page_size=200)
                    rows += more.rows
                    cursor = more.next_cursor
            except (Module2Error, PermissionError, DataUnavailable):
                rows = []
        for name, field_name, label in (("m2_f_channel", "channel", "All channels"),
                                        ("m2_f_region", "region", "All regions"),
                                        ("m2_f_tier", "segment_tier", "All tiers")):
            values = sorted({r[field_name] for r in rows if r.get(field_name)})
            ui.update_select(name, choices={"": label, **{v: v for v in values}}, selected="")

    seen = reactive.value(None)

    @reactive.effect
    async def _clear_rendered_rows():
        """Sign-out, identity or workspace switch removes the previous content from the page."""
        p = who()
        current = (p.principal_id, mode()) if p else None
        with reactive.isolate():
            last = seen.get()
        if last is not None and current != last:
            await session.send_custom_message("m2-clear", {})
        seen.set(current)

    @reactive.effect
    @reactive.event(input.m2_signout)
    def _sign_out():
        """Module 2 sign-out uses the host's trusted server-side logout (root app or demo)."""
        p = who()
        if service is not None and p is not None:
            service.clear(p.principal_id)
        if logout_fn is not None:
            logout_fn()

    @reactive.effect
    @reactive.event(input.m2_go)
    def _go():
        go(input.m2_go())

    @reactive.effect
    @reactive.event(input.m2_open_copilot)
    def _open_copilot():
        """Host navigation only (no question or store context is passed to the copilot)."""
        if open_copilot_fn is not None and who() is not None:
            open_copilot_fn()

    @reactive.effect
    @reactive.event(input.m2_focus_go)
    def _focus_go():
        view = input.m2_focus_go()
        if view in VIEW_LABELS:
            ui.update_radio_buttons("m2_view", selected=view)
            go("ranking")

    @reactive.effect
    @reactive.event(input.m2_f_channel, input.m2_f_region, input.m2_f_tier, input.m2_f_search, input.m2_view,
                    input.m2_f_urgency, input.m2_sort, input.m2_rows)
    def _reset_page():
        page.set(0)

    @reactive.effect
    @reactive.event(input.m2_f_search)
    def _search_shows_list():
        """The top-bar store search filters the store list; typing there opens the ranking page."""
        if (input.m2_f_search() or "").strip() and current_page() in ("overview", "detail"):
            go("ranking")

    @reactive.effect
    @reactive.event(input.m2_sort_click)
    def _sort_click():
        """Column header: same column toggles direction; a new column starts high-to-low (rank: 1 first)."""
        key = input.m2_sort_click()
        if key not in bp.SORT_KEYS:
            return
        current, direction = current_sort()
        if key == current:
            direction = "asc" if direction == "desc" else "desc"
        else:
            direction = "asc" if key == "rank" else "desc"
        ui.update_select("m2_sort", selected=f"{key}:{direction}")

    @reactive.effect
    @reactive.event(input.m2_next)
    def _next():
        if priorities().get("next_cursor"):
            page.set(page() + 1)

    @reactive.effect
    @reactive.event(input.m2_prev)
    def _prev():
        page.set(max(0, page() - 1))

    @reactive.effect
    @reactive.event(input.m2_open_store)
    async def _open():
        with reactive.isolate():
            origin = current_page()
        if origin != "detail":
            return_page.set(origin)
        selected_store.set(input.m2_open_store())
        sku_page.set(0)
        ui.update_select("m2_sku_sort", selected="sales:desc")
        current_page.set("detail")
        ui.update_navset("m2_pages", selected="detail")
        ui.update_navset("m2_detail_tabs", selected="skus")
        await session.send_custom_message("m2-scroll-top", {})

    @reactive.effect
    @reactive.event(input.m2_back)
    async def _back():
        target = return_page()
        current_page.set(target)
        ui.update_navset("m2_pages", selected=target)
        await session.send_custom_message("m2-restore-scroll", {"page": target})

    @render.text
    def m2_back_label():
        return f"Back to {BUSINESS_PAGES.get(return_page(), 'Overview').lower()}"

    @render.ui
    def m2_signed_out():
        return state("Signed out", "Sign in to see your store priorities.")

    # -------------------------------------------------------------- shell
    def identity():
        p = who()
        if p is None:
            return None, None
        if identity_fn is not None:
            try:
                name, title = identity_fn()
            except Exception:  # noqa: BLE001 - a missing host profile falls back to the session identity
                name, title = None, None
        else:
            name, title = None, None
        name = name or p.display_name or p.principal_id.replace("demo:", "").replace("-", " ").title()
        if mode() == "ops":
            title = "ML Operations (demo)"
        title = title or ROLE_TITLES.get(role(), "No configured scope")
        return name, title

    @output(suspend_when_hidden=False)
    @render.ui
    def m2_nav():
        if mode() == "none":
            return None
        pages = OPS_PAGES if mode() == "ops" else BUSINESS_PAGES
        if hosted:                                  # the host's sidebar: page items under the module entry
            return tags.nav(*[kit_nav_item(NAV_ICONS[key], text, active=current_page() == key or
                                           (key == return_page() and current_page() == "detail"),
                                           onclick=f"m2Go('{key}')", **{"data-page": key})
                              for key, text in pages.items()],
                            class_="ws-subnav-items", **{"aria-label": f"{module_label} pages"})
        label = "ML Operations · demo" if mode() == "ops" else "Module 2"
        return tags.nav(tags.p(label, class_="m2-nav-label"),
                        *[tags.button(icon(NAV_ICONS[key]), tags.span(text), type="button",
                                      class_="m2-nav-item" + (" active" if current_page() == key or
                                                              (key == return_page() and current_page() == "detail")
                                                              else ""),
                                      onclick=f"m2Go('{key}')", **({"aria-current": "page"}
                                                                   if current_page() == key else {}),
                                      **{"data-page": key})
                          for key, text in pages.items()],
                        class_="m2-nav-group", **{"aria-label": "Module 2 pages"})

    @output(suspend_when_hidden=False)
    @render.ui
    def m2_user_card():
        name, title = identity()
        if name is None:
            return None
        return tags.div(tags.span(_initials(name), class_="m2-avatar", **{"aria-hidden": "true"}),
                        tags.div(tags.strong(name), tags.span(title), class_="m2-user-text"),
                        class_="m2-user-card", id="m2-demo-identity")

    @render.ui
    def m2_user_block():
        name, title = identity()
        if name is None:
            return None
        return tags.div(tags.span(_initials(name), class_="m2-avatar", **{"aria-hidden": "true"}),
                        tags.div(tags.strong(name), tags.span(title), class_="m2-user-text"),
                        class_="m2-user", id="m2-user")

    @render.ui
    def m2_crumb():
        if mode() == "none":
            return None
        pages = OPS_PAGES if mode() == "ops" else {**BUSINESS_PAGES, "detail": "Store"}
        return tags.nav(tags.span(module_label), tags.span("/", **{"aria-hidden": "true"}),
                        tags.strong(pages.get(current_page(), "")), class_="m2-crumb", **{"aria-label": "Breadcrumb"})

    def _model_label(element_id="m2-model-status"):
        """Candidate disclosure shown on every business page (overview, ranking, analytics, store)."""
        if service is None or (service.record.get("model_outputs") or {}).get("publication_kind") != \
                "Local_review_candidate":
            return None
        return tags.span("Model: review candidate, not an approved production model", class_="m2-model-label",
                         id=element_id)

    @render.ui
    @safe
    def m2_page_head():
        if mode() != "business" or current_page() == "detail":
            return None
        roles = (scope() or {}).get("roles") or []
        titles = {"overview": "Team risk overview" if roles == ["manager"] else "Store risk overview",
                  "ranking": "Store ranking", "analytics": "Out-of-stock analytics"}
        if service is None:
            return tags.div(tags.h1(titles[current_page()], class_="m2-title"), tags.p("Unavailable", class_="m2-meta"))
        sc = scope() or {}
        as_of = service.record.get("as_of")
        start, end = _window(as_of)
        meta = [tags.span(tags.b(ROLE_TEXT.get(role(), "No configured scope")),
                          f" · {fmt_int(sc.get('stores'))} assigned stores"),
                tags.span("Reported as of ", tags.b(_day(date.fromisoformat(str(as_of))) if as_of else "N/A")),
                tags.span("Stockout risk window ", tags.b(f"{_day(start)} – {_day(end)}" if start else "N/A"))]
        label = _model_label()
        if label is not None:
            meta.append(label)
        action = ui.download_button("m2_export", "Export visit list", class_="m2-btn primary") \
            if current_page() == "ranking" else None
        return tags.div(tags.div(tags.h1(titles[current_page()], class_="m2-title"), tags.p(*meta, class_="m2-meta"),
                                 id="m2_header"), action, class_="m2-pagehead")

    @render.ui
    @safe
    def m2_banners():
        if mode() != "business":
            return None
        if service is None:
            return tags.div(tags.strong("Unavailable. "), unavailable_reason, class_="m2-banner bad", role="alert")
        record = service.record
        outputs = record.get("model_outputs", {})
        banners = []
        if outputs.get("synthetic"):
            banners.append(tags.div(tags.strong("Synthetic demo. "), "Stores, products and predictions are generated "
                                    "test data, not client evidence.", class_="m2-banner warn", role="note"))
        elif outputs.get("publication_kind") == "Local_review_candidate":
            banners.append(tags.div("Stockout risks are predictions from a model under review. Because parts of the "
                                    "reported data are missing, some other figures are estimates.",
                                    class_="m2-banner info", role="note", id="m2-estimate-note"))
        as_of = record.get("as_of")
        if as_of and service._stale(date.fromisoformat(str(as_of))):
            banners.append(tags.div(tags.strong("Out of date. "),
                                    f"These priorities are for {as_of}; newer business data "
                                    f"({service.latest_source_as_of}) exists and has not been scored yet.",
                                    class_="m2-banner warn", role="alert", id="m2-stale-banner"))
        if record.get("model_outputs_status") != "Available":
            banners.append(tags.div(tags.strong("Predictions not released. "),
                                    "There are no published predictions for this data; that is not zero risk.",
                                    class_="m2-banner bad", role="alert"))
        return TagList(*banners)

    # -------------------------------------------------------------- overview
    def _state_for(data):
        status = data.get("status")
        if status == "Unavailable":
            return state("Unavailable", data.get("message") or "No Module 2 data.", "bad")
        if status == "Permission_denied":
            return state("No access", "Your account has no configured store scope.", "bad")
        if status == "Unreleased":
            return state("Predictions not released", "There are no published predictions for this data; "
                                                     "that is not zero risk.", "bad")
        if status == "Error":
            return state("Could not load priorities", data.get("message", ""), "bad")
        return None

    @render.ui
    @safe
    def m2_kpis():
        data = overview()
        if data["status"] == "Signed_out":
            return None
        problem = _state_for(data)
        if problem is not None:
            return problem
        f, s, summary = data["totals"]["focus"], data["totals"]["stats"], data["totals"]["summary"]
        impact = s["sales_at_risk"]
        impact_note = ("7-day scenario, USD" if impact["status"] == "Complete" else
                       f"lower bound · {impact['incomplete_stores']} store(s) incomplete"
                       if impact["status"] == "Lower bound" else "inputs missing; not zero")
        return tags.section(
            _kpi("Stores needing action", fmt_int(s["action_stores"]), f"urgent or high · of {fmt_int(summary['stores'])}",
                 "action_stores", "list", "red"),
            _kpi("Act urgently", fmt_int(f["urgent"]), "current MSL stockouts", "urgent", "bell", "red"),
            _kpi("Prevent stockouts", fmt_int(f["prevention"]), "in stock now, alert next 7 days", "prevention",
                 "pulse"),
            _kpi("Verify data", fmt_int(f["verify_data"]), "missing inventory, MSL or score", "verify_data", "help"),
            _kpi("Est. Marvel sales at risk", _usd(impact["value"], impact["status"]), impact_note, "sales_at_risk",
                 "chart", "red"),
            _kpi("Current MSL gaps", f"{fmt_int(s['msl_gap_skus'])} SKUs", "mandatory SKUs out of stock now",
                 "msl_gap_skus", "gauge"),
            class_="m2-kpis", id="m2-summary", **{"aria-label": "Key figures for the filtered stores"})

    @render.ui
    @safe
    def m2_focus():
        data = overview()
        if data["status"] not in ("Ok", "Stale", "Empty") or "focus" not in data["totals"]:
            return None
        f = data["totals"]["focus"]
        as_of = service.record.get("as_of") if service else None

        def item(count, unit, text, key, view):
            return tags.button(tags.strong(f"{fmt_int(count)} {'store' if count == 1 else 'stores'} · {unit}"),
                               tags.span(text), type="button", class_="m2-focus-item",
                               onclick=f"Shiny.setInputValue('m2_focus_go', '{view}', {{priority: 'event'}})",
                               **{"data-key": key})
        return tags.section(
            tags.div(tags.h2("Today's focus", class_="m2-h"),
                     tags.p(f"Reported as of {_day(date.fromisoformat(str(as_of)))} · follows the filters"
                            if as_of else "Follows the filters", class_="m2-meta"), class_="m2-card-head"),
            tags.div(item(f["urgent"], "act urgently", "Current MSL stockouts: check shelf and backroom", "urgent",
                          "current_msl"),
                     item(f["prevention"], "prevent stockouts", "In stock now, predicted stockout in 7 days: confirm "
                          "replenishment", "prevention", "prevention"),
                     item(f["verify_data"], "verify data", "Missing inventory, MSL status or model score",
                          "verify_data", "verify_data"),
                     class_="m2-focus-row"),
            class_="m2-card m2-focus", id="m2-focus",
            **{"aria-label": "Today's focus", **{f"data-{k.replace('_', '-')}": str(v)
                                                 for k, v in data["totals"]["summary"].items()}})

    def _top_rows(rows, compact=False):
        body = []
        for r in rows:
            name = r["store_name"] or r["store_id"]
            body.append(tags.tr(
                {"data-open-store": "1", "data-store": r["store_id"], "data-rank": str(r["overall_rank"] or "")},
                _rank_cell(r),
                tags.td(_store_link(r["store_id"], name),
                        tags.span(f"{r['store_id']} · {r['region'] or '—'}", class_="m2-store-meta"),
                        class_="m2-cell-store"),
                tags.td(_score(r["priority_score"]), class_="num m2-score", **{"data-label": "Score"}),
                tags.td(_usd(r["sales_at_risk_usd"], r["sales_at_risk_status"]), class_="num",
                        **{"data-label": "Sales at risk"}),
                tags.td(_urgency_tag(r["urgency"]), **{"data-label": "Urgency"}),
                None if compact else tags.td(_category(r.get("risk_category")), class_="m2-category",
                                             **{"data-label": "Risk category"})))
        return body

    @render.ui
    @safe
    def m2_top10():
        data = overview()
        if data["status"] not in ("Ok", "Stale", "Empty"):
            return None
        head = tags.div(tags.div(tags.h2("Highest-priority stores", class_="m2-h"),
                                 tags.p("Top 10 by the proposed overall priority score · select a store to open it",
                                        class_="m2-meta")),
                        tags.button("View all stores →", type="button", class_="btn m2-btn",
                                    onclick="m2Go('ranking')"), class_="m2-card-head")
        if not data["rows"]:
            return tags.section(head, state("No stores match", "No stores in your scope match these filters."),
                                class_="m2-card")
        table = tags.div(tags.table(
            tags.thead(tags.tr(*[tags.th(h, scope="col", class_="num" if h in ("Overall rank", "Score", "Sales at risk")
                                         else None) for h in ("Overall rank", "Store", "Score", "Sales at risk",
                                                              "Urgency", "Risk category")])),
            tags.tbody(*_top_rows(data["rows"])), class_="m2-table m2-cards m2-clickable", id="m2-top10-table"),
            class_="m2-table-wrap")
        return tags.section(head, table, class_="m2-card", id="m2-top10")

    def _oos_bar(detail, group, top=10, element_id="m2-oos-bar", height=None, select_input=None):
        """Top SKUs by out-of-stock count, stacked by MSL / non-MSL / unknown (text labels in legend/hover).
        ``select_input``: clicking a bar sends its SKU to this input (the SKU select is the keyboard route)."""
        import plotly.graph_objects as go_
        key = (lambda s: s["oos"]) if group == "all" else (lambda s: s["msl"][group])
        skus = [s for s in detail["skus"] if key(s) > 0]
        skus = sorted(skus, key=lambda s: (-key(s), s["sku_id"]))[:top][::-1]
        if not skus:
            return None
        fig = go_.Figure()
        for g in ("MSL", "Non-MSL", "Unknown"):
            if group not in ("all", g):
                continue
            values = [s["msl"][g] for s in skus]
            if not any(values):
                continue
            fig.add_trace(go_.Bar(y=[f"{s['sku_id']}" for s in skus], x=values, name=g, orientation="h",
                                  marker_color=MSL_COLORS[g],
                                  customdata=[s["product_name"] or "" for s in skus],
                                  hovertemplate="%{y} · %{customdata}<br>" + g + ": %{x} " + detail["unit"]
                                                + "<extra></extra>"))
        fig.update_layout(barmode="stack", xaxis_title=detail["unit"].capitalize(), legend=dict(orientation="h", y=-0.2),
                          height=height or max(260, 34 * len(skus) + 90),
                          xaxis=dict(tickformat=",d", rangemode="tozero", dtick=1 if max(map(key, skus)) <= 8 else None))
        script = None
        if select_input:
            script = ("var gd=document.getElementById('{plot_id}');gd.on('plotly_click',function(e){"
                      "if(e&&e.points&&e.points.length&&window.Shiny){Shiny.setInputValue('" + select_input +
                      "',String(e.points[0].y),{priority:'event'});}});")
        return plot(fig, element_id, script)

    @render.ui
    @safe
    def m2_overview_oos():
        if mode() != "business":
            return None
        data = oos_current()
        head = tags.div(tags.div(tags.h2("SKUs most out of stock now", class_="m2-h"),
                                 tags.p("Distinct stores reporting the SKU out of stock on the as-of date · MSL split",
                                        class_="m2-meta")),
                        tags.button("Analyze →", type="button", class_="btn m2-btn", onclick="m2Go('analytics')"),
                        class_="m2-card-head")
        if data is None:
            return None
        if data["status"] != "Ok":
            return tags.section(head, state("Unavailable", data.get("message") or "Could not load.", "bad"),
                                class_="m2-card")
        detail = data["detail"]
        chart = _oos_bar(detail, "all", top=8, element_id="m2-overview-oos-bar", height=300)
        total = detail["totals"]
        note = tags.p(f"{fmt_int(total['oos'])} out-of-stock store-SKU reports across "
                      f"{fmt_int(detail['stores_with_observations'])} stores · {fmt_int(total['unknown'])} not reported "
                      "(unknown, not in stock)", class_="m2-meta")
        return tags.section(head, chart or state("Nothing reported out of stock",
                                                 "No store in these filters reported a SKU out of stock on the as-of "
                                                 "date."), note, class_="m2-card", id="m2-overview-oos")

    @render.ui
    @safe
    def m2_regions():
        data = overview()
        if role() != "manager" or data["status"] not in ("Ok", "Stale") or not data["totals"].get("regions"):
            return None
        rows = [tags.tr(tags.td(g["region"], class_="m2-cell-store"),
                        tags.td(fmt_int(g["stores"]), class_="num", **{"data-label": "Stores"}),
                        tags.td(fmt_int(g["action_stores"]), class_="num", **{"data-label": "Needing action"}),
                        tags.td(_usd(g["sales_at_risk"]["value"], g["sales_at_risk"]["status"]), class_="num",
                                **{"data-label": "Est. sales at risk"}))
                for g in data["totals"]["regions"]]
        return tags.section(
            tags.div(tags.div(tags.h2("Territory overview", class_="m2-h"),
                              tags.p("By region in the published store data. Sales-rep territories are not in the "
                                     "published data, so no rep owner is shown.", class_="m2-meta")),
                     class_="m2-card-head"),
            tags.div(tags.table(tags.thead(tags.tr(tags.th("Region", scope="col"),
                                                   *[tags.th(h, scope="col", class_="num") for h in
                                                     ("Stores", "Needing action", "Est. sales at risk")])),
                                tags.tbody(*rows), class_="m2-table m2-cards", id="m2-region-table"),
                     class_="m2-table-wrap"),
            class_="m2-card", id="m2-regions")

    # -------------------------------------------------------------- ranking
    @render.ui
    @safe
    def m2_count():
        data = priorities()
        if data["status"] not in ("Ok", "Stale", "Empty"):
            return None
        n = data["totals"]["filtered_stores"]
        size = rows_per_page()
        first = page() * size + 1 if n else 0
        last = min((page() + 1) * size, n)
        key, direction = current_sort()
        order = "low to high" if direction == "asc" else "high to low"
        if key == "rank":
            order = "last first" if direction == "desc" else "1 first"
        return tags.span(f"Showing {first}–{last} of {n} matching stores · {VIEW_LABELS[current_view()]} · "
                         f"sorted by {SORT_LABELS[key].lower()}, {order}", class_="m2-meta", id="m2-count")

    @render.ui
    def m2_rank_help():
        if mode() != "business":
            return None
        return tags.details(tags.summary("How the overall rank works"), tags.p(RANK_HELP), class_="m2-help")

    @render.ui
    @safe
    def m2_page_label():
        data = priorities()
        if data["status"] not in ("Ok", "Stale"):
            return None
        pages = max(1, -(-data["totals"]["filtered_stores"] // rows_per_page()))
        return tags.span(f"Page {page() + 1} of {pages}", class_="m2-meta")

    @render.ui
    @safe
    def m2_priority_table():
        data = priorities()
        if data["status"] == "Signed_out":
            return None
        problem = _state_for(data)
        if problem is not None:
            return problem
        if not data["rows"]:
            return state("No stores match", "No stores in your scope match this view and these filters.")
        key, direction = current_sort()

        def sortable(label, sort_key, numeric=True):
            active = sort_key == key
            arrow = ("↑" if direction == "asc" else "↓") if active else "↕"
            attrs = {"aria-sort": "ascending" if direction == "asc" else "descending"} if active else {}
            return tags.th(tags.button(label, " ", tags.span(arrow, **{"aria-hidden": "true"}), type="button",
                                       class_="m2-sort" + (" active" if active else ""),
                                       onclick=f"m2Sort('{sort_key}')", **{"data-sort": sort_key}),
                           scope="col", class_="num" if numeric else None, **attrs)
        head = [sortable("Overall rank", "rank"), tags.th("Store", scope="col"), sortable("Score", "score"),
                sortable("Sales at risk", "sales_at_risk"), sortable("Shelf risk", "shelf_risk"),
                sortable("MSL risk", "msl_risk"), sortable("Urgency", "urgency", numeric=False),
                tags.th("Risk category", scope="col")]
        body = []
        for r in data["rows"]:
            name = r["store_name"] or r["store_id"]

            def attr(value):
                return "" if value is None else str(value)
            body.append(tags.tr(
                {"data-open-store": "1", "data-store": r["store_id"], "data-rank": attr(r["overall_rank"]),
                 "data-score": attr(r["priority_score"]), "data-sales": attr(r["sales_at_risk_usd"]),
                 "data-shelf": attr(r["shelf_risk"]), "data-msl": attr(r["msl_risk"]),
                 "data-urgency": attr(r["urgency"]), "data-msl-gaps": attr(r["msl_gap_count"]),
                 "data-prevention": attr(r["prevention_alert_count"]),
                 "data-checks": str(len(r["data_check_reasons"]))},
                _rank_cell(r),
                tags.td(_store_link(r["store_id"], name),
                        tags.span(f"{r['store_id']} · {r['channel']} · {r['region'] or '—'}", class_="m2-store-meta"),
                        class_="m2-cell-store"),
                tags.td(_score(r["priority_score"]), class_="num m2-score", **{"data-label": "Score"}),
                tags.td(_usd(r["sales_at_risk_usd"], r["sales_at_risk_status"]),
                        class_="num" + (" m2-unavail" if r["sales_at_risk_usd"] is None else ""),
                        **{"data-label": "Sales at risk"}),
                tags.td(_score(r["shelf_risk"]), class_="num", **{"data-label": "Shelf risk"}),
                tags.td(_score(r["msl_risk"]), class_="num", **{"data-label": "MSL risk"}),
                tags.td(_urgency_tag(r["urgency"]), **{"data-label": "Urgency"}),
                tags.td(_category(r.get("risk_category")), class_="m2-category", **{"data-label": "Risk category"})))
        stale = tags.div(tags.strong("Out of date. "), "These priorities are older than the latest business data.",
                         class_="m2-banner warn") if data["status"] == "Stale" else None
        return TagList(stale, tags.div(tags.table(
            tags.caption("Default order: overall priority score, highest first. Select a column to sort; overall "
                         "ranks stay fixed. Score, shelf and MSL risk are 0–100; — means unavailable, not zero. "
                         "Select a store name (or its row) to open the store."),
            tags.thead(tags.tr(*head)), tags.tbody(*body), class_="m2-table m2-cards m2-clickable",
            id="m2-priority-table"), class_="m2-table-wrap"))

    @render.ui
    @safe
    def m2_risk_chart():
        if mode() != "business" or service is None or who() is None:
            return None
        response = service.get_population(who())
        if response.status != "Ok":
            return state("No distribution", "Appears when published predictions are available.")
        bins = response.detail["risk_histogram"]
        import plotly.graph_objects as go_
        fig = go_.Figure(go_.Bar(x=[f"{b['lower']:.1f}–{b['upper']:.1f}" for b in bins], y=[b["stores"] for b in bins],
                                 marker_color=CHART_COLORS[0],
                                 hovertemplate="Highest SKU risk %{x}<br>%{y} stores<extra></extra>"))
        fig.update_layout(xaxis_title="Highest SKU stockout risk in the store (not a store probability)",
                          yaxis_title="Stores in your scope", bargap=0.08)
        return TagList(plot(fig, "m2-risk-histogram"),
                       tags.p(f"All {response.detail['scored_stores']} ranked stores in your scope; the list above "
                              "is the action view.", class_="m2-meta", id="m2-histogram-total"))

    @render.download(filename=lambda: f"visit-list-{current_view()}-{(service.record.get('as_of') if service else 'na')}.csv")
    def m2_export():
        p = who()
        if service is None or p is None or mode() != "business":
            yield "status\nUnavailable\n"
            return
        try:
            yield service.export_visit_plan(p, filters(), sort=current_sort())
        except Exception as error:
            yield f"status\n{type(error).__name__}\n"

    # -------------------------------------------------------------- analytics
    @render.ui
    @safe
    def m2_oos_note():
        if mode() != "business":
            return None
        group = oos_group()
        data = oos_selected()
        if data is None:
            return None
        if data["status"] != "Ok":
            return state("Unavailable", data.get("message") or "Could not complete the analysis.", "bad")
        d = data["detail"]
        t = d["totals"]
        return tags.div(
            tags.p(tags.b(d["definition"]), f" · {d['window'][0]} – {d['window'][1]}"
                   if d["window"][0] != d["window"][1] else f" · {d['window'][1]}", class_="m2-meta"),
            tags.p(f"{fmt_int(t['oos'])} out-of-stock observations (MSL {fmt_int(t['msl']['MSL'])} · non-MSL "
                   f"{fmt_int(t['msl']['Non-MSL'])} · unknown {fmt_int(t['msl']['Unknown'])}) from "
                   f"{fmt_int(t['observed'])} reported observations in {fmt_int(d['stores_with_observations'])} of "
                   f"{fmt_int(d['stores_in_filter'])} filtered stores; {fmt_int(t['unknown'])} not reported"
                   + (f"; {fmt_int(t['duplicates'])} duplicate rows ignored" if t["duplicates"] else "") + ".",
                   class_="m2-meta"),
            tags.p(d["coverage_note"], class_="m2-meta"),
            tags.p(f"MSL filter: {group}. It applies to the chart, the SKU list and the store list; the totals above "
                   "and the MSL share include all observations.", class_="m2-meta", id="m2-oos-filter-note")
            if group != "all" else None,
            id="m2-oos-note")

    @render.ui
    @safe
    def m2_oos_chart():
        data = oos_selected()
        if data is None or data["status"] != "Ok":
            return None
        group = oos_group()
        chart = _oos_bar(data["detail"], group, select_input="m2_oos_bar_click")
        title = tags.div(tags.h2("Top 10 SKUs out of stock", class_="m2-h"),
                         tags.p(f"{data['detail']['unit'].capitalize()} · "
                                + ("all SKUs" if group == "all" else f"{group} observations only")
                                + " · select a bar to list its stores", class_="m2-meta"),
                         class_="m2-card-head")
        return tags.section(title, chart or state("None reported", "No out-of-stock observations for this selection."),
                            class_="m2-card", id="m2-oos-chart-card")

    @render.ui
    @safe
    def m2_oos_donut():
        data = oos_selected()
        if data is None or data["status"] != "Ok":
            return None
        split = data["detail"]["totals"]["msl"]
        title = tags.div(tags.h2("MSL share", class_="m2-h"),
                         tags.p("Share of all out-of-stock observations (not filtered by MSL)", class_="m2-meta"),
                         class_="m2-card-head")
        if not sum(split.values()):
            return tags.section(title, tags.p("No out-of-stock observations, so no share is shown.", class_="m2-meta"),
                                class_="m2-card")
        import plotly.graph_objects as go_
        labels = [g for g in ("MSL", "Non-MSL", "Unknown") if split[g]]
        fig = go_.Figure(go_.Pie(labels=labels, values=[split[g] for g in labels], hole=0.62, sort=False,
                                 marker=dict(colors=[MSL_COLORS[g] for g in labels]), textinfo="label+percent",
                                 insidetextorientation="horizontal",
                                 hovertemplate="%{label}: %{value} observations<extra></extra>"))
        fig.update_layout(showlegend=False, height=280)
        return tags.section(title, plot(fig, "m2-oos-donut"), class_="m2-card", id="m2-oos-donut-card")

    @render.ui
    @safe
    def m2_oos_daily():
        data = oos_selected()
        if data is None or data["status"] != "Ok" or data["detail"]["measure"] != "28d":
            return None
        days = data["detail"]["daily"]
        if not days:
            return None
        import plotly.graph_objects as go_
        from plotly.subplots import make_subplots
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go_.Bar(x=[d["date"] for d in days], y=[d["oos"] for d in days], name="Out-of-stock observations",
                              marker_color=CHART_COLORS[0]), secondary_y=False)
        fig.add_trace(go_.Scatter(x=[d["date"] for d in days], y=[d["observed"] for d in days],
                                  name="Reported observations (coverage)", mode="lines",
                                  line=dict(color=CHART_NEUTRAL, dash="dot")), secondary_y=True)
        fig.update_layout(legend=dict(orientation="h", y=-0.25), height=300)
        fig.update_yaxes(title_text="OOS observations", secondary_y=False)
        fig.update_yaxes(title_text="Reported observations", secondary_y=True)
        return tags.section(tags.div(tags.h2("Out-of-stock observations by day", class_="m2-h"),
                                     tags.p("Counts, not rates: the applicable assortment is not confirmed, so the "
                                            "dotted line shows how many store-SKU reports exist each day.",
                                            class_="m2-meta"), class_="m2-card-head"),
                            plot(fig, "m2-oos-daily"), class_="m2-card", id="m2-oos-daily-card")

    def oos_group():
        value = input.m2_oos_msl()
        return value if value in ("all", "MSL", "Non-MSL", "Unknown") else "all"

    def _group_count(item, group):
        """Out-of-stock count of a SKU (or of one store for a SKU) within the selected MSL group."""
        return item["oos"] if group == "all" else item["msl"][group]

    @reactive.effect
    def _oos_sku_choices():
        group = oos_group()
        try:
            data = oos_selected()
        except Exception:  # noqa: BLE001 - an effect must never end the session; the card shows the error
            data = None
        with reactive.isolate():
            current = input.m2_oos_sku()
        # SKUs with observations in the selected group; the current choice stays listed (count 0) so
        # changing the MSL filter never silently drops the user's selection.
        skus = [s for s in (data or {}).get("detail", {}).get("skus", [])
                if _group_count(s, group) > 0 or (s["sku_id"] == current and s["oos"] > 0)] \
            if data and data.get("status") == "Ok" else []
        skus.sort(key=lambda s: (-_group_count(s, group), s["sku_id"]))
        choices = {"": "Select a SKU", **{s["sku_id"]: f"{s['sku_id']} · {s['product_name'] or ''} "
                                                       f"({_group_count(s, group)})" for s in skus}}
        ui.update_select("m2_oos_sku", choices=choices, selected=current if current in choices else "")

    @reactive.effect
    @reactive.event(input.m2_oos_bar_click)
    def _oos_bar_click():
        """A bar click selects that SKU (same choices as the select, which is the keyboard route)."""
        ui.update_select("m2_oos_sku", selected=str(input.m2_oos_bar_click() or ""))

    @render.ui
    @safe
    def m2_oos_stores():
        data = oos_selected()
        sku = input.m2_oos_sku()
        if data is None or data["status"] != "Ok" or not sku:
            return tags.p("Choose a SKU to list the stores (in your scope and filters) that reported it out of stock.",
                          class_="m2-meta")
        group = oos_group()
        match = next((s for s in data["detail"]["skus"] if s["sku_id"] == sku), None)
        stores = sorted((s for s in (match or {}).get("stores", []) if _group_count(s, group) > 0),
                        key=lambda s: (-_group_count(s, group), s["store_id"]))
        if not stores:
            return state("No stores", "No store reported this SKU out of stock for this measure"
                         + ("." if group == "all" else f" in the {group} group."))
        unit = "OOS days" if data["detail"]["measure"] == "28d" else "Out of stock now"
        rows = [tags.tr({"data-open-store": "1", "data-store": s["store_id"], "data-count": str(_group_count(s, group))},
                        tags.td(_store_link(s["store_id"], s["store_name"]), tags.span(s["store_id"],
                                                                                       class_="m2-store-meta"),
                                class_="m2-cell-store"),
                        tags.td(str(_group_count(s, group)) if data["detail"]["measure"] == "28d" else "Yes",
                                class_="num", **{"data-label": unit}),
                        _rank_cell(s))
                for s in stores]
        return tags.div(tags.table(tags.thead(tags.tr(tags.th("Store", scope="col"), tags.th(unit, scope="col", class_="num"),
                                                      tags.th("Overall rank", scope="col", class_="num"))),
                                   tags.tbody(*rows), class_="m2-table m2-cards m2-clickable", id="m2-oos-store-table"),
                        class_="m2-table-wrap")

    # -------------------------------------------------------------- store detail
    def _detail_response():
        p, store_id = who(), selected_store()
        if service is None or p is None or not store_id or mode() != "business":
            return None, None
        try:
            return store_id, service.get_store_detail(p, store_id)
        except (PermissionDenied, PermissionError):
            return store_id, "denied"
        except Exception as error:  # noqa: BLE001 - shown as a sanitized error state, never a stack trace
            return store_id, ("error", type(error).__name__)

    @reactive.calc
    def detail_data():
        return _detail_response()

    def _detail_parts():
        store_id, response = detail_data()
        if store_id is None or response == "denied" or isinstance(response, tuple) or \
                response.status in ("Unreleased", "Unavailable"):
            return store_id, response, None, None
        detail = response.detail
        return store_id, response, detail, detail.get("business") or {}

    @reactive.effect
    def _trend_choices():
        try:
            _, _, detail, _ = _detail_parts()
        except Exception:  # noqa: BLE001 - an effect must never end the session; the page shows the error
            return
        skus = (detail or {}).get("skus") or []
        choices = {s["sku_id"]: f"{s['sku_id']} · {s['product_name'] or ''}" for s in skus}
        top = [s["sku_id"] for s in skus[:1]]
        ui.update_selectize("m2_trend_skus", choices=choices, selected=top)
        sku_page.set(0)

    @render.ui
    @safe
    def m2_detail_head():
        store_id, response, detail, business = _detail_parts()
        if store_id is None:
            return state("Choose a store", "Select a store in the overview or the store ranking.")
        if response == "denied":
            return state("Not available", "This store is not in your scope.", "bad")
        if isinstance(response, tuple):
            return state("Something went wrong", f"This store could not load ({response[1]}). Try again; if it "
                         "persists, check the published candidate.", "bad")
        if response.status == "Unreleased":
            return state("Predictions not released", "There are no published predictions for this data; "
                                                     "that is not zero risk.", "bad")
        if response.status == "Unavailable":
            return state("Not available right now", response.message or "Please retry.", "bad")
        store = detail.get("store") or {}
        as_of = date.fromisoformat(detail["scoring_date"])
        start, end = _window(as_of)

        def chip(text, kind, label):
            return tags.span(text or "—", class_=f"m2-chip m2-chip-{kind}", title=label, **{"aria-label": f"{label}: {text or 'unknown'}"})

        def figure(label, value, extra=None, key=""):
            return tags.div(tags.div(label, class_="m2-fig-label"), tags.div(value, class_="m2-fig-value"), extra,
                            class_="m2-fig", **{"data-key": key})
        head = tags.section(
            tags.div(tags.h1(store.get("store_name") or store_id, class_="m2-title"),
                     tags.div(chip(store_id, "id", "Store ID"), chip(store.get("channel"), "channel", "Channel"),
                              chip(store.get("region"), "region", "Region"), chip(store.get("segment_tier"), "tier", "Tier"),
                              class_="m2-chips"),
                     _model_label("m2-detail-model-status"), class_="m2-head-main"),
            tags.div(figure("Overall rank", str(store["overall_rank"]) if store.get("overall_rank") else
                            tags.span("Not ranked", class_="m2-fig-muted", title=NOT_RANKED), key="rank"),
                     figure("Score", tags.span(_score(store.get("priority_score")),
                                               tags.small(" / 100") if store.get("priority_score") is not None else None),
                            key="score"),
                     figure("Urgency", _urgency_tag(store.get("urgency")), key="urgency"),
                     class_="m2-figs"),
            class_="m2-card m2-detail-head", id="m2_detail")
        dates = tags.section(
            tags.div(icon("calendar", 18), class_="m2-kpi-icon"),
            tags.div(tags.div("Data as of", class_="m2-fig-label"), tags.strong(_day(as_of)), class_="m2-date"),
            tags.div(tags.div("Forecast window", class_="m2-fig-label"), tags.strong(f"{_day(start)} – {_day(end)}"),
                     class_="m2-date"),
            class_="m2-card m2-card-tight m2-dates", id="m2-detail-dates", **{"aria-label": "Dates"})
        return TagList(head, dates)

    @render.ui
    @safe
    def m2_detail_stats():
        store_id, _, detail, business = _detail_parts()
        if detail is None:
            return None
        store = detail.get("store") or {}
        cov = business.get("coverage") or {}
        return tags.section(
            _kpi("Est. Marvel sales at risk", _usd(store.get("sales_at_risk_usd"), store.get("sales_at_risk_status")),
                 "7-day scenario, USD", "sales_at_risk", "chart", "red"),
            _kpi("Current MSL gaps", f"{_dash(store.get('msl_gap_count'))} SKUs", "reported out of stock now", "msl",
                 "bell", "red"),
            _kpi("Future OOS alerts", f"{_dash(store.get('prevention_alert_count'))} SKUs",
                 "in stock now, predicted stockout", "prevention", "pulse"),
            _kpi("Estimate confidence", business.get("confidence") or "Unavailable",
                 f"input coverage: {cov.get('estimated', 0)} of {cov.get('skus', 0)} SKUs estimated" if cov else
                 "no usable inputs", "confidence", "gauge"),
            class_="m2-kpis m2-kpis-4", id="m2-detail-stats")

    @render.ui
    @safe
    def m2_detail_actions():
        store_id, _, detail, business = _detail_parts()
        if detail is None:
            return None
        store = detail.get("store") or {}
        weights = business.get("weights") or bp.WEIGHTS
        pct = store.get("impact_percentile")
        actions = tags.section(tags.h2("Recommended actions", class_="m2-h"),
                               tags.ul(*[tags.li(a) for a in business.get("actions") or []], class_="m2-action-list"),
                               tags.p("Rule-based suggestions from reported data and the published model; not proof "
                                      "of cause, a delivery guarantee or a completed visit.", class_="m2-meta"),
                               class_="m2-card m2-focus", id="m2-actions")
        why = tags.section(
            tags.h2(f"Why this store ranks {store['overall_rank']}" if store.get("overall_rank")
                    else "Why this store is not ranked", class_="m2-h"),
            tags.p(tags.b(f"Score {_score(store.get('priority_score'))} / 100")), _category(store.get("risk_category")),
            tags.ul(tags.li(f"Sales at risk ({weights['impact']:.0%}): "
                            + ("unavailable, so no score" if pct is None else
                               f"{pct * 100:.0f} on a 0–100 scale among your stores")),
                    tags.li(f"Shelf risk ({weights['shelf']:.0%}): "
                            + (f"no audit in the last {bp.AUDIT_MAX_AGE_DAYS} days" if store.get("shelf_risk") is None
                               else _score(store.get("shelf_risk")))),
                    tags.li(f"MSL risk ({weights['msl']:.0%}): " + _score(store.get("msl_risk"))), class_="m2-list"),
            tags.p(("Missing components are left out and the remaining weights rescaled: "
                    + ", ".join(store["score_missing_components"]) + ". ") if store.get("score_missing_components")
                   else "", f"Policy {bp.POLICY_VERSION}: weights and cut-offs are proposals, not approved rules.",
                   class_="m2-meta"),
            class_="m2-card", id="m2-why")
        return tags.div(actions, why, class_="m2-grid m2-grid-2")

    def _sku_rows():
        """One row per published SKU: reported now (as-of report) and next 7 days kept as separate groups."""
        _, _, detail, business = _detail_parts()
        if not detail:
            return []
        est = business.get("skus") or {}
        days = business.get("oos_days") or {}
        rows = []
        for s in detail["skus"]:
            ev = {e["feature"]: e["value"] for e in s["evidence"]}
            e = est.get(s["sku_id"]) or {}
            d = days.get(s["sku_id"]) or {}
            current = d.get("current")
            if current is True:
                action = "Check shelf and backroom; reorder" if s["is_msl"] else "Check replenishment"
            elif s["is_predicted_oos"]:
                action = "Confirm next delivery"
            elif (e.get("units_short") or 0) > 0:
                action = "Check stock cover (scenario shortfall, no model alert)"
            elif e.get("reasons"):
                action = "Verify missing inputs"
            else:
                action = "Monitor"
            rows.append({"sku": s["sku_id"], "product": s["product_name"], "msl": s["is_msl"],
                         "status": current, "status_text": _as_of_status(d) if d else "No as-of report",
                         "inventory": ev.get("inventory_observed"),
                         "oos_days": d.get("reported_oos_days") if d else None,
                         "unreported_days": d.get("unreported_days"), "days_with_rows": d.get("days_with_rows"),
                         "risk": s["probability"], "alert": s["is_predicted_oos"], "units_short": e.get("units_short"),
                         "price": e.get("price"), "sales": e.get("sales_at_risk"), "estimable": e.get("estimable"),
                         "reasons": e.get("reasons") or [], "action": action})
        return rows

    def sku_sort():
        """One control (key:direction) shared by the header buttons and the Sort-by select."""
        key, _, direction = (input.m2_sku_sort() or "sales:desc").partition(":")
        return (key if key in SKU_SORTS else "sales"), ("asc" if direction == "asc" else "desc")

    @reactive.effect
    @reactive.event(input.m2_sku_sort_click)
    def _sku_sort_click():
        key = input.m2_sku_sort_click()
        if key not in SKU_SORTS:
            return
        current, direction = sku_sort()
        direction = ("asc" if direction == "desc" else "desc") if key == current else ("asc" if key == "sku" else "desc")
        ui.update_select("m2_sku_sort", selected=f"{key}:{direction}")

    @reactive.effect
    @reactive.event(input.m2_open_sku)
    async def _open_sku():
        """Selecting a SKU shows its inventory and sales trend in this store."""
        sku = input.m2_open_sku()
        if not sku:
            return
        ui.update_selectize("m2_trend_skus", selected=[sku])
        ui.update_navset("m2_detail_tabs", selected="trend")
        await session.send_custom_message("m2-scroll-to", {"id": "m2_detail_tabs"})

    @reactive.effect
    @reactive.event(input.m2_sku_filter, input.m2_sku_search, input.m2_sku_rows, input.m2_sku_sort)
    def _sku_reset():
        sku_page.set(0)

    @reactive.effect
    @reactive.event(input.m2_sku_next)
    def _sku_next():
        sku_page.set(sku_page() + 1)

    @reactive.effect
    @reactive.event(input.m2_sku_prev)
    def _sku_prev():
        sku_page.set(max(0, sku_page() - 1))

    @reactive.calc
    def sku_view():
        rows = _sku_rows()
        flt = input.m2_sku_filter() or "all"
        text = (input.m2_sku_search() or "").strip().lower()
        keep = {"all": lambda r: True, "msl": lambda r: r["msl"] is True, "oos": lambda r: r["status"] is True,
                "alert": lambda r: r["alert"], "unestimated": lambda r: not r["estimable"]}.get(flt, lambda r: True)
        rows = [r for r in rows if keep(r) and (not text or text in f"{r['sku']} {r['product'] or ''}".lower())]
        key, direction = sku_sort()
        value = (lambda r: r["sku"]) if key == "sku" else (
            (lambda r: None if r["status"] is None else int(bool(r["status"]))) if key == "status" else
            (lambda r: r[key]))
        present = sorted((r for r in rows if value(r) is not None), key=lambda r: r["sku"])
        present.sort(key=value, reverse=(direction == "desc"))        # stable: ties keep SKU order
        missing = sorted((r for r in rows if value(r) is None), key=lambda r: r["sku"])
        return present + missing                                      # missing values last in both directions

    @render.ui
    @safe
    def m2_sku_table():
        store_id, response, detail, business = _detail_parts()
        if detail is None:
            return None
        if not detail["skus"]:
            reported = detail.get("reported_now") or []
            rows = [tags.tr(tags.td(tags.span(r["sku_id"], class_="m2-store-name"),
                                    tags.span(r.get("product_name") or "", class_="m2-store-meta"), class_="m2-cell-store"),
                            tags.td(_msl_text(r.get("is_msl")), **{"data-label": "MSL"}),
                            tags.td(_status(r.get("oos_observed")), **{"data-label": "Status"}),
                            tags.td(fmt_num(r.get("inventory_observed"), 0), class_="num", **{"data-label": "Inventory (units)"}),
                            tags.td(r["date"], **{"data-label": "Reported on"})) for r in reported]
            table = tags.div(tags.table(tags.thead(tags.tr(*[tags.th(h, scope="col", class_="num" if h == "Inventory (units)" else None)
                                                             for h in ("SKU", "MSL", "Status", "Inventory (units)", "Reported on")])),
                                        tags.tbody(*rows), class_="m2-table m2-cards", id="m2-reported-table"),
                             class_="m2-table-wrap") if rows else tags.p("No reported SKU rows in the past 30 days.",
                                                                        class_="m2-meta")
            return TagList(state("Predictions unavailable", "This store has no usable model score, so no stockout "
                                 "risk or sales at risk is shown. Reported information is below."), table)
        rows = sku_view()
        size = rows_per_page("m2_sku_rows")
        pages = max(1, -(-len(rows) // size))
        current = min(sku_page(), pages - 1)
        shown = rows[current * size:(current + 1) * size]
        key, direction = sku_sort()

        def sortable(label, sort_key, numeric=True):
            active = sort_key == key
            arrow = ("↑" if direction == "asc" else "↓") if active else "↕"
            attrs = {"aria-sort": "ascending" if direction == "asc" else "descending"} if active else {}
            return tags.th(tags.button(label, " ", tags.span(arrow, **{"aria-hidden": "true"}), type="button",
                                       class_="m2-sort" + (" active" if active else ""),
                                       onclick=f"m2SkuSort('{sort_key}')", **{"data-sort": sort_key}),
                           scope="col", class_="num" if numeric else None, **attrs)
        groups = tags.tr(tags.th("", scope="col"), tags.th("Reported now · as of " + detail["scoring_date"],
                                                           colspan="4", scope="colgroup", class_="m2-group now"),
                         tags.th("Next 7 days", colspan="5", scope="colgroup", class_="m2-group next"),
                         tags.th("", scope="col"))
        head = tags.tr(sortable("SKU", "sku", numeric=False), tags.th("MSL", scope="col"),
                       sortable("Status", "status", numeric=False), sortable("Inventory", "inventory"),
                       sortable("OOS days · 28", "oos_days"), sortable("Stockout risk", "risk"),
                       tags.th("Alert", scope="col"), sortable("Units short", "units_short"),
                       tags.th("Price (USD)", scope="col", class_="num"), sortable("Sales at risk", "sales"),
                       tags.th("Action", scope="col"))
        body = [tags.tr(
            {"data-sku": r["sku"], "data-status": "" if r["status"] is None else str(int(r["status"])),
             "data-risk": "" if r["risk"] is None else str(r["risk"]),
             "data-sales": "" if r["sales"] is None else str(r["sales"]),
             "data-oos-days": "" if r["oos_days"] is None else str(r["oos_days"])},
            tags.td(tags.button(tags.span(r["sku"], class_="m2-store-name"),
                                tags.span(r["product"] or "", class_="m2-store-meta"), type="button",
                                class_="m2-sku-link", onclick=f"m2OpenSku('{r['sku']}')",
                                **{"aria-label": f"Show inventory and sales trend for {r['sku']}", "data-sku": r["sku"]}),
                    class_="m2-cell-store"),
            tags.td(_msl_text(r["msl"]), **{"data-label": "MSL (as of)"}),
            tags.td(r["status_text"], **{"data-label": "Status now"}),
            tags.td(fmt_num(r["inventory"], 0) if r["inventory"] is not None else "—", class_="num",
                    **{"data-label": "Inventory (units)"}),
            tags.td(_dash(r["oos_days"]) + (f" (+{r['unreported_days']} unreported)" if r["unreported_days"] else ""),
                    class_="num", title=(f"{r['days_with_rows']} of {service_module.OOS_WINDOW_DAYS} days have data; "
                                         "days without data are not counted") if r["days_with_rows"] is not None else None,
                    **{"data-label": "Reported OOS days · past 28"}),
            tags.td(fmt_pct(r["risk"]), class_="num", **{"data-label": "Stockout risk · 7 days"}),
            tags.td(tags.span("Alert" if r["alert"] else "No alert", class_="m2-pill " + ("alert" if r["alert"] else "plain")),
                    **{"data-label": "Alert"}),
            tags.td(fmt_num(r["units_short"], 0) if r["units_short"] is not None else "—", class_="num",
                    **{"data-label": "Units short (scenario)"}),
            tags.td("—" if r["price"] is None else f"${r['price']:,.2f}", class_="num", **{"data-label": "Price (USD)"}),
            tags.td(_usd(r["sales"]) if r["sales"] is not None else tags.span("Unavailable", title="; ".join(r["reasons"])),
                    class_="num", **{"data-label": "Sales at risk"}),
            tags.td(r["action"], **{"data-label": "Action"})) for r in shown]
        caption = tags.caption("Reported now = the as-of report (unreported is unknown, not in stock). Next 7 days: "
                               "stockout risk is the model's chance of any stockout day; units short and sales at risk "
                               "are the 7-day scenario at the stated price basis — not lost sales or revenue.")
        if not shown:
            return state("No SKUs match", "No SKUs in this store match these filters.")
        return tags.div(tags.table(caption, tags.thead(groups, head), tags.tbody(*body),
                                   class_="m2-table m2-cards m2-sku-table", id="m2-sku-table"),
                        tags.p(f"Showing {current * size + 1}–{current * size + len(shown)} of {len(rows)} SKUs",
                               class_="m2-meta", id="m2-sku-count"),
                        class_="m2-table-wrap")

    @render.ui
    @safe
    def m2_sku_page_label():
        rows = sku_view() if _detail_parts()[2] else []
        size = rows_per_page("m2_sku_rows")
        pages = max(1, -(-len(rows) // size))
        return tags.span(f"Page {min(sku_page(), pages - 1) + 1} of {pages}", class_="m2-meta")

    @render.ui
    @safe
    def m2_trend_chart():
        store_id, response, detail, business = _detail_parts()
        if detail is None:
            return None
        chosen = [s for s in (input.m2_trend_skus() or [])][:5]
        if not chosen:
            return state("No SKU selected", "Choose up to five SKUs to see their inventory trend.")
        import plotly.graph_objects as go_
        from plotly.subplots import make_subplots
        as_of = date.fromisoformat(detail["scoring_date"])
        history = detail.get("history") or []
        projection = business.get("projection") or {}
        probs = {s["sku_id"]: s["probability"] for s in detail["skus"]}
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32], vertical_spacing=0.12,
                            subplot_titles=("Inventory (units): reported, then the 7-day scenario",
                                            "Units sold per day (reported)"))
        all_days = [(as_of - timedelta(days=n)).isoformat() for n in range(29, -1, -1)]
        future = [(as_of + timedelta(days=n)).isoformat() for n in range(1, 8)]
        notes = []
        for i, sku in enumerate(chosen):
            color = SKU_COLORS[i % len(SKU_COLORS)]
            rows = {h["date"]: h for h in history if h["sku_id"] == sku}
            inv = [rows.get(d, {}).get("inventory_observed") for d in all_days]       # unreported days stay gaps
            sold = [rows.get(d, {}).get("units_sold_observed") for d in all_days]
            oos_days = [d for d in all_days if rows.get(d, {}).get("oos_observed")]
            risk = probs.get(sku)
            label = f"{sku} · model 7-day stockout risk {fmt_pct(risk)}" if risk is not None else sku
            fig.add_trace(go_.Scatter(x=all_days, y=inv, mode="lines+markers", name=label, connectgaps=False,
                                      line=dict(color=color, width=2), marker=dict(size=4),
                                      legendgroup=sku, hovertemplate="%{x}: %{y} units reported<extra>" + sku + "</extra>"),
                          row=1, col=1)
            if oos_days:
                fig.add_trace(go_.Scatter(x=oos_days, y=[0] * len(oos_days), mode="markers", name=f"{sku} reported OOS",
                                          marker=dict(symbol="x", size=9, color=color), legendgroup=sku,
                                          showlegend=False, hovertemplate="%{x}: reported out of stock<extra>"
                                                                           + sku + "</extra>"), row=1, col=1)
            path = projection.get(sku)
            start = rows.get(as_of.isoformat(), {}).get("inventory_observed")
            if path is not None and start is not None:
                fig.add_trace(go_.Scatter(x=[as_of.isoformat()] + future, y=[start] + [d["stock_end"] for d in path],
                                          mode="lines", name=f"{sku} scenario (not a forecast)", legendgroup=sku,
                                          line=dict(color=color, width=2, dash="dash"),
                                          hovertemplate="%{x}: %{y:.0f} units left in the scenario<extra>"
                                                        + sku + "</extra>"), row=1, col=1)
                first_short = next((d for d in path if d["short"] > 0), None)
                if first_short:
                    day = future[first_short["day"] - 1]
                    fig.add_trace(go_.Scatter(x=[day], y=[0], mode="markers+text", text=["runs out"],
                                              textposition="top center", showlegend=False, legendgroup=sku,
                                              marker=dict(symbol="triangle-down", size=11, color=color),
                                              hovertemplate=f"Scenario: demand not covered from {day}<extra>{sku}</extra>"),
                                  row=1, col=1)
            elif path is None:
                notes.append(f"{sku}: no scenario — " + ("; ".join((business.get("skus") or {}).get(sku, {}).get("reasons")
                                                                    or ["inputs missing"])))
            fig.add_trace(go_.Bar(x=all_days, y=sold, name=f"{sku} units sold", marker_color=color, opacity=0.55,
                                  legendgroup=sku, showlegend=False,
                                  hovertemplate="%{x}: %{y} units sold (reported)<extra>" + sku + "</extra>"),
                          row=2, col=1)
        fig.add_vline(x=as_of.isoformat(), line=dict(color="#65595a", dash="dot", width=1))
        fig.add_annotation(x=as_of.isoformat(), y=1, yref="paper", text="Reported as of", showarrow=False,
                           xanchor="right", font=dict(size=11, color="#65595a"))
        fig.update_yaxes(row=1, col=1, rangemode="tozero")
        fig.update_yaxes(row=2, col=1, rangemode="tozero")
        fig.update_annotations(font=dict(size=12, color="#4a3f40"), selector=dict(text="Units sold per day (reported)"))
        fig.update_layout(height=470, barmode="group", legend=dict(orientation="h", y=-0.12))
        return TagList(plot(fig, "m2-trend-chart"),
                       tags.p("Solid: reported inventory over the past 30 days; gaps are days without a report "
                              "(unknown, not zero). ✕ marks a reported stockout. Dashed: the 7-day scenario used for "
                              "sales at risk — daily sales at the prior in-stock rate, the scheduled units added on the "
                              "next scheduled receipt day; not a model forecast or a confirmed delivery. The model's "
                              "7-day stockout risk is one number for the whole window, not a daily probability.",
                              class_="m2-meta"),
                       *[tags.p(n, class_="m2-meta") for n in notes])

    @render.ui
    @safe
    def m2_detail_oos():
        store_id, response, detail, business = _detail_parts()
        if detail is None:
            return None
        days = business.get("oos_days") or {}
        window = business.get("oos_window") or [None, detail["scoring_date"]]
        rows = sorted(((s, d) for s, d in days.items()), key=lambda x: (-x[1]["reported_oos_days"], x[0]))
        if not rows:
            return state("No reports", "No SKU reports for this store in the past 28 days.")
        products = {s["sku_id"]: s["product_name"] for s in detail["skus"]}
        with_oos = [(s, d) for s, d in rows if d["reported_oos_days"]]
        head = tags.div(tags.h2("Out-of-stock days per SKU · past 28 days", class_="m2-h"),
                        tags.p(f"Reported out-of-stock days {window[0]} – {window[1]}. "
                               + (business.get("oos_coverage_note") or ""), class_="m2-meta", id="m2-detail-oos-note"),
                        class_="m2-card-head")
        if not with_oos:
            chart = state("No stockouts reported", "No SKU was reported out of stock in this window.")
        else:
            import plotly.graph_objects as go_
            top = with_oos[:12][::-1]
            groups = {s: ("MSL" if d.get("is_msl") is True else "Non-MSL" if d.get("is_msl") is False else "Unknown")
                      for s, d in top}
            fig = go_.Figure()
            for g in ("MSL", "Non-MSL", "Unknown"):
                part = [(s, d) for s, d in top if groups[s] == g]
                if part:
                    fig.add_trace(go_.Bar(y=[s for s, _ in part], x=[d["reported_oos_days"] for _, d in part], name=g,
                                          orientation="h", marker_color=MSL_COLORS[g],
                                          customdata=[products.get(s) or "" for s, _ in part],
                                          hovertemplate="%{y} · %{customdata}<br>" + g + ": %{x} OOS days<extra></extra>"))
            fig.update_layout(barmode="stack", xaxis_title="Reported out-of-stock days", height=max(240, 30 * len(top) + 90),
                              xaxis=dict(tickformat=",d", rangemode="tozero"),
                              legend=dict(orientation="h", y=-0.25), yaxis=dict(categoryorder="array",
                                                                                 categoryarray=[s for s, _ in top]))
            chart = plot(fig, "m2-detail-oos-bar")
        table = tags.div(tags.table(
            tags.thead(tags.tr(*[tags.th(h, scope="col", class_="num" if h in ("OOS days", "Unreported", "Days with data")
                                         else None)
                                 for h in ("SKU", "MSL (as of)", "Now", "OOS days", "Unreported", "Days with data")])),
            tags.tbody(*[tags.tr(tags.td(tags.button(tags.span(s, class_="m2-store-name"),
                                                     tags.span(products.get(s) or "", class_="m2-store-meta"), type="button",
                                                     class_="m2-sku-link", onclick=f"m2OpenSku('{s}')",
                                                     **{"aria-label": f"Show inventory and sales trend for {s}",
                                                        "data-sku": s}), class_="m2-cell-store"),
                                 tags.td(_msl_text(d.get("is_msl")), **{"data-label": "MSL (as of)"}),
                                 tags.td(_as_of_status(d), **{"data-label": "Now"}),
                                 tags.td(str(d["reported_oos_days"]), class_="num", **{"data-label": "OOS days"}),
                                 tags.td(str(d["unreported_days"]), class_="num", **{"data-label": "Unreported"}),
                                 tags.td(f"{d['days_with_rows']} / {d['window_days']}", class_="num",
                                         **{"data-label": "Days with data"}))
                         for s, d in rows]),
            class_="m2-table m2-cards", id="m2-detail-oos-table"), class_="m2-table-wrap")
        return TagList(head, chart, table)

    @render.ui
    @safe
    def m2_shelf():
        store_id, response, detail, business = _detail_parts()
        if detail is None:
            return None
        audits = business.get("audits") or {}
        fresh = {s: a for s, a in audits.items() if a["status"] == "Fresh"}
        latest = max((a["date"] for a in audits.values() if a.get("date") and a["status"] != "Future audit excluded"),
                     default=None)
        noncompliant = sorted(s for s, a in fresh.items() if a["compliant"] is False)
        secondary = [a["secondary_display"] for a in fresh.values() if a.get("secondary_display") is not None]
        return tags.div(
            tags.h2("Shelf execution", class_="m2-h"),
            tags.p(f"Latest audit: {latest or 'none on record'} · " + (
                f"planogram non-compliant for {len(noncompliant)} of {len(fresh)} audited SKU(s)"
                + (f" ({', '.join(noncompliant)})" if noncompliant else "") if fresh else
                f"no audit in the last {bp.AUDIT_MAX_AGE_DAYS} days") + (
                f" · secondary display on {sum(1 for x in secondary if x)} of {len(secondary)}" if secondary else
                " · secondary display unknown")),
            tags.p("A past audit is a priority signal, not proof of today's shelf; no shelf-loss dollar amount is "
                   "assumed.", class_="m2-meta"), id="m2-shelf")

    @render.ui
    @safe
    def m2_quality():
        store_id, response, detail, business = _detail_parts()
        if detail is None:
            return None
        cov = business.get("coverage") or {}
        est = business.get("skus") or {}
        missing = [(s, e["reasons"]) for s, e in sorted(est.items()) if not e.get("estimable")]
        return tags.div(
            tags.h2("Estimate quality", class_="m2-h"),
            tags.p(tags.b(f"Confidence: {business.get('confidence') or 'Unavailable'}"),
                   f" · price basis: {business.get('price_basis') or '—'}"),
            tags.ul(*[tags.li(f"{label}: {cov.get(k, 0)} of {cov.get('skus', 0)} SKUs") for k, label in (
                ("inventory", "Current inventory reported"), ("sales_rate", "Prior in-stock sales rate"),
                ("schedule", "Known replenishment schedule"), ("price", "Usable price"),
                ("fresh_audit", f"Shelf audit within {bp.AUDIT_MAX_AGE_DAYS} days"))], class_="m2-list"),
            tags.ul(*[tags.li(f"{s}: not estimated — " + "; ".join(r)) for s, r in missing], class_="m2-list")
            if missing else None,
            tags.h3("Assumptions and limitations", class_="m2-h"),
            tags.ul(*[tags.li(a) for a in business.get("assumptions") or []],
                    *[tags.li(x) for x in response.limitations], class_="m2-list"), id="m2-quality")

    # -------------------------------------------------------------- ML Operations (separate entry)
    @render.ui
    @safe
    def m2_ops_header():
        if mode() != "ops":
            return None
        record = service.record if service else {}
        outputs = record.get("model_outputs", {}) if service else {}
        return TagList(
            tags.div(tags.h1("ML Operations", class_="m2-title"),
                     tags.p(tags.span("Model ", tags.b(outputs.get("model_version") or "None released")),
                            tags.span("Data as of ", tags.b(str(record.get("as_of") or "N/A"))),
                            tags.span("Publication ", tags.b(str(outputs.get("publication_kind") or "Base serving"))),
                            class_="m2-meta")),
            tags.div(tags.strong("Demo entry, read-only. "),
                     "Uses the demo manager's data permission; not production authentication. This page observes model "
                     "quality only: it cannot train, change thresholds, publish or approve.", class_="m2-banner info",
                     role="note", id="m2-ops-note"))

    @render.ui
    @safe
    def m2_health():
        p = who()
        if mode() != "ops":
            return None
        if service is None or p is None:
            return state("Unavailable", unavailable_reason or "Sign in required.")
        response = service.get_model_health(p)
        if response.status == "Permission_denied":
            return state("No access", "Model reports need the demo manager's full-store permission.", "bad")
        if response.status in ("Unavailable", "Empty"):
            return state("No model reports", response.message or "No aggregated model reports in this publication.")
        rows = response.rows
        dev = [r for r in rows if r["evaluation_kind"] == "development" and r["group_kind"] == "all"]
        selected = [r for r in rows if r["selected"] and r["evaluation_kind"] in ("iterative_validation", "holdout_confirmation")]
        return TagList(_selected_card(rows, selected), _comparison(dev), _groups(rows),
                       tags.div(tags.h2("Limitations", class_="m2-h"),
                                tags.ul(*[tags.li(t) for t in response.limitations], class_="m2-list"),
                                class_="m2-card"))

    @render.ui
    @safe
    def m2_monitoring():
        p = who()
        if mode() != "ops":
            return None
        if service is None or p is None:
            return state("Unavailable", unavailable_reason or "Sign in required.")
        response = service.get_monitoring(p)
        if response.status == "Permission_denied":
            return state("No access", "Monitoring needs the demo manager's full-store permission.", "bad")
        if response.status == "Unavailable" or not response.rows:
            return state("No monitoring yet", response.message or "No monitoring package in this publication.")
        return _monitoring(response.rows)


def _status(oos):
    return "Unknown" if oos is None else ("Out of stock" if oos else "In stock")


def _as_of_status(day):
    """Store x SKU state on the as-of date: no row is not the same as a row without a report."""
    if day.get("as_of_report") == "missing":
        return "No as-of report"
    return "Not reported" if day.get("current") is None else _status(day.get("current"))


def _msl_text(value):
    return "Yes" if value is True else ("No" if value is False else "Unknown")


NOT_RANKED = "Not ranked: no priority score (sales at risk unavailable or no usable model score)"


def _rank_cell(row):
    """Overall rank, or an explicit 'Not ranked' for stores without a priority score."""
    if row.get("overall_rank") is None:
        return tags.td(tags.span("—", **{"aria-hidden": "true"}), tags.span("Not ranked", class_="m2-sr"),
                       class_="num m2-rank m2-unranked", title=NOT_RANKED, **{"data-label": "Overall rank"})
    return tags.td(str(row["overall_rank"]), class_="num m2-rank", **{"data-label": "Overall rank"})


NUMERIC_HEADERS = {"Rows", "Positives", "Precision", "Recall", "ROC-AUC", "AP", "Label coverage", "Brier", "ECE",
                   "Trials", "Completed", "Feasible", "Best precision", "Its recall", "Its ROC-AUC"}


def th(label):
    return tags.th(label, scope="col", class_="num" if label in NUMERIC_HEADERS else None)


def _metric_row(r):
    undefined = any(r.get(k) is None for k in ("precision", "recall", "roc_auc"))
    return [tags.td(fmt_int(r["sample_count"]), class_="num"), tags.td(fmt_int(r["positive_count"]), class_="num"),
            tags.td(fmt_pct(r["precision"]), class_="num"), tags.td(fmt_pct(r["recall"]), class_="num"),
            tags.td(fmt_num(r["roc_auc"]), class_="num"), tags.td(fmt_num(r["average_precision"]), class_="num"),
            tags.td(gate_badge(r["meets_numeric_targets"], undefined))]


def _selected_card(rows, selected):
    if not selected:
        dev = [r for r in rows if r["selected"] and r["group_kind"] == "all"]
        return tags.div(tags.h2("Selected model", class_="m2-h"),
                        state("No released evaluation", "No validation/holdout report is published for a selected model.")
                        if not dev else tags.p(f"Development selection: {dev[0]['candidate_id']} ({dev[0]['algorithm']}).",
                                               class_="m2-muted"), class_="m2-card")
    overall = [r for r in selected if r["group_kind"] == "all"]
    first = overall[0]
    table = tags.table(
        tags.caption("Overall complete-label cohort; targets P ≥ 75%, R ≥ 70%, ROC-AUC ≥ 0.80 simultaneously."),
        tags.thead(tags.tr(*[th(h) for h in ("Evaluation", "Window", "Rows", "Positives", "Precision",
                                                                "Recall", "ROC-AUC", "AP", "Gate", "Label coverage",
                                                                "Brier", "ECE")])),
        tags.tbody(*[tags.tr(tags.td(r["evaluation_kind"].replace("_", " ")),
                             tags.td(f"{r['window_start']} – {r['window_end']}"), *_metric_row(r)[:6],
                             tags.td(gate_badge(r["meets_numeric_targets"])), tags.td(fmt_pct(r["label_coverage"]), class_="num"),
                             tags.td(fmt_num(r["brier_score"]), class_="num"), tags.td(fmt_num(r["ece"]), class_="num"))
                     for r in overall]), class_="m2-table", id="m2-selected-table")
    return tags.div(
        tags.h2("Selected model", class_="m2-h"),
        tags.dl(tags.dt("Algorithm"), tags.dd(first["algorithm"]), tags.dt("Model version"), tags.dd(first["model_version"]),
                tags.dt("Development trial"), tags.dd(first["candidate_id"]), tags.dt("Parameters"),
                tags.dd(tags.code(first["parameters_json"])), class_="m2-kv"),
        tags.p("Validation is an iterative re-use of a previously examined window; the holdout is a disclosed "
               "confirmation, not an untouched independent test.", class_="m2-muted"),
        tags.div(table, class_="m2-table-wrap"), class_="m2-card")


def _comparison(dev):
    import plotly.graph_objects as go
    dev2 = [r for r in dev if r["fold_id"] == "rolling_dev_2"]
    complete = [r for r in dev2 if r["evaluation_status"] == "Completed" and r["precision"] is not None]
    if not dev2:
        return tags.div(tags.h2("Algorithm comparison", class_="m2-h"), state("No benchmark", "No development trials."),
                        class_="m2-card")
    fig = go.Figure()
    symbols = {"lightgbm": "circle", "xgboost": "square", "random_forest": "diamond", "logistic_regression": "triangle-up"}
    # Three tree models take the first three palette slots (all-pairs safe); the baseline is neutral.
    algorithm_colors = {"lightgbm": CHART_COLORS[0], "xgboost": CHART_COLORS[1], "random_forest": CHART_COLORS[2]}
    for algorithm in sorted({r["algorithm"] for r in complete}):
        mine = [r for r in complete if r["algorithm"] == algorithm]
        fig.add_trace(go.Scatter(x=[r["recall"] for r in mine], y=[r["precision"] for r in mine], mode="markers",
                                 name=algorithm.replace("_", " "),
                                 marker=dict(symbol=symbols.get(algorithm, "circle"), size=[14 if r["selected"] else 8 for r in mine],
                                             color=algorithm_colors.get(algorithm, CHART_NEUTRAL),
                                             line=dict(width=[2 if r["selected"] else 0 for r in mine], color="#0f1f2e")),
                                 text=[r["candidate_id"] for r in mine],
                                 hovertemplate="%{text}<br>Recall %{x:.3f}<br>Precision %{y:.3f}<extra></extra>"))
    fig.add_hline(y=TARGETS["precision"], line_dash="dot", line_color="#5b738b", annotation_text="P target")
    fig.add_vline(x=TARGETS["recall"], line_dash="dot", line_color="#5b738b", annotation_text="R target")
    fig.update_layout(xaxis_title="rolling_dev_2 recall", yaxis_title="rolling_dev_2 precision",
                      legend=dict(orientation="h", y=-0.25))
    summary = []
    for algorithm in sorted({r["algorithm"] for r in dev2}):
        mine = [r for r in dev2 if r["algorithm"] == algorithm]
        done = [r for r in mine if r["evaluation_status"] == "Completed"]
        feasible = [r for r in done if r["recall"] is not None and r["roc_auc"] is not None
                    and r["recall"] >= TARGETS["recall"] and r["roc_auc"] >= TARGETS["roc_auc"]]
        best = max(feasible, key=lambda r: r["precision"] or 0) if feasible else None
        summary.append(tags.tr(tags.td(algorithm.replace("_", " ")), tags.td(str(len(mine)), class_="num"),
                               tags.td(str(len(done)), class_="num"), tags.td(str(len(feasible)), class_="num"),
                               tags.td(fmt_pct(best["precision"]) if best else "None", class_="num"),
                               tags.td(fmt_pct(best["recall"]) if best else "None", class_="num"),
                               tags.td(fmt_num(best["roc_auc"]) if best else "None", class_="num"),
                               tags.td("Selected" if any(r["selected"] for r in mine) else "")))
    return tags.div(
        tags.h2("Algorithm comparison (development)", class_="m2-h"),
        tags.p("Each point is a completed Optuna trial evaluated on rolling_dev_2 with the dev1 calibrator and "
               "thresholds. dev2 informed the search, so these are development results.", class_="m2-muted"),
        plot(fig, "m2-algorithm-comparison"),
        tags.div(tags.table(tags.thead(tags.tr(*[th(h) for h in (
            "Algorithm", "Trials", "Completed", "Feasible", "Best precision", "Its recall", "Its ROC-AUC", "")])),
            tags.tbody(*summary), class_="m2-table", id="m2-comparison-table"), class_="m2-table-wrap"),
        class_="m2-card")


def _groups(rows):
    blocks = []
    for kind in ("iterative_validation", "holdout_confirmation"):
        mine = [r for r in rows if r["selected"] and r["evaluation_kind"] == kind and r["group_kind"] != "all"]
        if not mine:
            continue
        body = [tags.tr(tags.td(_group_label(r)), *_metric_row(r)) for r in mine]
        blocks.append(tags.div(tags.h3(kind.replace("_", " ").capitalize() + ": segments and current state", class_="m2-h"),
                               tags.div(tags.table(tags.thead(tags.tr(*[th(h) for h in (
                                   "Group", "Rows", "Positives", "Precision", "Recall", "ROC-AUC", "AP", "Targets")])),
                                   tags.tbody(*body), class_="m2-table"), class_="m2-table-wrap"),
                               style="margin-bottom:12px"))
    if not blocks:
        return None
    return tags.div(tags.h2("Segment and current-state results", class_="m2-h"),
                    tags.p("No separate subgroup gate exists; shortfalls are shown, not hidden. Undefined means a "
                           "single class or zero alerts.", class_="m2-muted"), *blocks, class_="m2-card")


def _group_label(r):
    if r["group_kind"] == "segment":
        try:
            channel, tier = json.loads(r["group_id"])
            return f"{channel} · {tier}"
        except ValueError:
            return r["group_id"]
    return {"not_oos": "Currently in stock", "oos": "Currently out of stock", "unknown": "Current state unknown"}.get(
        r["group_id"], r["group_id"])


def _monitoring(rows):
    import plotly.graph_objects as go
    cards = []
    for r in rows:
        drift = json.loads(r["drift_json"] or "{}")
        reasons = json.loads(r["reasons_json"] or "[]")
        top = sorted(drift.get("numeric", {}).items(), key=lambda kv: -kv[1]["psi"])[:8]
        chart = None
        if top:
            fig = go.Figure(go.Bar(x=[v["psi"] for _, v in top][::-1], y=[k for k, _ in top][::-1], orientation="h",
                                   marker_color=CHART_COLORS[0]))
            fig.add_vline(x=0.2, line_dash="dot", line_color="#8a4b00", annotation_text="warning .20")
            fig.update_layout(xaxis_title="Population stability index vs training", height=300)
            chart = plot(fig, f"m2-psi-{r['model_version']}"[:60])
        performance = r["performance_status"]
        cards.append(tags.div(
            tags.h2(f"Model {r['model_version']}", class_="m2-h"),
            tags.dl(tags.dt("Observation cutoff"), tags.dd(str(r["observation_cutoff"])),
                    tags.dt("Matured window"), tags.dd(f"{r['window_start']} – {r['window_end']}"),
                    tags.dt("Issued predictions"), tags.dd(fmt_int(r["issued_count"])),
                    tags.dt("Matured & complete"), tags.dd(fmt_int(r["eligible_count"])),
                    tags.dt("Pending outcomes"), tags.dd(fmt_int(r["pending_count"])),
                    tags.dt("Performance"), tags.dd(performance.replace("_", " ")),
                    tags.dt("Input drift"), tags.dd(r["drift_status"].replace("_", " ")),
                    tags.dt("Retraining"), tags.dd(r["retrain_status"].replace("_", " ")), class_="m2-kv"),
            state("Pending outcomes", "Issued predictions have not matured yet (7-day horizon). No performance trend "
                  "is drawn until real outcomes exist.") if performance == "Pending_outcomes" else
            tags.p(f"Precision {fmt_pct(r['precision'])}, recall {fmt_pct(r['recall'])}, ROC-AUC {fmt_num(r['roc_auc'])} "
                   f"on {fmt_int(r['eligible_count'])} matured rows.", class_="m2-muted"),
            tags.h3("Input drift (top features)", class_="m2-h") if chart else None, chart,
            tags.ul(*[tags.li(t) for t in reasons], class_="m2-list") if reasons else
            tags.p("No drift or performance trigger.", class_="m2-muted"),
            class_="m2-card"))
    return TagList(tags.p("Monitoring evaluates issued predictions once outcomes mature; it is not the release test.",
                          class_="m2-muted"), *cards)
