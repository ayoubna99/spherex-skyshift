"""Entry point for cPanel "Setup Python App" (Phusion Passenger / LiteSpeed, which speak WSGI).

FastAPI is ASGI, so a2wsgi wraps it. In cPanel: startup file passenger_wsgi.py, entry point
application. Works whether the app is mounted at a sub-path (e.g. /skyshift) or at the root.
If the app cannot start, every URL answers with the reason (no secrets in it) so it can be fixed
without digging through server logs.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))

try:
    from a2wsgi import ASGIMiddleware
    from backend.app import app as asgi_app
    _app = ASGIMiddleware(asgi_app)
    _startup_error = None
except Exception:
    _app = None
    _startup_error = "SkyShift could not start (Python %s):\n\n%s" % (sys.version.split()[0], traceback.format_exc())


def application(environ, start_response):
    if _app is None:
        start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8")])
        return [_startup_error.encode()]
    # some servers pass the full URL path in PATH_INFO; keep only the part from /api or /img on
    path = environ.get("PATH_INFO", "")
    for marker in ("/api/", "/img/"):
        i = path.find(marker)
        if i > 0:
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + path[:i]
            environ["PATH_INFO"] = path[i:]
            break
    return _app(environ, start_response)
