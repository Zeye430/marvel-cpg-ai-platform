"""
module_1/app.py
===============
Backwards-compatible entrypoint for Module 1.
Forwards to the unified multi-module platform application in root app.py.
"""

import sys
from pathlib import Path

# Ensure root directory is on python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import app, app_ui, server

__all__ = ["app", "app_ui", "server"]

if __name__ == "__main__":
    from shiny import run_app
    run_app(app)
