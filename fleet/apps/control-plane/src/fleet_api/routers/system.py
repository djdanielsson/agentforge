"""System routes: health, providers, models, usage totals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fleet_core import service
from fleet_core.config import get_settings
from fleet_core.llm import get_gateway, usage_totals

from ..security import require_token

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    gateway = get_gateway()
    return {
        "status": "ok",
        "namespace": settings.namespace,
        "configured_workspace_provider": settings.workspace_provider,
        "llm_gateway_configured": gateway.configured,
        "llm_gateway_url": settings.llm_gateway_url,
        "authenticated": bool(settings.api_token),
    }


@router.get("/providers")
def providers(_: str = Depends(require_token)) -> dict[str, Any]:
    """What each provider can do, and which one is actually in use.

    A substitution shows up here rather than being invisible.
    """
    return service.provider_report()


@router.get("/models")
def models(_: str = Depends(require_token)) -> dict[str, Any]:
    """The logical model names the gateway resolves (SPEC §12).

    `source: gateway` means the gateway answered; `source: config` means we are
    reporting a configured alias because the gateway could not be reached, which
    is a diagnosis rather than a failure.
    """
    gateway = get_gateway()
    settings = get_settings()
    if gateway.configured:
        try:
            return {"source": "gateway", "models": gateway.models()}
        except Exception as exc:  # noqa: BLE001
            return {
                "source": "config",
                "models": [settings.llm_default_model],
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {"source": "config", "models": [settings.llm_default_model]}


@router.get("/usage")
def usage(_: str = Depends(require_token)) -> dict[str, Any]:
    return usage_totals()
