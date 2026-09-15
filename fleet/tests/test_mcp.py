"""The MCP surface (docs/T3-INTEGRATION.md §A).

T3 Code has no plugin API, so "drive fleet from T3" means "T3's agent session
calls these tools". These tests are about the contract an MCP client actually
depends on: the handshake, the tool list, and a tool failure being readable
rather than fatal.

The transport in these tests is Streamable HTTP over the real FastAPI app, which
is the same path an MCP client takes — the endpoint was shaped by observing
OpenCode 1.18.31's MCP client (docs/T3-INTEGRATION.md §A), not guessed.
"""

from __future__ import annotations

import json

REQUIRED_TOOLS = {
    "list_projects",
    "create_project",
    "list_agents",
    "submit_task",
    "task_status",
    "workspace_status",
}


def rpc(client, method, params=None, ident=1, headers=None):
    body = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


def test_initialize_handshake(client):
    response = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {}},
            "clientInfo": {"name": "opencode", "version": "1.18.31"},
        },
        ident=0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 0
    result = body["result"]
    # The client asked for a revision; a server that knows it must echo it back.
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "fleet-control-plane"
    assert "tools" in result["capabilities"]


def test_initialize_negotiates_down_to_a_known_revision(client):
    body = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()
    assert body["result"]["protocolVersion"] == "2025-06-18"


def test_initialized_notification_is_accepted_without_a_body(client):
    """The client sends this and does not read a reply; 202 is the contract."""
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response.status_code == 202
    assert response.content == b""


def test_tools_list_exposes_the_fleet_operations(client):
    body = rpc(client, "tools/list").json()
    tools = {tool["name"]: tool for tool in body["result"]["tools"]}
    assert set(tools) >= REQUIRED_TOOLS
    for name, tool in tools.items():
        assert tool["description"], f"{name} has no description for the model"
        assert tool["inputSchema"]["type"] == "object"
    # The two that mutate are the two that need arguments.
    assert tools["create_project"]["inputSchema"]["required"] == ["name"]
    assert tools["submit_task"]["inputSchema"]["required"] == ["project", "prompt"]


def test_a_tool_call_runs_against_the_control_plane(client, fake_provider, clean_db):
    created = rpc(
        client,
        "tools/call",
        {"name": "create_project", "arguments": {"name": "mcp-alpha"}},
    ).json()
    assert "isError" not in created["result"], created
    payload = json.loads(created["result"]["content"][0]["text"])
    assert payload["name"] == "mcp-alpha"
    assert payload["workspace"]["provider"] == "fake"

    listed = rpc(client, "tools/call", {"name": "list_projects", "arguments": {}}).json()
    payload = json.loads(listed["result"]["content"][0]["text"])
    assert [p["name"] for p in payload["items"]] == ["mcp-alpha"]
    assert payload["count"] == 1

    # The workspace view is the same data the HTTP API returns.
    workspace = rpc(
        client, "tools/call", {"name": "workspace_status", "arguments": {"project": "mcp-alpha"}}
    ).json()
    payload = json.loads(workspace["result"]["content"][0]["text"])
    assert payload["provider"] == "fake"


def test_a_tool_failure_is_a_readable_result_not_a_transport_error(client, clean_db):
    """The caller is a model: it must be able to read the failure and adapt."""
    body = rpc(
        client, "tools/call", {"name": "task_status", "arguments": {"task": "tsk_nope"}}
    ).json()
    assert body["result"]["isError"] is True
    assert "tsk_nope" in body["result"]["content"][0]["text"]


def test_unknown_tools_and_methods_are_json_rpc_errors(client):
    body = rpc(client, "tools/call", {"name": "delete_everything", "arguments": {}}).json()
    assert body["result"]["isError"] is True
    assert "unknown tool" in body["result"]["content"][0]["text"]

    body = rpc(client, "resources/subscribe").json()
    assert body["error"]["code"] == -32601


def test_missing_required_arguments_are_reported_as_tool_errors(client, clean_db):
    body = rpc(client, "tools/call", {"name": "create_project", "arguments": {}}).json()
    assert body["result"]["isError"] is True
    assert "name" in body["result"]["content"][0]["text"]


def test_get_has_no_stream_and_says_so(client):
    """A server with no server-initiated stream answers 405 with `Allow`."""
    response = client.get("/mcp")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST, DELETE"


def test_mcp_requires_the_operator_token(client):
    """The endpoint is inside the same authentication boundary as the API."""
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": ""},
    )
    assert response.status_code == 401
