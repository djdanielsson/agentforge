"""Workspace provider CLI, for poking at one workspace without the stack.

agentforge-workspaces providers
agentforge-workspaces provision af-demo me/demo --repo https://github.com/example/demo
agentforge-workspaces status af-demo
agentforge-workspaces exec af-demo -- git -C /workspace status
agentforge-workspaces destroy af-demo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from agentforge_shared.config import get_settings

from .providers import WorkspaceSpec, available_providers, get_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentforge-workspaces", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--provider", default=None, help="kubernetes | podman | local")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers", help="list providers and their capabilities")

    p = sub.add_parser("provision", help="create a workspace")
    p.add_argument("reference")
    p.add_argument("--repo")
    p.add_argument("--revision", default="main")
    p.add_argument("--wait", action="store_true", help="block until the pod is ready")

    p = sub.add_parser("status", help="show workspace state")
    p.add_argument("reference")

    p = sub.add_parser("exec", help="run a command inside the workspace")
    p.add_argument("reference")
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    p = sub.add_parser("stop", help="stop without destroying")
    p.add_argument("reference")

    p = sub.add_parser("destroy", help="delete the workspace and its volume")
    p.add_argument("reference")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    provider_name = args.provider or settings.workspace_provider
    provider = get_provider(provider_name)

    if args.command == "providers":
        print(f"configured: {provider_name}\n")
        for name in available_providers():
            instance = get_provider(name)
            print(f"{name}: {json.dumps(instance.capabilities())}")
        return 0

    if args.command == "provision":
        spec = WorkspaceSpec(
            project_id=args.reference,
            slug=args.reference.removeprefix(f"{settings.workspace_namespace_prefix}-"),
            name=args.reference,
            reference=args.reference,
            repository_url=args.repo,
            revision=args.revision,
            image=settings.workspace_image,
            agent_image=settings.workspace_agent_image,
            agent_runtime=settings.workspace_agent_runtime,
            storage=settings.workspace_storage,
            storage_class=settings.workspace_storage_class,
            code_server_port=settings.workspace_code_server_port,
        )
        state = provider.create(spec)
        if args.wait and hasattr(provider, "wait_ready"):
            state = provider.wait_ready(args.reference)
        print(json.dumps(state.__dict__, indent=2, default=str))
        return 0

    if args.command == "status":
        print(json.dumps(provider.get_status(args.reference).__dict__, indent=2, default=str))
        return 0

    if args.command == "exec":
        command = [c for c in args.cmd if c != "--"]
        if not command:
            print("nothing to exec", file=sys.stderr)
            return 2
        print(provider.exec(args.reference, command))
        return 0

    if args.command == "stop":
        provider.stop(args.reference)
        print(f"stopped {args.reference}")
        return 0

    if args.command == "destroy":
        provider.destroy(args.reference)
        print(f"destroyed {args.reference}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
