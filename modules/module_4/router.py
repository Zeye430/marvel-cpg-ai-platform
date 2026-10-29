"""
modules/module_4/router.py
==========================
Decides whether a question needs the business data (SQL), the knowledge base (RAG)
or is out of scope. Clear keyword evidence decides on its own; otherwise the model
classifies the question. The route never changes who the user is or what they can see.
"""

from __future__ import annotations

import json
import re

DATA = "data"
KNOWLEDGE = "knowledge"
OTHER = "other"

DATA_PATTERNS = [
    r"\bwhich (stores?|skus?|products?|regions?|channels?|cities|states?|categor(y|ies))\b",
    r"\b(top|bottom) \d+\b", r"\bhow many\b", r"\b(average|avg|mean|median|total|sum|count|number of)\b",
    r"\b(rate|ratio|percentage|share|trend|compare|comparison|growth|decline|increase|decrease)\b",
    r"\b(sell[- ]?in|sell[- ]?out|units?|revenue|net sales|gross sales|invoic\w*|volume)\b",
    r"\b(oos|out[- ]of[- ]stock|stock[- ]?outs?)\b.*\b(rate|highest|lowest|most|least|by|which|top)\b",
    r"\b(this|last|past|previous) (week|month|quarter|year|\d+ (days|weeks|months))\b", r"\bytd\b",
    r"\b(in|for) (the )?(northeast|southeast|midwest|west|modern trade|traditional trade|distributors?)\b",
    r"\b(shelf share|planogram compliance|msl compliance|fill rate|deliver(y|ies)|delay\w*|audits?)\b",
    r"\bby (store|sku|product|region|channel|category|week|month|tier)\b", r"\b(highest|lowest|best|worst)\b",
]
KNOWLEDGE_PATTERNS = [
    r"\b(argument|arguments|pitch|convince|persuade|objection|refus\w*|push ?back|reluctant|hesitant)\b",
    r"\b(substitut\w*|replace|replacement|alternative|instead of|swap)\b",
    r"\b(policy|policies|guideline|rule|rules|guardrail|approval|approve|escalat\w*|override|allowed|permitted)\b",
    r"\b(what is|what's|tell me about|describe|explain)\b.*\b(product|sku|marvel \w+)\b",
    r"\b(benefit|benefits|claim|claims|feature|features|ingredients?|safety|safe|usage|how to use|dilut\w*)\b",
    r"\b(what (should|do|can) i (say|tell|offer|do)|how (should|do|can) i)\b",
    r"\b(definition|defined|define|calculated|formula|meaning|means)\b",
    r"\b(degreaser|launch|new line)\b", r"\b(standard|standards|sop|procedure|checklist)\b",
]
# Phrases that only make sense as a document question; each counts double.
KNOWLEDGE_STRONG = [
    r"\b(substitut\w*|replacement|instead of)\b", r"\b(objection|refus\w*|push ?back|convince|persuade)\b",
    r"\b(argument|arguments|pitch)\b", r"\b(policy|policies|guardrail|approval|escalat\w*|override)\b",
    r"\b(safety|safe|mix\w*|dilut\w*|how to use)\b",
]
OTHER_PATTERNS = [r"^\s*(hi|hello|hey|thanks?|thank you)\b[\s!.]*$",
                  r"\b(weather|joke|poem|recipe|football|movie|stock price|bitcoin)\b"]

ROUTER_PROMPT = """Classify a question for Marvel CPG's commercial copilot. Reply with JSON only: {"route": "..."}.
- "data": needs numbers computed from sales, stock, out-of-stock, shelf audit, delivery, invoice or promotion records
  (rates, totals, rankings, trends, comparisons for stores, products, regions, channels, periods).
- "knowledge": needs Marvel's documents: product facts, substitutes, selling arguments, objection handling,
  pricing/discount policy, MSL and execution standards, promotion rules, KPI definitions, deal approvals, safety.
- "other": unrelated to Marvel CPG's commercial work."""


def _score(patterns, text):
    return sum(1 for p in patterns if re.search(p, text))


def keyword_route(question: str) -> tuple[str | None, dict]:
    text = question.lower()
    scores = {DATA: _score(DATA_PATTERNS, text),
              KNOWLEDGE: _score(KNOWLEDGE_PATTERNS, text) + _score(KNOWLEDGE_STRONG, text),
              OTHER: _score(OTHER_PATTERNS, text)}
    if scores[OTHER] and not scores[DATA] and not scores[KNOWLEDGE]:
        return OTHER, scores
    strong = _score(KNOWLEDGE_STRONG, text)
    if strong and scores[DATA] <= strong:  # e.g. "Which SKU can I substitute for ...?"
        return KNOWLEDGE, scores
    if scores[DATA] >= scores[KNOWLEDGE] + 2:
        return DATA, scores
    if scores[KNOWLEDGE] >= scores[DATA] + 2:
        return KNOWLEDGE, scores
    return None, scores


def route(question: str, llm=None) -> dict:
    decided, scores = keyword_route(question)
    if decided is not None or llm is None:
        if decided is None:  # no model: lean on the stronger signal, data on a tie
            decided = KNOWLEDGE if scores[KNOWLEDGE] > scores[DATA] else DATA
        return {"route": decided, "method": "keywords", "scores": scores}
    reply = llm.chat([{"role": "system", "content": ROUTER_PROMPT},
                      {"role": "user", "content": question}], json_mode=True, max_tokens=20)
    try:
        decided = json.loads(reply).get("route")
    except (json.JSONDecodeError, AttributeError):
        decided = None
    if decided not in (DATA, KNOWLEDGE, OTHER):
        decided = KNOWLEDGE if scores[KNOWLEDGE] > scores[DATA] else DATA
    return {"route": decided, "method": "model", "scores": scores}
