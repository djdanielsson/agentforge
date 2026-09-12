"""Liveness / readiness and a small cluster-of-facts endpoint."""

from __future__ import annotations

from aiw_shared import __version__
from aiw_shared.config import get_settings
from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
    }


@router.get("/version")
def version() -> dict:
    return {"name": "ai-workbench", "version": __version__}
