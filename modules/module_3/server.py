"""
modules/module_3/server.py
==========================
Server logic for Module 3: Negotiation Simulator (brief Scenarios A and B).

Data comes only from the shared business publication pinned by the app runtime, through the same
per-principal scoped copy Module 1 uses (modules/module_3/data.py), loaded in a background task on the
first visit (immediate when Business Performance has already loaded it). Calculations are the pure
functions of modules/module_3/calc.py. A verdict is a guardrail check under a proposed rule version,
never an approval; unknown inputs give "cannot assess", never a pass. Charts are strict-JSON Plotly
figures (Module 1's helpers), with a sanitized error card if one fails.
"""

import asyncio
import math

import pandas as pd
import plotly.graph_objects as go
from htmltools import TagList, tags
from shiny import reactive, render, req, ui

from modules.module_1.server import plot, safe, state
from shared import ui_kit as kit

from . import calc
from .data import Module3Data
from .ui import PAGE_ICONS, PAGES, SCENARIOS

MODULE_KEY = "module_3"
PAGE_HEADS = {
    "deal": ("Deal simulator",
             "Compare three proposals for one account and product against its commercial guardrail before you "
             "offer one."),
    "price": ("Price change",
              "What a list-price increase on a product or category could do to volume, revenue and gross profit "
              "in your stores."),
}
VERDICT_KIND = {calc.APPROVED: "ok", calc.CONDITIONS: "warn", calc.REJECTED: "bad", calc.NOT_ASSESSED: "outline"}
VERDICT_COLOR = {calc.APPROVED: "#1d6b45", calc.CONDITIONS: "#b26a00", calc.REJECTED: "#b41824",
                 calc.NOT_ASSESSED: "#a39797"}
SCENARIO_COLORS = ("#2b6fd6", "#86121c", "#1d6b45")


def _num(value):
    return None if value is None or pd.isna(value) else float(value)


def _money(value):
    return "N/A" if value is None else f"${value:,.2f}" if abs(value) < 1000 else f"${value:,.0f}"


def _day(value):
    return "N/A" if value is None else f"{pd.Timestamp(value):%Y-%m-%d}"


def _pct(value, pattern="{:.1f}%"):
    return "N/A" if value is None else pattern.format(value)


def deal_from(context):
    """The calc input for one negotiation-context row."""
    return calc.Deal(channel=context.get("channel") or "", list_price=_num(context.get("reference_list_price")),
                     unit_cost=_num(context.get("reference_unit_cost")),
                     current_discount=_num(context.get("weighted_trade_discount_pct_28d")),
                     cap=_num(context.get("max_discount_pct_source")),
                     floor=_num(context.get("min_target_margin_source")),
                     tradeoff=context.get("required_exec_tradeoff") or "")


def default_proposals(deal):
    """Three starting proposals that show the trade-off: within the headroom; above the cap without the
    commitment; the same discount with it. (Values on a 0.5-point grid.)"""
    first = calc.commitments(deal.tradeoff)
    above = first[0].key if first and not deal.distributor else "none"
    headroom = None if deal.cap is None else deal.cap - (deal.current_discount or 0.0)
    within = 0.0 if headroom is None or headroom <= 0.5 else math.floor(headroom) / 2 or 0.5
    beyond = 2.0 if headroom is None or headroom <= 0 else math.ceil(headroom) + 1.0
    return [(within, "none"), (beyond, "none"), (beyond, above)]


def context_panel(context, deal):
    """The account's facts behind the proposals (reference values, recent business, evidence, guardrail)."""
    def fact(label, value, note=None):
        return tags.div(tags.span(label, class_="m3-fact-label"), tags.span(value, class_="m3-fact-value"),
                        tags.span(note, class_="m3-fact-note") if note else None, class_="m3-fact")
    ref_margin = calc.unit_economics(deal.list_price, deal.unit_cost, 0.0)[1]
    current = deal.current_discount
    headroom = None if deal.cap is None or current is None else deal.cap - current
    stock = context.get("inventory_data_status") or "Unknown"
    stock_value = f"{_num(context.get('inventory_observed')):,.0f} units" if stock == "Observed" and \
        _num(context.get("inventory_observed")) is not None else "Unknown"
    audit = context.get("last_audit_date")
    audit_text = "No audit" if audit is None else f"{pd.Timestamp(audit):%b %d, %Y}"
    age = _num(context.get("days_since_last_audit"))
    execution = []
    if context.get("planogram_compliance_latest") is not None:
        execution.append("planogram " + ("compliant" if context["planogram_compliance_latest"] else "not compliant"))
    if context.get("secondary_display_latest") is not None:
        execution.append("display " + ("present" if context["secondary_display_latest"] else "absent"))
    if _num(context.get("shelf_share_pct_latest")) is not None:
        execution.append(f"allocation {_num(context['shelf_share_pct_latest']):.1f}%")
    rule = tags.div(
        tags.div(tags.span("Guardrail", class_="m3-fact-label"),
                 tags.span(f"{context.get('channel')} · {context.get('category')}", class_="m3-fact-value"),
                 class_="m3-rule-head"),
        tags.ul(tags.li("Maximum discount without extra approval: ", tags.b(_pct(deal.cap, "{:.2f}%"))),
                tags.li("Minimum gross margin on net price: ", tags.b(_pct(deal.floor, "{:.2f}%"))),
                tags.li("Commitment that can unlock more: ", tags.b(deal.tradeoff or "not supplied")),
                class_="m3-rule-list"),
        tags.p(kit.pill(context.get("policy_status") or "Requires review", "outline"),
               " ", context.get("policy_limitation") or "", class_="m3-rule-note"),
        class_="m3-rule")
    return tags.section(
        tags.div(tags.div(tags.h2(f"{context.get('product_name')} at {context.get('store_name')}", class_="ws-h"),
                          tags.p(f"{context.get('store_id')} · {context.get('channel')} · {context.get('region')} · "
                                 f"{context.get('segment_tier')}" + (" · MSL product" if context.get("is_msl") else ""),
                                 class_="ws-muted m3-logic")),
                 class_="ws-card-head"),
        tags.div(
            tags.div(
                fact("List price", _money(deal.list_price), "static reference; currency not supplied"),
                fact("Unit cost", _money(deal.unit_cost), context.get("cost_basis") or "static reference"),
                fact("Margin at list", _pct(ref_margin), "before any discount"),
                fact("Current trade discount", _pct(current) if current is not None else "Unknown",
                     "weighted, last 28 days of invoices" if current is not None else "no invoice in 28 days"),
                fact("Headroom to the cap", "N/A" if headroom is None else f"{headroom:+.2f} pts",
                     "already above the cap" if headroom is not None and headroom < 0 else None),
                fact("Stock", stock_value, f"{stock} · reported"),
                fact("Sell-out, last 28 days", f"{_num(context.get('sell_out_units_observed_28d')) or 0:,.0f} units",
                     f"{int(_num(context.get('observed_sales_days_28d')) or 0)} of "
                     f"{int(_num(context.get('expected_days_28d')) or 0)} days reported"),
                fact("Last audit", audit_text, (f"{age:.0f} day{'' if round(age) == 1 else 's'} ago · " if age is not None else "")
                     + (", ".join(execution) if execution else "no execution evidence")),
                class_="m3-facts"),
            rule, class_="m3-context-grid"),
        tags.p(f"Data through {_day(context.get('as_of_date'))} · {context.get('context_version') or ''} · "
               f"guardrail {str(context.get('rule_version') or '')[:12]}", class_="ws-muted m3-source"),
        class_="ws-card m3-context", id="m3-context")


def result_panel(deal, result, quantity):
    """One proposal's numbers, checks and what the commitment would need to pay for itself."""
    def row(label, value, cls=""):
        return tags.div(tags.span(label), tags.b(value), class_="m3-row " + cls)

    def check(ok, text):
        mark = "–" if ok is None else ("✓" if ok else "✗")
        return tags.li(tags.span(mark, class_="m3-mark " + ("na" if ok is None else ("ok" if ok else "bad")),
                                 **{"aria-hidden": "true"}),
                       ("Not assessed: " if ok is None else ("Pass: " if ok else "Fails: ")) + text)
    compared_label = "total" if result["compared"] == result["total"] else "additional"
    checks = tags.ul(
        check(result["cap_ok"], f"{compared_label} discount {result['compared']:.2f}% vs cap "
                                f"{_pct(deal.cap, '{:.2f}%')}"),
        check(result["floor_ok"], f"margin {_pct(result['margin'])} vs minimum {_pct(deal.floor, '{:.2f}%')}"),
        check(None if result["cap_ok"] or deal.distributor else result["commitment"] is not None,
              "commitment " + (result["commitment"].label if result["commitment"] else "not offered")
              + (" (only needed above the cap)" if result["cap_ok"] else "")
              + (" (distributors: manager review instead)" if deal.distributor and not result["cap_ok"] else "")),
        class_="m3-checks")
    rows = [row("Total discount", _pct(result["total"], "{:.2f}%")),
            row("Net price", _money(result["net"])),
            row("Gross margin", _pct(result["margin"]), "strong")]
    if quantity:
        rows += [row(f"Revenue ({quantity:,.0f} units)", _money(result.get("revenue"))),
                 row("Discount given", _money(result.get("discount_cost"))),
                 row("Gross profit", _money(result.get("gross_profit")))]
    payback = None
    if result["breakeven"] is not None and result["additional"] > 0:
        need = result["breakeven"] * 100
        has = result["uplift"] * 100
        weeks = result["commitment"].weeks if result["commitment"] else None
        payback = tags.p(
            f"Needs +{need:.1f}% volume to keep today's gross profit. ",
            (f"The commitment is assumed to add +{has:.1f}%" + (f" for {weeks} weeks" if weeks else "") + ": "
             + ("it covers the extra discount." if result["covers"] else "not enough on its own."))
            if result["commitment"] else "No commitment offered, so no volume uplift is assumed.",
            class_="m3-payback" + ("" if not result["commitment"] else (" ok" if result["covers"] else " bad")))
    return TagList(tags.div(*rows, class_="m3-rows"), checks, payback,
                   tags.p(result["reason"], class_="m3-reason"),
                   *[tags.p(n, class_="ws-muted m3-note") for n in result["notes"]])


def margin_chart(deal, results):
    labels = [f"Scenario {k.upper()}" for k in SCENARIOS]
    margins = [r["margin"] for r in results]
    if all(m is None for m in margins):
        return state("Nothing to chart", "No proposal has a computable margin.")
    fig = go.Figure(go.Bar(
        x=labels, y=[m if m is not None else 0 for m in margins],
        marker=dict(color=[VERDICT_COLOR[r["verdict"]] for r in results], cornerradius=4),
        text=[("N/A" if r["margin"] is None else f"{r['margin']:.1f}%") + f"<br>{r['verdict'].title()}"
              for r in results], textposition="outside", cliponaxis=False,
        customdata=[f"{r['total']:.2f}%" for r in results],
        hovertemplate="%{x}<br>Margin %{y:.1f}%<br>Total discount %{customdata}<extra></extra>"))
    top = max([m for m in margins if m is not None] + [deal.floor or 0])
    if deal.floor is not None:
        fig.add_hline(y=deal.floor, line=dict(color="#86121c", width=1.5, dash="dash"),
                      annotation=dict(text=f"Minimum margin {deal.floor:.2f}%", font=dict(size=11, color="#86121c"),
                                      xanchor="right", x=1, yanchor="bottom"))
    layout = dict(showlegend=False, margin=dict(l=16, r=16, t=24, b=16), bargap=0.45,
                  yaxis=dict(range=[0, top * 1.25 + 2], ticksuffix="%", title=None), xaxis=dict(title=None))
    return plot(fig, "m3-margin-chart", "Gross margin by scenario", 300, layout)


# ---------------------------------------------------------------- Scenario B helpers

def weighted_unit(baseline):
    """Units-weighted list price, unit cost and trade discount of the selection (13-week observed units)."""
    frame = baseline[baseline["units"].fillna(0) > 0]
    units = float(frame["units"].sum()) if len(frame) else 0.0
    if units <= 0:
        return None
    disc = frame["trade_discount"].fillna(0.0)
    net = float((frame["units"] * frame["list_price"] * (1 - disc / 100)).sum()) / units
    cost = float((frame["units"] * frame["unit_cost"]).sum()) / units
    return {"units": units, "unit_net": net, "unit_cost": cost,
            "trade_discount": float((frame["units"] * disc).sum()) / units,
            "stores": int(frame["stores"].max()), "skus": int(len(frame)),
            "first": frame["first_day"].min(), "last": frame["last_day"].max()}


def price_curve(elasticity, low, high, pass_through, unit_net, unit_cost, chosen):
    steps = [i / 2 for i in range(0, 31)]
    fig = go.Figure()
    for key, name, color in (("revenue", "Revenue", SCENARIO_COLORS[0]), ("profit", "Gross profit", SCENARIO_COLORS[2])):
        mid = [100 * (calc.price_response(p, elasticity, pass_through, unit_net, unit_cost)[key] or 0) for p in steps]
        if low is not None and high is not None:
            a = [100 * (calc.price_response(p, low, pass_through, unit_net, unit_cost)[key] or 0) for p in steps]
            b = [100 * (calc.price_response(p, high, pass_through, unit_net, unit_cost)[key] or 0) for p in steps]
            fig.add_trace(go.Scatter(x=steps + steps[::-1], y=a + b[::-1], fill="toself", line=dict(width=0),
                                     fillcolor=("rgba(43,111,214,0.12)" if key == "revenue" else "rgba(29,107,69,0.12)"),
                                     hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=steps, y=mid, mode="lines", name=name, line=dict(color=color, width=2.5),
                                 hovertemplate=f"{name}: %{{y:+.1f}}% at +%{{x:.1f}}% price<extra></extra>"))
    point = calc.price_response(chosen, elasticity, pass_through, unit_net, unit_cost)
    fig.add_trace(go.Scatter(x=[chosen, chosen], y=[100 * point["revenue"], 100 * (point["profit"] or 0)],
                             mode="markers", marker=dict(size=10, color="#211b1c"), showlegend=False,
                             hovertemplate="Your choice: +%{x:.1f}%<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#a39797", width=1))
    layout = dict(margin=dict(l=16, r=16, t=40, b=16), yaxis=dict(ticksuffix="%", title=None),
                  xaxis=dict(ticksuffix="%", title="List-price increase"))
    return plot(fig, "m3-price-chart", "Revenue and gross profit response to a price increase", 320, layout)


# ---------------------------------------------------------------- server

def module_3_server(input, output, session, context: dict, auth_state: dict):
    """Hosted entry point. ``context['runtime']`` is the pinned shared source (shared.runtime)."""
    runtime = context.get("runtime") if isinstance(context, dict) else None
    if runtime is None:
        return  # the legacy root app: the UI explains where the module runs; nothing is loaded
    module_label = context.get("module_label") or "Negotiation Simulator"
    active_module = auth_state.get("active_module") or (lambda: MODULE_KEY)
    navigate = auth_state.get("navigate")
    data = reactive.value(None)
    load_state = reactive.value(("signed_out", ""))
    generation = reactive.value(0)
    page = reactive.value("deal")
    applied = {"key": None}                 # the store × product whose starting proposals were applied

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return pid()

    @reactive.effect
    @reactive.event(input.m3_open_copilot)
    def _open_copilot():
        if navigate is not None and principal() is not None:
            navigate("module_4")

    @reactive.extended_task
    async def load_task(pid, gen):
        def work():
            item = Module3Data(runtime.module1_pool().get(pid))
            return item, item.accounts()
        try:
            item, accounts = await asyncio.to_thread(work)
            return gen, pid, item, accounts, None
        except PermissionError:
            return gen, pid, None, None, "Your account has no data scope in the shared publication."
        except Exception as error:  # sanitized: class name only
            reason = str(error) if type(error).__name__ in ("SourceUnavailable", "DataUnavailable") else \
                f"Shared data could not be loaded ({type(error).__name__})."
            return gen, pid, None, None, reason

    @reactive.effect
    async def _on_principal():
        pid = principal()
        with reactive.isolate():
            generation.set(generation() + 1)
            data.set(None)
            applied["key"] = None
            load_state.set(("idle", "") if pid else ("signed_out", ""))
            page.set("deal")
            ui.update_navset("m3_pages", selected="deal")
        await session.send_custom_message("m3-clear", {})

    @reactive.effect
    def _load_on_first_visit():
        pid, kind = principal(), load_state()[0]
        if pid and kind == "idle" and active_module() == MODULE_KEY:
            with reactive.isolate():
                load_state.set(("loading", ""))
                load_task.invoke(pid, generation())

    handled = {"result": None}

    @reactive.effect
    def _loaded():
        if load_task.status() != "success":
            return
        result = load_task.result()
        gen, pid, item, accounts, reason = result
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
            load_state.set(("ready", ""))
            stores = {r.store_id: f"{r.store_name} · {r.channel}" for r in accounts.itertuples()}
            ui.update_selectize("m3_store", choices=stores, selected=next(iter(stores), None))
            categories = item.categories()
            ui.update_selectize("m3_b_target", choices=categories, selected=categories[0] if categories else None)

    @reactive.effect
    @reactive.event(input.m3_go)
    async def _go():
        target = input.m3_go()
        if target in PAGES:
            page.set(target)
            ui.update_navset("m3_pages", selected=target)
            await session.send_custom_message("m3-scroll-top", {})

    @reactive.calc
    def checked():
        """The principal's data after a fresh scope/publication check, or (None, reason)."""
        item = data()
        if item is None:
            return None, load_state()[1]
        input.m3_store(), input.m3_sku(), input.m3_b_target()    # re-check on every selection change
        try:
            item.check()
            return item, None
        except Exception as error:
            return None, ("Your account scope or the shared publication changed; sign out and sign in again "
                          f"({type(error).__name__}).")

    # ---------------------------------------------------------- navigation and header
    @output(suspend_when_hidden=False)
    @render.ui
    def m3_nav():
        if principal() is None:
            return None
        return tags.nav(*[kit.nav_item(PAGE_ICONS[key], text, active=page() == key, onclick=f"m3Go('{key}')",
                                       **{"data-page": key}) for key, text in PAGES.items()],
                        class_="ws-subnav-items", **{"aria-label": f"{module_label} pages"})

    @render.ui
    def m3_crumb():
        return tags.nav(tags.span(module_label), tags.span("/", **{"aria-hidden": "true"}), tags.strong(PAGES[page()]),
                        class_="ws-crumb", **{"aria-label": "Breadcrumb"})

    @render.ui
    def m3_user_block():
        name, title = auth_state["name"](), auth_state["title"]()
        return None if name is None else kit.user_block(name, title, id="m3-user")

    @output(suspend_when_hidden=False)
    @render.text
    def m3_ready():
        return "yes" if checked()[0] is not None else "no"

    @render.ui
    def m3_page_head():
        if principal() is None:
            return None
        title, sub = PAGE_HEADS[page()]
        item, _ = checked()
        if item is None:
            return kit.page_head(title, sub=sub)
        role = "Regional manager view" if item.manager else "Field sales rep view"
        return kit.page_head(title, tags.span(tags.b(role), f" · {item.scope['stores']} stores in your demo scope"),
                             tags.span("Data through ", tags.b(str(item.record.get("as_of")))),
                             kit.pill("Rules pending client confirmation", "outline"), sub=sub)

    @render.ui
    def m3_status():
        kind, reason = load_state()
        item, check_reason = checked()
        if kind == "signed_out" or item is not None:
            return None
        if kind in ("idle", "loading"):
            return state("Preparing the negotiation context…", "This reads the stores your account can see. It is "
                         "instant if Business Performance has already loaded.")
        return kit.notice(tags.strong("The Negotiation Simulator is unavailable. "), check_reason or reason or "",
                          kind="bad", role="alert", id="m3-unavailable")

    # ---------------------------------------------------------- Scenario A
    @reactive.effect
    @reactive.event(input.m3_store)
    def _store_products():
        item = data()
        store = input.m3_store()
        if item is None or not store:
            return
        frame = item.products_for(store)
        grouped = {}
        for r in frame.itertuples():
            grouped.setdefault(r.category, {})[r.sku_id] = r.product_name + (" · MSL" if r.is_msl else "")
        current = input.m3_sku()
        keep = current if current in set(frame["sku_id"]) else (frame["sku_id"].iloc[0] if len(frame) else None)
        ui.update_selectize("m3_sku", choices=grouped, selected=keep)

    @reactive.calc
    def deal_context():
        item, _ = checked()
        req(item)
        store, sku = input.m3_store(), input.m3_sku()
        req(store and sku)
        row = item.context(store, sku)
        req(row)
        return row, deal_from(row)

    @reactive.effect
    def _defaults():
        """New store × product: start from three proposals that show the three outcomes (only then, so a
        re-check or another page's change never resets what the user typed)."""
        row, deal = deal_context()
        key = (generation(), row.get("store_id"), row.get("sku_id"))
        if key == applied["key"]:
            return
        applied["key"] = key
        with reactive.isolate():
            options = {"none": "None offered", **{c.key: c.label for c in calc.commitments(deal.tradeoff)}}
            for key, (additional, commitment) in zip(SCENARIOS, default_proposals(deal)):
                ui.update_numeric(f"m3_add_{key}", value=additional)
                ui.update_select(f"m3_commit_{key}", choices=options, selected=commitment)
            sold = _num(row.get("sell_in_units_reported_28d"))
            ui.update_numeric("m3_qty", value=max(1, int(round(sold))) if sold else 100)

    def proposal(key):
        _, deal = deal_context()
        additional = input[f"m3_add_{key}"]()
        commitment = input[f"m3_commit_{key}"]()
        quantity = input.m3_qty()
        return calc.scenario(deal, additional if additional is not None else 0,
                             None if commitment in (None, "", "none") else commitment,
                             quantity if quantity and quantity > 0 else None,
                             basis=input.m3_basis() or "total", uplift_source=input.m3_uplift() or "client")

    @reactive.calc
    def results():
        return [proposal(k) for k in SCENARIOS]

    @render.ui
    @safe
    def m3_context():
        row, deal = deal_context()
        return context_panel(row, deal)

    for key in SCENARIOS:
        def bind(key=key):
            @output(id=f"m3_result_{key}")
            @render.ui
            @safe
            def _result():
                _, deal = deal_context()
                quantity = input.m3_qty()
                return result_panel(deal, results()[SCENARIOS.index(key)], quantity if quantity and quantity > 0 else None)

            @output(id=f"m3_verdict_{key}")
            @render.ui
            def _verdict():
                result = results()[SCENARIOS.index(key)]
                return kit.pill(result["verdict"], VERDICT_KIND[result["verdict"]], class_="m3-verdict",
                                **{"data-verdict": result["verdict"]})
        bind()

    @render.ui
    @safe
    def m3_chart():
        _, deal = deal_context()
        return margin_chart(deal, results())

    # ---------------------------------------------------------- Scenario B
    @reactive.effect
    @reactive.event(input.m3_b_kind)
    def _b_targets():
        item = data()
        if item is None:
            return
        if input.m3_b_kind() == "sku":
            frame = item.products()
            grouped = {}
            for r in frame.itertuples():
                grouped.setdefault(r.category, {})[r.sku_id] = r.product_name
            ui.update_selectize("m3_b_target", choices=grouped, selected=frame["sku_id"].iloc[0] if len(frame) else None)
        else:
            categories = item.categories()
            ui.update_selectize("m3_b_target", choices=categories, selected=categories[0] if categories else None)

    @reactive.calc
    def b_inputs():
        item, _ = checked()
        req(item)
        kind, target = input.m3_b_kind(), input.m3_b_target()
        req(kind and target)
        if kind == "sku":
            products = item.products()
            match = products[products["sku_id"] == target]
            req(len(match))
            category, label = match["category"].iloc[0], match["product_name"].iloc[0]
        else:
            req(target in item.categories())
            category, label = target, target
        base = weighted_unit(item.baseline(kind, target))
        return label, category, base, item.elasticity(category)

    def chosen_elasticity(estimate):
        if input.m3_b_source() == "custom":
            value = input.m3_b_custom()
            return (min(0.0, float(value)) if value is not None else None), None, None, "custom"
        if estimate["estimate"] is None:
            return None, None, None, "estimated"
        # A positive estimate (more sold at a higher price) is no measurable price response, not a law of demand:
        # it is used as 0 and said so; the interval is kept to the non-positive side.
        e = min(0.0, estimate["estimate"])
        return e, min(0.0, estimate["low"]), min(0.0, estimate["high"]), "estimated"

    @render.ui
    @safe
    def m3_b_results():
        label, category, base, estimate = b_inputs()
        if base is None:
            return state("No observed sales", f"No in-stock sell-out was reported for {label} in your stores in the "
                         "last 13 weeks, so there is no baseline to change.")
        e, low, high, source = chosen_elasticity(estimate)
        if e is None:
            return state("No elasticity", "Too few priced, in-stock days to estimate one; choose Custom to set it.")
        p, through = input.m3_b_increase() or 0.0, input.m3_b_pass() if input.m3_b_pass() is not None else 100
        r = calc.price_response(p, e, through, base["unit_net"], base["unit_cost"])
        revenue0 = base["units"] * base["unit_net"]
        profit0 = base["units"] * (base["unit_net"] - base["unit_cost"])

        def card(title, change, before, after, fmt):
            kind = "ok" if change is not None and change > 0 else ("bad" if change is not None and change < 0 else "outline")
            return tags.div(tags.span(title, class_="m3-b-label"),
                            tags.span("N/A" if change is None else f"{100 * change:+.1f}%", class_="m3-b-value"),
                            kit.pill(f"{fmt(before)} → {fmt(after)}", kind),
                            class_="m3-b-card")
        units = lambda v: f"{v:,.0f} units"
        return tags.section(
            card("Volume", r["volume"], base["units"], base["units"] * (1 + r["volume"]), units),
            card("Revenue", r["revenue"], revenue0, revenue0 * (1 + r["revenue"]), _money),
            card("Gross profit", r["profit"], profit0, None if r["profit"] is None else profit0 * (1 + r["profit"]),
                 lambda v: "N/A" if v is None else _money(v)),
            tags.div(tags.span("Elasticity used", class_="m3-b-label"),
                     tags.span(f"{e:.2f}", class_="m3-b-value"),
                     kit.pill("Custom assumption" if source == "custom" else f"Estimated · {category}", "outline"),
                     class_="m3-b-card"),
            class_="m3-b-cards", id="m3-b-cards", **{"aria-label": f"Price change response for {label}"})

    @render.ui
    @safe
    def m3_b_chart():
        label, category, base, estimate = b_inputs()
        e, low, high, _ = chosen_elasticity(estimate)
        if base is None or e is None:
            return None
        through = input.m3_b_pass() if input.m3_b_pass() is not None else 100
        return price_curve(e, low, high, through, base["unit_net"], base["unit_cost"], input.m3_b_increase() or 0.0)

    @render.ui
    @safe
    def m3_b_notes():
        label, category, base, estimate = b_inputs()
        e, low, high, source = chosen_elasticity(estimate)
        if base is None or e is None:
            return None
        p = input.m3_b_increase() or 0.0
        through = input.m3_b_pass() if input.m3_b_pass() is not None else 100
        notes = []
        breakeven = calc.breakeven_elasticity(p, through)
        if breakeven is not None:
            notes.append(f"Revenue stays flat at an elasticity of {breakeven:.2f}; more negative than that, the increase "
                         "loses revenue.")
        if estimate["estimate"] is not None and estimate["estimate"] > 0:
            notes.append(f"The estimate for {category} in your stores is {estimate['estimate']:+.2f}, i.e. no measurable "
                         "price response (higher prices did not sell less); it is used as 0. Choose Custom to test "
                         "another value.")
        if estimate["estimate"] is not None:
            notes.append(f"Estimated elasticity for {category} in your stores: {estimate['estimate']:.2f} "
                         f"(approximate 95% interval {estimate['low']:.2f} to {estimate['high']:.2f}; "
                         f"{estimate['n']:,} in-stock store-SKU-days with a shelf price). It compares days within the "
                         "same store, product and month, so store size and season do not drive it.")
        rng = estimate.get("price_range")
        if rng is not None and p * through / 100 > 100 * rng[1] + 1e-9:
            notes.append(f"Extrapolation: shelf prices in the data were at most {100 * rng[1]:+.1f}% above the reference "
                         f"price, and down to {100 * rng[0]:+.1f}% on promotion; nearly all the price variation comes "
                         "from temporary promotions, so a lasting increase of this size is outside the data.")
        notes.append(f"Baseline: {base['units']:,.0f} units of reported sell-out on in-stock days over the last 13 weeks "
                     f"({pd.Timestamp(base['first']):%b %d} – {pd.Timestamp(base['last']):%b %d, %Y}), {base['skus']} "
                     f"product(s); not complete demand. Revenue at the reference list price less the recent trade "
                     f"discount ({base['trade_discount']:.1f}%), which is assumed unchanged.")
        notes.append("Not modelled: pack-size changes, switching between Marvel products or to competitors, and "
                     "competitor reactions. This is a what-if, not a forecast.")
        return tags.ul(*[tags.li(n) for n in notes], class_="m3-notes", id="m3-b-notes")
