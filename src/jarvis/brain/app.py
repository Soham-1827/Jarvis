"""FastAPI application factory for the Jarvis brain."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from jarvis import __version__
from jarvis.shared.config import Settings


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    uptime_s: float


def create_app(settings: Settings) -> FastAPI:
    """Build the brain app. Interactive docs are off: the API is private, not a public surface."""
    # The data dir holds the DB and personal memory: owner-only.
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    app = FastAPI(title="Jarvis brain", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok", version=__version__, uptime_s=time.monotonic() - started)

    return app
