"""The agent's actions, as the dashboard shows them.

Our own tables hold turns: what was *said*. Between two turns an agent runs
commands, edits files and reads code, and none of that was visible anywhere — a
task appeared in the queue and later a result arrived, with the work in between
invisible. The agent server is the only place that stream exists, so the
dashboard reads it from there and this module turns it into something a person
can scan.

The mapping is deliberately one-way and lossy: OpenHands' vocabulary is much
bigger than ours, and an unrecognised event is dropped rather than guessed at.
"""

from __future__ import annotations

from typing import Any

from agentforge_agent_server.client import AgentServerClient

#: Nobody reads past a screen of command output in a sidebar, and the raw stream
#: can carry whole files.
DETAIL_LIMIT = 2000

#: How the file-editing action names itself in its arguments.
_EDIT_VERBS = {
    "create": "created",
    "str_replace": "edited",
    "insert": "edited",
    "undo_edit": "reverted",
}


def client_for(url: str, *, api_key: str | None = None) -> AgentServerClient:
    """A client for one workspace's agent server.

    Separate from the call site so tests can replace it without a live server.
    """
    return AgentServerClient(url, api_key=api_key, timeout=20.0)


def _clip(text: Any) -> str | None:
    if text is None:
        return None
    text = str(text)
    if len(text) <= DETAIL_LIMIT:
        return text or None
    return text[:DETAIL_LIMIT] + f"\n… ({len(text) - DETAIL_LIMIT} more characters)"


def summarise(event: Any) -> dict[str, Any] | None:
    """One normalised activity item, or None for something we do not show.

    `event` is an `AgentEvent`; its `raw` payload is the agent server's own.
    """
    raw: dict[str, Any] = getattr(event, "raw", None) or {}
    action = str(raw.get("action") or "")
    observation = str(raw.get("observation") or "")
    args = raw.get("args") or {}
    extras = raw.get("extras") or {}
    content = raw.get("message") or raw.get("content") or ""

    kind: str | None = None
    title: str | None = None
    detail: str | None = None
    ok: bool | None = None

    # --- what the agent did ---
    if action == "message":
        kind, detail = "message", _clip(content)
    elif action == "run":
        kind, title = "command", f"$ {str(args.get('command') or '').strip()}"
    elif action == "edit":
        verb = _EDIT_VERBS.get(str(args.get("command") or ""), "edited")
        kind, title = "edit", f"{verb} {args.get('path') or 'a file'}"
    elif action == "read":
        kind, title = "read", f"read {args.get('path') or 'a file'}"
    elif action in ("browse", "browse_interactive"):
        kind, title = "browse", f"opened {args.get('url') or 'a page'}"
    elif action == "think":
        kind, detail = "thought", _clip(args.get("thought") or content)
    elif action == "finish":
        kind, detail = "finished", _clip(args.get("message") or content or "finished")
    elif action == "delegate":
        kind, title = "delegated", f"handed to {args.get('agent') or 'another agent'}"
    elif action == "reject":
        kind, title = "rejected", str(args.get("reason") or "rejected a suggestion")

    # --- what came back ---
    elif observation == "run":
        exit_code = extras.get("exit_code")
        ok = None if exit_code is None else int(exit_code) == 0
        label = f"exit {exit_code}" if exit_code is not None else "output"
        kind, title, detail = "output", label, _clip(content)
    elif observation == "edit":
        kind, title, detail = "edit-result", "edit applied", _clip(content)
    elif observation == "read":
        kind, title = "read-result", f"read {args.get('path') or 'a file'}"
    elif observation == "browse":
        kind, detail = "browse-result", _clip(content or str(extras.get("url") or ""))
    elif observation == "agent_state_changed":
        state = str(extras.get("agent_state") or content)
        # `running`, `awaiting_user_input`, `finished`, `loading` — the agent's
        # own view of itself, which is what makes a long task legible.
        kind, title = "state", state
    elif observation == "task_tracking":
        kind, detail = "plan", _clip(content)
    elif observation == "error":
        kind = "error"
        title = _clip(content) or "error"
        detail, ok = _clip(extras.get("error")), False

    if kind is None:
        return None
    return {
        "id": raw.get("id"),
        "at": raw.get("timestamp"),
        "source": raw.get("source"),
        "kind": kind,
        "title": title,
        "detail": detail,
        "ok": ok,
    }


def summarise_all(events: list[Any]) -> list[dict[str, Any]]:
    return [item for item in (summarise(event) for event in events) if item is not None]
