"""Deterministic OpenAI-compatible model stub for browser acceptance (loopback only).

Usage: python tests/browser/llm_stub.py PORT

Answers by prompt type so the copilot runs end to end without a real model:
routing -> data, Text-to-SQL -> a fixed read-only query over the scoped
sandbox (chosen by a phrase for prediction/ranking questions), summary -> a fixed sentence, knowledge -> a sentence citing [S1].
A question containing SLOWANSWER is delayed so a test can sign out mid-answer.
Nothing here is a model result; it only exercises the application plumbing.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SQL = ("SELECT channel, COUNT(DISTINCT store_id) AS stores, SUM(units_sold) AS reported_units "
       "FROM sku_daily GROUP BY channel ORDER BY channel")
# Prediction questions (by phrase) read Module 2's published tables when the sandbox has them.
PREDICTION_SQL = {
    "stockout alert": "SELECT store_id, store_name, sku_id, product_name, predicted_oos_probability, alert_threshold "
                      "FROM sku_risk WHERE is_alert ORDER BY predicted_oos_probability DESC",
    "business priority": "SELECT business_rank, store_id, store_name, ROUND(priority_score, 1) AS priority_score, "
                         "stored_rank_in_scope FROM business_priority ORDER BY business_rank NULLS LAST, store_id",
    "restock first": "SELECT sku_id, product_name, is_msl, predicted_oos_probability, is_alert FROM sku_risk "
                     "ORDER BY is_alert DESC, predicted_oos_probability DESC LIMIT 5",
    "visit priority": "SELECT stored_rank_in_scope, store_id, store_name, risk_index, "
                      "alert_sku_count FROM store_priority ORDER BY stored_rank_in_scope",
}


def reply_for(messages):
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")
    if system.startswith("Classify a question"):
        return '{"route": "data"}'
    if "SLOWANSWER" in user:
        time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 6)
    if system.startswith("You are Marvel CPG's commercial copilot. Explain a database result"):
        return "STUB ANSWER: a fixed summary of the result table for your stores."
    if user.startswith("Sources:"):                     # knowledge answer over retrieved passages
        return "STUB KNOWLEDGE ANSWER based on the retrieved passage [S1]."
    if "Question:" in user and "SQL" in system:
        question = user.lower()
        sql = next((q for phrase, q in PREDICTION_SQL.items() if phrase in question), SQL)
        return "```sql\n" + sql + "\n```"
    return "STUB KNOWLEDGE ANSWER based on the retrieved passage [S1]."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send({"data": [{"id": "stub"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self._send({"choices": [{"message": {"role": "assistant", "content": reply_for(request["messages"])}}]})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
