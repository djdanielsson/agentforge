"""The fleet control-plane API.

One process serves both the JSON API and the single-page UI, so the browser and
an external automation client (Hermes) talk to exactly the same endpoints on one
origin — no CORS, and no private API for the UI (SPEC §22, §26).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fleet_core.config import get_settings
from fleet_core.db import init_db
from fleet_core.events import bus
from fleet_core.workspaces.base import ProviderError

from .routers import agents, events, llm, projects, system

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fleet")

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    init_db()
    bus.bind_loop(asyncio.get_running_loop())
    log.info(
        "fleet control plane up: namespace=%s workspace_provider=%s gateway=%s",
        settings.namespace,
        settings.workspace_provider,
        settings.llm_gateway_url or "(unset)",
    )
    yield


app = FastAPI(
    title="Fleet Control Plane",
    version="0.1.0",
    summary="A control plane for a fleet of AI coding agents",
    lifespan=lifespan,
)


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    log.warning("provider error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=502, content={"detail": {"error": str(exc), **(exc.detail or {})}}
    )


for router in (system.router, projects.router, agents.router, events.router, llm.router):
    app.include_router(router)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC / "favicon.svg")


def main() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    uvicorn.run("fleet_api.main:app", host="0.0.0.0", port=8000, log_level="info")
