"""End-to-end tests over the versioned control-plane API."""

from __future__ import annotations

API = "/api/v1"


def make_project(client, name: str = "ComplianceFlow", repo: str | None = None):
    resp = client.post(
        f"{API}/projects",
        json={"name": name, "repository_url": repo, "default_branch": "main"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_liveness_is_unversioned(client):
    """A probe must not break when the API version moves."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_versioned_health(client):
    body = client.get(f"{API}/health").json()
    assert body["status"] == "ok"


def test_create_project_provisions_workspace_row(client):
    project = make_project(client, "ComplianceFlow", "https://github.com/example/cf")

    assert project["slug"] == "complianceflow"
    assert project["status"] == "creating"
    assert project["workspace"]["status"] == "pending"
    assert project["workspace"]["namespace"] == "af-complianceflow"
    assert project["agents"] == []


def test_slug_collisions_get_suffixed(client):
    first = make_project(client, "My SaaS")
    second = make_project(client, "My SaaS")
    assert first["slug"] == "my-saas"
    assert second["slug"] == "my-saas-2"


def test_list_and_get_projects(client):
    make_project(client, "Bible")
    make_project(client, "Homelab")

    projects = client.get(f"{API}/projects").json()
    assert {p["name"] for p in projects} == {"Bible", "Homelab"}

    one = client.get(f"{API}/projects/{projects[0]['id']}").json()
    assert one["id"] == projects[0]["id"]


def test_agent_creation_binds_branch_and_model(client):
    project = make_project(client, "Radar Clone")

    resp = client.post(
        f"{API}/projects/{project['id']}/agents", json={"name": "Auth API", "model": "smart"}
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()

    assert agent["branch"] == "agent/auth-api"
    assert agent["model"] == "smart"
    assert agent["status"] == "starting"

    agents = client.get(f"{API}/projects/{project['id']}/agents").json()
    assert len(agents) == 1


def test_agent_model_can_be_changed_after_creation(client):
    """The dashboard needs a way to point an existing agent at another alias."""
    project = make_project(client, "ModelSwitch")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Builder"}).json()
    assert agent["model"] == "local-coder"

    resp = client.patch(f"{API}/agents/{agent['id']}", json={"model": "smart"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "smart"

    # And it sticks: the detail read is not a stale row.
    assert client.get(f"{API}/agents/{agent['id']}").json()["model"] == "smart"


class _FakeGatewayResponse:
    """Just enough of an httpx.Response for the model-list probe."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_model_aliases_fall_back_to_config_without_a_gateway(client):
    """No LiteLLM in this deployment: the picker still offers something."""
    body = client.get(f"{API}/models").json()

    assert body["source"] == "config"
    assert body["models"] == ["local-coder", "fast", "smart"]
    assert body["default"] in body["models"]


def test_model_aliases_come_from_the_gateway_when_it_answers(client, monkeypatch):
    """The gateway decides what aliases exist; settings are only a fallback."""
    from agentforge_api.routers import models as models_router

    payload = {"data": [{"id": "smart"}, {"id": "local-coder"}]}
    monkeypatch.setattr(
        models_router.httpx, "get", lambda url, timeout: _FakeGatewayResponse(payload)
    )

    body = client.get(f"{API}/models").json()
    assert body["source"] == "gateway"
    assert body["models"] == ["smart", "local-coder"]


def test_task_lifecycle_queues_work(client):
    project = make_project(client, "TaskFlow")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Builder"}).json()

    resp = client.post(
        f"{API}/projects/{project['id']}/tasks",
        json={"prompt": "Add a /health endpoint", "agent_id": agent["id"]},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()

    assert task["status"] == "queued"
    assert task["position"] == 1
    assert task["kind"] == "implement"
    assert task["priority"] == "normal"

    second = client.post(
        f"{API}/projects/{project['id']}/tasks",
        json={"description": "Write tests", "agent_id": agent["id"]},
    ).json()
    assert second["position"] == 2

    cancelled = client.post(f"{API}/tasks/{task['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"


def test_task_requires_a_prompt_or_description(client):
    project = make_project(client, "EmptyPrompt")
    resp = client.post(f"{API}/projects/{project['id']}/tasks", json={"agent_id": None})
    assert resp.status_code == 422


def test_agent_can_be_queued_work_directly(client):
    project = make_project(client, "Direct")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Solo"}).json()

    resp = client.post(f"{API}/agents/{agent['id']}/tasks", json={"prompt": "Ship it"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["agent_id"] == agent["id"]


def test_agent_conversation_and_permissions(client):
    project = make_project(client, "Chatty")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Talker"}).json()

    client.post(f"{API}/agents/{agent['id']}/messages", json={"content": "Use JWT or cookies?"})
    convo = client.get(f"{API}/agents/{agent['id']}/conversation").json()
    assert convo["messages"][0]["role"] == "user"
    assert "JWT" in convo["messages"][0]["content"]

    decided = client.post(
        f"{API}/agents/{agent['id']}/permissions",
        json={"request_id": "req-1", "decision": "allow_once"},
    ).json()
    assert decided["status"] == "working"


def test_denied_permission_idles_the_agent(client):
    project = make_project(client, "Denied")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Asker"}).json()

    decided = client.post(
        f"{API}/agents/{agent['id']}/permissions",
        json={"request_id": "req-2", "decision": "deny"},
    ).json()
    assert decided["status"] == "idle"


def test_git_diff_requires_a_ready_workspace(client):
    project = make_project(client, "NoWorkspace")
    resp = client.get(f"{API}/projects/{project['id']}/git/diff")
    assert resp.status_code == 503
    assert "unavailable until it is ready" in resp.json()["detail"].lower()


def test_missing_project_is_404(client):
    assert client.get(f"{API}/projects/does-not-exist").status_code == 404


def test_event_stream_backfills_over_websocket(client):
    project = make_project(client, "Streamer")

    with client.websocket_connect(f"{API}/projects/{project['id']}/events") as ws:
        first = ws.receive_json()
        assert first["type"] == "project.created"
        assert first["project_id"] == project["id"]
