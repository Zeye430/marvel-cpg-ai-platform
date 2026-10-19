"""
modules/module_2/__init__.py
============================
Module 2: Predictive Out-of-Stock Risk Engine
Supervised Classification / Time-series ML model predicting SKU OOS probability at t+7 days
and prioritizing store visit routes.
"""

from .ui import module_2_ui
from .server import module_2_server

__all__ = ["module_2_ui", "module_2_server"]
