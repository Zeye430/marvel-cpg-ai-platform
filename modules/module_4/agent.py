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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .charts import auto_chart
from .llm import ChatModel, LLMUnavailable
from .nlq import NLQEngine
from .rag import KnowledgeAssistant, KnowledgeUnavailable
from .router import DATA, KNOWLEDGE, OTHER, route
from .sandbox import MODEL_TABLE_SQL, SandboxPool, ScopeDenied
from .sql_guard import referenced_tables

MAX_QUESTION_CHARS = 1000
OUT_OF_SCOPE = ("I can answer questions about Marvel CPG's sales, stock, shelf execution and deliveries, and about "
                "Marvel's product and policy documents. This question is outside that scope.")
MODEL_DOWN = ("The language model is not running, so I can't answer this right now. Start it with `ollama serve` "
              "(see modules/module_4/README.md) and ask again.")
SCOPE_CHANGED = ("Your data access changed while this question was being answered, so the answer was discarded. "
                 "Please ask again.")
# What a data question asks for, from its words (deterministic). It only decides when the copilot must
# answer from Module 2's published outputs or say they are unavailable, so that observed stock history
# never stands in for a prediction; it never grants access to anything. A plain "ranking" (for example
# of sales) is an ordinary history question.
FUTURE = re.compile(r"(?<!\bi )(?<!\bwe )\b(will|won't|going to|gonna|likely|expected to|about to|upcoming|"
                    r"forecast\w*|project(ed|ion|ions)|next (\d+ |few |seven |couple of )?(days?|weeks?|months?|"
                    r"quarters?|periods?)|coming (\d+ )?(days?|weeks?|months?)|tomorrow|soon|in the future)\b", re.I)
STOCKOUT = re.compile(r"\b(out[- ]of[- ]stock|stock[- ]?outs?|oos|run(s|ning)? (out|dry)|ran out|go(es|ing)? out|"
                      r"sell(s|ing)? out|shortages?|empty shel\w*)\b", re.I)
MODEL_TERMS = re.compile(r"\b(predict\w*|probabilit\w*|alerts?|risk\w*)\b", re.I)
VISIT_RANKING = re.compile(r"\b(visit (priorit\w*|list|plan|order|rank\w*|first|next)|priority (rank\w*|list|order)|"
                           r"(stores?|where) (to|should i|should we|do i|must i) visit|prioriti[sz]\w*|"
                           r"priorit(y|ies))\b", re.I)
BUSINESS_RANKING_QUESTION = re.compile(r"\b(business[- ]priorit\w*|business rank\w*|priority score|overall rank\w*|"
                                       r"optimi[sz]ed rank\w*|module ?2'?s? (rank\w*|order|list))\b", re.I)
MODEL_TABLES = frozenset(MODEL_TABLE_SQL) | {"business_priority"}
# Intent -> the published tables an answer must read.
REQUIRED_TABLES = {"prediction": MODEL_TABLES, "visit_ranking": frozenset({"store_priority", "business_priority"}),
                   "business_ranking": frozenset({"business_priority"})}
FORECAST_UNAVAILABLE = ("There is no forecast for this. The copilot answers from reported history and, for stockouts "
                        "only, from Module 2's published predictions. Ask about a past period (for example \"last 4 "
                        "weeks\") or about predicted stockouts.")
NOT_FROM_PREDICTIONS = ("This question asks for a prediction or the visit ranking, but the generated query read only "
                        "observed history, so no answer is shown: observed stock data is not a prediction. Try asking "
                        "for the predicted stockout probability, the stockout alerts or the visit priority ranking.")
NOT_FROM_BUSINESS_RANKING = ("This question asks for Module 2's business ranking, but the generated query did not read "
                             "it, so no answer is shown. Ask for the business priority ranking.")
HISTORY_SUMMARY = ("These are reported (historical) figures for your stores, shown in the table below. They are not a "
                   "prediction.")
MODEL_STATUS = {"Synthetic_demo": "Synthetic demo predictions (generated test data), not client evidence or an "
                                  "approved production model.",
                "Local_review_candidate": "Model: review candidate, not an approved production model."}


def data_intent(question: str) -> str | None:
    """'business_ranking' | 'visit_ranking' | 'prediction' | 'forecast' (of anything else) | None (history)."""
    if BUSINESS_RANKING_QUESTION.search(question):
        return "business_ranking"
    if VISIT_RANKING.search(question):
        return "visit_ranking"
    if MODEL_TERMS.search(question) or (FUTURE.search(question) and STOCKOUT.search(question)):
        return "prediction"
    if FUTURE.search(question):
        return "forecast"
    return None


def model_disclosure(model: dict) -> str:
    return MODEL_STATUS.get(model.get("publication_kind"),
                            f"Model release status: {model.get('publication_kind') or 'unknown'}; "
                            "not an approved production model.")


def model_notes(model: dict, tables: set, frame, sandbox) -> list:
    """Fixed statements for answers that used Module 2's outputs (never left to the language model)."""
    notes = [model_disclosure(model)]
    if model.get("synthetic") and model.get("publication_kind") != "Synthetic_demo":
        notes.append("These predictions are synthetic test data.")
    notes.append(f"Predictions scored {model['scoring_date']} (run {model['scoring_run_id']}, model "
                 f"{model['model_version']}) for {model['window_start']} to {model['window_end']}. A predicted "
                 "probability is a model estimate, not an observed stockout; a missing prediction is not zero risk.")
    if str(sandbox.as_of) != str(model["scoring_date"]):
        notes.append(f"Business data runs to {sandbox.as_of}, later than this scoring date.")
    if "store_priority" in tables:
        notes.append("Stored priority rank is the published v1 visit ranking renumbered within "
                     f"{sandbox.scope_label}. Risk index is the highest SKU probability in the store, "
                     "not a probability that the store runs out of stock.")
    if "business_priority" in tables:
        business = model["business_ranking"]
        notes.append(f"Business rank and score are Module 2's business-priority ranking ({business['policy_version']}, "
                     f"{business['policy_status'].lower()}), computed over {sandbox.scope_label}. It is a different "
                     "ordering from the stored v1 rank.")
    if frame is not None and len(frame) and frame.isna().to_numpy().any():
        notes.append("Blank values are unknown or not published, not zero.")
    return notes


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
    notes: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    elapsed_s: float = 0.0
    route_method: str = ""


def open_configured_reader(environ=None):
    """Pin one accepted business publication (same reader and checks as the other modules)."""
    from examples.read_shared_data import open_reader
    env = os.environ if environ is None else environ
    if env.get("SHARED_DATA_BACKEND", "duckdb") == "postgres":
        from data_platform.readers import open_configured_reader as shared_reader
        return shared_reader(environ=env)
    if env.get("SHARED_DATA_BACKEND", "duckdb") != "duckdb":
        raise ValueError("Unsupported shared data backend")
    artifact_dir = env.get("MODULE4_SERVING_ARTIFACT_DIR")
    publication = env.get("MODULE4_SERVING_PUBLICATION")
    if not artifact_dir and not publication:
        from shared.business import open_reader as shared_reader
        return shared_reader(env)
    if artifact_dir:
        return open_reader(artifact_dir=Path(artifact_dir))
    return open_reader(publication=Path(publication) if publication else None)


class Copilot:
    def __init__(self, llm=None, reader=None, knowledge=None, data_reason=None, business_ranking=None):
        self.llm = llm or ChatModel()
        self.reader = reader
        # business_ranking: optional Module 2 adapter (shared.runtime); absent when standalone.
        self.pool = SandboxPool(reader, business_ranking=business_ranking) if reader is not None else None
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
        outputs = record.get("model_outputs") or {}
        return {"model": self.llm.name, "model_ready": self.llm.available(),
                "data_ready": self.reader is not None, "data_reason": self.data_reason,
                "as_of": record.get("as_of"), "artifact_id": record.get("artifact_id"),
                "predictions": record.get("model_outputs_status") if self.reader is not None else None,
                "predictions_kind": outputs.get("publication_kind"),
                "predictions_run": outputs.get("scoring_run_id")}

    # ------------------------------------------------------------------ paths
    def _still_authorized(self, principal_id, sandbox) -> bool:
        """Re-check after the answer: same scope fingerprint and the pinned release still serves."""
        try:
            scope = self.reader.principal_scope(principal_id)
            if scope.get("scope_fingerprint") != sandbox.scope_fingerprint:
                return False
            self.reader.read("stores", principal_id, limit=1)  # PostgreSQL: release not withdrawn
            return self.reader.record.get("artifact_id") == sandbox.artifact_id
        except Exception:  # noqa: BLE001 - any failure to re-verify means the answer is not shown
            return False

    def _data_path(self, question, principal_id, out: CopilotAnswer) -> CopilotAnswer:
        if self.pool is None:
            out.status, out.answer = "unavailable", self.data_reason or "No business data is configured."
            return out
        try:
            sandbox = self.pool.get(principal_id)
        except ScopeDenied:
            out.status, out.answer = "denied", "Your account has no data scope configured, so no data can be shown."
            return out
        model = sandbox.model
        provenance = {"source": "shared business publication", "artifact_id": sandbox.artifact_id,
                      "backend": self.reader.record.get("backend", "duckdb"),
                      "release_id": self.reader.record.get("release_id"),
                      "as_of": str(sandbox.as_of), "dataset_hash": sandbox.dataset_hash[:12],
                      "scope": sandbox.scope_label, "metric_version": "shared_kpi.v1, reported data only",
                      "model": self.llm.name}
        intent, unavailable = data_intent(question), None
        if intent == "forecast":
            unavailable = FORECAST_UNAVAILABLE
        elif intent is not None and not model["available"]:
            unavailable = (model["reason"] + " Observed stock history is not used instead. Business data questions "
                           "(sales, stock, shelf, deliveries) still work.")
        elif intent == "business_ranking" and not model["business_ranking"]["available"]:
            unavailable = (model["business_ranking"]["reason"] + " The stored v1 ranking is a different ordering; "
                           "ask for the stored priority ranking to see it.")
        if unavailable is not None:
            # Explicit, without running model-written SQL: observed data must not stand in for predictions.
            if not self._still_authorized(principal_id, sandbox):
                out.status, out.answer = "denied", SCOPE_CHANGED
                return out
            out.status, out.answer = "unavailable", unavailable
            out.provenance = {**provenance, "model_outputs": {k: model[k] for k in
                                                              ("status", "publication_kind", "scoring_run_id")}}
            return out
        required = REQUIRED_TABLES.get(intent)
        result = (self.nlq.answer(question, sandbox, required=required) if required
                  else self.nlq.answer(question, sandbox))
        if not self._still_authorized(principal_id, sandbox):
            # Scope revoked or changed, or the release withdrawn, while answering: fail closed.
            out.status, out.answer = "denied", SCOPE_CHANGED
            return out
        used = referenced_tables(result.sql) & MODEL_TABLES if result.sql else set()
        if required and result.sql and not used & required:
            # Enforced on the executed query, whatever the model wrote: history never answers a prediction.
            out.status = "unavailable"
            out.answer = NOT_FROM_BUSINESS_RANKING if intent == "business_ranking" else NOT_FROM_PREDICTIONS
            out.attempts = result.attempts
            out.provenance = {**provenance, "model_outputs": {k: model[k] for k in
                                                              ("status", "publication_kind", "scoring_run_id")}}
            return out
        out.status, out.answer, out.sql, out.frame = result.status, result.answer, result.sql, result.frame
        out.truncated, out.attempts = result.truncated, result.attempts
        if result.status == "ok" and not used and FUTURE.search(result.answer or "") \
                and (STOCKOUT.search(result.answer) or MODEL_TERMS.search(result.answer)):
            out.answer = HISTORY_SUMMARY             # a summary of history must not read as a prediction
        if used:
            if result.status == "empty":
                out.answer = ("No published predictions matched this question for your stores. "
                              "That is not the same as zero risk.")
            out.notes = model_notes(model, used, result.frame, sandbox)
            provenance["model_outputs"] = {
                "tables": sorted(used), "status": model["status"], "publication_kind": model["publication_kind"],
                "synthetic": model["synthetic"], "model_version": model["model_version"],
                "scoring_run_id": model["scoring_run_id"], "scoring_date": model["scoring_date"],
                "window": [model["window_start"], model["window_end"]],
                "ranking": ("stored v1 rank renumbered within scope" if "store_priority" in used else None),
                "business_ranking_policy": (model["business_ranking"].get("policy_version")
                                            if "business_priority" in used else None)}
        if result.status == "ok":
            out.chart = auto_chart(result.frame)
        out.provenance = {**provenance, "rows": None if result.frame is None else len(result.frame),
                          "rows_capped": result.truncated}
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
