"""
modules/module_4/agent.py
=========================
The copilot: routes a question to the data path (Text-to-SQL) or the document path
(RAG) and returns one answer object for the UI, the CLI and the evaluation.

Identity rules (Module 4 guide): `principal_id` (store scope) and `doc_role`
(document permission) come from the verified app session. Nothing in the question,
the documents or the model output can change them.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .charts import auto_chart
from .llm import ChatModel, LLMUnavailable
from .nlq import NLQEngine
from .rag import KnowledgeAssistant, KnowledgeUnavailable
from .router import DATA, KNOWLEDGE, OTHER, route
from .sandbox import SandboxPool, ScopeDenied

MAX_QUESTION_CHARS = 1000
OUT_OF_SCOPE = ("I can answer questions about Marvel CPG's sales, stock, shelf execution and deliveries, and about "
                "Marvel's product and policy documents. This question is outside that scope.")
MODEL_DOWN = ("The language model is not running, so I can't answer this right now. Start it with `ollama serve` "
              "(see modules/module_4/README.md) and ask again.")


@dataclass
class CopilotAnswer:
    question: str
    route: str
    status: str  # ok | empty | failed | no_evidence | out_of_scope | unavailable | denied
    answer: str
    sql: str | None = None
    frame: pd.DataFrame | None = None
    truncated: bool = False
    chart: object | None = None
    citations: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)
    elapsed_s: float = 0.0
    route_method: str = ""


def open_configured_reader(environ=None):
    """Pin one accepted business publication (same reader and checks as the other modules)."""
    from examples.read_shared_data import open_reader
    env = os.environ if environ is None else environ
    artifact_dir = env.get("MODULE4_SERVING_ARTIFACT_DIR")
    publication = env.get("MODULE4_SERVING_PUBLICATION")
    if artifact_dir:
        return open_reader(artifact_dir=Path(artifact_dir))
    return open_reader(publication=Path(publication) if publication else None)


class Copilot:
    def __init__(self, llm=None, reader=None, knowledge=None, data_reason=None):
        self.llm = llm or ChatModel()
        self.reader = reader
        self.pool = SandboxPool(reader) if reader is not None else None
        self.data_reason = data_reason
        self.nlq = NLQEngine(self.llm)
        self._knowledge = knowledge
        self.knowledge_reason = None

    @classmethod
    def from_env(cls, environ=None):
        try:
            reader, reason = open_configured_reader(environ), None
        except Exception as error:  # surfaced as an explicit unavailable state, never a stack trace
            reader, reason = None, f"No accepted business publication is available ({type(error).__name__})."
        return cls(reader=reader, data_reason=reason)

    @property
    def knowledge(self):
        if self._knowledge is None and self.knowledge_reason is None:
            try:
                dense = os.environ.get("MODULE4_RAG_DENSE", "1") != "0"
                self._knowledge = KnowledgeAssistant(self.llm, use_dense=dense)
            except (KnowledgeUnavailable, OSError, ValueError) as error:
                self.knowledge_reason = str(error) if isinstance(error, KnowledgeUnavailable) else \
                    f"The knowledge base could not be loaded ({type(error).__name__})."
        return self._knowledge

    def status(self) -> dict:
        record = self.reader.record if self.reader is not None else {}
        return {"model": self.llm.name, "model_ready": self.llm.available(),
                "data_ready": self.reader is not None, "data_reason": self.data_reason,
                "as_of": record.get("as_of"), "artifact_id": record.get("artifact_id")}

    # ------------------------------------------------------------------ paths
    def _data_path(self, question, principal_id, out: CopilotAnswer) -> CopilotAnswer:
        if self.pool is None:
            out.status, out.answer = "unavailable", self.data_reason or "No business data is configured."
            return out
        try:
            sandbox = self.pool.get(principal_id)
        except ScopeDenied:
            out.status, out.answer = "denied", "Your account has no data scope configured, so no data can be shown."
            return out
        result = self.nlq.answer(question, sandbox)
        out.status, out.answer, out.sql, out.frame = result.status, result.answer, result.sql, result.frame
        out.truncated, out.attempts = result.truncated, result.attempts
        if result.status == "ok":
            out.chart = auto_chart(result.frame)
        out.provenance = {"source": "business.duckdb (shared serving publication)", "artifact_id": sandbox.artifact_id,
                          "as_of": str(sandbox.as_of), "dataset_hash": sandbox.dataset_hash[:12],
                          "scope": sandbox.scope_label, "metric_version": "shared_kpi.v1, reported data only",
                          "rows": None if result.frame is None else len(result.frame),
                          "rows_capped": result.truncated, "model": self.llm.name}
        return out

    def _document_path(self, question, doc_role, out: CopilotAnswer) -> CopilotAnswer:
        assistant = self.knowledge
        if assistant is None:
            out.status, out.answer = "unavailable", self.knowledge_reason
            return out
        try:
            result = assistant.answer(question, doc_role)
        except LLMUnavailable:
            # Still useful: show the permitted passages without a generated summary.
            search = assistant.search(question, doc_role)
            out.status, out.answer = "unavailable", MODEL_DOWN
            out.sources = [{"marker": f"S{i}", "citation": r["citation"], "document_id": r["document_id"],
                            "version": r["version"], "section": r["section"], "title": r["title"],
                            "cited": False, "dense_score": r["dense_score"], "text": r["text"]}
                           for i, r in enumerate(search.get("results", []), 1)]
            return out
        out.status, out.answer = result["status"], result["answer"]
        out.citations, out.sources = result["citations"], result["sources"]
        out.provenance = {"source": "knowledge_base/ (12 authorized documents)", "role": doc_role,
                          "corpus_hash": str(assistant.kb.manifest.get("corpus_hash", ""))[:12],
                          "retrieval": assistant.retrieval_mode, "best_dense_score": result.get("best_dense_score"),
                          "model": self.llm.name}
        return out

    def ask(self, question: str, principal_id: str | None, doc_role: str | None) -> CopilotAnswer:
        started = time.perf_counter()
        question = " ".join((question or "").split())[:MAX_QUESTION_CHARS]
        out = CopilotAnswer(question=question, route="", status="", answer="")
        if not question:
            out.status, out.answer = "failed", "Type a question first."
            return out
        if not principal_id or doc_role not in ("sales", "manager"):
            out.status, out.answer = "denied", "Sign in to use the copilot."
            return out
        try:
            decision = route(question, self.llm)
        except LLMUnavailable:
            decision = route(question, None)
        out.route, out.route_method = decision["route"], decision["method"]
        try:
            if out.route == OTHER:
                out.status, out.answer = "out_of_scope", OUT_OF_SCOPE
            elif out.route == KNOWLEDGE:
                self._document_path(question, doc_role, out)
            else:
                out.route = DATA
                self._data_path(question, principal_id, out)
        except LLMUnavailable:
            out.status, out.answer = "unavailable", MODEL_DOWN
        out.elapsed_s = time.perf_counter() - started
        return out
