"""
modules/module_3/__init__.py
============================
Module 3: Negotiation Simulator (brief Scenarios A and B), hosted in the integrated workspace:
a deal simulator that checks three proposals against the account's commercial guardrail, and a
price-change simulator from an elasticity estimated on the user's stores.
"""

from .ui import module_3_ui
from .server import module_3_server

__all__ = ["module_3_ui", "module_3_server"]
