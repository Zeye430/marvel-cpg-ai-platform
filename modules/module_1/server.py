"""
modules/module_1/server.py
==========================
Server logic for Module 1: Business Performance (Commercial Execution Analytics).

Overview: the five KPI cards (the selected one's trend and, collapsed, what it measures and how it is
calculated) and the store exception list. Pages: Geography & flow (state/city map with the stores of a
city; sell-in vs sell-out per biweekly period with how long a gap has lasted), Store cards, Out-of-stock,
Shelf execution and Trade terms. Chart builders are in charts.py and the HTML panels in panels.py.

Data comes only from the shared business publication pinned by the app runtime (modules/module_1/data.py),
restricted to the signed-in principal's account scope and loaded on the first visit to the module, in a
background task. The page starts in the Reported view; the "Estimated data view" toggle loads the separate
estimate views (explicit opt-in) in the background and switches every availability, MSL and flow
figure to them at once. A failure to load it keeps the Reported view and says why. Sign-out or another
account resets the toggle. Unknown inputs stay Unknown/N/A; heuristics and unapproved rules are labelled.
"""

import asyncio
import functools

import pandas as pd
from htmltools import TagList, tags
from shiny import reactive, render, req, ui

from shared import ui_kit as kit

from .charts import (CHARTS, STATUS_CODES, Chart, NonFiniteChart, _reject, chart_layout, city_frame, city_key,  # noqa: F401
                     figure_json,
                     fmt as _fmt, locked, palette, plot, render_chart, state)
from .data import Filters, composite_scores
from .panels import (KPI_SPECS, KPI_TARGETS, KPI_TILES, _delta, city_stores, flow_summary,  # noqa: F401
                     flow_table, kpi_detail, kpi_tiles, mode_toggle, ranking_panel, store_card)
from .ui import PAGE_ICONS, PAGES

MODULE_KEY = "module_1"

PAGE_HEADS = {
    "overview": ("Business performance", None),
    "geo": ("Commercial strategy & geospatial execution",
            "Where stores execute well or poorly, and whether invoiced stock keeps pace with what consumers buy."),
    "stores": ("Store cards", "One store's execution profile, compared with similar stores in your scope."),
    "oos": ("Out-of-stock & supply chain insights",
            "Which products are out of stock, for how long, and what it may cost."),
    "shelf": ("Shelf execution & compliance",
              "Planogram compliance and must-stock availability by brand."),
    "trade": ("Trade terms & commercial guardrails",
              "Trade discounts against the guardrail reference, and whether execution commitments are delivered."),
}


# ---------------------------------------------------------------- filter pills and the welcome screen
# Weeks run Monday to Sunday. "Last week" and "Last 4 weeks" are the latest complete weeks; the to-date
# presets run through the data's last day.
DATE_PRESETS = {"1w": "Last week", "4w": "Last 4 weeks", "mtd": "Month to date", "qtd": "Quarter to date",
                "ytd": "Year to date", "all": "All data"}
DEFAULT_PRESET = "4w"
MSL_OPTIONS = {"all": "All products", "msl": "MSL products only", "non": "Non-MSL products only"}
MSL_PILL = {"all": "All", "msl": "MSL only", "non": "Non-MSL only"}


def snap(a, b, first, last):
    """Whole weeks (Monday to Sunday) covering a..b, within the data's first and last day."""
    a, b, first, last = (pd.Timestamp(x) for x in (a, b, first, last))
    if a > b:
        a, b = b, a
    return max(a - pd.Timedelta(days=a.dayofweek), first), min(b + pd.Timedelta(days=6 - b.dayofweek), last)


def preset_span(key, first, last):
    """The whole weeks a date preset covers, counted back from the data's last day."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    if key in ("1w", "4w"):
        end = last if last.dayofweek == 6 else last - pd.Timedelta(days=last.dayofweek + 1)   # last complete week
        if end < first:
            return snap(first, last, first, last)
        return snap(end - pd.Timedelta(days=6 if key == "1w" else 27), end, first, last)
    start = {"mtd": pd.Timestamp(last.year, last.month, 1), "qtd": last.to_period("Q").start_time,
             "ytd": pd.Timestamp(last.year, 1, 1), "all": first}[key]
    return snap(max(first, start), last, first, last)


def matching_preset(span_, first, last, chosen=None):
    """The preset whose span is ``span_``, or None (a custom range). Presets can cover the same weeks
    (a month starting on a Monday: month to date = last 4 weeks plus the open week); the one the user
    ``chosen`` is named then."""
    if chosen in DATE_PRESETS and preset_span(chosen, first, last) == tuple(span_):
        return chosen
    for key in DATE_PRESETS:
        if preset_span(key, first, last) == tuple(span_):
            return key
    return None


def choice_label(selected, names, empty_means_all=True):
    """(pill value, changed): "All", one name, "k of n" or "None"; changed = narrower than everything."""
    selected = [s for s in selected or () if s in names]
    if not selected:
        return ("All", False) if empty_means_all else ("None", True)
    if len(selected) == len(names):
        return ("All" if empty_means_all else f"All {len(names)}"), False
    if len(selected) == 1:
        return names[selected[0]], True
    return f"{len(selected)} of {len(names)}", True


def msl_skus(info, msl="all"):
    """The SKUs the MSL pill allows, in list order: all, MSL products only or non-MSL products only."""
    required = set(info.get("msl_products") or ())
    return [s for s in info["products"] if msl not in ("msl", "non") or (s in required) == (msl == "msl")]


def sku_choices(info, skus=None):
    """SKU checkbox labels in category order (``skus``: only these); the category is carried for the list's
    group headings."""
    names, categories = info.get("product_names") or {}, info.get("product_categories") or {}
    keep = set(info["products"] if skus is None else skus)
    return {sku: tags.span(names.get(sku, label), **{"data-category": categories.get(sku, "")})
            for sku, label in info["products"].items() if sku in keep}


def greeting(hour):
    """A time-of-day greeting from the browser's local hour (None: no clock received)."""
    if hour is None:
        return "Welcome"
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 23:
        return "Good evening"
    return "Working late"


def display_name(name):
    return (name or "").replace("(demo)", "").strip()


def welcome_detail(stores=None, as_of=None):
    """The line under the greeting; the scope summary is added once it has been read (in the background)."""
    detail = ["Getting your business performance ready"]
    if stores:
        detail.append(f" for {stores} stores")
    if as_of:
        detail.append(f", with data through {as_of}")
    return "".join(detail) + "."


def welcome(name, clock=None, detail=None):
    """Shown at once while the first load runs (it reads nothing): who it is for, what is being prepared, and
    that it is still working. ``detail``: the line under the greeting (default: without the scope summary)."""
    clock = clock if isinstance(clock, dict) else {}
    hour = clock.get("hour")
    who = display_name(name)
    title = f"{greeting(hour if isinstance(hour, int) else None)}{', ' + who if who else ''}"
    return tags.section(
        tags.p(clock.get("day") or "", class_="m1-welcome-day") if clock.get("day") else None,
        tags.h1(title, class_="m1-welcome-title"),
        tags.div(detail if detail is not None else welcome_detail(), class_="m1-welcome-sub"),
        tags.div(tags.span(class_="m1-progress-bar"), class_="m1-progress", role="progressbar",
                 **{"aria-label": "Loading business performance"}),
        tags.p("The first visit takes a few seconds. Stockout Risk and the Copilot are ready in the sidebar "
               "meanwhile.", class_="m1-welcome-note"),
        tags.div(*[tags.div(tags.span(class_="m1-skel-line short"), tags.span(class_="m1-skel-line big"),
                            tags.span(class_="m1-skel-line"), class_="m1-skel-card") for _ in range(5)],
                 class_="m1-skeleton", **{"aria-hidden": "true"}),
        class_="m1-welcome", id="m1-welcome", role="status", **{"aria-live": "polite"})


def safe(function):
    """Unexpected failures render a sanitized error state, never a stack trace or other data."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - deliberate UI boundary
            if type(error).__name__ in ("SilentException", "SilentCancelOutputException"):
                raise
            return state("This section could not load", f"({type(error).__name__}). Change a filter or reload the "
                         "page; other sections are not affected.", "bad")
    return wrapper


def prior_filters(f):
    """The previous period of equal length (same stores and products)."""
    a, b = pd.Timestamp(f.start), pd.Timestamp(f.end)
    n = (b - a).days + 1
    return Filters(f.channels, f.regions, f.skus, f.all_skus, (a - pd.Timedelta(days=n)).date(),
                   (a - pd.Timedelta(days=1)).date())


_REP_CACHE = {}


def rep_directory(reader):
    """store_id -> [{name, title, avatar}] of the demo field reps assigned to it, read from the shared access scope
    (trusted server code; shown only in the manager's own store cards). No contact details. Cached per publication."""
    from shared.shell import DEMO_ACCOUNTS
    key = (reader.record.get("artifact_id"), reader.record.get("dataset_hash"))
    if key in _REP_CACHE:
        return _REP_CACHE[key]
    directory = {}
    for account in DEMO_ACCOUNTS:
        if account.role != "Field Rep":
            continue
        try:
            stores = reader.read("stores", account.principal_id, limit=10000)
        except Exception:  # noqa: BLE001 - an unassigned or unreadable rep is simply not listed
            continue
        person = {"name": account.name, "title": account.short_role or "Field sales rep", "avatar": account.avatar}
        for row in stores:
            directory.setdefault(row["store_id"], []).append(person)
    _REP_CACHE.clear()
    _REP_CACHE[key] = directory
    return directory


def _load_reason(error):
    """A sanitized reason for a failed load (class name only, except the platform's own messages)."""
    if isinstance(error, PermissionError):
        return "Your account has no data scope in the shared publication."
    if type(error).__name__ in ("SourceUnavailable", "DataUnavailable"):
        return str(error)
    return f"Shared data could not be loaded ({type(error).__name__})."


# ------------------------------------------------------------------ server

def module_1_server(input, output, session, context: dict, auth_state: dict):
    """Hosted entry point. ``context['runtime']`` is the pinned shared source (shared.runtime)."""
    runtime = context.get("runtime") if isinstance(context, dict) else None
    if runtime is None:
        return  # the legacy root app: the UI explains where the module runs; nothing is loaded
    module_label = context.get("module_label") or "Business Performance"
    stockout_nav = bool(context.get("stockout_nav"))
    active_module = auth_state.get("active_module") or (lambda: MODULE_KEY)
    data = reactive.value(None)                 # the Reported view
    estimated_data = reactive.value(None)       # the Estimated view, loaded on request
    mode = reactive.value("reported")
    estimate_state = reactive.value(("idle", ""))
    load_state = reactive.value(("signed_out", ""))
    choice_info = reactive.value(None)
    synced = reactive.value(False)          # filters received from the browser after the per-session update
    generation = reactive.value(0)          # bumped on every identity change; late results are discarded
    page = reactive.value("overview")
    scope_summary = reactive.value(None)    # (stores, as_of) for the welcome line, read in the background
    show_targets = reactive.value(False)
    flow_dim = reactive.value("channel")
    flow_all = reactive.value(False)
    city = reactive.value(None)
    rank_side = reactive.value("top")

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return pid()

    navigate = auth_state.get("navigate")

    @reactive.effect
    @reactive.event(input.m1_open_copilot)
    def _open_copilot():
        """Top-bar Copilot button: host navigation only (no question or filter is passed along)."""
        if navigate is not None and principal() is not None:
            navigate("module_4")

    @reactive.effect
    @reactive.event(input.m1_open_risk)
    def _open_risk():
        """A store's "Stockout risk" button: the browser has passed the store id to Stockout Risk, which checks
        the account's access itself; this only switches the visible module."""
        if stockout_nav and navigate is not None and principal() is not None:
            navigate("module_2")

    @reactive.extended_task
    async def load_task(pid, gen):
        def work():
            item = runtime.module1_pool().get(pid)
            return item, item.choices()
        try:
            item, choices = await asyncio.to_thread(work)
            return gen, pid, item, choices, None
        except Exception as error:  # sanitized
            return gen, pid, None, None, _load_reason(error)

    @reactive.extended_task
    async def estimate_task(pid, gen):
        def work():
            item = runtime.module1_pool().get(pid, "estimated")
            return item, item.estimate_info()
        try:
            item, info = await asyncio.to_thread(work)
            return gen, pid, item, info, None
        except Exception as error:  # sanitized
            return gen, pid, None, None, _load_reason(error)

    @reactive.effect
    async def _on_principal():
        pid = principal()
        with reactive.isolate():
            generation.set(generation() + 1)
            data.set(None)
            estimated_data.set(None)
            mode.set("reported")
            estimate_state.set(("idle", ""))
            choice_info.set(None)
            synced.set(False)
            chosen_preset.set(DEFAULT_PRESET)
            scope_summary.set(None)
            load_state.set(("idle", "") if pid else ("signed_out", ""))
            page.set("overview")
            city.set(None)
            flow_dim.set("channel")
            rank_side.set("top")
            ui.update_navset("m1_pages", selected="overview")
        await session.send_custom_message("m1-clear", {})

    @reactive.effect
    def _load_on_first_visit():
        """The scoped copy is built when the module is first opened, not at sign-in."""
        pid, kind = principal(), load_state()[0]
        if pid and kind == "idle" and active_module() == MODULE_KEY:
            with reactive.isolate():
                load_state.set(("loading", "Loading your scoped shared data…"))
                load_task.invoke(pid, generation())
                scope_task.invoke(pid, generation())

    @reactive.extended_task
    async def scope_task(pid, gen):
        """The welcome line's store count and as-of date, read off the session (a slow connection never holds
        up the welcome itself). A failure just leaves the line without them; the load reports real problems."""
        def work():
            reader = runtime.reader()
            return reader.principal_scope(pid)["stores"], reader.record.get("as_of")
        try:
            return gen, pid, await asyncio.to_thread(work)
        except Exception:  # noqa: BLE001 - optional detail only
            return gen, pid, None

    @reactive.effect
    def _scope_read():
        if scope_task.status() != "success":
            return
        gen, pid, summary = scope_task.result()
        with reactive.isolate():
            if gen == generation() and pid == principal():
                scope_summary.set(summary)

    handled = {"result": None, "estimate": None}

    @reactive.effect
    def _loaded():
        if load_task.status() != "success":
            return
        result = load_task.result()
        gen, pid, item, choices, reason = result
        with reactive.isolate():
            if result is handled["result"]:
                return
            handled["result"] = result
            if gen != generation() or pid != principal() or load_state()[0] != "loading":
                return  # a late result for a previous sign-in is discarded
            if item is None:
                load_state.set(("unavailable", reason))
                return
            data.set(item)
            choice_info.set(choices)
            load_state.set(("ready", ""))
            ui.update_checkbox_group("m1_channel", choices=choices["channels"], selected=choices["channels"])
            ui.update_checkbox_group("m1_region", choices=choices["regions"], selected=choices["regions"])
            ui.update_checkbox_group("m1_sku", choices=sku_choices(choices), selected=list(choices["products"]))
            ui.update_radio_buttons("m1_msl", selected="all")
            ui.update_select("m1_store", choices=choices["stores"], selected=next(iter(choices["stores"]), None))
            ui.update_selectize("m1_compare", choices=choices["stores"], selected=[])
            last, first = pd.Timestamp(choices["last_date"]), pd.Timestamp(choices["first_date"])
            start, end = preset_span(DEFAULT_PRESET, first, last)
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()),
                                 min=str(first.date()), max=str(last.date()))

    @reactive.effect
    @reactive.event(input.m1_mode)
    def _select_mode():
        wanted = input.m1_mode()
        pid = principal()
        if wanted not in ("reported", "estimated") or pid is None or data() is None:
            return
        if wanted == "reported":
            mode.set("reported")
            if estimate_state()[0] == "unavailable":
                estimate_state.set(("idle", ""))
            return
        if estimated_data() is not None:
            mode.set("estimated")
        elif estimate_state()[0] != "loading":
            estimate_state.set(("loading", ""))
            estimate_task.invoke(pid, generation())

    @reactive.effect
    def _estimate_loaded():
        if estimate_task.status() != "success":
            return
        result = estimate_task.result()
        gen, pid, item, info, reason = result
        with reactive.isolate():
            if result is handled["estimate"]:
                return
            handled["estimate"] = result
            if gen != generation() or pid != principal() or estimate_state()[0] != "loading":
                return  # late: another sign-in, or the request was superseded
            if item is None:
                estimate_state.set(("unavailable", reason))
                return
            estimated_data.set((item, info))
            estimate_state.set(("ready", ""))
            mode.set("estimated")

    @reactive.effect
    def _filters_synced():
        info = choice_info()
        if info is None or synced.get():
            return
        dates = input.m1_dates()
        # The first values sent back after ui.update_* carry the scoped choices and dates.
        _, end = preset_span(DEFAULT_PRESET, info["first_date"], info["last_date"])
        if input.m1_sku() and dates and str(dates[1]) == str(end.date()):
            synced.set(True)

    @reactive.effect
    @reactive.event(input.m1_sku_all)
    def _select_all_skus():
        info = choice_info()
        if info:
            ui.update_checkbox_group("m1_sku", selected=msl_skus(info, input.m1_msl()))

    @reactive.effect
    @reactive.event(input.m1_msl)
    def _msl_narrows_skus():
        """MSL only / non-MSL only: the SKU list shows (and selects) just those products; All brings back all."""
        info = choice_info()
        if info:
            skus = msl_skus(info, input.m1_msl())
            ui.update_checkbox_group("m1_sku", choices=sku_choices(info, skus), selected=skus)

    @reactive.effect
    @reactive.event(input.m1_sku_none)
    def _deselect_all_skus():
        ui.update_checkbox_group("m1_sku", selected=[])

    chosen_preset = reactive.value(DEFAULT_PRESET)      # the preset last picked (several can cover the same weeks)

    @reactive.effect
    @reactive.event(input.m1_preset)
    def _apply_preset():
        info, key = choice_info(), input.m1_preset()
        if info and key in DATE_PRESETS:
            chosen_preset.set(key)
            start, end = preset_span(key, info["first_date"], info["last_date"])
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()))

    @reactive.effect
    @reactive.event(input.m1_reset)
    def _reset_filters():
        info = choice_info()
        if info:
            chosen_preset.set(DEFAULT_PRESET)
            ui.update_checkbox_group("m1_channel", selected=info["channels"])
            ui.update_checkbox_group("m1_region", selected=info["regions"])
            ui.update_checkbox_group("m1_sku", choices=sku_choices(info), selected=list(info["products"]))
            ui.update_radio_buttons("m1_msl", selected="all")
            start, end = preset_span(DEFAULT_PRESET, info["first_date"], info["last_date"])
            ui.update_date_range("m1_dates", start=str(start.date()), end=str(end.date()))

    async def go(target):
        page.set(target)
        ui.update_navset("m1_pages", selected=target)
        await session.send_custom_message("m1-scroll-top", {})

    @reactive.effect
    @reactive.event(input.m1_go)
    async def _go():
        target = input.m1_go()
        if target in PAGES:
            await go(target)

    @reactive.effect
    @reactive.event(input.m1_open_store)
    async def _open_store():
        """A ranking row or a "Store card" button: only a store in the filtered list of the account can be chosen."""
        store_id = input.m1_open_store()
        try:
            allowed = filtered_stores()
        except Exception:  # noqa: BLE001 - nothing loaded yet
            return
        if store_id in allowed:
            ui.update_select("m1_store", choices=allowed, selected=store_id)
            await go("stores")

    @reactive.effect
    @reactive.event(input.m1_targets)
    def _toggle_targets():
        show_targets.set(not show_targets())

    @reactive.effect
    @reactive.event(input.m1_rank_side)
    def _rank_side():
        if input.m1_rank_side() in ("top", "bottom"):
            rank_side.set(input.m1_rank_side())

    @reactive.effect
    @reactive.event(input.m1_flow_dim)
    def _flow_dim():
        if input.m1_flow_dim() in ("channel", "region", "store", "sku"):
            flow_dim.set(input.m1_flow_dim())
            flow_all.set(False)

    @reactive.effect
    @reactive.event(input.m1_flow_more)
    def _flow_more():
        flow_all.set(not flow_all())

    @reactive.effect
    @reactive.event(input.m1_city)
    def _city_clicked():
        """A map bubble or the city list: the city's stores open below the map."""
        key = input.m1_city()
        city.set(key or None)
        if key and key != input.m1_city_pick():
            ui.update_select("m1_city_pick", selected=key)

    @reactive.effect
    @reactive.event(input.m1_city_pick)
    def _city_picked():
        city.set(input.m1_city_pick() or None)

    # ---------------------------------------------------------- filters and scope
    @reactive.calc
    def span():
        info = choice_info()
        req(info and synced())
        dates = input.m1_dates()
        req(dates and dates[0] and dates[1])
        return snap(dates[0], dates[1], info["first_date"], info["last_date"])

    def make_filters(a, b):
        info = choice_info()
        channels = tuple(input.m1_channel()) if input.m1_channel() else tuple(info["channels"])
        regions = tuple(input.m1_region()) if input.m1_region() else tuple(info["regions"])
        skus = tuple(input.m1_sku()) if input.m1_sku() is not None else tuple(info["products"])
        msl = input.m1_msl() or "all"
        if msl in ("msl", "non"):                # the MSL pill narrows the selected SKUs to (non-)MSL products
            required = set(info.get("msl_products") or ())
            skus = tuple(s for s in skus if (s in required) == (msl == "msl"))
        return Filters(channels, regions, skus, set(skus) == set(info["products"]), a.date(), b.date())

    def active_item():
        if mode() == "estimated" and estimated_data() is not None:
            return estimated_data()[0]
        return data()

    @reactive.calc
    def checked():
        """The principal's data in the current view after a fresh scope/publication check, or (None, reason)."""
        item = active_item()
        state_kind, reason = load_state()
        if item is None:
            return None, reason
        if synced():  # re-check whenever the filters change
            input.m1_dates(), input.m1_channel(), input.m1_region(), input.m1_sku(), input.m1_msl()
        try:
            item.check()
            return item, None
        except Exception as error:
            return None, ("Your account scope or the shared publication changed; sign out and sign in again "
                          f"({type(error).__name__}).")

    @reactive.calc
    def current():
        item, _ = checked()
        req(item)
        a, b = span()
        return item, make_filters(a, b)

    @reactive.calc
    def colors():
        info = choice_info()
        req(info)
        return {"channel": palette(info["channels"]), "region": palette(info["regions"]),
                "tier": palette(info["tiers"]), "category": palette(info["categories"])}

    @reactive.calc
    def store_frame():
        item, f = current()
        return item.store_metrics(f)

    @reactive.calc
    def store_prior():
        item, f = current()
        return item.store_metrics(prior_filters(f))

    @reactive.calc
    def scored():
        return composite_scores(store_frame())

    @reactive.calc
    def filtered_stores():
        """The account's stores that match the current filters, as the store menus list them."""
        info = choice_info()
        ids = set(store_frame()["store_id"])
        return {sid: label for sid, label in info["stores"].items() if sid in ids}

    @reactive.effect
    def _store_menus_follow_filters():
        """The store and compare menus offer only the stores in the current filters; a choice that drops out is
        replaced by the first store left (the compare list keeps the ones still in)."""
        try:
            allowed = filtered_stores()
        except Exception:  # noqa: BLE001 - filters not ready yet
            return
        with reactive.isolate():
            current_store = input.m1_store()
            current_compare = list(input.m1_compare() or ())
        selected = current_store if current_store in allowed else next(iter(allowed), None)
        ui.update_select("m1_store", choices=allowed, selected=selected)
        ui.update_selectize("m1_compare", choices=allowed, selected=[c for c in current_compare if c in allowed])

    @reactive.calc
    def scored_prior():
        return composite_scores(store_prior())

    # ---------------------------------------------------------- navigation and header
    @output(suspend_when_hidden=False)               # in the app sidebar, hidden while another module is open
    @render.ui
    def m1_nav():
        if principal() is None:
            return None
        return tags.nav(*[kit.nav_item(PAGE_ICONS[key], text, active=page() == key, onclick=f"m1Go('{key}')",
                                       **{"data-page": key}) for key, text in PAGES.items()],
                        class_="ws-subnav-items", **{"aria-label": f"{module_label} pages"})

    @render.ui
    def m1_crumb():
        return tags.nav(tags.span(module_label), tags.span("/", **{"aria-hidden": "true"}), tags.strong(PAGES[page()]),
                        class_="ws-crumb", **{"aria-label": "Breadcrumb"})

    @render.ui
    def m1_user_block():
        name, title = auth_state["name"](), auth_state["title"]()
        avatar = auth_state.get("avatar")
        return None if name is None else kit.user_block(name, title, id="m1-user",
                                                        avatar_src=avatar() if avatar else None)

    @output(suspend_when_hidden=False)
    @render.text
    def m1_ready():
        return "yes" if checked()[0] is not None else "no"

    @render.ui
    @safe
    def m1_page_head():
        if principal() is None:
            return None
        title, sub = PAGE_HEADS[page()]
        item, _ = checked()
        if item is None:  # the welcome screen stands in for the page head while the first load runs
            return None if load_state()[0] in ("idle", "loading") else kit.page_head(title, sub=sub)
        scope = item.scope
        role = "Regional manager view" if item.manager else "Field sales rep view"
        kind, reason = estimate_state()
        info = estimated_data()[1] if estimated_data() is not None else None
        meta = [tags.span(tags.b(role), f" · {scope['stores']} stores in your demo scope"),
                tags.span("Data through ", tags.b(str(item.record.get("as_of")))),
                mode_toggle(mode(), kind, reason, info)]
        return kit.page_head(title, *meta, sub=sub)

    @render.text
    def m1_welcome_detail():
        summary = scope_summary()
        return welcome_detail(*summary) if summary else welcome_detail()

    @render.ui
    def m1_status():
        kind, reason = load_state()
        item, check_reason = checked()
        if kind in ("signed_out",) or item is not None:
            return None
        if kind in ("idle", "loading"):
            with reactive.isolate():
                clock = input.m1_client_clock() if "m1_client_clock" in input else None
            return welcome(auth_state["name"](), clock, ui.output_text("m1_welcome_detail", inline=True))
        return kit.notice(tags.strong("Business Performance is unavailable. "), check_reason or reason or "",
                          kind="bad", role="alert", id="m1-unavailable")

    # ---------------------------------------------------------- filter pills
    def pill_value(text, changed, extra=None):
        return TagList(tags.span(text, class_="m1-pill-val ws-fpill-val" + (" set" if changed else "")),
                       tags.span(extra, class_="m1-pill-extra ws-fpill-extra") if extra else None)

    @render.ui
    def m1_pill_dates():
        info = choice_info()
        req(info)
        a, b = span()
        key = matching_preset((a, b), info["first_date"], info["last_date"], chosen_preset())
        dates = f"{a:%b %d} – {b:%b %d, %Y}"
        if key is None:
            return pill_value(dates, True)
        return pill_value(DATE_PRESETS[key], key != DEFAULT_PRESET, dates)

    @render.ui
    def m1_presets():
        info = choice_info()
        req(info)
        key = matching_preset(span(), info["first_date"], info["last_date"], chosen_preset())
        return tags.div(*[tags.button(label, type="button", class_="m1-preset" + (" on" if k == key else ""),
                                      onclick=f"Shiny.setInputValue('m1_preset', '{k}', {{priority: 'event'}})",
                                      **{"aria-pressed": "true" if k == key else "false"})
                          for k, label in DATE_PRESETS.items()], class_="m1-presets")

    @render.ui
    def m1_pill_channel():
        info = choice_info()
        req(info)
        return TagList(tags.span("Channel", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_channel(), {c: c for c in info["channels"]})))

    @render.ui
    def m1_pill_region():
        info = choice_info()
        req(info)
        return TagList(tags.span("Region", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_region(), {r: r for r in info["regions"]})))

    @render.ui
    def m1_pill_msl():
        req(choice_info())
        value = input.m1_msl() or "all"
        return TagList(tags.span("Products", class_="m1-pill-key ws-fpill-key"),
                       pill_value(MSL_PILL.get(value, MSL_PILL["all"]), value != "all"))

    @render.ui
    def m1_pill_sku():
        info = choice_info()
        req(info)
        names = info.get("product_names") or info["products"]
        shown = {s: names.get(s, s) for s in msl_skus(info, input.m1_msl())}
        return TagList(tags.span("SKU", class_="m1-pill-key ws-fpill-key"),
                       pill_value(*choice_label(input.m1_sku(), shown, empty_means_all=False)))

    @render.ui
    def m1_sku_count():
        info = choice_info()
        req(info)
        shown = msl_skus(info, input.m1_msl())
        return tags.span(f"{len([s for s in input.m1_sku() or () if s in shown])} of {len(shown)} selected",
                         class_="ws-muted")

    @render.ui
    @safe
    def m1_filter_summary():
        info = choice_info()
        item, f = current()
        changed = (choice_label(input.m1_channel(), {c: c for c in info["channels"]})[1]
                   or choice_label(input.m1_region(), {r: r for r in info["regions"]})[1]
                   or choice_label(input.m1_sku(), {s: s for s in msl_skus(info, input.m1_msl())},
                                   empty_means_all=False)[1]
                   or (input.m1_msl() or "all") != "all"
                   or matching_preset(span(), info["first_date"], info["last_date"], chosen_preset()) != DEFAULT_PRESET)
        reset = tags.button("Reset filters", type="button", class_="m1-reset", id="m1-reset",
                            onclick="Shiny.setInputValue('m1_reset', Date.now(), {priority: 'event'})") \
            if changed else None
        return TagList(reset, tags.span("Showing ", tags.b(f"{item.store_count(f)} stores"), " · ",
                                        tags.b(f"{len(f.skus)} SKUs"), class_="m1-fnote"))

    # ---------------------------------------------------------- KPIs
    @reactive.calc
    def kpi_values():
        item, f = current()
        return item.kpis(f), item.kpis(prior_filters(f))

    selected_kpi = reactive.value("msl")

    @reactive.effect
    @reactive.event(input.m1_kpi)
    def _select_kpi():
        if input.m1_kpi() in KPI_SPECS:
            selected_kpi.set(input.m1_kpi())

    @render.ui
    @safe
    def m1_kpis():
        item, f = current()
        if not f.skus:
            return state("No SKU selected", "Select at least one product SKU, or another MSL option, in the filters.")
        return kpi_tiles(*kpi_values(), selected=selected_kpi(), estimated=item.estimated)

    @reactive.calc
    def kpi_trend():
        item, f = current()
        return item.kpi_trend(f, selected_kpi())

    @render.ui
    @safe
    def m1_kpi_detail():
        item, f = current()
        if not f.skus:
            return None
        c, p = kpi_values()
        return kpi_detail(selected_kpi(), c, p, kpi_trend(), f, estimated=item.estimated,
                          show_targets=show_targets())

    @render.ui
    @safe
    def m1_ranking():
        item, f = current()
        if not f.skus:
            return state("No SKU selected", "Select at least one product SKU in the filters.")
        return ranking_panel(scored(), scored_prior(), rank_side())

    # ---------------------------------------------------------- store cards
    @render.ui
    @safe
    def m1_store_card():
        item, f = current()
        store_id = input.m1_store()
        stores = scored()
        if not store_id:
            return state("Choose a store", "Pick a store above, or open one from the ranking or the map.")
        row = stores[stores["store_id"] == store_id]
        if row.empty:
            return state("Store not in the current filters", "This store is outside the selected channels or regions; "
                         "reset the filters to see its card.")
        record = row.iloc[0].to_dict()
        reps = rep_directory(runtime.reader()).get(store_id, []) if item.manager else None
        compare = [c for c in (input.m1_compare() or ()) if c in set(stores["store_id"])]
        return store_card(record, stores, item.store_skus(f, store_id) if f.skus else None,
                          record.get("latest_date"), stockout_nav, item.estimated, prior=scored_prior(),
                          trend=item.store_trend(f, store_id), reps=reps, compare=compare, manager=item.manager)

    # ---------------------------------------------------------- map and flow extras
    @render.ui
    @safe
    def m1_city_stores():
        current()
        return city_stores(store_frame(), city(), stockout_nav)

    @reactive.effect
    def _city_choices():
        """The keyboard / touch alternative to clicking a bubble: every city of the scoped stores."""
        try:
            stores = store_frame()
        except Exception:  # noqa: BLE001 - nothing to list yet
            return
        cities = sorted({(c, s) for c, s in zip(stores["city"], stores["state"])})
        choices = {"": "Choose a city"} | {city_key(c, s): f"{c}, {s}" for c, s in cities}
        with reactive.isolate():
            selected = city() if city() in choices else ""
        ui.update_select("m1_city_pick", choices=choices, selected=selected)

    @render.ui
    @safe
    def m1_flow_summary():
        item, f = current()
        if not f.skus:
            return None
        return flow_summary(kpi_values()[0])

    @render.ui
    @safe
    def m1_flow_table():
        item, f = current()
        if not f.skus:
            return None
        dimension = flow_dim() if item.manager or flow_dim() in ("store", "sku") else "store"
        return flow_table(item.flow_breakdown(f, dimension), dimension, flow_all(), item.manager)

    # ---------------------------------------------------------- charts
    for output_id, (_, option_id, _, _) in CHARTS.items():
        def bind(output_id=output_id, option_id=option_id):
            @output(id=output_id)
            @render.ui
            @safe
            def _chart():
                item, f = current()
                option = input[option_id]() if option_id else None
                extra = {"stores": store_frame()} if output_id == "m1_chart9_geo_map" else {}
                return render_chart(output_id, item, f, option, colors(), **extra)
        bind()
