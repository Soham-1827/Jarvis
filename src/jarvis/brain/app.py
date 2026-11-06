"""FastAPI application factory for the Jarvis brain."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from jarvis import __version__
from jarvis.brain.api.limits import ApiLimits
from jarvis.brain.api.ws import router as ws_router
from jarvis.brain.services import Services, build_services
from jarvis.shared.config import Settings


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

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok", version=__version__, uptime_s=time.monotonic() - started)

    return app
