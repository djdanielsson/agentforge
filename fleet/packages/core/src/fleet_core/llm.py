"""The LLM gateway boundary (SPEC §11, §12, §13, §14).

Two rules shape this module:

1. Agents never hold model-provider credentials. They hold a *project-scoped
   token for the fleet gateway*, and the gateway holds the real keys.
2. Every call the platform makes on behalf of a project is attributed to that
   project, agent and task, so usage is a query rather than a guess.

The gateway client is provider-neutral: it speaks the OpenAI-compatible shape,
which is what LiteLLM exposes and what every other candidate gateway does too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, select

from .config import Settings, get_settings
from .db import session_scope
from .models import UsageRecord

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class GatewayUnavailable(RuntimeError):
    pass


class LLMGateway:
    """An OpenAI-compatible gateway client that records what it spends."""

    name = "gateway"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm_gateway_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_gateway_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_gateway_key}"
        return headers

    def _url(self, path: str) -> str:
        return self.settings.llm_gateway_url.rstrip("/") + path

    def models(self) -> list[str]:
        if not self.configured:
            return []
        with httpx.Client(timeout=30) as client:
            response = client.get(self._url("/v1/models"), headers=self._headers())
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        project_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None,
        record: bool = True,
    ) -> LLMResult:
        if not self.configured:
            raise GatewayUnavailable("no LLM gateway configured (FLEET_LLM_GATEWAY_URL)")

        requested = model or self.settings.llm_default_model
        payload = {
            "model": requested,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=self.settings.llm_request_timeout) as client:
            response = client.post(
                self._url("/v1/chat/completions"), headers=self._headers(), json=payload
            )
            if response.status_code >= 400:
                raise GatewayUnavailable(
                    f"gateway returned {response.status_code}: {response.text[:300]}"
                )
            data = response.json()

        usage = data.get("usage") or {}
        result = LLMResult(
            content=(data.get("choices") or [{}])[0].get("message", {}).get("content", ""),
            model=data.get("model", requested),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            cost=float(usage.get("cost") or 0.0),
            raw=data,
        )
        if record:
            record_usage(
                result,
                requested_model=requested,
                project_id=project_id,
                agent_id=agent_id,
                task_id=task_id,
                workspace_id=workspace_id,
            )
        return result


def record_usage(
    result: LLMResult,
    *,
    requested_model: str,
    project_id: str | None,
    agent_id: str | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
    provider: str = "gateway",
) -> None:
    """Persist a usage row.

    A row without a project is still written: unattributed spend is information,
    not something to drop silently.
    """
    with session_scope() as session:
        session.add(
            UsageRecord(
                project_id=project_id or "",
                agent_id=agent_id,
                task_id=task_id,
                workspace_id=workspace_id,
                provider=provider,
                model=result.model,
                requested_model=requested_model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cached_tokens=result.cached_tokens,
                total_tokens=result.prompt_tokens + result.completion_tokens,
                cost=result.cost,
            )
        )


def project_usage(project_id: str) -> dict[str, Any]:
    """Roll usage up by agent, model and task (SPEC §13)."""
    with session_scope() as session:
        rows = session.execute(
            select(UsageRecord).where(UsageRecord.project_id == project_id)
        ).scalars().all()

        def rollup(key: Any) -> list[dict[str, Any]]:
            buckets: dict[Any, dict[str, Any]] = {}
            for row in rows:
                bucket = buckets.setdefault(
                    key(row), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
                )
                bucket["calls"] += 1
                bucket["prompt_tokens"] += row.prompt_tokens
                bucket["completion_tokens"] += row.completion_tokens
                bucket["cost"] += row.cost
            return [{"key": k, **v} for k, v in buckets.items()]

        total = {
            "calls": len(rows),
            "prompt_tokens": sum(r.prompt_tokens for r in rows),
            "completion_tokens": sum(r.completion_tokens for r in rows),
            "cached_tokens": sum(r.cached_tokens for r in rows),
            "cost": sum(r.cost for r in rows),
        }
        return {
            "project_id": project_id,
            "total": total,
            "by_agent": rollup(lambda r: r.agent_id or "(control plane)"),
            "by_model": rollup(lambda r: r.model),
            "by_task": rollup(lambda r: r.task_id or "(none)"),
        }


def usage_totals() -> dict[str, Any]:
    with session_scope() as session:
        row = session.execute(
            select(
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cost), 0.0),
            )
        ).one()
        return {"calls": int(row[0]), "total_tokens": int(row[1]), "cost": float(row[2])}


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


# --- project-scoped gateway tokens -------------------------------------------
#
# A workspace must not hold the gateway master key (SPEC §11). It holds a token
# that names exactly one project; the control plane's `/llm` proxy checks it,
# stamps the usage with that project, and is the only holder of the real key.

_TOKEN_SECRET: str | None = None


def _token_secret() -> str:
    global _TOKEN_SECRET
    if _TOKEN_SECRET is None:
        import os

        _TOKEN_SECRET = (
            os.environ.get("FLEET_TOKEN_SECRET")
            or get_settings().api_token
            or "fleet-development-secret"
        )
    return _TOKEN_SECRET


def project_token(project_id: str) -> str:
    import base64
    import hashlib
    import hmac

    signature = hmac.new(
        _token_secret().encode(), project_id.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{project_id}.{signature}".encode()).decode()


def verify_project_token(token: str) -> str | None:
    """Return the project id a token names, or None. Constant-time compare."""
    import base64
    import binascii
    import hmac

    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    project_id, _, signature = decoded.rpartition(".")
    if not project_id:
        return None
    expected = project_token(project_id).rpartition(".")[2]
    if not hmac.compare_digest(signature, expected):
        return None
    return project_id


def gateway_token_env(project_id: str) -> dict[str, str]:
    """Environment a workspace needs to reach the gateway through the fleet proxy."""
    settings = get_settings()
    base = settings.control_plane_url or f"http://{settings.namespace}-api.{settings.namespace}.svc.cluster.local:8000"
    return {
        "FLEET_LLM_TOKEN": project_token(project_id),
        "FLEET_LLM_BASE_URL": f"{base.rstrip('/')}/llm/v1",
    }
