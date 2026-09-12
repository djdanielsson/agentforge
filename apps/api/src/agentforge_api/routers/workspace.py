"""Workspace files and the terminal — the native editor, proxied.

The dashboard can never reach a workspace directly: `code_server_url` is an
in-cluster Service name that no browser can resolve, which is why embedding it in
an iframe showed nothing. Everything goes through the API instead, which execs
into the workspace pod, and that is also what lets the editor and the terminal be
part of the app rather than a second app in a frame.

Every path is resolved inside `/workspace`; anything that would escape it is
refused rather than normalised away.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import posixpath
import shlex
import threading

from agentforge_shared.db import session_factory
from agentforge_shared.enums import WorkspaceStatus
from agentforge_shared.models import Project
from agentforge_shared.schemas import FileWriteRequest
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from ..deps import DbSession

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])

#: The only tree an agent may touch, and therefore the only tree the editor may.
WORKSPACE_ROOT = "/workspace"

#: One write per request, with the content travelling as a command argument, so
#: this stays well clear of the kernel's argument limit.
MAX_WRITE_BYTES = 1_000_000

#: `find -printf` emits one record per line; anything else means the listing is
#: unusable and the caller should see the raw output rather than a wrong list.
_LIST_FORMAT = "%y\\t%s\\t%f"


def _workspace(session, project_id: str):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    workspace = project.workspace
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not provisioned")
    if workspace.status != WorkspaceStatus.READY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"workspace is {workspace.status}; the editor is unavailable until it is ready",
        )
    return project, workspace


def _exec(workspace, command: list[str]) -> str:
    """Run a command inside the workspace pod. Imported lazily so the API can
    start without a kubeconfig present."""
    from agentforge_workspaces.providers.kubernetes import exec_in_workspace

    return exec_in_workspace(workspace.namespace, workspace.pod_name, command)


def _safe_path(raw: str | None, *, default: str = WORKSPACE_ROOT) -> str:
    """Resolve a client-supplied path inside the workspace, or refuse it."""
    path = (raw or "").strip() or default
    if not path.startswith("/"):
        path = f"{WORKSPACE_ROOT}/{path.lstrip('/')}"
    normalised = posixpath.normpath(path)
    if normalised != WORKSPACE_ROOT and not normalised.startswith(f"{WORKSPACE_ROOT}/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path escapes the workspace")
    return normalised


@router.get("/files")
def list_files(session: DbSession, project_id: str, path: str | None = None) -> dict:
    """One directory level, directories first, the way a file tree expects it."""
    _, workspace = _workspace(session, project_id)
    target = _safe_path(path)

    out = _exec(
        workspace,
        [
            "sh",
            "-c",
            f"find {shlex.quote(target)} -mindepth 1 -maxdepth 1 -printf '{_LIST_FORMAT}\\n'",
        ],
    )

    entries = []
    for line in out.splitlines():
        kind, _, rest = line.partition("\t")
        size, _, name = rest.partition("\t")
        if not name:
            continue
        entries.append(
            {
                "name": name,
                "path": posixpath.join(target, name),
                "type": {"d": "dir", "f": "file", "l": "symlink"}.get(kind, "other"),
                "size": int(size) if size.isdigit() else 0,
            }
        )
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    return {"path": target, "root": WORKSPACE_ROOT, "entries": entries}


@router.get("/file")
def read_file(session: DbSession, project_id: str, path: str) -> dict:
    """A file's content, base64 on the wire, decoded when it is really text."""
    _, workspace = _workspace(session, project_id)
    target = _safe_path(path)

    encoded = _exec(workspace, ["sh", "-c", f"base64 -w0 -- {shlex.quote(target)}"]).strip()
    raw = base64.b64decode(encoded) if encoded else b""

    try:
        content, encoding = raw.decode("utf-8"), "utf8"
    except UnicodeDecodeError:
        # Binary: hand the bytes over as-is so the editor can refuse to show
        # them rather than showing mojibake.
        content, encoding = encoded, "base64"
    return {"path": target, "size": len(raw), "encoding": encoding, "content": content}


@router.put("/file")
def write_file(session: DbSession, project_id: str, payload: FileWriteRequest) -> dict:
    _, workspace = _workspace(session, project_id)
    target = _safe_path(payload.path)

    data = (
        payload.content.encode("utf-8")
        if payload.encoding == "utf8"
        else base64.b64decode(payload.content)
    )
    if len(data) > MAX_WRITE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"file is larger than the {MAX_WRITE_BYTES} byte editor limit",
        )

    encoded = base64.b64encode(data).decode()
    parent = posixpath.dirname(target) or WORKSPACE_ROOT
    command = (
        f"mkdir -p -- {shlex.quote(parent)} && "
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(target)}"
    )
    _exec(workspace, ["sh", "-c", command])
    return {"path": target, "size": len(data)}


@router.websocket("/terminal")
async def terminal(websocket: WebSocket, project_id: str, shell: str = "bash") -> None:
    """A shell in the workspace pod, streamed to the browser.

    The Kubernetes exec stream is blocking, so it is pumped from a thread into an
    asyncio queue; keystrokes go the other way on the event loop.
    """
    with session_factory() as session:
        project = session.get(Project, project_id)
        workspace = project.workspace if project is not None else None
        if workspace is None or workspace.status != WorkspaceStatus.READY:
            await websocket.close(code=4404)
            return
        namespace, pod_name = workspace.namespace, workspace.pod_name

    from agentforge_workspaces.providers.kubernetes import stream_in_workspace

    await websocket.accept()

    try:
        handle = stream_in_workspace(
            namespace,
            pod_name,
            command=["/bin/sh", "-c", f"command -v {shell} >/dev/null && exec {shell} || exec sh"],
        )
    except Exception as exc:  # noqa: BLE001 - report, then hang up cleanly
        with contextlib.suppress(Exception):
            await websocket.send_text(f"\r\ncould not open a shell: {exc}\r\n")
            await websocket.close(code=1011)
        return

    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue[str | None] = asyncio.Queue()

    def pump() -> None:
        """Blocking reader: the exec websocket until it closes."""
        try:
            while handle.is_open():
                handle.update(timeout=1)
                if handle.peek_stdout():
                    loop.call_soon_threadsafe(outbox.put_nowait, handle.read_stdout())
                if handle.peek_stderr():
                    loop.call_soon_threadsafe(outbox.put_nowait, handle.read_stderr())
        except Exception:  # noqa: BLE001 - a dead stream is a closed terminal
            pass
        finally:
            loop.call_soon_threadsafe(outbox.put_nowait, None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    async def drain() -> None:
        while True:
            chunk = await outbox.get()
            if chunk is None:
                break
            await websocket.send_text(chunk)
        with contextlib.suppress(Exception):
            await websocket.close()

    writer_task = asyncio.create_task(drain())
    try:
        while True:
            handle.write_stdin(await websocket.receive_text())
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - transport level
        pass
    finally:
        writer_task.cancel()
        with contextlib.suppress(Exception):
            handle.close()
