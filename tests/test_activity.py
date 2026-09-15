"""The dashboard's view of what an agent is doing.

The stream is OpenHands' vocabulary and the panel is ours, so the translation is
pinned here: an action that gets dropped is exactly the bug this feature exists
to fix, and it fails silently.
"""

from __future__ import annotations

import pytest
from agentforge_agent_server.models import AgentEvent


@pytest.fixture()
def activity():
    return pytest.importorskip("agentforge_api.activity")


def _event(**payload) -> AgentEvent:
    return AgentEvent.from_payload(payload)


def test_a_command_reads_as_the_command_itself(activity):
    item = activity.summarise(
        _event(
            id=3,
            source="agent",
            action="run",
            args={"command": "pytest -q"},
            timestamp="2026-01-01T00:00:00Z",
        )
    )

    assert item["kind"] == "command"
    assert item["title"] == "$ pytest -q"
    assert item["source"] == "agent"


def test_command_output_keeps_the_exit_code_that_was_hidden(activity):
    """A failed command is the thing a person most needs to see."""
    item = activity.summarise(
        _event(
            id=4,
            source="agent",
            observation="run",
            content="1 failed, 2 passed",
            extras={"exit_code": 1},
        )
    )

    assert item["kind"] == "output"
    assert item["ok"] is False
    assert "1 failed" in item["detail"]


def test_an_edit_says_which_file_and_what_happened_to_it(activity):
    created = activity.summarise(
        _event(action="edit", args={"command": "create", "path": "src/app.py"})
    )
    edited = activity.summarise(
        _event(action="edit", args={"command": "str_replace", "path": "src/app.py"})
    )

    assert created["title"] == "created src/app.py"
    assert edited["title"] == "edited src/app.py"


def test_the_agents_own_state_is_visible(activity):
    """`running` versus `awaiting_user_input` is the difference between waiting
    and being stuck, and it was previously not shown anywhere."""
    item = activity.summarise(
        _event(
            id=9,
            source="agent",
            observation="agent_state_changed",
            extras={"agent_state": "running"},
        )
    )

    assert item["kind"] == "state"
    assert item["title"] == "running"


def test_an_error_is_not_dropped(activity):
    item = activity.summarise(_event(observation="error", content="command not found: uv"))

    assert item["kind"] == "error"
    assert item["ok"] is False
    assert "command not found" in item["title"]


def test_long_output_is_clipped_rather_than_flooding_the_panel(activity):
    item = activity.summarise(
        _event(observation="run", content="x" * 5000, extras={"exit_code": 0})
    )

    assert len(item["detail"]) < 5000
    assert "more characters" in item["detail"]


def test_an_unknown_event_is_dropped_rather_than_guessed_at(activity):
    assert activity.summarise(_event(observation="something_we_have_never_seen")) is None
    assert activity.summarise_all(
        [_event(observation="unknown"), _event(action="run", args={"command": "ls"})]
    ) == [activity.summarise(_event(action="run", args={"command": "ls"}))]


# --- the endpoint -------------------------------------------------------------


def test_activity_is_empty_before_the_agent_has_a_session(client):
    project = client.post(
        "/api/v1/projects", json={"name": "ActivityOne", "default_branch": "main"}
    ).json()
    agent = client.post(f"/api/v1/projects/{project['id']}/agents", json={"name": "Worker"}).json()

    body = client.get(f"/api/v1/agents/{agent['id']}/activity").json()

    assert body["session"] is None
    assert body["activity"] == []


def test_an_unreachable_workspace_is_reported_not_raised(client, monkeypatch):
    """A workspace mid-restart is an ordinary state, not a 500."""
    from agentforge_shared.db import session_scope
    from agentforge_shared.models import Agent

    project = client.post(
        "/api/v1/projects", json={"name": "ActivityTwo", "default_branch": "main"}
    ).json()
    agent = client.post(f"/api/v1/projects/{project['id']}/agents", json={"name": "Worker"}).json()
    with session_scope() as session:
        row = session.get(Agent, agent["id"])
        row.session_id = "deadbeef"

    class _Unreachable:
        def __enter__(self):
            raise ConnectionError("connection refused")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "agentforge_api.routers.agents.client_for", lambda url, api_key=None: _Unreachable()
    )

    body = client.get(f"/api/v1/agents/{agent['id']}/activity").json()

    assert body["session"] == "deadbeef"
    assert body["activity"] == []
    assert "connection refused" in body["unavailable"]
