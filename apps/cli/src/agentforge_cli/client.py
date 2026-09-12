"""HTTP client for the control plane.

Deliberately thin: the CLI is just another API consumer, exactly like the web UI
and Hermes. If a command needs something the API cannot do, the API is what
should change.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:8000/api/v1"


class ApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class WorkbenchClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AGENTFORGE_API_URL") or DEFAULT_URL).rstrip(
            "/"
        )
        self.api_key = api_key or os.environ.get("AGENTFORGE_API_KEY")
        if client is not None:
            self._client = client
            return
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=30.0)

    def __enter__(self) -> WorkbenchClient:
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- convenience wrappers ---------------------------------------------

    def projects(self) -> list[dict]:
        return self.request("GET", "/projects")

    def project(self, project_id: str) -> dict:
        return self.request("GET", f"/projects/{project_id}")

    def create_project(self, name: str, repository_url: str | None = None) -> dict:
        return self.request(
            "POST", "/projects", json={"name": name, "repository_url": repository_url}
        )

    def agents(self, project_id: str) -> list[dict]:
        return self.request("GET", f"/projects/{project_id}/agents")

    def create_agent(self, project_id: str, name: str, model: str | None = None) -> dict:
        return self.request(
            "POST", f"/projects/{project_id}/agents", json={"name": name, "model": model}
        )

    def agent_summary(self, agent_id: str) -> dict:
        return self.request("GET", f"/agents/{agent_id}/summary")

    def stop_agent(self, agent_id: str) -> dict:
        return self.request("POST", f"/agents/{agent_id}/stop")

    def create_task(
        self, project_id: str, prompt: str, agent_id: str | None = None, kind: str = "implement"
    ) -> dict:
        return self.request(
            "POST",
            f"/projects/{project_id}/tasks",
            json={"prompt": prompt, "agent_id": agent_id, "kind": kind},
        )

    def tasks(self, project_id: str) -> list[dict]:
        return self.request("GET", "/tasks", params={"project_id": project_id})

    def review(self, project_id: str, branch: str | None = None) -> dict:
        return self.request("POST", f"/projects/{project_id}/review", json={"branch": branch})

    def test(self, project_id: str, command: str | None = None) -> dict:
        return self.request("POST", f"/projects/{project_id}/test", json={"command": command})

    def deploy(
        self, project_id: str, environment: str = "staging", branch: str | None = None
    ) -> dict:
        return self.request(
            "POST",
            f"/projects/{project_id}/deploy",
            json={"environment": environment, "branch": branch},
        )

    def git_diff(self, project_id: str) -> dict:
        return self.request("GET", f"/projects/{project_id}/git/diff")

    def event_catalog(self) -> list[dict]:
        return self.request("GET", "/events/catalog")

    def webhooks(self) -> list[dict]:
        return self.request("GET", "/webhooks")

    def create_webhook(self, url: str, events: list[str]) -> dict:
        return self.request("POST", "/webhooks", json={"url": url, "events": events})

    def create_key(self, name: str, scopes: list[str] | None = None) -> dict:
        return self.request(
            "POST", "/keys", json={"name": name, "scopes": scopes or ["read", "write"]}
        )

    def find_project(self, needle: str) -> dict:
        """Resolve by id, exact name, or slug — so a caller can say 'ComplianceFlow'."""
        projects = self.projects()
        for project in projects:
            if project["id"] == needle:
                return project
        for project in projects:
            if project["name"].lower() == needle.lower() or project["slug"] == needle:
                return project
        matches = [p for p in projects if needle.lower() in p["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ApiError(404, f"no project matches {needle!r}")
        raise ApiError(409, f"{needle!r} is ambiguous: {[p['name'] for p in matches]}")
