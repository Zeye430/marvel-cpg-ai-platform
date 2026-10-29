"""
modules/module_4/server.py
==========================
Server logic for Module 4: Commercial Copilot.

The principal (store scope) and the document role come from the server-side
session (shared/auth.py), never from the question. Questions run in a background
task so the app stays responsive while the local model works. Signing out or
switching user clears the conversation.
"""

from __future__ import annotations

import asyncio
import html
import threading

import pandas as pd
from htmltools import TagList, tags
from shiny import reactive, render, ui

from .agent import Copilot, CopilotAnswer
from .ui import PROMPTS

_COPILOT = None
_COPILOT_LOCK = threading.Lock()
ROUTE_LABEL = {"data": ("DATA · TEXT-TO-SQL", "data"), "knowledge": ("KNOWLEDGE · RAG", "knowledge"),
               "other": ("OUT OF SCOPE", "other")}
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
    shown = frame.head(TABLE_ROWS)
    table = shown.to_html(index=False, border=0, classes="m4-table", na_rep="—",
                          float_format=lambda v: f"{v:,.2f}")
    return ui.HTML(f'<div class="m4-table-wrap">{table}</div>')


def _chart(fig, element_id: str) -> ui.HTML:
    markup = fig.to_html(full_html=False, include_plotlyjs=False, div_id=element_id,
                         config={"displayModeBar": False, "responsive": True})
    return ui.HTML(f'<div role="img" aria-label="Chart of the query result">{markup}</div>')


def render_answer(answer: CopilotAnswer, index: int) -> ui.Tag:
    label, kind = ROUTE_LABEL.get(answer.route, ("COPILOT", "other"))
    body = [_markdown(answer.answer)]
    if answer.status in ("unavailable", "denied", "failed"):
        body = [tags.div(_markdown(answer.answer), class_="m4-warn")]
    if answer.route == "data" and answer.frame is not None and len(answer.frame):
        if answer.chart is not None:
            body.append(tags.div(tags.div("Chart", class_="m4-section-title"),
                                 _chart(answer.chart, f"m4-chart-{index}"), class_="m4-section"))
        rows = len(answer.frame)
        note = f"first {TABLE_ROWS} of {rows}" if rows > TABLE_ROWS else f"{rows} row{'s' if rows != 1 else ''}"
        if answer.truncated:
            note += " (result capped at 200 rows)"
        body.append(tags.div(tags.div(f"Result table · {note}", class_="m4-section-title"),
                             _table(answer.frame), class_="m4-section"))
    if answer.sql:
        body.append(tags.details(tags.summary("Show the SQL that produced this answer"),
                                 tags.pre(answer.sql, class_="m4-sql"), class_="m4-details m4-section"))
    if answer.citations:
        body.append(tags.div(tags.div("Sources cited", class_="m4-section-title"),
                             tags.ul(*[tags.li(tags.span(c["marker"], class_="m4-marker"), c["citation"])
                                       for c in answer.citations], class_="m4-cites"), class_="m4-section"))
    if answer.sources and answer.route == "knowledge":
        passages = [tags.div(tags.span(s["marker"], class_="m4-marker"), s["citation"],
                             tags.div(s.get("text", ""), class_="m4-passage") if s.get("text") else None)
                    for s in answer.sources]
        body.append(tags.details(tags.summary(f"All {len(answer.sources)} retrieved passages"),
                                 *passages, class_="m4-details m4-section"))
    p = answer.provenance
    if p:
        if answer.route == "data":
            line = (f"Source: shared business publication {p.get('artifact_id')} · data through {p.get('as_of')} · "
                    f"scope: {p.get('scope')} · {p.get('metric_version')} · model: {p.get('model')}")
        else:
            line = (f"Source: Marvel knowledge base (corpus {p.get('corpus_hash')}) · document access: "
                    f"{p.get('role')} · retrieval: {p.get('retrieval')} · model: {p.get('model')}")
        body.append(tags.div(line, class_="m4-provenance"))
    head = tags.div(tags.span(label, class_=f"m4-route {kind}"),
                    tags.span(f"{answer.elapsed_s:.1f}s", class_="m4-meta"), class_="m4-a-head")
    return tags.div(tags.div(tags.div(answer.question, class_="m4-q"), class_="m4-q-row"),
                    tags.div(head, tags.div(*body, class_="m4-answer"), class_="sleek-card m4-a"),
                    class_="m4-turn")


def module_4_server(input, output, session, dataset: dict, auth_state: dict):
    """Root-app entry point; ``dataset`` is accepted for compatibility and not used."""
    copilot = shared_copilot()
    history = reactive.value([])
    pending = reactive.value(None)
    last_result = reactive.value(None)

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
    async def ask_task(question, principal_id, role):
        return await asyncio.to_thread(copilot.ask, question, principal_id, role)

    def start(question):
        question = (question or "").strip()
        if not question or ask_task.status() == "running":
            return
        pending.set(question)
        ask_task.invoke(question, principal(), doc_role())

    @reactive.effect
    @reactive.event(input.m4_btn_ask)
    def _ask():
        start(input.m4_user_query())

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
            result = ask_task.result()
        else:
            result = CopilotAnswer(question=pending.get() or "", route="", status="failed",
                                   answer="Something went wrong while answering. Please try again.")
        with reactive.isolate():
            if last_result.get() is result:
                return
            last_result.set(result)
            if result.question and principal() is not None:
                history.set([result] + history.get())
            pending.set(None)

    @reactive.effect
    @reactive.event(input.m4_btn_clear)
    def _clear():
        history.set([])

    @reactive.effect
    def _reset_on_identity_change():
        principal()  # dependency: sign-out or a different user clears the conversation
        with reactive.isolate():
            history.set([])
            pending.set(None)

    @render.ui
    def m4_status():
        history()  # refresh after each answer
        st = copilot.status()
        def chip(ok, text):
            return tags.span(tags.span(class_="m4-dot " + ("ok" if ok else "bad")), text, class_="m4-chip")
        data_text = (f"Data through {st['as_of']} · {st['artifact_id']}" if st["data_ready"]
                     else (st["data_reason"] or "No business data configured"))
        role = doc_role() if principal() else None
        return tags.div(
            chip(st["model_ready"], f"Model: {st['model']}" + ("" if st["model_ready"] else " (not running)")),
            chip(st["data_ready"], data_text),
            chip(True, "Knowledge base: 12 documents" + (f" · access: {role}" if role else "")),
            class_="m4-status", role="status")

    @render.ui
    def m4_conversation():
        items = []
        question = pending()
        if question and ask_task.status() == "running":
            items.append(tags.div(tags.div(tags.div(question, class_="m4-q"), class_="m4-q-row"),
                                  tags.div(tags.div(tags.span(class_="m4-spinner"),
                                                    "Working on it: choosing data or documents, then answering…",
                                                    class_="m4-thinking"), class_="sleek-card m4-a"),
                                  class_="m4-turn", role="status", aria_live="polite"))
        turns = history()
        for n, answer in enumerate(turns):
            items.append(render_answer(answer, len(turns) - n))
        if not items:
            items.append(tags.div("Ask a question above, or start with one of the example questions.",
                                  class_="m4-empty sleek-card"))
        return TagList(*items)
