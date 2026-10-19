"""
modules/module_3/__init__.py
============================
Module 3: Commercial Negotiation Simulator
Scenario modeling for trade discount limits, margin floors, and execution trade-offs.
"""

from .ui import module_3_ui
from .server import module_3_server

__all__ = ["module_3_ui", "module_3_server"]
