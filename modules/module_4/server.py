"""
modules/module_4/server.py
==========================
Server logic for Module 4: Commercial Copilot.

The principal (store scope) and the document role come from the server-side
session (shared/auth.py or the integrated shell), never from the question.
Questions run in a background task so the app stays responsive while the local
model works. Signing out or switching user clears the conversation, and an
answer that finishes after that is never shown in the new session context.
"""

from __future__ import annotations

import asyncio
import html
import threading

import pandas as pd
from htmltools import TagList, tags
from shiny import reactive, render, ui

from shared import ui_kit as kit

from .agent import Copilot, CopilotAnswer
from .formatting import display_frame
from .ui import PROMPTS

_COPILOT = None
_COPILOT_LOCK = threading.Lock()
ROUTE_LABEL = {"data": ("Your data", "data", "database"), "knowledge": ("Marvel documents", "knowledge", "doc"),
               "other": ("Outside the copilot's scope", "other", "help")}
# Offered after an unavailable data answer: they fill the question box, they are not sent.
REPHRASE = ("How many units were sold in each channel last month?",
            "Which SKUs were out of stock most often in the last 4 weeks?")
TABLE_ROWS = 50


def shared_copilot() -> Copilot:
    """One copilot per process: the pinned publication, sandbox cache and index are shared by sessions."""
    global _COPILOT
    with _COPILOT_LOCK:
        if _COPILOT is None:
            _COPILOT = Copilot.from_env()
        return _COPILOT


def _markdown(text: str):
    # Escape first: model output and document text are never rendered as raw HTML.
    return ui.markdown(html.escape(text or ""))


def _table(frame: pd.DataFrame) -> ui.HTML:
    # Same text as the summarized result: probabilities as percentages, small values never shown as zero.
    shown = display_frame(frame.head(TABLE_ROWS))
    table = shown.to_html(index=False, border=0, classes="m4-table ws-table")
    return ui.HTML(f'<div class="m4-table-wrap ws-table-wrap">{table}</div>')


def _chart(fig, element_id: str) -> ui.HTML:
    markup = fig.to_html(full_html=False, include_plotlyjs=False, div_id=element_id,
                         config={"displayModeBar": False, "responsive": True})
    return ui.HTML(f'<div class="m4-chart" role="img" aria-label="Chart of the query result">{markup}</div>')


def _tabs(answer: CopilotAnswer, index: int):
    """Chart | Table | SQL for one data answer; the chart is first when there is one."""
    rows = len(answer.frame)
    note = f"First {TABLE_ROWS} of {rows} rows" if rows > TABLE_ROWS else f"{rows} row{'s' if rows != 1 else ''}"
    if answer.truncated:
        note += " (result capped at 200 rows)"
    panels = []
    if answer.chart is not None:
        panels.append(("chart", "Chart", _chart(answer.chart, f"m4-chart-{index}")))
    panels.append(("table", "Table", tags.div(_table(answer.frame), tags.p(note, class_="ws-muted m4-table-note"))))
    if answer.sql:
        panels.append(("sql", "SQL", tags.pre(answer.sql, class_="m4-sql ws-code")))
    first = panels[0][0]
    bar = tags.div(*[tags.button(title, type="button", role="tab", class_="m4-tab", id=f"m4-tab-{index}-{key}",
                                 tabindex="0" if key == first else "-1",
                                 **{"data-m4-tab": key, "aria-selected": "true" if key == first else "false",
                                    "aria-controls": f"m4-panel-{index}-{key}"})
                     for key, title, _ in panels], class_="m4-tabs", role="tablist", **{"aria-label": "Result views"})
    views = [tags.div(body, id=f"m4-panel-{index}-{key}", role="tabpanel", class_=f"m4-panel m4-panel-{key}",
                      hidden=None if key == first else "hidden",
                      **{"data-m4-panel": key, "aria-labelledby": f"m4-tab-{index}-{key}"})
             for key, _, body in panels]
    return tags.div(bar, *views, class_="m4-result")


def _trust(answer: CopilotAnswer):
    """Compact source tags (always visible) and the technical provenance (on request)."""
    p = answer.provenance or {}
    if not p:
        return None
    pills, line = [], ""
    if answer.route == "data":
        outputs = p.get("model_outputs") or {}
        pills.append(kit.pill(f"Reported data · to {p.get('as_of')}", "outline"))
        if p.get("scope"):
            pills.append(kit.pill(str(p["scope"]).capitalize(), "outline"))
        if outputs.get("tables"):
            kind = (outputs.get("publication_kind") or "").replace("_", " ").lower()
            window = outputs.get("window") or [None, None]
            pills.append(kit.pill(f"Prediction · {kind} · {window[0]} to {window[1]}", "outline", class_="m4-pill-model"))
        line = (f"Source: shared business publication {p.get('artifact_id')} · data through {p.get('as_of')} · "
                f"scope: {p.get('scope')} · {p.get('metric_version')} · model: {p.get('model')}")
        if outputs.get("tables"):
            line += (f" · predictions: {', '.join(outputs['tables'])} from scoring run {outputs['scoring_run_id']}"
                     f" ({outputs['scoring_date']}, {outputs['publication_kind']}, model {outputs['model_version']})")
    else:
        pills.append(kit.pill(f"Marvel documents · access: {p.get('role')}", "outline"))
        line = (f"Source: Marvel knowledge base (corpus {p.get('corpus_hash')}) · document access: "
                f"{p.get('role')} · retrieval: {p.get('retrieval')} · model: {p.get('model')}")
    return tags.div(tags.div(*pills, class_="m4-pills"),
                    tags.details(tags.summary("How this was answered"), tags.div(line, class_="m4-provenance ws-muted"),
                                 class_="m4-how"),
                    class_="m4-trust")


def render_answer(answer: CopilotAnswer, index: int) -> ui.Tag:
    label, kind, icon_name = ROUTE_LABEL.get(answer.route, ("Copilot", "other", "help"))
    problem = answer.status in ("unavailable", "denied", "failed")
    body = [tags.div(_markdown(answer.answer), class_="m4-warn ws-banner warn") if problem
            else tags.div(_markdown(answer.answer), class_="m4-headline")]
    if problem and answer.route == "data" and answer.status == "unavailable":
        body.append(tags.div(tags.span("You could ask", class_="ws-label"),
                             *[tags.button(q, type="button", class_="m4-rephrase", **{"data-m4-fill": q})
                               for q in REPHRASE], class_="m4-rephrases"))
    if answer.route == "data" and answer.frame is not None and len(answer.frame):
        body.append(_tabs(answer, index))
    elif answer.sql:
        body.append(tags.details(tags.summary("Show the SQL that produced this answer"),
                                 tags.pre(answer.sql, class_="m4-sql ws-code"),
                                 class_="m4-details ws-disclosure m4-section"))
    if answer.notes:
        body.append(tags.div(tags.strong("About these predictions"),
                             tags.ul(*[tags.li(n) for n in answer.notes]),
                             class_="m4-section m4-model-note ws-banner info", role="note"))
    if answer.citations:
        body.append(tags.div(tags.div("Sources", class_="ws-label m4-section-title"),
                             tags.ul(*[tags.li(tags.span(c["marker"], class_="m4-marker"), c["citation"])
                                       for c in answer.citations], class_="m4-cites"), class_="m4-section"))
    if answer.sources and answer.route == "knowledge":
        passages = [tags.div(tags.span(s["marker"], class_="m4-marker"), s["citation"],
                             tags.div(s.get("text", ""), class_="m4-passage") if s.get("text") else None)
                    for s in answer.sources]
        body.append(tags.details(tags.summary(f"All {len(answer.sources)} retrieved passages"),
                                 *passages, class_="m4-details ws-disclosure m4-section"))
    head = tags.div(tags.span(kit.icon(icon_name, 14), class_=f"m4-route-mark {kind}"),
                    tags.span(label, class_=f"m4-route {kind}"),
                    tags.span(f"{answer.elapsed_s:.1f}s", class_="m4-meta ws-muted"), class_="m4-a-head")
    return tags.div(tags.div(tags.div(answer.question, class_="m4-q"), class_="m4-q-row"),
                    tags.article(head, tags.div(*body, class_="m4-answer"), _trust(answer), class_="m4-a"),
                    class_="m4-turn")


def module_4_server(input, output, session, dataset: dict, auth_state: dict):
    """App entry point.

    In the integrated app ``dataset['runtime']`` (shared.runtime.SharedRuntime) supplies the
    copilot bound to the shared source used by every module; otherwise the standalone
    configuration (Copilot.from_env) is used.
    """
    runtime = dataset.get("runtime") if isinstance(dataset, dict) else None
    copilot = runtime.copilot() if runtime is not None else shared_copilot()
    history = reactive.value([])
    pending = reactive.value(None)
    last_result = reactive.value(None)
    active_turn = reactive.value(None)      # (principal_id, sequence) of the question being answered
    turns = {"n": 0}

    def principal():
        pid, authenticated = auth_state.get("principal_id"), auth_state.get("authenticated")
        if pid is None or authenticated is None or not authenticated() or not pid():
            return None
        return pid()

    def doc_role():
        # Document permission is a separate check from store scope (Module 4 guide, section 5).
        return "manager" if auth_state["role"]() == "Regional Manager" else "sales"

    @ui.bind_task_button(button_id="m4_btn_ask")
    @reactive.extended_task
    async def ask_task(question, principal_id, role, turn):
        try:
            result = await asyncio.to_thread(copilot.ask, question, principal_id, role)
        except Exception:  # noqa: BLE001 - shown as a failed answer, never a stack trace
            result = CopilotAnswer(question=question, route="", status="failed",
                                   answer="Something went wrong while answering. Please try again.")
        return turn, result

    def start(question):
        question = (question or "").strip()
        pid = principal()
        if not question or pid is None or ask_task.status() == "running":
            return
        turns["n"] += 1
        turn = (pid, turns["n"])
        active_turn.set(turn)
        pending.set(question)
        ask_task.invoke(question, pid, doc_role(), turn)
        ui.update_text_area("m4_user_query", value="")          # the question moves into the conversation

    @reactive.effect
    @reactive.event(input.m4_btn_ask)
    def _ask():
        start(input.m4_user_query())

    request = auth_state.get("copilot_request")
    if request is not None:
        @reactive.effect
        @reactive.event(request)
        def _host_question():
            """A question from another module of the shell (Module 2's overview or store page)."""
            question = request()[0]
            if ask_task.status() == "running":
                ui.update_text_area("m4_user_query", value=question)   # the earlier answer is still running
            else:
                start(question)

    for i, prompt in enumerate(PROMPTS):
        def make(i=i, prompt=prompt):
            @reactive.effect
            @reactive.event(input[f"m4_ex_{i}"])
            def _example():
                ui.update_text_area("m4_user_query", value=prompt)
                start(prompt)
        make()

    @reactive.effect
    def _collect():
        status = ask_task.status()
        if status not in ("success", "error"):
            return
        if status == "success":
            turn, result = ask_task.result()
        else:
            turn, result = None, CopilotAnswer(question=pending.get() or "", route="", status="failed",
                                               answer="Something went wrong while answering. Please try again.")
        with reactive.isolate():
            if last_result.get() is result:
                return
            last_result.set(result)
            current = active_turn.get()
            # Sign-out, a different user or "Clear" while answering: the answer belongs to no visible turn.
            if current is None or (turn is not None and (turn != current or principal() != turn[0])):
                return
            if result.question and principal() is not None:
                history.set([result] + history.get())
            pending.set(None)
            active_turn.set(None)

    @reactive.effect
    @reactive.event(input.m4_btn_clear)
    def _clear():
        history.set([])
        pending.set(None)
        active_turn.set(None)
        if ask_task.status() == "running":
            ask_task.cancel()                # its answer would be discarded anyway; free the Ask button

    @reactive.effect
    def _reset_on_identity_change():
        principal()  # dependency: sign-out or a different user clears the conversation
        with reactive.isolate():
            history.set([])
            pending.set(None)
            active_turn.set(None)
            if ask_task.status() == "running":
                ask_task.cancel()            # never delivered to the next user; frees the Ask button

    @render.ui
    def m4_user_block():
        name, title = auth_state.get("name"), auth_state.get("title")
        if principal() is None or name is None or not name():
            return None
        return kit.user_block(name(), title() if title else "", id="m4-user")

    @render.ui
    def m4_status():
        """The data-scope rail: source, scope, prediction release, language model and document access."""
        history()  # refresh after each answer
        st = copilot.status()
        pid = principal()

        def row(ok, title, text, detail=None):
            return tags.div(tags.span(class_="ws-dot " + ("ok" if ok else "bad"), **{"aria-hidden": "true"}),
                            tags.div(tags.span(title, class_="m4-rail-title"), tags.span(text, class_="m4-rail-text"),
                                     tags.span(detail, class_="m4-rail-detail") if detail else None),
                            class_="m4-rail-row m4-chip")
        rows = []
        if st["data_ready"]:
            rows.append(row(True, "Business data", f"Data through {st['as_of']}", st["artifact_id"]))
            scope = None
            if pid is not None and copilot.reader is not None:
                try:
                    scope = copilot.scope_label(pid)
                except Exception:  # noqa: BLE001 - no scope: the answer path says so
                    scope = "no configured scope"
            if scope:
                rows.append(row(scope != "no configured scope", "Your scope", str(scope).capitalize()))
            released = st["predictions"] == "Available"
            rows.append(row(released, "Stockout predictions",
                            f"Predictions: {(st['predictions_kind'] or '').replace('_', ' ').lower()} · "
                            f"run {st['predictions_run']}" if released else "Predictions: not released"))
        else:
            rows.append(row(False, "Business data", st["data_reason"] or "No business data configured"))
        rows.append(row(st["model_ready"], "Language model",
                        f"Model: {st['model']}" + ("" if st["model_ready"] else " (not running)")))
        role = doc_role() if pid else None
        rows.append(row(True, "Documents", "Knowledge base: 12 documents" + (f" · access: {role}" if role else "")))
        return tags.div(*rows, tags.p("Answers use only this data and these documents. Predictions are model "
                                      "estimates, not observed stockouts.", class_="ws-muted m4-rail-note"),
                        class_="m4-status", role="status")

    @output(suspend_when_hidden=False)
    @render.text
    def m4_has_turns():
        return "yes" if history() or (pending() and active_turn() is not None) else "no"

    @render.ui
    def m4_conversation():
        items = []
        turns = history()                                     # newest first; shown oldest first
        for n, answer in enumerate(reversed(turns), 1):
            items.append(render_answer(answer, n))
        question = pending()
        if question and active_turn() is not None and ask_task.status() == "running":
            items.append(tags.div(
                tags.div(tags.div(question, class_="m4-q"), class_="m4-q-row"),
                tags.article(tags.div(tags.span(class_="m4-spinner", **{"aria-hidden": "true"}),
                                      "Working on it: choosing data or documents, then answering…",
                                      class_="m4-thinking"),
                             tags.div(tags.span(class_="m4-line w80"), tags.span(class_="m4-line w60"),
                                      tags.span(class_="m4-block"), class_="m4-skeleton", **{"aria-hidden": "true"}),
                             class_="m4-a m4-pending"),
                class_="m4-turn", role="status", aria_live="polite"))
        return TagList(*items)
