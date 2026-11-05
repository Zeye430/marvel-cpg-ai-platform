"""Integrated local app: Stockout Risk (Module 2) and Commercial Copilot (Module 4).

Run from the repository root (see docs/integrated-app.md for the settings):

    SHARED_DATA_PUBLICATION=<serving.json> shiny run integrated_app.py --port 8000

Both modules read ONE pinned shared business publication through shared.runtime:
a verified local DuckDB publication (default) or, with SHARED_DATA_BACKEND=postgres,
the shared PostgreSQL release. Nothing is opened at import time; a failure is shown
as unavailable, never replaced by another source. Sign-in is demo-only.

Modules 1 and 3 are not part of this build: their code is not imported and the
legacy CSV loader (shared.data) is never used. The full four-module app can host
them later through the same shell (shared.shell.build_app) once they read the
shared publication; the sidebar already lists them (Business Performance, Negotiation Simulator)
as not available in this build.
"""

from modules.module_2 import module_2_server, module_2_ui
from modules.module_2.ui import pages_nav_ui as module_2_pages
from modules.module_4 import module_4_server, module_4_ui
from shared.runtime import SharedRuntime
from shared.shell import MODULE_NAMES, ModuleSpec, build_app

MODULES = (
    ModuleSpec("module_2", MODULE_NAMES["module_2"], "Module 2", module_2_ui, module_2_server, module_2_pages),
    ModuleSpec("module_4", MODULE_NAMES["module_4"], "Module 4", module_4_ui, module_4_server),
)

RUNTIME = SharedRuntime()
app = build_app(MODULES, RUNTIME, title="Marvel CPG · Commercial workspace (demo)",
                host_options={"copilot_nav": True})
