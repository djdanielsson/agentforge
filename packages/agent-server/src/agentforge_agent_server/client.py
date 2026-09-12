"""HTTP and WebSocket client for the OpenHands Agent Server.

Endpoint shapes differ between Agent Server releases. Every path lives in
`Routes` below, so adapting to a version bump is a change to one dataclass and
the handful of methods that read a response, not a hunt through the codebase.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import AgentServerError, AgentServerUnavailable
from .models import AgentEvent, AgentRun, AgentSpec, FileEntry, WorkspaceRef

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Routes:
    """Every path we depend on, in one place."""

    health: str = "/health"

    workspaces: str = "/api/workspaces"
    workspace: str = "/api/workspaces/{workspace_id}"

    agents: str = "/api/agents"
    agent: str = "/api/agents/{agent_id}"
    agent_stop: str = "/api/agents/{agent_id}/stop"
    agent_restart: str = "/api/agents/{agent_id}/restart"

    conversations: str = "/api/agents/{agent_id}/conversations"
    conversation: str = "/api/conversations/{session_id}"
    messages: str = "/api/conversations/{session_id}/messages"
    events: str = "/api/conversations/{session_id}/events"

    files: str = "/api/workspaces/{workspace_id}/files"
    file: str = "/api/workspaces/{workspace_id}/files/{path}"
    git: str = "/api/workspaces/{workspace_id}/git/{operation}"
    terminal: str = "/api/workspaces/{workspace_id}/terminal"
    vscode: str = "/api/workspaces/{workspace_id}/vscode"


class AgentServerClient:
    """One instance per workspace's Agent Server."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        routes: Routes | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.routes = routes or Routes()
        self._api_key = api_key
        if client is not None:
            self._client = client
        else:
            headers = {"user-agent": "agentforge/0.1"}
            if api_key:
                headers["authorization"] = f"Bearer {api_key}"
            self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    # --- plumbing ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AgentServerClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _path(self, template: str, **values: str) -> str:
        return self.routes.__getattribute__(template).format(**values)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AgentServerUnavailable(
                f"could not reach the agent server at {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise AgentServerError(
                f"agent server returned {response.status_code} for {method} {path}",
                status_code=response.status_code,
                detail=response.text[:1000],
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": response.text}

    # --- health -----------------------------------------------------------

    def health(self) -> bool:
        """Never raises: a health check that throws is hard to use in a loop."""
        try:
            self._request("GET", self.routes.health)
            return True
        except AgentServerError:
            return False

    # --- workspaces -------------------------------------------------------

    def create_workspace(
        self, *, path: str = "/workspace", name: str | None = None
    ) -> WorkspaceRef:
        body: dict[str, Any] = {"path": path}
        if name:
            body["name"] = name
        data = self._request("POST", self.routes.workspaces, json=body) or {}
        return WorkspaceRef(
            id=str(data.get("id") or data.get("workspace_id") or path),
            path=data.get("path", path),
            status=str(data.get("status", "ready")),
            detail=data,
        )

    def list_workspaces(self) -> list[WorkspaceRef]:
        data = self._request("GET", self.routes.workspaces) or []
        items = data if isinstance(data, list) else data.get("items", [])
        return [
            WorkspaceRef(
                id=str(item.get("id")),
                path=item.get("path", "/workspace"),
                status=str(item.get("status", "unknown")),
                detail=item,
            )
            for item in items
        ]

    def delete_workspace(self, workspace_id: str) -> None:
        self._request("DELETE", self._path("workspace", workspace_id=workspace_id))

    # --- agents -----------------------------------------------------------

    def create_agent(self, spec: AgentSpec) -> dict[str, Any]:
        body: dict[str, Any] = {"name": spec.name, "llm_model": spec.model}
        if spec.workspace_id:
            body["workspace_id"] = spec.workspace_id
        if spec.system_prompt:
            body["system_prompt"] = spec.system_prompt
        if spec.metadata:
            body["metadata"] = spec.metadata
        return self._request("POST", self.routes.agents, json=body) or {}

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._request("GET", self._path("agent", agent_id=agent_id)) or {}

    def stop_agent(self, agent_id: str) -> None:
        self._request("POST", self._path("agent_stop", agent_id=agent_id))

    def restart_agent(self, agent_id: str) -> dict[str, Any]:
        return self._request("POST", self._path("agent_restart", agent_id=agent_id)) or {}

    # --- conversations ----------------------------------------------------

    def start_conversation(self, agent_id: str, *, prompt: str | None = None) -> AgentRun:
        body = {"prompt": prompt} if prompt else {}
        data = (
            self._request("POST", self._path("conversations", agent_id=agent_id), json=body) or {}
        )
        session_id = data.get("session_id") or data.get("id")
        if not session_id:
            raise AgentServerError(
                "agent server did not return a session id", detail=str(data)[:500]
            )
        return AgentRun(
            session_id=str(session_id), status=str(data.get("status", "running")), detail=data
        )

    def send_message(self, session_id: str, content: str) -> AgentEvent:
        """Send a turn and return the agent's response event."""
        data = (
            self._request(
                "POST", self._path("messages", session_id=session_id), json={"content": content}
            )
            or {}
        )
        return AgentEvent.from_payload(data)

    def get_events(self, session_id: str) -> list[AgentEvent]:
        data = self._request("GET", self._path("events", session_id=session_id)) or []
        items = data if isinstance(data, list) else data.get("items", [])
        return [AgentEvent.from_payload(item) for item in items]

    def stream_events(self, session_id: str) -> Iterator[AgentEvent]:
        """Yield events as the agent produces them, over the WebSocket API.

        Converted to a plain iterator so callers do not need an async runtime.
        The orchestrator runs one turn per task, so blocking is acceptable and
        much simpler to reason about than a second event loop.
        """
        import asyncio

        import websockets

        url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{url}{self._path('events', session_id=session_id)}"

        async def _consume() -> list[AgentEvent]:
            collected: list[AgentEvent] = []
            headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            async with websockets.connect(url, additional_headers=headers) as socket:
                async for raw in socket:
                    try:
                        collected.append(AgentEvent.from_payload(json.loads(raw)))
                    except json.JSONDecodeError:
                        log.warning("unparseable agent event: %r", raw[:200])
            return collected

        yield from asyncio.run(_consume())

    def answer_permission(self, session_id: str, request_id: str, decision: str) -> None:
        self._request(
            "POST",
            f"{self._path('messages', session_id=session_id)}/permissions",
            json={"request_id": request_id, "decision": decision},
        )

    # --- files ------------------------------------------------------------

    def list_files(self, workspace_id: str, path: str = ".") -> list[FileEntry]:
        data = (
            self._request(
                "GET", self._path("files", workspace_id=workspace_id), params={"path": path}
            )
            or []
        )
        items = data if isinstance(data, list) else data.get("items", [])
        return [
            FileEntry(
                path=str(item.get("path")),
                is_dir=bool(item.get("is_dir")),
                size=item.get("size"),
            )
            for item in items
        ]

    def read_file(self, workspace_id: str, path: str) -> str:
        data = self._request(
            "GET", f"{self._path('files', workspace_id=workspace_id)}/{path.lstrip('/')}"
        )
        if isinstance(data, dict):
            return data.get("content", "")
        return "" if data is None else str(data)

    def write_file(self, workspace_id: str, path: str, content: str) -> None:
        self._request(
            "PUT",
            f"{self._path('files', workspace_id=workspace_id)}/{path.lstrip('/')}",
            json={"content": content},
        )

    # --- git --------------------------------------------------------------

    def git(self, workspace_id: str, operation: str, **params: Any) -> Any:
        """`operation` is status, diff, commit, branches, checkout or push."""
        return self._request(
            "POST",
            self._path("git", workspace_id=workspace_id, operation=operation),
            json=params or {},
        )

    # --- terminal and IDE -------------------------------------------------

    def exec(self, workspace_id: str, command: str, *, timeout: float | None = None) -> str:
        data = self._request(
            "POST",
            self._path("terminal", workspace_id=workspace_id),
            json={"command": command},
            **({"timeout": timeout} if timeout else {}),
        )
        if isinstance(data, dict):
            return data.get("output", "")
        return "" if data is None else str(data)

    def vscode_url(self, workspace_id: str) -> str | None:
        data = self._request("GET", self._path("vscode", workspace_id=workspace_id)) or {}
        return data.get("url") if isinstance(data, dict) else None
