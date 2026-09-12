"""AgentForge control-plane API.

Everything the platform can do is exposed here. The web UI, the CLI and Hermes
are all just clients of this API — none of them has a private path in.

    GET  /health                 unversioned, for liveness probes
    GET  /api/v1/...             the control plane
    WS   /api/v1/projects/{id}/events

Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from agentforge_shared import __version__
from agentforge_shared.config import get_settings
from agentforge_shared.db import SessionLocal, init_db
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (
    actions,
    agents,
    git,
    health,
    keys,
    projects,
    secrets,
    stream,
    tasks,
    webhooks,
)
from .security import ensure_bootstrap_key

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    with SessionLocal() as session:
        ensure_bootstrap_key(session)
    if not settings.auth_enabled:
        log.warning(
            "AGENTFORGE_AUTH_ENABLED is false: the API is open. "
            "Set it true before exposing this beyond localhost."
        )
    log.info("agentforge api %s up (env=%s)", __version__, settings.environment)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AgentForge API",
        description=(
            "Control plane for autonomous coding agents in isolated workspaces.\n\n"
            "Projects, tasks, agents, permissions, git and events — the same surface "
            "for the dashboard, the CLI and any external orchestrator."
        ),
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    v1 = APIRouter(prefix=API_PREFIX)
    for module in (
        health,
        projects,
        agents,
        tasks,
        actions,
        git,
        keys,
        secrets,
        webhooks,
        stream,
    ):
        v1.include_router(module.router)
    app.include_router(v1)

    @app.get("/health", tags=["meta"], include_in_schema=False)
    def liveness() -> dict:
        """Unversioned so a probe never breaks when the API version moves."""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
