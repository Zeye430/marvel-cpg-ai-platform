"""
modules/module_2/__init__.py
============================
Module 2: Predictive Out-of-Stock Risk Engine
Supervised Classification / Time-series ML model predicting SKU OOS probability at t+7 days
and prioritizing store visit routes.
"""

__all__ = ["module_2_ui", "module_2_server"]


def __getattr__(name):
    # Offline data/model jobs must not load the Shiny application on import.
    if name == "module_2_ui":
        from .ui import module_2_ui
        return module_2_ui
    if name == "module_2_server":
        from .server import module_2_server
        return module_2_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
