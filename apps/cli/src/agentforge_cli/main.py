"""The `agentforge` CLI — talk to the AgentForge control plane.

agentforge projects
agentforge ask ComplianceFlow "Fix the authentication bug"
agentforge fleet
agentforge watch ComplianceFlow
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys

from .client import ApiError, WorkbenchClient

STATUS_GLYPH = {
    "ready": "\033[32m●\033[0m",
    "working": "\033[32m●\033[0m",
    "succeeded": "\033[32m●\033[0m",
    "idle": "\033[34m●\033[0m",
    "starting": "\033[33m●\033[0m",
    "queued": "\033[90m●\033[0m",
    "provisioning": "\033[33m●\033[0m",
    "cloning": "\033[33m●\033[0m",
    "blocked": "\033[33m●\033[0m",
    "awaiting_approval": "\033[33m●\033[0m",
    "pending": "\033[90m●\033[0m",
    "failed": "\033[31m●\033[0m",
    "error": "\033[31m●\033[0m",
    "cancelled": "\033[90m●\033[0m",
    "stopped": "\033[90m●\033[0m",
}


def dot(status: str) -> str:
    return STATUS_GLYPH.get(status, "\033[90m●\033[0m")


def emit(value, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str))


# --- commands ---------------------------------------------------------------


def cmd_projects(client: WorkbenchClient, args) -> int:
    if args.create:
        project = client.create_project(args.create, args.repo)
        print(f"created {project['name']} ({project['id']})")
        print(f"  workspace namespace: {project['workspace']['namespace']}")
        return 0

    projects = client.projects()
    if args.json:
        emit(projects, True)
        return 0
    if not projects:
        print("no projects")
        return 0
    for project in projects:
        print(f"  {dot(project['status'])} {project['name']:<24} {project['slug']}")
    return 0


def cmd_agents(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    agents = client.agents(project["id"])
    if args.json:
        emit(agents, True)
        return 0
    if not agents:
        print(f"{project['name']}: no agents")
        return 0
    for agent in agents:
        print(
            f"  {dot(agent['status'])} {agent['name']:<20} {agent['model']:<12} {agent['branch']}"
        )
    return 0


def cmd_fleet(client: WorkbenchClient, args) -> int:
    """'What are my agents doing?' — the whole point of the control plane."""
    projects = client.projects()
    rows = []
    for project in projects:
        for agent in client.agents(project["id"]):
            summary = client.agent_summary(agent["id"])
            rows.append((project, agent, summary))

    if args.json:
        emit([{"project": p["name"], "agent": a["name"], **s} for p, a, s in rows], True)
        return 0

    if not rows:
        print("no agents anywhere")
        return 0
    for project, agent, summary in rows:
        print(f"{project['name']} / {agent['name']}")
        print(
            f"  {dot(summary['status'])} {summary['status']}"
            + (f" — {summary['task']}" if summary["task"] else "")
        )
        if summary.get("progress"):
            print(f"    progress: {summary['progress']}")
        if summary.get("blocked_reason"):
            print(f"    ⚠ blocked: {summary['blocked_reason']}")
        if summary.get("question"):
            print(f"    ? {summary['question']}")
        if summary.get("tests"):
            tests = summary["tests"]
            print(
                f"    tests: {tests.get('passed', '?')} passed, {tests.get('failed', '?')} failed"
            )
        if summary.get("commits"):
            print(f"    commits: {', '.join(c[:8] for c in summary['commits'])}")
        print()
    return 0


def cmd_ask(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    agents = client.agents(project["id"])
    agent_id = agents[0]["id"] if agents else None
    if agent_id is None and not args.no_agent:
        agent = client.create_agent(project["id"], "coding")
        agent_id = agent["id"]
        print(f"created agent {agent['name']} on {agent['branch']}")
    task = client.create_task(project["id"], args.prompt, agent_id)
    print(f"queued task #{task['position']} ({task['id']})")
    print(f"  {task['description']}")
    return 0


def cmd_review(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    task = client.review(project["id"], args.branch)
    print(f"queued review ({task['id']}): {task['description']}")
    return 0


def cmd_test(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    task = client.test(project["id"], args.command)
    print(f"queued test run ({task['id']}): {task['description']}")
    return 0


def cmd_deploy(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    task = client.deploy(project["id"], args.environment, args.branch)
    print(f"queued deploy ({task['id']}): {task['description']}")
    return 0


def cmd_tasks(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    tasks = client.tasks(project["id"])
    if args.json:
        emit(tasks, True)
        return 0
    if not tasks:
        print(f"{project['name']}: no tasks")
        return 0
    for task in tasks:
        print(
            f"  {dot(task['status'])} #{task['position']:<3} "
            f"{task['kind']:<9} {task['description'][:70]}"
        )
    return 0


def cmd_diff(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    diff = client.git_diff(project["id"])
    print(diff["diff"] or "(no changes)")
    return 0


def cmd_webhooks(client: WorkbenchClient, args) -> int:
    if args.add:
        webhook = client.create_webhook(args.add, args.events)
        print(f"created webhook {webhook['id']}")
        print(f"  signing secret: {webhook['secret']}")
        return 0
    for webhook in client.webhooks():
        state = "active" if webhook["active"] else "inactive"
        print(f"  {webhook['id']}  {state:<8} {webhook['events']}  {webhook['url']}")
    return 0


def cmd_catalog(client: WorkbenchClient, args) -> int:
    for entry in client.event_catalog():
        print(f"  {entry['type']:<28} {entry['description']}")
    return 0


def cmd_keys(client: WorkbenchClient, args) -> int:
    key = client.create_key(args.name, args.scopes)
    print(f"created key {key['name']} ({key['id']})")
    print(f"  {key['key']}")
    print("  ^ store this now; it is not recoverable")
    return 0


async def _tail(url: str) -> None:
    import websockets

    async with websockets.connect(url) as socket:
        async for raw in socket:
            event = json.loads(raw)
            print(
                f"[{event.get('created_at', '')}] {event['type']} "
                f"{json.dumps(event.get('payload', {}))[:160]}"
            )


def cmd_watch(client: WorkbenchClient, args) -> int:
    project = client.find_project(args.project)
    base = client.base_url.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{base}/projects/{project['id']}/events"
    print(f"watching {project['name']} ({url}) — ctrl-c to stop")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_tail(url))
    return 0


# --- wiring -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AGENTFORGE_API_URL"),
        help="control plane base url (default $AGENTFORGE_API_URL or localhost)",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("AGENTFORGE_API_KEY"),
        help="api key (default $AGENTFORGE_API_KEY)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("projects", help="list or create projects")
    p.add_argument("--create")
    p.add_argument("--repo")
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser("agents", help="list a project's agents")
    p.add_argument("project")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("fleet", help="what is every agent doing?")
    p.set_defaults(func=cmd_fleet)

    p = sub.add_parser("ask", help="queue work for a project's agent")
    p.add_argument("project")
    p.add_argument("prompt")
    p.add_argument("--no-agent", action="store_true", help="queue unassigned")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("review", help="have an agent review a branch")
    p.add_argument("project")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("test", help="queue the project's test suite")
    p.add_argument("project")
    p.add_argument("--command")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("deploy", help="queue a deployment")
    p.add_argument("project")
    p.add_argument("environment", nargs="?", default="staging")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("tasks", help="list a project's tasks")
    p.add_argument("project")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("diff", help="show the project's working diff")
    p.add_argument("project")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("watch", help="tail a project's event stream")
    p.add_argument("project")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("webhooks", help="list or add webhook subscriptions")
    p.add_argument("--add", metavar="URL")
    p.add_argument("--events", nargs="*", default=["*"])
    p.set_defaults(func=cmd_webhooks)

    p = sub.add_parser("catalog", help="list the event catalog")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("keys", help="mint an API key")
    p.add_argument("name", nargs="?", default="cli")
    p.add_argument("--scopes", nargs="*", default=["read", "write"])
    p.set_defaults(func=cmd_keys)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with WorkbenchClient(args.url, args.key) as client:
            return args.func(client, args)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
