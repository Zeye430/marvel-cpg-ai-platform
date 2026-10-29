"""
modules/module_4/__init__.py
============================
Module 4: Commercial Copilot
Conversational assistant: Text-to-SQL over the signed-in user's scoped business data
and cited answers from the authorized knowledge base (RAG). See README.md.
"""

from .ui import module_4_ui
from .server import module_4_server

__all__ = ["module_4_ui", "module_4_server"]
