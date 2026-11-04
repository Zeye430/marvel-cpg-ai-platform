"""TEST DOUBLE for browser acceptance only: the integrated app over the SYNTHETIC publication with
its in-memory release record relabelled as a review candidate, so the candidate disclosure can be
checked in the browser. Nothing on disk changes; the governed publisher only allows the
``Synthetic_demo`` kind for synthetic scoring, so a real candidate fixture cannot be built here.

Run (repository root): SHARED_DATA_ARTIFACT_DIR=<synthetic model artifact> \
    shiny run tests/browser/candidate_disclosure_app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrated_app import MODULES  # noqa: E402
from shared.business import open_reader  # noqa: E402
from shared.runtime import SharedRuntime  # noqa: E402
from shared.shell import build_app  # noqa: E402


def candidate_reader(environ):
    reader = open_reader(environ)
    outputs = {**(reader.record.get("model_outputs") or {}), "publication_kind": "Local_review_candidate",
               "synthetic": False}
    reader.record = {**reader.record, "model_outputs": outputs}
    return reader


app = build_app(MODULES, SharedRuntime(opener=candidate_reader), host_options={"copilot_nav": True},
                title="TEST DOUBLE · candidate disclosure")
