"""Model aliases an agent can run on.

An agent's `model` is a logical alias, not a provider model name: the LiteLLM
gateway decides what an alias maps to (`deploy/litellm/config.yaml`). The gateway
is therefore the source of truth whenever it answers, and the alias list in
settings is only a fallback for a deployment that has not stood one up yet.
"""

from __future__ import annotations

import logging

import httpx
from agentforge_shared.config import get_settings
from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["agents"])

log = logging.getLogger(__name__)

#: A gateway hop is local. If it does not answer promptly, treat it as absent so
#: the model picker still renders instead of hanging the dashboard.
GATEWAY_TIMEOUT_SECONDS = 3.0


def _gateway_aliases(base_url: str) -> list[str]:
    """The aliases the gateway advertises, or [] when it cannot be reached."""
    url = base_url.rstrip("/") + "/v1/models"
    try:
        response = httpx.get(url, timeout=GATEWAY_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any failure means "no gateway"
        log.debug("no model list from %s: %s", url, exc)
        return []
    return [str(entry["id"]) for entry in payload.get("data", []) if entry.get("id")]


@router.get("")
def list_models() -> dict:
    """Aliases the dashboard offers, plus the one a new agent gets by default."""
    settings = get_settings()
    aliases = _gateway_aliases(settings.llm_gateway_url)
    if aliases:
        return {
            "models": aliases,
            "source": "gateway",
            "default": settings.default_agent_model,
        }
    return {
        "models": settings.model_aliases,
        "source": "config",
        "default": settings.default_agent_model,
    }
