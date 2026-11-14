"""Entry point for cPanel "Setup Python App" (Phusion Passenger, which speaks WSGI).

FastAPI is ASGI, so a2wsgi wraps it. Application startup file: passenger_wsgi.py,
entry point: application.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware  # noqa: E402

from backend.app import app as asgi_app  # noqa: E402

application = ASGIMiddleware(asgi_app)
