"""The Agent Server client.

Tested against a mock transport so the contract is pinned without needing a live
Agent Server. The endpoint shapes are the part most likely to drift upstream, so
these tests exist to make that drift loud rather than silent.
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
    client = make_client(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert client.health() is True


def test_health_is_false_when_unreachable(make_client):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)
    assert client.health() is False


def test_create_agent_sends_the_model_and_metadata(make_client):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "server-agent-1"})

    client = make_client(handler)
    from agentforge_agent_server import AgentSpec

    result = client.create_agent(
        AgentSpec(
            name="Auth API",
            model="smart",
            workspace_id="af-demo",
            metadata={"agentforge_agent_id": "a1"},
        )
    )
    assert result["id"] == "server-agent-1"
    assert captured["body"]["llm_model"] == "smart"
    assert captured["body"]["workspace_id"] == "af-demo"
    assert captured["body"]["metadata"]["agentforge_agent_id"] == "a1"


def test_start_conversation_requires_a_session_id(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"ok": True}))
    from agentforge_agent_server import AgentServerError

    with pytest.raises(AgentServerError, match="session id"):
        client.start_conversation("server-agent-1")


def test_start_conversation_returns_the_run(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"session_id": "s-1"}))
    run = client.start_conversation("server-agent-1")
    assert run.session_id == "s-1"


def test_send_message_maps_a_plain_reply(make_client):
    client = make_client(
        lambda request: httpx.Response(200, json={"type": "message", "content": "done"})
    )
    event = client.send_message("s-1", "add a health endpoint")
    assert event.content == "done"
    assert event.blocked is False
    assert event.finished is False


def test_send_message_surfaces_a_blocking_question(make_client):
    client = make_client(
        lambda request: httpx.Response(
            200, json={"blocked": True, "question": "JWT or session cookies?"}
        )
    )
    event = client.send_message("s-1", "add auth")
    assert event.blocked is True
    assert event.question == "JWT or session cookies?"


def test_send_message_surfaces_a_permission_request(make_client):
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "permission_request": {
                    "request_id": "req-1",
                    "command": "npm install stripe",
                    "reason": "add the billing SDK",
                }
            },
        )
    )
    event = client.send_message("s-1", "add billing")
    assert event.permission_request["command"] == "npm install stripe"


def test_send_message_surfaces_an_error(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"error": "model unreachable"}))
    event = client.send_message("s-1", "do work")
    assert event.error == "model unreachable"


def test_event_payload_tolerates_alternate_key_names():
    """Upstream naming varies between releases; the mapper must not be brittle."""
    from agentforge_agent_server import AgentEvent

    event = AgentEvent.from_payload({"event": "turn", "message": "hi", "done": True})
    assert event.type == "turn"
    assert event.content == "hi"
    assert event.finished is True


def test_get_events_reads_a_bare_list(make_client):
    client = make_client(lambda request: httpx.Response(200, json=[{"type": "a"}, {"type": "b"}]))
    assert [e.type for e in client.get_events("s-1")] == ["a", "b"]


def test_get_events_reads_an_envelope(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"items": [{"type": "a"}]}))
    assert [e.type for e in client.get_events("s-1")] == ["a"]


def test_file_read_and_write_round_trip(make_client):
    seen = {}

    def handler(request):
        seen[request.method] = str(request.url)
        if request.method == "GET":
            return httpx.Response(200, json={"content": "print('hi')"})
        return httpx.Response(200, json={})

    client = make_client(handler)
    assert client.read_file("ws-1", "src/main.py") == "print('hi')"
    client.write_file("ws-1", "src/main.py", "print('bye')")
    assert "files/src/main.py" in seen["GET"]
    assert "files/src/main.py" in seen["PUT"]


def test_git_operation_posts_the_operation_name(make_client):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"diff": "..."})

    client = make_client(handler)
    client.git("ws-1", "diff", base="main")
    assert "/git/diff" in captured["url"]


def test_exec_returns_output(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"output": "142 passed"}))
    assert client.exec("ws-1", "pytest -q") == "142 passed"


def test_vscode_url_is_returned(make_client):
    client = make_client(lambda request: httpx.Response(200, json={"url": "http://vs/abc"}))
    assert client.vscode_url("ws-1") == "http://vs/abc"


def test_client_errors_are_not_retryable(make_client):
    from agentforge_agent_server import AgentServerError

    client = make_client(lambda request: httpx.Response(400, text="bad request"))
    with pytest.raises(AgentServerError) as caught:
        client.get_agent("x")
    assert caught.value.retryable is False
    assert caught.value.status_code == 400


def test_server_errors_are_retryable(make_client):
    from agentforge_agent_server import AgentServerError

    client = make_client(lambda request: httpx.Response(503, text="down"))
    with pytest.raises(AgentServerError) as caught:
        client.get_agent("x")
    assert caught.value.retryable is True


def test_transport_failure_is_reported_as_unavailable(make_client):
    from agentforge_agent_server import AgentServerUnavailable

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_client(handler)
    with pytest.raises(AgentServerUnavailable, match="could not reach"):
        client.get_agent("x")


def test_base_url_is_normalised(make_client):
    from agentforge_agent_server import AgentServerClient

    client = AgentServerClient("http://agent-server/")
    assert client.base_url == "http://agent-server"
    client.close()
