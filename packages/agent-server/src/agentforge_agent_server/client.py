"""Client for the OpenHands Agent Server.

AgentForge does not embed an agent SDK. Each project workspace runs an OpenHands
server, and this package is the only place that knows how to talk to it, so
upstream API drift stays contained here.

The paths below are pinned against OpenHands 0.59, read from the running server's
own `/openapi.json`. That matters more than it sounds: every unknown path on that
server answers with the single-page app and HTTP 200, so a wrong path looks like
a success until you try to read the body. `tests/test_agent_server.py` exists to
make the next drift loud.

Two shape differences from the design this replaces:

* A conversation *is* the agent session — there is no separate agent object to
  create, so `create_conversation` replaces `create_agent`.
* The model is a server-level setting (`configure_model`), not a per-conversation
  field, which is why an agent's `model` alias is applied before the conversation
  starts.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import AgentServerError, AgentServerUnavailable
from .models import AgentEvent, AgentRun, ConversationSpec, FileEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Routes:
    """Every path we depend on, in one place."""

    health: str = "/health"
    settings: str = "/api/settings"
    options_models: str = "/api/options/models"
    options_config: str = "/api/options/config"

    conversations: str = "/api/conversations"
    conversation: str = "/api/conversations/{session_id}"
    conversation_start: str = "/api/conversations/{session_id}/start"
    conversation_stop: str = "/api/conversations/{session_id}/stop"
    messages: str = "/api/conversations/{session_id}/message"
    events: str = "/api/conversations/{session_id}/events"

    list_files: str = "/api/conversations/{session_id}/list-files"
    select_file: str = "/api/conversations/{session_id}/select-file"
    git_diff: str = "/api/conversations/{session_id}/git/diff"
    git_changes: str = "/api/conversations/{session_id}/git/changes"
    vscode: str = "/api/conversations/{session_id}/vscode-url"


class AgentServerClient:
    """One instance per workspace's OpenHands server."""

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

    # --- model configuration ---------------------------------------------

    def configure_model(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Point this server at a model.

        `model` is an AgentForge alias; `base_url` is the LiteLLM gateway, which
        is what resolves the alias to a provider. OpenHands stores this per
        server, so it must be applied before a conversation starts (and again
        after a restart, since the server keeps its settings in the pod).
        """
        body: dict[str, Any] = {"llm_model": model}
        if base_url:
            body["llm_base_url"] = base_url
        if api_key:
            body["llm_api_key"] = api_key
        return self._request("POST", self.routes.settings, json=body) or {}

    def settings(self) -> dict[str, Any]:
        return self._request("GET", self.routes.settings) or {}

    def available_models(self) -> list[str]:
        """Aliases the server will accept — it asks the gateway for these."""
        data = self._request("GET", self.routes.options_models) or []
        if isinstance(data, dict):
            data = data.get("models", [])
        return [str(item) for item in data]

    # --- conversations ----------------------------------------------------

    def create_conversation(self, spec: ConversationSpec | None = None) -> dict[str, Any]:
        spec = spec or ConversationSpec()
        body: dict[str, Any] = {}
        if spec.repository:
            body["repository"] = spec.repository
        if spec.branch:
            body["selected_branch"] = spec.branch
        if spec.instructions:
            body["conversation_instructions"] = spec.instructions
        return self._request("POST", self.routes.conversations, json=body) or {}

    def list_conversations(self, *, limit: int = 20) -> list[dict[str, Any]]:
        data = self._request("GET", self.routes.conversations, params={"limit": limit}) or []
        if isinstance(data, dict):
            data = data.get("items", data.get("conversations", []))
        return list(data)

    def start_conversation(
        self, session_id: str, *, providers_set: list[str] | None = None
    ) -> AgentRun:
        """Start a conversation, or report the one already running.

        Creating a conversation already starts it, so this is usually a no-op —
        and it still has to carry a JSON body: OpenHands declares one, and a
        request with no body at all is a 422 rather than an empty object.
        """
        body: dict[str, Any] = {}
        if providers_set:
            body["providers_set"] = providers_set
        try:
            data = (
                self._request(
                    "POST",
                    self._path("conversation_start", session_id=session_id),
                    json=body,
                )
                or {}
            )
        except AgentServerError as exc:
            if exc.status_code in (400, 409):
                log.debug("conversation %s already started: %s", session_id, exc)
                data = self._request("GET", self._path("conversation", session_id=session_id)) or {}
            else:
                raise
        return AgentRun(
            session_id=str(data.get("conversation_id") or data.get("id") or session_id),
            status=str(data.get("status") or data.get("state") or "running"),
            detail=data,
        )

    def stop_conversation(self, session_id: str) -> None:
        self._request("POST", self._path("conversation_stop", session_id=session_id))

    def send_message(self, session_id: str, message: str) -> None:
        """Queue a turn. OpenHands acknowledges it; the reply arrives as events."""
        self._request(
            "POST", self._path("messages", session_id=session_id), json={"message": message}
        )

    # --- events -----------------------------------------------------------

    def events(self, session_id: str, *, start_id: int = 0, limit: int = 100) -> list[AgentEvent]:
        """Events after `start_id`, oldest first.

        The cursor is omitted when there is not one: the server answers
        `start_id=0` with 400 rather than treating it as "from the beginning",
        and the first page is what a missing cursor means anyway.
        """
        params: dict[str, int] = {"limit": limit}
        if start_id > 0:
            params["start_id"] = start_id
        data = (
            self._request(
                "GET",
                self._path("events", session_id=session_id),
                params=params,
            )
            or []
        )
        if isinstance(data, dict):
            data = data.get("events", data.get("items", []))
        return [AgentEvent.from_payload(item) for item in data if isinstance(item, dict)]

    def wait_for_reply(
        self,
        session_id: str,
        *,
        start_id: int = 0,
        timeout: float = 900.0,
        poll: float = 2.0,
    ) -> AgentEvent:
        """Follow the event stream until the turn ends.

        OpenHands has no request/response turn: `send_message` returns as soon as
        it is queued, and everything the agent does arrives as events. This polls
        the same events endpoint until the conversation finishes, then reports the
        last thing the agent said.
        """
        deadline = time.monotonic() + timeout
        last: AgentEvent | None = None
        cursor = start_id
        while time.monotonic() < deadline:
            for event in self.events(session_id, start_id=cursor):
                cursor = max(cursor, int(event.raw.get("id", cursor) or cursor))
                last = event
                if event.error or event.blocked or event.finished:
                    return event
            time.sleep(poll)

        if last is None:
            raise AgentServerError(f"conversation {session_id} produced no events")
        return last

    def stream_events(self, session_id: str, *, start_id: int = 0) -> Iterator[AgentEvent]:
        """Yield events as they appear. Blocking, because the orchestrator runs
        one turn per task and a second event loop would buy nothing."""
        cursor = start_id
        while True:
            batch = self.events(session_id, start_id=cursor)
            if not batch:
                return
            for event in batch:
                cursor = max(cursor, int(event.raw.get("id", cursor) or cursor))
                yield event

    # --- files and git ----------------------------------------------------

    def list_files(self, session_id: str, path: str = "/workspace") -> list[FileEntry]:
        data = (
            self._request(
                "GET", self._path("list_files", session_id=session_id), params={"path": path}
            )
            or {}
        )
        if isinstance(data, dict):
            data = data.get("files", data.get("items", []))
        entries: list[FileEntry] = []
        for item in data:
            if isinstance(item, str):
                entries.append(FileEntry(path=item, is_dir=item.endswith("/")))
                continue
            entries.append(
                FileEntry(
                    path=str(item.get("path") or item.get("name")),
                    is_dir=bool(item.get("is_dir") or item.get("type") == "directory"),
                    size=item.get("size"),
                )
            )
        return entries

    def read_file(self, session_id: str, path: str) -> str:
        data = (
            self._request(
                "GET", self._path("select_file", session_id=session_id), params={"file": path}
            )
            or {}
        )
        if isinstance(data, dict):
            return str(data.get("code", data.get("content", "")))
        return "" if data is None else str(data)

    def git_diff(self, session_id: str) -> str:
        data = self._request("GET", self._path("git_diff", session_id=session_id)) or {}
        return str(data.get("diff", "")) if isinstance(data, dict) else str(data)

    def git_changes(self, session_id: str) -> dict[str, Any]:
        data = self._request("GET", self._path("git_changes", session_id=session_id)) or {}
        return data if isinstance(data, dict) else {"raw": data}

    # --- IDE --------------------------------------------------------------

    def vscode_url(self, session_id: str) -> str | None:
        data = self._request("GET", self._path("vscode", session_id=session_id)) or {}
        if isinstance(data, dict):
            return data.get("url") or data.get("vscode_url")
        return None
