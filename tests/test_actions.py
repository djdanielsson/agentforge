"""Semantic actions: review / test / deploy, and the agent summary."""

from __future__ import annotations

API = "/api/v1"


def _project_with_agent(client, name="Actor"):
    project = client.post(f"{API}/projects", json={"name": name}).json()
    agent = client.post(f"{API}/projects/{project['id']}/agents", json={"name": "coding"}).json()
    return project, agent


def test_review_queues_a_review_kind_task(client):
    project, _ = _project_with_agent(client, "ReviewMe")

    resp = client.post(f"{API}/projects/{project['id']}/review", json={"branch": "agent/x"})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["kind"] == "review"
    assert "agent/x" in task["description"]


def test_test_endpoint_defaults_to_the_project_test_command(client):
    project, _ = _project_with_agent(client, "TestMe")

    task = client.post(f"{API}/projects/{project['id']}/test", json={}).json()
    assert task["kind"] == "test"
    assert "make test" in task["description"]


def test_test_endpoint_accepts_an_explicit_command(client):
    project, _ = _project_with_agent(client, "CustomTest")

    task = client.post(f"{API}/projects/{project['id']}/test", json={"command": "pytest -q"}).json()
    assert "pytest -q" in task["description"]


def test_deploy_endpoint_captures_the_environment(client):
    project, _ = _project_with_agent(client, "DeployMe")

    task = client.post(
        f"{API}/projects/{project['id']}/deploy", json={"environment": "prod"}
    ).json()
    assert task["kind"] == "deploy"
    assert "prod" in task["description"]


def test_agent_summary_reports_the_current_task(client):
    project, agent = _project_with_agent(client, "Summarise")
    client.post(f"{API}/agents/{agent['id']}/tasks", json={"prompt": "Fix the auth bug"})

    # simulate the orchestrator picking it up
    from agentforge_shared.db import session_scope
    from agentforge_shared.enums import AgentStatus
    from agentforge_shared.models import Agent, Task
    from sqlalchemy import select

    with session_scope() as session:
        row = session.get(Agent, agent["id"])
        task = session.scalars(select(Task).where(Task.agent_id == agent["id"])).first()
        row.status = AgentStatus.WORKING
        row.current_task_id = task.id

    summary = client.get(f"{API}/agents/{agent['id']}/summary").json()
    assert summary["status"] == "working"
    assert summary["task"] == "Fix the auth bug"
    assert summary["agent_id"] == agent["id"]
    assert summary["tests"] is None


def test_agent_summary_surfaces_a_blocked_question(client):
    project, agent = _project_with_agent(client, "Blocker")

    from agentforge_shared.db import session_scope
    from agentforge_shared.enums import AgentStatus, EventType
    from agentforge_shared.events import record_event
    from agentforge_shared.models import Agent

    with session_scope() as session:
        row = session.get(Agent, agent["id"])
        row.status = AgentStatus.BLOCKED
        record_event(
            session,
            type=EventType.AGENT_WAITING,
            project_id=project["id"],
            agent_id=agent["id"],
            payload={"question": "JWT or session cookies?"},
        )

    summary = client.get(f"{API}/agents/{agent['id']}/summary").json()
    assert summary["blocked_reason"] == "JWT or session cookies?"
    assert summary["question"] == "JWT or session cookies?"


def test_event_catalog_is_discoverable(client):
    catalog = client.get(f"{API}/events/catalog").json()
    types = {entry["type"] for entry in catalog}
    for expected in (
        "agent.started",
        "agent.permission_required",
        "permission.granted",
        "workspace.created",
        "test.completed",
        "commit.created",
    ):
        assert expected in types
