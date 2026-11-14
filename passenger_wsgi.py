"""Entry point for cPanel "Setup Python App" (Phusion Passenger / LiteSpeed, which speak WSGI).

FastAPI is ASGI, so a2wsgi wraps it. In cPanel: startup file passenger_wsgi.py, entry point
application. Works whether the app is mounted at a sub-path (e.g. /skyshift) or at the root.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware  # noqa: E402

from backend.app import app as asgi_app  # noqa: E402

_app = ASGIMiddleware(asgi_app)


def application(environ, start_response):
    # some servers pass the full URL path in PATH_INFO; keep only the part from /api or /img on
    path = environ.get("PATH_INFO", "")
    for marker in ("/api/", "/img/"):
        i = path.find(marker)
        if i > 0:
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + path[:i]
            environ["PATH_INFO"] = path[i:]
            break
    return _app(environ, start_response)
