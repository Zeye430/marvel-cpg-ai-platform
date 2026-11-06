"""
Module 1: Business Performance (Commercial Execution Analytics Dashboard)
The five client KPIs and interactive charts across channel, region, SKU and user role, read
from the shared account-scoped business publication (modules/module_1/data.py).
"""

from .ui import module_1_ui
from .server import module_1_server

__all__ = ["module_1_ui", "module_1_server"]
