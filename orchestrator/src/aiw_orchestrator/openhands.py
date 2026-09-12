"""Thin client over the OpenHands agent runtime.

OpenHands gives us conversation, tool use, file manipulation and shell execution
inside the workspace. We consume its HTTP API rather than forking it.

Endpoint shapes differ between OpenHands deployments; this client keeps the
surface tiny and isolated so adapting to a version bump is a one-file change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentReply:
    content: str
    blocked: bool = False
    question: str | None = None
    permission_request: dict | None = None
    finished: bool = False


class OpenHandsClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenHandsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def health(self) -> bool:
        try:
            return self._client.get("/health").status_code < 500
        except httpx.HTTPError:
            return False

    def start_agent(self, *, agent_id: str, workspace_url: str, model: str) -> dict:
        """Create a conversation bound to a workspace and a model alias."""
        payload = {
            "agent_id": agent_id,
            "workspace": workspace_url,
            "llm_model": model,
        }
        resp = self._client.post("/api/agents", json=payload)
        resp.raise_for_status()
        return resp.json()

    def send_message(self, *, session_id: str, content: str) -> AgentReply:
        resp = self._client.post(f"/api/agents/{session_id}/messages", json={"content": content})
        resp.raise_for_status()
        data = resp.json()
        return AgentReply(
            content=data.get("content", ""),
            blocked=bool(data.get("blocked")),
            question=data.get("question"),
            permission_request=data.get("permission_request"),
            finished=bool(data.get("finished")),
        )

    def stop_agent(self, session_id: str) -> None:
        try:
            self._client.post(f"/api/agents/{session_id}/stop")
        except httpx.HTTPError as exc:  # best effort
            log.warning("failed to stop agent %s: %s", session_id, exc)

    def answer_permission(self, *, session_id: str, request_id: str, decision: str) -> None:
        self._client.post(
            f"/api/agents/{session_id}/permissions",
            json={"request_id": request_id, "decision": decision},
        ).raise_for_status()
