"""End-to-end tests over the versioned control-plane API."""

from __future__ import annotations

import pytest

API = "/api/v1"


@pytest.fixture(autouse=True)
def _fresh_model_catalog():
    """The catalog caches its answer; a cached list would leak between tests."""
    from agentforge_api import model_catalog

    model_catalog._cache = None
    yield
    model_catalog._cache = None


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


def _gateway_serves(monkeypatch, *aliases: str, api_key: str | None = None) -> dict:
    """Make the model catalog see a gateway serving exactly these aliases."""
    import types

    from agentforge_api import model_catalog

    seen: dict = {}
    payload = {"data": [{"id": alias} for alias in aliases]}

    def fake_get(url, timeout, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeGatewayResponse(payload)

    monkeypatch.setattr(model_catalog.httpx, "get", fake_get)
    monkeypatch.setattr(
        model_catalog,
        "get_settings",
        lambda: types.SimpleNamespace(
            llm_gateway_url="http://gateway:4000",
            llm_gateway_api_key=api_key,
            default_agent_model="local-coder",
            model_aliases=["local-coder", "fast", "smart"],
        ),
    )
    return seen


def test_model_aliases_fall_back_to_config_without_a_gateway(client):
    """No LiteLLM in this deployment: the picker still offers something."""
    body = client.get(f"{API}/models").json()

    assert body["source"] == "config"
    assert body["models"] == ["local-coder", "fast", "smart"]
    assert body["default"] in body["models"]


def test_model_aliases_come_from_the_gateway_when_it_answers(client, monkeypatch):
    """The gateway decides what aliases exist; settings are only a fallback."""
    _gateway_serves(monkeypatch, "smart", "local-coder")

    body = client.get(f"{API}/models").json()

    assert body["source"] == "gateway"
    assert body["models"] == ["smart", "local-coder"]


def test_the_gateway_probe_presents_the_configured_key(client, monkeypatch):
    """A gateway that authenticates its callers must still be asked for aliases."""
    seen = _gateway_serves(monkeypatch, "smart", api_key="sk-test")

    client.get(f"{API}/models")

    assert seen["headers"] == {"Authorization": "Bearer sk-test"}


def test_an_unknown_model_is_refused_when_the_agent_is_created(client):
    """A typo in an alias must fail here, not at dispatch time."""
    project = make_project(client, "BadModel")

    resp = client.post(
        f"{API}/projects/{project['id']}/agents",
        json={"name": "Builder", "model": "gpt-9-turbo"},
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()["detail"]
    assert "unknown model alias" in body["message"]
    assert body["models"] == ["local-coder", "fast", "smart"]


def test_validation_follows_the_gateway_rather_than_the_config(client, monkeypatch):
    """When the gateway answers, its list is the one that counts."""
    _gateway_serves(monkeypatch, "smart")
    project = make_project(client, "GatewayModels")

    refused = client.post(
        f"{API}/projects/{project['id']}/agents",
        json={"name": "Builder", "model": "local-coder"},
    )
    accepted = client.post(
        f"{API}/projects/{project['id']}/agents",
        json={"name": "Builder", "model": "smart"},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"]["source"] == "gateway"
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["model"] == "smart"


def test_an_unknown_model_is_refused_when_the_agent_is_updated(client, monkeypatch):
    """Editing an agent's model is the other way a bad alias could get in."""
    _gateway_serves(monkeypatch, "smart", "local-coder")
    project = make_project(client, "EditModel")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Builder"}).json()

    refused = client.patch(f"{API}/agents/{agent['id']}", json={"model": "gpt-9-turbo"})
    accepted = client.patch(f"{API}/agents/{agent['id']}", json={"model": "smart"})

    assert refused.status_code == 422
    assert accepted.status_code == 200
    assert accepted.json()["model"] == "smart"


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
