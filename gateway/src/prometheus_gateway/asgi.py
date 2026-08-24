"""ASGI entrypoint for production servers.

Usage: uvicorn prometheus_gateway.asgi:app

This module exists so that `main.py` (create_app factory) can be imported
in tests without triggering Settings() validation against env vars.
"""

from prometheus_gateway.main import create_app

app = create_app()
