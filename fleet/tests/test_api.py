"""The control-plane API end to end, against a fake workspace provider."""

from __future__ import annotations

import json
import time


def _wait_for(fn, timeout: float = 15.0, interval: float = 0.1):
    """Poll until `fn` returns something truthy. Task execution is asynchronous."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def test_health_reports_configuration(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["authenticated"] is True


def test_authentication_is_required(client):
    response = client.get("/api/v1/projects", headers={"Authorization": ""})
    assert response.status_code == 401
    response = client.get("/api/v1/projects", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_project_lifecycle(client, fake_provider, clean_db):
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "demo-alpha",
            "repository": {"url": "https://github.com/example/alpha", "branch": "main"},
            "llmPolicy": {"mode": "localOnly"},
            "credentials": [{"name": "github", "value": "not-a-real-token"}],
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["workspaces"][0]["reference"] == "fleet-demo-alpha"
    # The workspace is provisioned in the background; the provider is synchronous
    # but the request is not.
    workspace = _wait_for(
        lambda: (
            client.get("/api/v1/projects/demo-alpha").json()["workspaces"][0]
            if client.get("/api/v1/projects/demo-alpha").json()["workspaces"][0]["status"]
            == "ready"
            else None
        )
    )
    assert workspace["pod"] == "fleet-demo-alpha-ws"
    assert workspace["t3_url"].startswith("https://")

    # Credential metadata is returned; the value never is.
    credentials = client.get("/api/v1/projects/demo-alpha/credentials").json()["items"]
    assert credentials and credentials[0]["name"] == "github"
    assert "value" not in json.dumps(credentials)

    # SPEC §16: no route may echo a secret value.
    detail = client.get("/api/v1/projects/demo-alpha").text
    assert "not-a-real-token" not in detail

    assert client.delete("/api/v1/projects/demo-alpha").status_code == 202
    assert client.get("/api/v1/projects/demo-alpha").status_code == 404
    assert "fleet-demo-alpha" in fake_provider.destroyed


def test_duplicate_project_is_a_conflict(client, fake_provider, clean_db):
    payload = {"name": "demo-beta"}
    assert client.post("/api/v1/projects", json=payload).status_code == 201
    assert client.post("/api/v1/projects", json=payload).status_code == 409


def test_agent_and_task_flow(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-gamma"})
    _wait_for(lambda: fake_provider.workspaces.get("fleet-demo-gamma"))

    agent = client.post(
        "/api/v1/projects/demo-gamma/agents",
        json={"name": "backend", "provider": "opencode", "role": "backend", "model": "local-coder"},
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    submitted = client.post(
        f"/api/v1/agents/{agent_id}/tasks",
        json={"prompt": "add a hello file", "priority": "normal"},
    )
    assert submitted.status_code == 202, submitted.text
    task_id = submitted.json()["id"]

    task = _wait_for(
        lambda: (
            client.get(f"/api/v1/tasks/{task_id}").json()
            if client.get(f"/api/v1/tasks/{task_id}").json()["status"] in {"completed", "failed"}
            else None
        )
    )
    assert task["agent_id"] == agent_id
    # The opencode provider runs `opencode run` in the workspace; the fake
    # workspace recorded the command.
    assert any("opencode run" in " ".join(command) for _, command in fake_provider.commands)
    # A worktree was requested for the agent before the run (SPEC §25).
    assert any("git worktree add" in " ".join(command) for _, command in fake_provider.commands)


def test_task_lifecycle_is_evented(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-delta"})
    _wait_for(lambda: fake_provider.workspaces.get("fleet-demo-delta"))
    agent_id = client.post("/api/v1/projects/demo-delta/agents", json={"name": "reviewer"}).json()[
        "id"
    ]
    task_id = client.post(
        f"/api/v1/agents/{agent_id}/tasks", json={"prompt": "review the diff"}
    ).json()["id"]
    _wait_for(
        lambda: client.get(f"/api/v1/tasks/{task_id}").json()["status"] in {"completed", "failed"}
    )

    events = client.get("/api/v1/events", params={"task": task_id, "limit": 50}).json()["items"]
    types = {event["type"] for event in events}
    assert "task.created" in types
    assert "task.started" in types
    assert types & {"task.completed", "task.failed"}


def test_cancelling_a_finished_task_is_rejected(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-epsilon"})
    _wait_for(lambda: fake_provider.workspaces.get("fleet-demo-epsilon"))
    agent_id = client.post("/api/v1/projects/demo-epsilon/agents", json={"name": "tester"}).json()[
        "id"
    ]
    task_id = client.post(f"/api/v1/agents/{agent_id}/tasks", json={"prompt": "run tests"}).json()[
        "id"
    ]
    _wait_for(
        lambda: client.get(f"/api/v1/tasks/{task_id}").json()["status"] in {"completed", "failed"}
    )
    assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 409


def test_the_isolation_boundary_is_built_before_the_credential_goes_in(
    client, fake_provider, clean_db
):
    """Ordering that a live deployment got wrong.

    The credential copy needs the namespace to exist, and the pod needs the
    credential to exist, so `prepare` -> credentials -> `create` is the only
    order that works. The first deployed control plane copied credentials first
    and every project failed with a 404 from the Kubernetes API.
    """
    client.post(
        "/api/v1/projects",
        json={"name": "demo-theta", "credentials": [{"name": "github", "value": "x"}]},
    )
    workspace = _wait_for(
        lambda: client.get("/api/v1/projects/demo-theta").json()["workspaces"][0]
        if client.get("/api/v1/projects/demo-theta").json()["workspaces"][0]["status"] == "ready"
        else None
    )
    assert workspace["reference"] == "fleet-demo-theta"
    assert fake_provider.lifecycle[:2] == ["prepare:fleet-demo-theta", "create:fleet-demo-theta"]


def test_a_prepare_failure_is_reported_not_swallowed(client, fake_provider, clean_db, monkeypatch):
    """A provider that cannot build the boundary must not leave a project 'ready'."""
    from fleet_core.workspaces.base import ProviderError

    def explode(spec):
        raise ProviderError("simulated: cannot create the namespace")

    monkeypatch.setattr(fake_provider, "prepare", explode)
    client.post("/api/v1/projects", json={"name": "demo-iota"})
    workspace = _wait_for(
        lambda: client.get("/api/v1/projects/demo-iota").json()["workspaces"][0]
        if client.get("/api/v1/projects/demo-iota").json()["workspaces"][0]["status"] == "failed"
        else None
    )
    assert workspace["status"] == "failed"
    assert "cannot create the namespace" in workspace["error"]


def test_a_t3_agent_cannot_be_given_a_task(client, fake_provider, clean_db):
    """T3 Code is a control surface. The API refuses rather than pretending."""
    client.post("/api/v1/projects", json={"name": "demo-zeta"})
    _wait_for(lambda: fake_provider.workspaces.get("fleet-demo-zeta"))
    agent_id = client.post(
        "/api/v1/projects/demo-zeta/agents", json={"name": "console", "provider": "t3code"}
    ).json()["id"]
    response = client.post(f"/api/v1/agents/{agent_id}/tasks", json={"prompt": "fix it"})
    assert response.status_code == 409
    assert "control surface" in response.text


def test_provider_report_names_the_selected_provider(client, fake_provider):
    report = client.get("/api/v1/providers").json()
    names = {provider["name"] for provider in report["workspace_providers"]}
    assert {"devpod", "kubernetes"} <= names
    for provider in report["workspace_providers"]:
        assert "capabilities" in provider or "error" in provider


def test_workspace_exec_is_reachable(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-eta"})
    _wait_for(lambda: fake_provider.workspaces.get("fleet-demo-eta"))
    response = client.post(
        "/api/v1/projects/demo-eta/workspace/exec", json={"command": "cat /etc/hostname"}
    )
    assert response.status_code == 200
    assert response.json()["exit_code"] == 0
