"""The set of model aliases an agent may run on, and what they actually are.

An agent stores a logical alias, and the LiteLLM gateway owns the list — so a
free-text field would let a typo sit in the database until a task failed at
dispatch time. Everything that writes a model asks here first.

The same catalog answers a second question the dashboard asks: an alias like
`smart` says nothing about what will run, so the gateway is asked for the
upstream model and its reasoning setting, and a label is built from that. The
gateway is authoritative whenever it answers;
`AGENTFORGE_AGENT_MODEL_ALIASES` covers a deployment that has not stood one up
yet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

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

_cache: tuple[float, list[dict[str, Any]], str] | None = None


def _display_model(upstream: str) -> str:
    """`anthropic/claude-sonnet-4-5` reads as `claude-sonnet-4-5` to a person."""
    return upstream.split("/", 1)[-1] if "/" in upstream else upstream


def _label(model: str, *, reasoning: str | None, local: bool) -> str:
    """What the picker shows: the model, and how hard it is asked to think."""
    parts = [model]
    if reasoning:
        parts.append(f"thinking: {reasoning}")
    elif local:
        parts.append("local")
    return " · ".join(parts)


def _describe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    described: list[dict[str, Any]] = []
    for entry in entries:
        name = str(entry.get("model_name") or entry.get("name") or "")
        if not name:
            continue
        params = entry.get("litellm_params") or {}
        upstream = str(params.get("model") or name)
        model = _display_model(upstream)
        reasoning = params.get("reasoning_effort")
        local = bool(params.get("api_base"))
        described.append(
            {
                "name": name,
                "model": model,
                "reasoning": reasoning,
                "local": local,
                "label": _label(model, reasoning=reasoning, local=local),
            }
        )
    return described


def _gateway_models(base_url: str, api_key: str | None) -> list[dict[str, Any]]:
    """What the gateway serves, or [] when it cannot be reached.

    `/model/info` is the useful one — it carries each alias's upstream model and
    reasoning setting, which is what makes an honest label possible. `/v1/models`
    is the fallback for a gateway that only offers ids.
    """
    root = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

    try:
        response = httpx.get(f"{root}/model/info", timeout=GATEWAY_TIMEOUT_SECONDS, headers=headers)
        if response.status_code < 400:
            payload = response.json()
            entries = payload.get("data", payload if isinstance(payload, list) else [])
            described = _describe(entries if isinstance(entries, list) else [])
            if described:
                return described
    except Exception as exc:  # noqa: BLE001 - fall through to the plain list
        log.debug("no model detail from %s: %s", root, exc)

    try:
        response = httpx.get(f"{root}/v1/models", timeout=GATEWAY_TIMEOUT_SECONDS, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any failure means "no gateway"
        log.debug("no model list from %s: %s", root, exc)
        return []
    return _describe(
        [{"model_name": entry["id"]} for entry in payload.get("data", []) if entry.get("id")]
    )


def models_and_source(*, refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    """The valid aliases with their detail, and where the list came from."""
    global _cache

    now = time.monotonic()
    if _cache is not None and not refresh and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1], _cache[2]

    settings = get_settings()
    models = _gateway_models(settings.llm_gateway_url, settings.llm_gateway_api_key)
    source = "gateway"
    if not models:
        source = "config"
        models = [
            {
                "name": alias,
                "model": alias,
                "reasoning": None,
                "local": False,
                "label": alias,
            }
            for alias in settings.model_aliases
        ]
    _cache = (now, models, source)
    return models, source


def model_names() -> list[str]:
    models, _ = models_and_source()
    return [model["name"] for model in models]


def validate_model(model: str | None) -> None:
    """Refuse an alias the gateway does not serve.

    Called from every path that writes a model, so an unknown alias fails at the
    point someone typed it rather than when an agent tries to use it.
    """
    if not model:
        return

    names = model_names()
    if model not in names:
        _, source = models_and_source()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": f"unknown model alias {model!r}", "source": source, "models": names},
        )
