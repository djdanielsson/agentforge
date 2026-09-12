"""The set of model aliases an agent may be set to.

An agent's `model` is a logical alias, and the LiteLLM gateway owns the list —
so a free-text field would let a typo sit in the database until a task fails at
dispatch time. Everything that writes a model asks here first, which is also what
lets the dashboard render a picker instead of a text box.

The gateway is authoritative whenever it answers; `AGENTFORGE_AGENT_MODEL_ALIASES`
covers a deployment that has not stood one up yet. The answer is cached briefly
because agent writes are frequent and the gateway is a network hop.
"""

from __future__ import annotations

import logging
import time

import httpx
from agentforge_shared.config import get_settings
from fastapi import HTTPException, status

log = logging.getLogger(__name__)

#: A gateway hop is local. If it does not answer promptly, treat it as absent so
#: the picker still renders instead of hanging the dashboard.
GATEWAY_TIMEOUT_SECONDS = 3.0

#: How long a fetched list is trusted. Long enough to keep a burst of writes from
#: hammering the gateway, short enough that an alias added upstream appears while
#: someone is still in the dashboard.
CACHE_TTL_SECONDS = 30.0

_cache: tuple[float, list[str], str] | None = None


def _gateway_aliases(base_url: str, api_key: str | None = None) -> list[str]:
    """The aliases the gateway advertises, or [] when it cannot be reached."""
    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    try:
        response = httpx.get(url, timeout=GATEWAY_TIMEOUT_SECONDS, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any failure means "no gateway"
        log.debug("no model list from %s: %s", url, exc)
        return []
    return [str(entry["id"]) for entry in payload.get("data", []) if entry.get("id")]


def models_and_source(*, refresh: bool = False) -> tuple[list[str], str]:
    """The valid aliases, and whether they came from the gateway or from config."""
    global _cache

    now = time.monotonic()
    if _cache is not None and not refresh and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1], _cache[2]

    settings = get_settings()
    aliases = _gateway_aliases(settings.llm_gateway_url, settings.llm_gateway_api_key)
    source = "gateway" if aliases else "config"
    models = aliases or settings.model_aliases
    _cache = (now, models, source)
    return models, source


def validate_model(model: str | None) -> None:
    """Refuse an alias the gateway does not serve.

    Called from every path that writes a model, so an unknown alias fails at the
    point someone typed it rather than when an agent tries to use it.
    """
    if not model:
        return

    models, source = models_and_source()
    if model not in models:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": f"unknown model alias {model!r}",
                "source": source,
                "models": models,
            },
        )
