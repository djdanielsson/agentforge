"""Tiny CLI for poking at workspaces without the rest of the stack.

uv run --package aiw-workspace-controller python -m aiw_workspace.cli provision aiw-demo
uv run ... status aiw-demo
uv run ... exec aiw-demo -- git -C /workspace status
uv run ... teardown aiw-demo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .controller import WorkspaceController, exec_in_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiw-workspace", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("provision", help="create namespace/pvc/pod for a workspace")
    p.add_argument("namespace")
    p.add_argument("--repo")
    p.add_argument("--revision", default="main")
    p.add_argument("--no-wait", action="store_true")

    p = sub.add_parser("status", help="show pod status")
    p.add_argument("namespace")

    p = sub.add_parser("exec", help="run a command inside the workspace pod")
    p.add_argument("namespace")
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    p = sub.add_parser("teardown", help="delete the workspace namespace")
    p.add_argument("namespace")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    controller = WorkspaceController()

    if args.command == "provision":
        info = controller.provision(
            namespace=args.namespace,
            repository_url=args.repo,
            revision=args.revision,
            wait=not args.no_wait,
        )
        print(json.dumps(info.__dict__, indent=2))
        return 0 if info.ready or args.no_wait else 1

    if args.command == "status":
        pod = f"{args.namespace}-ws"
        print(json.dumps(controller.status(args.namespace, pod), indent=2))
        return 0

    if args.command == "exec":
        cmd = [c for c in args.cmd if c != "--"]
        if not cmd:
            print("nothing to exec", file=sys.stderr)
            return 2
        print(exec_in_workspace(args.namespace, f"{args.namespace}-ws", cmd))
        return 0

    if args.command == "teardown":
        controller.teardown(args.namespace)
        print(f"teardown requested for {args.namespace}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
