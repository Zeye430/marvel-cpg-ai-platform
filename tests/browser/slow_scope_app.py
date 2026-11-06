"""TEST DOUBLE for browser acceptance only: the integrated app over the SYNTHETIC publication with every
account-scope lookup (``principal_scope``) delayed, as a slow database connection would. Used to show that
Business Performance's welcome appears at once and that a late scope result for a previous sign-in is
discarded. Nothing on disk changes.

Stockout Risk is left out of this test app on purpose: its service reads the account scope on the session's
main thread at sign-in (to fill its filter choices), which would hold up every module under a slow
connection. That is a separate, known limit of Module 2; this double isolates Business Performance's own
loading path.

Run (repository root): SHARED_DATA_ARTIFACT_DIR=<synthetic model artifact> SLOW_SCOPE_SECONDS=4 \
    shiny run tests/browser/slow_scope_app.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrated_app import MODULES  # noqa: E402
from shared.business import open_reader  # noqa: E402
from shared.runtime import SharedRuntime  # noqa: E402
from shared.shell import build_app  # noqa: E402

DELAY = float(os.environ.get("SLOW_SCOPE_SECONDS", "4"))


def slow_reader(environ):
    reader = open_reader(environ)
    lookup = reader.principal_scope

    def delayed(principal_id):
        time.sleep(DELAY)
        return lookup(principal_id)
    reader.principal_scope = delayed
    return reader


app = build_app([s for s in MODULES if s.key != "module_2"], SharedRuntime(opener=slow_reader),
                host_options={"copilot_nav": True},
                title="TEST DOUBLE · slow scope lookups")
