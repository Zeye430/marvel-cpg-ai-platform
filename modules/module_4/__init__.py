"""
modules/module_4/__init__.py
============================
Module 4: Commercial Copilot
Conversational assistant providing Text-to-SQL querying and RAG document assistance.
"""

from .ui import module_4_ui
from .server import module_4_server

__all__ = ["module_4_ui", "module_4_server"]
