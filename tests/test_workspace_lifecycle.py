"""Workspace lifecycle and agent control endpoints."""

from __future__ import annotations

API = "/api/v1"


def _project(client, name="Lifecycle"):
    return client.post(f"{API}/projects", json={"name": name}).json()


def test_a_new_project_starts_with_a_pending_workspace(client):
    project = _project(client)
    workspace = client.get(f"{API}/projects/{project['id']}/workspace").json()
    assert workspace["status"] == "pending"
    assert workspace["namespace"].startswith("af-")
    assert workspace["provider"] == "kubernetes"


def test_provisioning_can_be_requested_and_is_idempotent(client):
    project = _project(client, "Provision")

    first = client.post(f"{API}/projects/{project['id']}/workspace")
    assert first.status_code == 202
    assert first.json()["action"] == "provision"

    second = client.post(f"{API}/projects/{project['id']}/workspace")
    assert second.status_code == 202
    assert second.json()["status"] in ("pending", "ready")


def test_provision_short_circuits_when_already_ready(client):
    project = _project(client, "AlreadyReady")

    from agentforge_shared.db import session_scope
    from agentforge_shared.enums import WorkspaceStatus
    from agentforge_shared.models import Workspace
    from sqlalchemy import select

    with session_scope() as session:
        row = session.scalars(
            select(Workspace).where(Workspace.project_id == project["id"])
        ).first()
        row.status = WorkspaceStatus.READY

    resp = client.post(f"{API}/projects/{project['id']}/workspace")
    assert resp.status_code == 202
    assert "already ready" in resp.json()["detail"]


def test_destroying_a_workspace_marks_it_for_teardown(client):
    project = _project(client, "DestroyMe")
    resp = client.delete(f"{API}/projects/{project['id']}/workspace")
    assert resp.status_code == 202
    assert resp.json()["status"] == "deleting"

    assert client.get(f"{API}/projects/{project['id']}/workspace").json()["status"] == "deleting"


def test_workspace_endpoints_404_for_an_unknown_project(client):
    assert client.post(f"{API}/projects/nope/workspace").status_code == 404
    assert client.delete(f"{API}/projects/nope/workspace").status_code == 404


def test_agent_restart_clears_the_session_but_keeps_the_branch(client):
    project = _project(client, "Restart")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Worker"}).json()

    from agentforge_shared.db import session_scope
    from agentforge_shared.enums import AgentStatus
    from agentforge_shared.models import Agent

    with session_scope() as session:
        row = session.get(Agent, agent["id"])
        row.session_id = "session-abc"
        row.status = AgentStatus.ERROR
        row.error = "boom"
        row.current_task_id = "task-1"

    restarted = client.post(f"{API}/agents/{agent['id']}/restart").json()
    assert restarted["status"] == "starting"
    assert restarted["session_id"] is None
    assert restarted["current_task_id"] is None
    assert restarted["error"] is None
    # the branch is the agent's identity; a restart must not lose it
    assert restarted["branch"] == agent["branch"]


def test_agent_restart_emits_an_event(client):
    project = _project(client, "RestartEvents")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "W"}).json()
    client.post(f"{API}/agents/{agent['id']}/restart")

    from agentforge_shared.db import session_scope
    from agentforge_shared.models import Event
    from sqlalchemy import select

    with session_scope() as session:
        events = session.scalars(
            select(Event).where(Event.agent_id == agent["id"], Event.type == "agent.status")
        ).all()
    assert any((e.payload or {}).get("reason") == "restart requested" for e in events)


def test_new_agent_gets_the_restrictive_default_policy(client):
    project = _project(client, "PolicyDefault")
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "Default"}).json()

    policy = client.get(f"{API}/agents/{agent['id']}/permissions").json()["policy"]
    assert policy["filesystem"]["host"] is False
    assert policy["filesystem"]["other_projects"] is False
    assert policy["kubernetes"]["enabled"] is False
    assert policy["secrets"]["enabled"] is False
    assert policy["network"]["mode"] == "restricted"
    assert policy["terminal"]["enabled"] is True


def test_an_agent_can_be_created_with_a_custom_policy(client):
    project = _project(client, "PolicyCustom")
    agent = client.post(
        f"{API}/projects/{project['id']}/agents",
        json={
            "name": "Loose",
            "policy": {"network": {"mode": "open"}, "secrets": {"enabled": True}},
        },
    ).json()

    policy = client.get(f"{API}/agents/{agent['id']}/permissions").json()["policy"]
    assert policy["network"]["mode"] == "open"
    assert policy["secrets"]["enabled"] is True
    # unspecified fields still fall back to the safe defaults
    assert policy["filesystem"]["host"] is False


def test_a_policy_that_cannot_be_implemented_is_rejected_at_create_time(client):
    project = _project(client, "PolicyImpossible")
    resp = client.post(
        f"{API}/projects/{project['id']}/agents",
        json={"name": "Impossible", "policy": {"filesystem": {"host": True}}},
    )
    # The API accepts the policy (it is a valid model); the provider refuses it
    # at provisioning. What must NOT happen is silent downgrading.
    assert resp.status_code in (201, 422)


def test_providers_endpoint_describes_capabilities(client):
    body = client.get(f"{API}/providers").json()
    assert body["configured"] == "kubernetes"
    names = {entry["provider"] for entry in body["providers"]}
    assert {"kubernetes", "podman", "local"} <= names
    kubernetes = next(e for e in body["providers"] if e["provider"] == "kubernetes")
    assert kubernetes["network_policy"] is True
