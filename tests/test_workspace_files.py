"""Workspace files and the terminal bridge.

The exec helpers are patched out: these tests pin the wire contract — how a
listing is parsed, how a file round-trips, and that keystrokes reach the pod —
not the cluster.
"""

from __future__ import annotations

API = "/api/v1"


def _ready_project(client, name: str = "Editor"):
    """A project whose workspace the orchestrator would have marked ready."""
    project = client.post(f"{API}/projects", json={"name": name}).json()

    from agentforge_shared.db import session_scope
    from agentforge_shared.enums import WorkspaceStatus
    from agentforge_shared.models import Workspace
    from sqlalchemy import select

    with session_scope() as session:
        row = session.scalars(
            select(Workspace).where(Workspace.project_id == project["id"])
        ).first()
        row.status = WorkspaceStatus.READY
    return project


def _patch_exec(monkeypatch, output: str = ""):
    """Capture the commands the API would run inside the pod."""
    calls: list[list[str]] = []

    def fake(namespace: str, pod_name: str, command: list[str], **kwargs):
        calls.append(command)
        return output

    monkeypatch.setattr("agentforge_workspaces.providers.kubernetes.exec_in_workspace", fake)
    return calls


def test_listing_parses_find_output_with_directories_first(client, monkeypatch):
    project = _ready_project(client)
    _patch_exec(monkeypatch, "f\t42\tREADME.md\nd\t0\tsrc\nf\t7\tmain.py\n")

    body = client.get(f"{API}/projects/{project['id']}/files").json()

    # Directories first, then names case-insensitively: main.py sorts before
    # README.md, which is what a file tree should show.
    assert [entry["name"] for entry in body["entries"]] == ["src", "main.py", "README.md"]
    assert body["entries"][0] == {
        "name": "src",
        "path": "/workspace/src",
        "type": "dir",
        "size": 0,
    }
    assert body["root"] == "/workspace"


def test_a_relative_path_is_resolved_into_the_workspace(client, monkeypatch):
    project = _ready_project(client)
    calls = _patch_exec(monkeypatch, "")

    client.get(f"{API}/projects/{project['id']}/files?path=src")

    assert "/workspace/src" in calls[0][-1]


def test_text_files_come_back_decoded(client, monkeypatch):
    project = _ready_project(client)
    _patch_exec(monkeypatch, "aGVsbG8gd29ybGQK")  # base64 of "hello world\n"

    body = client.get(f"{API}/projects/{project['id']}/file?path=/workspace/note.txt").json()

    assert body == {
        "path": "/workspace/note.txt",
        "size": 12,
        "encoding": "utf8",
        "content": "hello world\n",
    }


def test_binary_files_stay_base64(client, monkeypatch):
    project = _ready_project(client)
    _patch_exec(monkeypatch, "/wAB")  # base64 of b"\xff\x00\x01"

    body = client.get(f"{API}/projects/{project['id']}/file?path=/workspace/blob").json()

    assert body["encoding"] == "base64"
    assert body["content"] == "/wAB"


def test_a_write_creates_the_parent_and_round_trips_the_bytes(client, monkeypatch):
    project = _ready_project(client)
    calls = _patch_exec(monkeypatch, "")

    resp = client.put(
        f"{API}/projects/{project['id']}/file",
        json={"path": "src/app.py", "content": "print('hi')\n"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "/workspace/src/app.py", "size": 12}
    command = calls[0][-1]
    assert "mkdir -p -- /workspace/src" in command
    assert "base64 -d > /workspace/src/app.py" in command


def test_paths_that_escape_the_workspace_are_refused(client, monkeypatch):
    project = _ready_project(client)
    _patch_exec(monkeypatch, "")

    for path in ("/etc/passwd", "../secrets", "/workspace/../../etc/shadow"):
        resp = client.get(f"{API}/projects/{project['id']}/file?path={path}")
        assert resp.status_code == 400, path


def test_a_write_larger_than_the_editor_limit_is_refused(client, monkeypatch):
    project = _ready_project(client)
    _patch_exec(monkeypatch, "")

    resp = client.put(
        f"{API}/projects/{project['id']}/file",
        json={"path": "/workspace/big.bin", "content": "x" * 1_000_001},
    )

    assert resp.status_code == 413


def test_the_editor_is_unavailable_until_the_workspace_is_ready(client, monkeypatch):
    project = client.post(f"{API}/projects", json={"name": "NotReady"}).json()
    _patch_exec(monkeypatch, "")

    resp = client.get(f"{API}/projects/{project['id']}/files")

    assert resp.status_code == 503
    assert "workspace is" in resp.json()["detail"]


class _FakeHandle:
    """Stand-in for the Kubernetes exec websocket."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = list(chunks)
        self.written: list[str] = []
        self.closed = False

    def is_open(self) -> bool:
        return bool(self.chunks)

    def update(self, timeout: int = 1) -> bool:
        return True

    def peek_stdout(self) -> bool:
        return bool(self.chunks)

    def read_stdout(self) -> str:
        return self.chunks.pop(0)

    def peek_stderr(self) -> bool:
        return False

    def read_stderr(self) -> str:
        return ""

    def write_stdin(self, data: str) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


def test_the_terminal_streams_output_and_forwards_keystrokes(client, monkeypatch):
    project = _ready_project(client, "Terminal")
    handle = _FakeHandle(["$ ", "hello\r\n"])
    monkeypatch.setattr(
        "agentforge_workspaces.providers.kubernetes.stream_in_workspace",
        lambda *args, **kwargs: handle,
    )

    with client.websocket_connect(f"{API}/projects/{project['id']}/terminal") as socket:
        assert socket.receive_text() == "$ "
        assert socket.receive_text() == "hello\r\n"
        socket.send_text("ls -la\n")

    assert handle.written == ["ls -la\n"]
