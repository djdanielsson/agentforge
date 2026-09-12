"""The Agent Server client.

Tested against a mock transport so the contract is pinned without needing a live
server. The paths and payload shapes are pinned against OpenHands 0.59, read from
its own `/openapi.json` — and that pinning matters, because every unknown path on
OpenHands answers with its single-page app and HTTP 200. A wrong path there is not
a 404 that shouts; it is an HTML body that looks like success.
"""

from __future__ import annotations

import json

import httpx
import pytest


@pytest.fixture()
def make_client():
    from agentforge_agent_server import AgentServerClient

    def _make(handler, **kwargs):
        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport, base_url="http://agent-server")
        return AgentServerClient("http://agent-server", client=client, **kwargs)

    return _make


def test_health_is_true_on_success(make_client):
    client = make_client(lambda request: httpx.Response(200, json="OK"))
    assert client.health() is True


def test_health_is_false_when_unreachable(make_client):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert make_client(handler).health() is False


def test_configure_model_posts_the_alias_and_the_gateway(make_client):
    """The alias is a server-level setting, so it is set before any conversation."""
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"llm_model": "smart"})

    make_client(handler).configure_model(
        model="smart", base_url="http://agentforge-llm:4000", api_key="sk-key"
    )

    assert captured["url"].endswith("/api/settings")
    assert captured["body"] == {
        "llm_model": "smart",
        "llm_base_url": "http://agentforge-llm:4000",
        "llm_api_key": "sk-key",
    }


def test_available_models_reads_the_gateway_list(make_client):
    client = make_client(lambda request: httpx.Response(200, json=["local-coder", "smart"]))
    assert client.available_models() == ["local-coder", "smart"]


def test_create_conversation_sends_repository_branch_and_instructions(make_client):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"conversation_id": "c-1"})

    from agentforge_agent_server import ConversationSpec

    created = make_client(handler).create_conversation(
        ConversationSpec(
            repository="https://github.com/example/cf",
            branch="agent/auth-api",
            instructions="You are Auth API.",
        )
    )

    assert created["conversation_id"] == "c-1"
    assert captured["url"].endswith("/api/conversations")
    assert captured["body"] == {
        "repository": "https://github.com/example/cf",
        "selected_branch": "agent/auth-api",
        "conversation_instructions": "You are Auth API.",
    }


def test_starting_an_already_started_conversation_is_not_an_error(make_client):
    """Creating a conversation can start it; starting it twice is not a failure."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(409, json={"detail": "already started"})
        return httpx.Response(200, json={"conversation_id": "c-1", "status": "running"})

    run = make_client(handler).start_conversation("c-1")

    assert run.session_id == "c-1"
    assert any(url.endswith("/api/conversations/c-1/start") for url in seen)
    assert any(url.endswith("/api/conversations/c-1") for url in seen)


def test_send_message_posts_the_turn_and_returns_nothing(make_client):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    assert make_client(handler).send_message("c-1", "add a health endpoint") is None
    assert captured["url"].endswith("/api/conversations/c-1/message")
    assert captured["body"] == {"message": "add a health endpoint"}


def test_events_read_a_bare_list_of_openhands_events(make_client):
    payload = [
        {"id": 1, "source": "user", "message": "add auth"},
        {"id": 2, "source": "agent", "action": {"message": "reading the router"}},
        {"id": 3, "source": "agent", "observation": {"content": "142 passed"}, "state": "finished"},
    ]
    client = make_client(lambda request: httpx.Response(200, json=payload))

    events = client.events("c-1")

    assert [event.type for event in events] == ["user", "agent", "agent"]
    assert events[1].content == "reading the router"
    assert events[2].content == "142 passed"
    assert events[2].finished is True


def test_events_ask_for_a_cursor_and_a_limit(make_client):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    make_client(handler).events("c-1", start_id=7, limit=25)

    assert "start_id=7" in seen["url"] and "limit=25" in seen["url"]


def test_wait_for_reply_follows_events_until_the_turn_ends(make_client):
    """A turn is not request/response: the message is queued, the reply is events."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=[{"id": 5, "source": "user", "message": "go"}])
        return httpx.Response(
            200,
            json=[{"id": 6, "source": "agent", "message": "done", "state": "finished"}],
        )

    event = make_client(handler).wait_for_reply("c-1", timeout=5, poll=0)

    assert event.content == "done"
    assert event.finished is True


def test_wait_for_reply_reports_an_error_event(make_client):
    client = make_client(
        lambda request: httpx.Response(
            200, json=[{"id": 1, "source": "agent", "error": "model unreachable"}]
        )
    )

    event = client.wait_for_reply("c-1", timeout=5, poll=0)

    assert event.error == "model unreachable"


def test_event_payload_tolerates_alternate_key_names():
    """Upstream naming varies between releases; the mapper must not be brittle."""
    from agentforge_agent_server import AgentEvent

    event = AgentEvent.from_payload({"event": "turn", "message": "hi", "done": True})
    assert event.type == "turn"
    assert event.content == "hi"
    assert event.finished is True


def test_list_files_reads_both_entry_shapes(make_client):
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "files": [
                    "src/",
                    {"path": "src/main.py", "size": 42},
                ]
            },
        )
    )

    entries = client.list_files("c-1")

    assert entries[0].path == "src/"
    assert entries[0].is_dir is True
    assert entries[1].path == "src/main.py"
    assert entries[1].size == 42


def test_read_file_uses_the_select_file_endpoint(make_client):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"code": "print('hi')"})

    assert make_client(handler).read_file("c-1", "src/main.py") == "print('hi')"
    assert "/api/conversations/c-1/select-file" in seen["url"]


def test_git_diff_and_changes_hit_their_own_endpoints(make_client):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"diff": "...", "changes": []})

    client = make_client(handler)
    assert client.git_diff("c-1") == "..."
    client.git_changes("c-1")

    assert any(url.endswith("/api/conversations/c-1/git/diff") for url in seen)
    assert any(url.endswith("/api/conversations/c-1/git/changes") for url in seen)


def test_vscode_url_is_returned(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"url": "http://vs/abc"}))
    assert client.vscode_url("c-1") == "http://vs/abc"


def test_client_errors_are_not_retryable(make_client):
    from agentforge_agent_server import AgentServerError

    client = make_client(lambda request: httpx.Response(400, json={"detail": "bad request"}))

    with pytest.raises(AgentServerError) as excinfo:
        client.events("c-1")

    assert excinfo.value.retryable is False


def test_transport_failures_are_retryable(make_client):
    from agentforge_agent_server import AgentServerUnavailable

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AgentServerUnavailable) as excinfo:
        make_client(handler).events("c-1")

    assert excinfo.value.retryable is True
