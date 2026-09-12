"""AI Workbench control-plane API.

Run:  uvicorn aiw_api.main:app --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from aiw_shared import __version__
from aiw_shared.config import get_settings
from aiw_shared.db import init_db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import agents, git, health, projects, stream, tasks

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    log.info("ai-workbench api %s up (env=%s)", __version__, settings.environment)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Workbench API",
        description="Control plane for autonomous coding agents in isolated workspaces.",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (health, projects, agents, tasks, git, stream):
        app.include_router(module.router)

    return app


app = create_app()
