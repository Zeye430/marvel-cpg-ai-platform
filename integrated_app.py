"""Integrated local app: Business Performance (Module 1), Stockout Risk (Module 2), Negotiation
Simulator (Module 3) and Commercial Copilot (Module 4).

Run from the repository root (see docs/integrated-app.md for the settings):

    SHARED_DATA_PUBLICATION=<serving.json> shiny run integrated_app.py --port 8000

All four modules read ONE pinned shared business publication through shared.runtime:
a verified local DuckDB publication (default) or, with SHARED_DATA_BACKEND=postgres,
the shared PostgreSQL release. Nothing is opened at import time; a failure is shown
as unavailable, never replaced by another source. Sign-in is demo-only. Business
Performance opens after sign-in (a welcome greeting while its scoped data loads); Stockout
Risk, the Negotiation Simulator and the copilot are one click away in the sidebar. The
Negotiation Simulator reads the same per-principal scoped copy as Business Performance. The
legacy CSV loader (shared.data) is never used.
"""

from modules.module_1 import module_1_server, module_1_ui
from modules.module_1.ui import pages_nav_ui as module_1_pages
from modules.module_2 import module_2_server, module_2_ui
from modules.module_2.ui import pages_nav_ui as module_2_pages
from modules.module_3 import module_3_server, module_3_ui
from modules.module_3.ui import pages_nav_ui as module_3_pages
from modules.module_4 import module_4_server, module_4_ui
from shared.runtime import SharedRuntime
from shared.shell import MODULE_NAMES, ModuleSpec, build_app

# The first spec opens after sign-in; the sidebar lists modules in catalogue order (1, 2, 3, 4).
MODULES = (
    ModuleSpec("module_1", MODULE_NAMES["module_1"], "Module 1", module_1_ui, module_1_server, module_1_pages),
    ModuleSpec("module_2", MODULE_NAMES["module_2"], "Module 2", module_2_ui, module_2_server, module_2_pages),
    ModuleSpec("module_3", MODULE_NAMES["module_3"], "Module 3", module_3_ui, module_3_server, module_3_pages),
    ModuleSpec("module_4", MODULE_NAMES["module_4"], "Module 4", module_4_ui, module_4_server),
)

RUNTIME = SharedRuntime()
app = build_app(MODULES, RUNTIME, title="Marvel CPG · Commercial workspace (demo)",
                host_options={"copilot_nav": True, "stockout_nav": True})
