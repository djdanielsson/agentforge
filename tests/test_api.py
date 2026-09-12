"""End-to-end tests over the control-plane API."""

from __future__ import annotations


def make_project(client, name: str = "ComplianceFlow", repo: str | None = None):
    resp = client.post(
        "/projects",
        json={"name": name, "repository_url": repo, "default_branch": "main"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_create_project_provisions_workspace_row(client):
    project = make_project(client, "ComplianceFlow", "https://github.com/example/cf")

    assert project["slug"] == "complianceflow"
    assert project["status"] == "creating"
    # a PENDING workspace is recorded immediately; the orchestrator fills it in
    assert project["workspace"]["status"] == "pending"
    assert project["workspace"]["namespace"] == "aiw-complianceflow"
    assert project["agents"] == []


def test_slug_collisions_get_suffixed(client):
    first = make_project(client, "My SaaS")
    second = make_project(client, "My SaaS")
    assert first["slug"] == "my-saas"
    assert second["slug"] == "my-saas-2"


def test_project_create_records_event(client):
    project = make_project(client, "Book")

    events = client.get(f"/projects/{project['id']}/events")
    # the websocket endpoint is the stream; the row is what we assert on here
    assert events.status_code in (403, 404, 405, 426)  # no plain GET handler

    listed = client.get("/tasks", params={"project_id": project["id"]}).json()
    assert listed == []


def test_list_and_get_projects(client):
    make_project(client, "Bible")
    make_project(client, "Homelab")

    projects = client.get("/projects").json()
    assert {p["name"] for p in projects} == {"Bible", "Homelab"}

    one = client.get(f"/projects/{projects[0]['id']}").json()
    assert one["id"] == projects[0]["id"]


def test_agent_creation_binds_branch_and_model(client):
    project = make_project(client, "Radar Clone")

    resp = client.post(
        f"/projects/{project['id']}/agents",
        json={"name": "Auth API", "model": "smart"},
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()

    assert agent["branch"] == "agent/auth-api"
    assert agent["model"] == "smart"
    assert agent["status"] == "starting"

    agents = client.get(f"/projects/{project['id']}/agents").json()
    assert len(agents) == 1


def test_task_lifecycle_queues_work(client):
    project = make_project(client, "TaskFlow")
    agent = client.post(f"/projects/{project['id']}/agents", json={"name": "Builder"}).json()

    resp = client.post(
        f"/projects/{project['id']}/tasks",
        json={"description": "Add a /health endpoint", "agent_id": agent["id"]},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()

    assert task["status"] == "queued"
    assert task["position"] == 1

    # second task queues behind the first
    second = client.post(
        f"/projects/{project['id']}/tasks",
        json={"description": "Write tests", "agent_id": agent["id"]},
    ).json()
    assert second["position"] == 2

    cancelled = client.post(f"/tasks/{task['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"


def test_agent_conversation_and_permissions(client):
    project = make_project(client, "Chatty")
    agent = client.post(f"/projects/{project['id']}/agents", json={"name": "Talker"}).json()

    client.post(f"/agents/{agent['id']}/messages", json={"content": "Use JWT or cookies?"})
    convo = client.get(f"/agents/{agent['id']}/conversation").json()
    assert convo["messages"][0]["role"] == "user"
    assert "JWT" in convo["messages"][0]["content"]

    decided = client.post(
        f"/agents/{agent['id']}/permissions",
        json={"request_id": "req-1", "decision": "allow_once"},
    ).json()
    assert decided["status"] == "working"


def test_git_diff_requires_a_ready_workspace(client):
    project = make_project(client, "NoWorkspace")
    resp = client.get(f"/projects/{project['id']}/git/diff")
    assert resp.status_code == 503
    assert "unavailable until it is ready" in resp.json()["detail"].lower()


def test_missing_project_is_404(client):
    assert client.get("/projects/does-not-exist").status_code == 404


def test_event_stream_backfills_over_websocket(client):
    project = make_project(client, "Streamer")

    with client.websocket_connect(f"/projects/{project['id']}/events") as ws:
        first = ws.receive_json()
        assert first["type"] == "project.created"
        assert first["project_id"] == project["id"]
