"""
modules/module_2/server.py
==========================
Module 2 server logic. Reads only the published Module 2 serving candidate via
the scoped service; the principal comes from server-side session state. No
training, labels, raw CSVs, random scores or fixed metrics.
"""

import json
import os
from datetime import date
from pathlib import Path

from htmltools import TagList, tags
from shiny import reactive, render, ui

from .contracts import Module2Error
from .service import Module2Service, PermissionDenied, Principal, resolve_published

PAGE_SIZE = 25
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


def plot(fig, element_id):
    fig.update_layout(template="plotly_white", margin=dict(l=48, r=16, t=24, b=44), height=300,
                      font=dict(family="Plus Jakarta Sans, -apple-system, Segoe UI, sans-serif", size=12),
                      paper_bgcolor="rgba(0,0,0,0)")
    html = fig.to_html(full_html=False, include_plotlyjs=False, div_id=element_id,
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
    """Root-app entry point; ``dataset`` is accepted for compatibility and not used."""
    service, reason = configured_service()

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return Principal(pid(), True, bool(auth_state.get("is_demo", lambda: True)()))

    register(input, output, session, principal, service, reason)


def register(input, output, session, principal_fn, service, unavailable_reason=None):
    page = reactive.value(0)
    previous = reactive.value(None)

    def filters():
        return {"channel": input.m2_f_channel() or None, "region": input.m2_f_region() or None,
                "segment_tier": input.m2_f_tier() or None, "search": input.m2_f_search() or None,
                "msl_only": bool(input.m2_f_msl()), "alert_only": bool(input.m2_f_alert()),
                "unknown_only": bool(input.m2_f_unknown())}

    @reactive.calc
    def who():
        return principal_fn()

    @reactive.calc
    def scope():
        p = who()
        if service is None or p is None:
            return None
        try:
            return service.reader.principal_scope(p.principal_id)
        except PermissionError:
            return {"roles": [], "stores": 0, "full_population": False, "manager_full_scope": False}

    @reactive.calc
    def all_rows():
        p = who()
        if service is None or p is None:
            return None
        return service.get_priorities(p, {}, page_size=200)

    @reactive.calc
    def population():
        p = who()
        if service is None or p is None:
            return None
        return service.get_population(p)

    @reactive.calc
    def priorities():
        p = who()
        if service is None:
            return {"status": "Unavailable", "message": unavailable_reason}
        if p is None:
            return {"status": "Signed_out"}
        try:
            return service.get_priorities(p, filters(), cursor=str(page() * PAGE_SIZE), page_size=PAGE_SIZE).to_dict()
        except (Module2Error, PermissionDenied) as error:
            return {"status": "Error", "message": str(error)}

    @reactive.effect
    def _session_change():
        p = who()
        old = previous.get()
        if old and (p is None or old != p.principal_id) and service is not None:
            service.clear(old)                                # logout/role switch drops cached data
        previous.set(p.principal_id if p else None)
        page.set(0)
        manager = bool(scope() and scope()["manager_full_scope"])
        for tab in ("health", "monitoring"):
            ui.update_nav_panel("m2_tabs", tab, "show" if manager else "hide")
        ui.update_navset("m2_tabs", selected="priorities")
        rows = []
        response = all_rows()
        if response is not None and response.status in ("Ok", "Stale"):
            rows = list(response.rows)
            cursor = response.next_cursor
            while cursor:
                more = service.get_priorities(p, {}, cursor=cursor, page_size=200)
                rows += more.rows
                cursor = more.next_cursor
        for name, key, label in (("m2_f_channel", "channel", "All channels"), ("m2_f_region", "region", "All regions"),
                                 ("m2_f_tier", "segment_tier", "All tiers")):
            values = sorted({r[key] for r in rows if r.get(key)})
            ui.update_select(name, choices={"": label, **{v: v for v in values}}, selected="")
        stores = sorted(rows, key=lambda r: r["display_rank"])
        ui.update_select("m2_store", choices={"": "Select a store",
                                              **{r["store_id"]: f"{r['display_rank']}. {r['store_name'] or r['store_id']}"
                                                 for r in stores}}, selected="")

    seen = reactive.value(None)

    @reactive.effect
    async def _clear_rendered_rows():
        """Sign-out or identity switch removes the previous user's rows from the page."""
        p = who()
        current = p.principal_id if p else None
        with reactive.isolate():
            last = seen.get()
        if last is not None and current != last:
            await session.send_custom_message("m2-clear", {})
        seen.set(current)

    @reactive.effect
    @reactive.event(input.m2_f_channel, input.m2_f_region, input.m2_f_tier, input.m2_f_search, input.m2_f_msl,
                    input.m2_f_alert, input.m2_f_unknown)
    def _reset_page():
        page.set(0)

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
    def _open():
        ui.update_select("m2_store", selected=input.m2_open_store())
        ui.update_navset("m2_tabs", selected="detail")

    @reactive.effect
    @reactive.event(input.m2_back)
    def _back():
        ui.update_navset("m2_tabs", selected="priorities")

    # -------------------------------------------------------------- header
    @render.ui
    @safe
    def m2_header():
        p = who()
        record = service.record if service else {}
        outputs = record.get("model_outputs", {}) if service else {}
        title = tags.div(tags.h1("OOS risk & store visit priorities", class_="m2-title"),
                         tags.p("Predicted risk that a store-SKU is out of stock on any day in the next 7 days, "
                                "with transparent visit priorities.", class_="m2-sub"))
        if service is None:
            return TagList(tags.div(title, class_="m2-header"),
                           tags.div(tags.strong("Unavailable. "), unavailable_reason, class_="m2-banner bad", role="alert"))
        as_of = record.get("as_of")
        start = date.fromisoformat(as_of).toordinal() + 1 if as_of else None
        window = (f"{date.fromordinal(start).isoformat()} – {date.fromordinal(start + 6).isoformat()}" if start else "N/A")
        chips = tags.div(
            tags.span("Data as of ", tags.b(as_of or "N/A"), class_="m2-chip"),
            tags.span("Horizon ", tags.b(window), class_="m2-chip"),
            tags.span("Model ", tags.b(outputs.get("model_version") or "None released"), class_="m2-chip"),
            tags.span("Publication ", tags.b(outputs.get("publication_kind", "Base serving")), class_="m2-chip"),
            tags.span("Signed in ", tags.b(p.principal_id if p else "—"), class_="m2-chip"),
            class_="m2-chips")
        banners = []
        if outputs.get("synthetic"):
            banners.append(tags.div(tags.strong("Synthetic demo. "),
                                    "All stores, products and model results are generated test data, not client evidence.",
                                    class_="m2-banner synthetic", role="note"))
        elif outputs.get("publication_kind") == "Local_review_candidate":
            banners.append(tags.div(tags.strong("Local review candidate. "),
                                    "Not production-approved; pending technical review and owner decision.",
                                    class_="m2-banner info", role="note"))
        status = outputs.get("eligibility_status")
        if status and status != "Eligible_for_review":
            banners.append(tags.div(tags.strong("Model qualification: "), status.replace("_", " "),
                                    ". See Model health for the measured results." if not outputs.get("synthetic") else
                                    ". Synthetic results only.", class_="m2-banner warn", role="note"))
        if as_of and service._stale(date.fromisoformat(as_of)):
            banners.append(tags.div(tags.strong("Stale data. "),
                                    f"This candidate is for {as_of}; a newer accepted source date "
                                    f"({service.latest_source_as_of}) exists. Run the daily batch.",
                                    class_="m2-banner warn", role="alert", id="m2-stale-banner"))
        if record.get("model_outputs_status") != "Available":
            banners.append(tags.div(tags.strong("Predictions unreleased. "),
                                    "No released model outputs; missing predictions are not zero risk.",
                                    class_="m2-banner bad", role="alert"))
        return TagList(tags.div(title, chips, class_="m2-header"), *banners)

    # -------------------------------------------------------------- priorities
    @render.ui
    @safe
    def m2_tiles():
        data = priorities()
        if data["status"] not in ("Ok", "Stale", "Empty"):
            return None
        totals = data["totals"]

        def tile(label, value, note):
            return tags.div(tags.div(label, class_="m2-tile-label"), tags.div(value, class_="m2-tile-value"),
                            tags.div(note, class_="m2-tile-note"), class_="m2-tile")
        return tags.div(
            tile("Stores in your scope", fmt_int(totals["scoped_stores"]), f"{totals['ranked_stores']} ranked"),
            tile("Stores with predicted alerts", fmt_int(totals["alert_stores"]), "segment thresholds"),
            tile("Stores with observed MSL gap", fmt_int(totals["msl_gap_stores"]), "currently reported OOS"),
            tile("Need data follow-up", fmt_int(totals["data_followup_stores"]), "unknown inventory/MSL or no score"),
            class_="m2-tiles")

    @render.ui
    @safe
    def m2_count():
        data = priorities()
        if data["status"] not in ("Ok", "Stale", "Empty"):
            return None
        totals = data["totals"]
        first = page() * PAGE_SIZE + 1 if totals["filtered_stores"] else 0
        last = min((page() + 1) * PAGE_SIZE, totals["filtered_stores"])
        return tags.span(f"Showing {first}–{last} of {totals['filtered_stores']} filtered stores "
                         f"({totals['ranked_stores']} ranked in your scope)", class_="m2-muted", id="m2-count")

    @render.ui
    @safe
    def m2_page_label():
        data = priorities()
        if data["status"] not in ("Ok", "Stale"):
            return None
        pages = max(1, -(-data["totals"]["filtered_stores"] // PAGE_SIZE))
        return tags.span(f"Page {page() + 1} of {pages}", class_="m2-muted")

    @render.ui
    @safe
    def m2_priority_table():
        data = priorities()
        status = data["status"]
        if status == "Signed_out":
            return state("Signed out", "Sign in to see your store priorities.")
        if status == "Unavailable":
            return state("Unavailable", data.get("message") or "No Module 2 data.", "bad")
        if status == "Permission_denied":
            return state("No access", "Your account has no configured store scope.", "bad")
        if status == "Unreleased":
            return state("Predictions unreleased", "No released model outputs for this snapshot. "
                                                  "Missing predictions are not zero risk.", "bad")
        if status == "Error":
            return state("Could not load priorities", data.get("message", ""), "bad")
        if not data["rows"]:
            return state("No stores match", "No stores in your scope match these filters.")
        full = data["totals"].get("full_population_scope")
        head = [tags.th("Rank", scope="col", class_="num"), tags.th("Store", scope="col"),
                tags.th("Channel · tier", scope="col"), tags.th("Urgency", scope="col"),
                tags.th("Risk index", scope="col", class_="num"), tags.th("MSL gaps", scope="col", class_="num"),
                tags.th("Alert SKUs", scope="col", class_="num"), tags.th("Unknown inventory", scope="col", class_="num"),
                tags.th("Top SKUs", scope="col"), tags.th(tags.span("Action", class_="visually-hidden"), scope="col")]
        if full:
            head.insert(1, tags.th("Global rank", scope="col", class_="num"))
        body = []
        for r in data["rows"]:
            cells = [tags.td(str(r["display_rank"]), class_="num", **{"data-label": "Rank"}),
                     tags.td(tags.strong(r["store_name"] or r["store_id"]), tags.br(),
                             tags.span(r["store_id"], class_="m2-muted"), class_="m2-cell-store"),
                     tags.td(f"{r['channel']} · {r['segment_tier']}", **{"data-label": "Channel · tier"}),
                     tags.td(tags.span(r["bucket_label"], class_=f"m2-badge {BUCKET_CLASS[r['bucket']]}"),
                             **{"data-label": "Urgency"}),
                     tags.td(fmt_num(r["risk_score"]), class_="num", **{"data-label": "Risk index"}),
                     tags.td(str(r["msl_gap_count"]), class_="num", **{"data-label": "MSL gaps"}),
                     tags.td(str(r["alert_count"]), class_="num", **{"data-label": "Alert SKUs"}),
                     tags.td(str(r["inventory_unknown_count"]), class_="num",
                             **{"data-label": "Unknown inventory"}),
                     tags.td(", ".join(f"{s['sku_id']} ({fmt_pct(s['probability'], 0)})" for s in r["top_skus"][:3]),
                             **{"data-label": "Top SKUs"}),
                     tags.td(tags.button("Details", type="button", class_="btn m2-btn", **{
                         "aria-label": f"Details for {r['store_name'] or r['store_id']}",
                         "data-store": r["store_id"],
                         "onclick": "Shiny.setInputValue('m2_open_store', this.dataset.store, {priority: 'event'})"}),
                             class_="m2-cell-action")]
            if full:
                cells.insert(1, tags.td(str(r["global_rank"]), class_="num", **{"data-label": "Global rank"}))
            body.append(tags.tr(*cells, class_="m2-row-alert" if r["alert_count"] else None))
        note = ("Rank = your scope's order (global rank shown for full-population scope)." if full
                else "Rank = order within your assigned stores only.")
        stale = tags.div(tags.strong("Stale. "), "This candidate's data date is older than the latest accepted source date.",
                         class_="m2-banner warn") if status == "Stale" else None
        return TagList(stale, tags.div(tags.table(tags.caption(note), tags.thead(tags.tr(*head)), tags.tbody(*body),
                                                  class_="m2-table m2-cards", id="m2-priority-table"),
                                       class_="m2-table-wrap"))

    @render.ui
    @safe
    def m2_risk_chart():
        response = population()
        if response is None or response.status != "Ok":
            return state("No chart", "Risk distribution appears when released predictions are available.")
        bins = response.detail["risk_histogram"]
        import plotly.graph_objects as go
        fig = go.Figure(go.Bar(x=[f"{b['lower']:.1f}–{b['upper']:.1f}" for b in bins], y=[b["stores"] for b in bins],
                               marker_color="#2f6fd1", hovertemplate="Risk %{x}<br>%{y} stores<extra></extra>"))
        fig.update_layout(xaxis_title="Store risk index (max SKU probability)", yaxis_title="Stores in your scope",
                          bargap=0.08)
        return TagList(plot(fig, "m2-risk-histogram"),
                       tags.p(f"All {response.detail['scored_stores']} scored stores in your scope.", class_="m2-muted",
                              id="m2-histogram-total"))

    @render.ui
    @safe
    def m2_followups():
        response = population()
        if response is None or response.status != "Ok":
            return state("Nothing to show", "Follow-up list needs released predictions.")
        rows = response.detail["followups"]
        if not rows:
            return tags.p("No stores with unknown inventory or MSL status in your scope.", class_="m2-muted")
        items = []
        for r in rows:
            if r["scored"]:
                items.append(tags.li(f"{r['store_name'] or r['store_id']} ({r['bucket_label']}): "
                                     f"{r['inventory_unknown_count']} SKU(s) without a current inventory report, "
                                     f"{r['msl_unknown_count']} MSL SKU(s) unknown"))
            else:
                items.append(tags.li(f"{r['store_name'] or r['store_id']}: no valid model score"))
        return TagList(tags.p(f"{len(rows)} store(s) need a data check; unknown is not stable.", class_="m2-muted",
                              id="m2-followup-count"),
                       tags.ul(*items, class_="m2-list", id="m2-followup-list"))

    @render.download(filename=lambda: f"module2-visit-list-{date.today().isoformat()}.csv")
    def m2_export():
        p = who()
        if service is None or p is None:
            yield "status\nUnavailable\n"
            return
        try:
            yield service.export_visit_plan(p, filters())
        except Exception as error:
            yield f"status\n{type(error).__name__}\n"

    # -------------------------------------------------------------- detail
    @render.ui
    @safe
    def m2_detail():
        p = who()
        store_id = input.m2_store()
        if service is None or p is None:
            return state("Unavailable", unavailable_reason or "Sign in to see store detail.")
        if not store_id:
            return state("Choose a store", "Select a store or use Details from the priority list.")
        try:
            response = service.get_store_detail(p, store_id)
        except PermissionDenied:
            return state("Not available", "This store is not in your scope.", "bad")
        if response.status == "Unreleased":
            return state("Predictions unreleased", "No released model outputs; missing predictions are not zero risk.", "bad")
        detail = response.detail
        skus = detail["skus"]
        ui.update_select("m2_detail_sku", choices={"": "Select a SKU", **{s["sku_id"]: f"{s['sku_id']} · {s['product_name']}"
                                                                         for s in skus}},
                         selected=skus[0]["sku_id"] if skus else "")
        rows = []
        for s in skus:
            evidence = {e["feature"]: e["value"] for e in s["evidence"]}
            oos = evidence.get("oos_observed")
            current = "Unknown" if oos is None else ("Out of stock" if oos else "In stock")
            rows.append(tags.tr(
                tags.td(tags.strong(s["sku_id"]), tags.br(), tags.span(s["product_name"] or "", class_="m2-muted"),
                        class_="m2-cell-store"),
                tags.td("Yes" if s["is_msl"] else "No", **{"data-label": "MSL"}),
                tags.td(current, **{"data-label": "Reported now"}),
                tags.td(fmt_num(evidence.get("inventory_observed"), 0), class_="num", **{"data-label": "Inventory"}),
                tags.td(fmt_num(evidence.get("days_since_inventory_report"), 0), class_="num", **{"data-label": "Report age (days)"}),
                tags.td(fmt_pct(s["probability"]), class_="num", **{"data-label": "7-day OOS risk"}),
                tags.td(fmt_pct(s["threshold"]), class_="num", **{"data-label": "Alert threshold"}),
                tags.td(tags.span("Alert" if s["is_predicted_oos"] else "No alert",
                                  class_="m2-badge " + ("b2" if s["is_predicted_oos"] else "b4")), **{"data-label": "Decision"}),
                tags.td(fmt_num(evidence.get("in_stock_velocity_28d"), 2), class_="num", **{"data-label": "Velocity 28d"}),
                tags.td(fmt_num(evidence.get("receipt_units_7d"), 0), class_="num", **{"data-label": "Received 7d"}),
                tags.td(fmt_num(evidence.get("scheduled_units_next7d"), 0), class_="num", **{"data-label": "Scheduled 7d"}),
                tags.td(s["input_status"].replace("_", " "), **{"data-label": "Inputs"})))
        head = ["SKU", "MSL", "Reported now", "Inventory", "Report age (days)", "7-day OOS risk", "Alert threshold",
                "Decision", "Velocity 28d", "Received 7d", "Scheduled 7d", "Inputs"]
        numeric = {"Inventory", "Report age (days)", "7-day OOS risk", "Alert threshold", "Velocity 28d",
                   "Received 7d", "Scheduled 7d"}
        window = skus[0]["prediction_window"] if skus else ["", ""]
        return tags.div(
            tags.h2(f"Store {store_id}", class_="m2-h"),
            tags.p(f"Reported state as of {detail['scoring_date']} · predicted window {window[0]} – {window[1]}. "
                   "Current reported state and future predicted risk are shown in separate columns.", class_="m2-muted"),
            tags.div(tags.table(tags.caption("SKUs sorted by predicted risk. Alerts use each segment's threshold."),
                                tags.thead(tags.tr(*[tags.th(h, scope="col", class_="num" if h in numeric else None)
                                                     for h in head])),
                                tags.tbody(*rows), class_="m2-table m2-cards", id="m2-detail-table"),
                     class_="m2-table-wrap"),
            tags.ul(*[tags.li(t) for t in response.limitations], class_="m2-list"),
            class_="m2-card")

    @render.ui
    @safe
    def m2_history_chart():
        p = who()
        store_id, sku = input.m2_store(), input.m2_detail_sku()
        if service is None or p is None or not store_id or not sku:
            return state("No SKU selected", "Pick a store and SKU to see its reported history.")
        try:
            response = service.get_store_detail(p, store_id)
        except PermissionDenied:
            return state("Not available", "This store is not in your scope.", "bad")
        history = sorted((h for h in response.detail.get("history", []) if h["sku_id"] == sku), key=lambda h: h["date"])
        if not history:
            return state("No history", "No reported rows in the last 28 days for this SKU.")
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[h["date"] for h in history], y=[h["inventory_observed"] for h in history],
                                 mode="lines+markers", name="Reported inventory", line=dict(color="#2f6fd1"),
                                 connectgaps=False))
        oos = [h for h in history if h["oos_observed"]]
        fig.add_trace(go.Scatter(x=[h["date"] for h in oos], y=[0] * len(oos), mode="markers", name="Reported OOS day",
                                 marker=dict(symbol="x", size=10, color="#9b1c1c")))
        fig.update_layout(yaxis_title="Units (reported)", legend=dict(orientation="h", y=-0.25))
        return TagList(plot(fig, "m2-history-chart"),
                       tags.p("Gaps are days without a report (Unknown), not zero.", class_="m2-muted"))

    # -------------------------------------------------------------- manager views
    @render.ui
    @safe
    def m2_health():
        p = who()
        if service is None or p is None:
            return state("Unavailable", unavailable_reason or "Sign in required.")
        response = service.get_model_health(p)
        if response.status == "Permission_denied":
            return state("Manager access only", "Model health requires full-population manager scope.", "bad")
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
        if service is None or p is None:
            return state("Unavailable", unavailable_reason or "Sign in required.")
        response = service.get_monitoring(p)
        if response.status == "Permission_denied":
            return state("Manager access only", "Monitoring requires full-population manager scope.", "bad")
        if response.status == "Unavailable" or not response.rows:
            return state("No monitoring yet", response.message or "No monitoring package in this publication.")
        return _monitoring(response.rows)


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
    for algorithm in sorted({r["algorithm"] for r in complete}):
        mine = [r for r in complete if r["algorithm"] == algorithm]
        fig.add_trace(go.Scatter(x=[r["recall"] for r in mine], y=[r["precision"] for r in mine], mode="markers",
                                 name=algorithm.replace("_", " "),
                                 marker=dict(symbol=symbols.get(algorithm, "circle"), size=[14 if r["selected"] else 8 for r in mine],
                                             color="#2f6fd1" if algorithm != "logistic_regression" else "#5b738b",
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
                                   marker_color="#2f6fd1"))
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
