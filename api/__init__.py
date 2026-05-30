"""HTTP route modules for the XBRL Agent server.

Each module defines a FastAPI ``APIRouter`` whose handlers reference shared
state, helpers, and models through the ``server`` module (``server.X``) at
call time. This keeps the long-standing test monkeypatch surface
(``patch("server._create_proxy_model")``, ``setattr(server, "OUTPUT_DIR", ...)``)
working: tests patch ``server.X`` and the live handler reads ``server.X``.

``server.py`` imports each router at the bottom of the module (after every
shared symbol is defined) and calls ``app.include_router(...)``. The routers
``import server`` at their top, but only *read* ``server.X`` inside handler
bodies — never at import time — so the partial-initialization during that
bottom import is harmless.
"""
