"""FastAPI application factory for the Jarvis brain."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis import __version__
from jarvis.brain.api.limits import ApiLimits
from jarvis.brain.api.ws import router as ws_router
from jarvis.brain.services import Services, build_services
from jarvis.shared.config import Settings

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

# Strict, same-origin only. CSSOM writes from app.js (gauge offsets) are allowed without
# 'unsafe-inline'; inline <script>/<style> and style attributes are not.
CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    uptime_s: float


def create_app(settings: Settings, services: Services | None = None) -> FastAPI:
    """Build the brain app. Interactive docs are off: the API is private, not a public surface.

    Pass `services` in tests; otherwise they are built from settings at startup.
    """
    # The data dir holds the DB and personal memory: owner-only.
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        app.state.services = services or build_services(settings)
        try:
            yield
        finally:
            if owned:
                app.state.services.close()

    app = FastAPI(
        title="Jarvis brain", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.services = services
    app.state.limits = ApiLimits()
    app.include_router(ws_router)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path == "/sw.js":
            response.headers["Cache-Control"] = "no-cache"
        if request.url.path.endswith(".webmanifest"):
            response.headers["Content-Type"] = "application/manifest+json"
        return response

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok", version=__version__, uptime_s=time.monotonic() - started)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
