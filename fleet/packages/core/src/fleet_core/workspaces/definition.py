"""Generates the workspace definition a provider provisions from.

DevPod reads a devcontainer.json. This module is the only place that knows that,
so swapping DevPod for something else means writing a different renderer rather
than rewriting the control plane.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from .base import WorkspaceSpec

ASSETS = Path(__file__).parent / "assets"

T3_PORT = 4096


def render_definition(spec: WorkspaceSpec, settings: Settings, root: Path) -> Path:
    """Write .devcontainer/devcontainer.json and .fleet/bootstrap.sh under `root`.

    Returns the directory to hand to the provider. It is rebuilt on every
    create/start so a changed project row actually takes effect.
    """
    definition_dir = root / "workspace"
    devcontainer_dir = definition_dir / ".devcontainer"
    fleet_dir = definition_dir / ".fleet"
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    fleet_dir.mkdir(parents=True, exist_ok=True)

    (fleet_dir / "bootstrap.sh").write_text((ASSETS / "bootstrap.sh").read_text())
    (fleet_dir / "bootstrap.sh").chmod(0o755)

    environment = {
        "FLEET_PROJECT": spec.project_name,
        "FLEET_WORKSPACE": spec.reference,
        "FLEET_NODE_VERSION": settings.node_version,
        "FLEET_OPENCODE_VERSION": settings.opencode_version,
        "FLEET_T3_VERSION": settings.t3_version,
        "FLEET_T3_ENABLED": "true" if spec.t3_enabled else "false",
        "FLEET_REPO_URL": spec.repository_url,
        "FLEET_REPO_BRANCH": spec.repository_branch,
        **spec.environment,
    }

    devcontainer = {
        "name": f"fleet-{spec.project_name}",
        "image": spec.image or settings.workspace_image,
        "remoteUser": "vscode",
        "containerEnv": environment,
        # The tools live on the workspace volume, so PATH has to be told about
        # them on every start, not just on the run that installed them. DevPod
        # mounts the volume at /workspaces/<workspace-id>, which we know here,
        # so the path is written literally rather than via a devcontainer
        # variable DevPod may not expand.
        "remoteEnv": {
            "PATH": (
                f"/workspaces/{spec.reference}/.fleet/tools/bin:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "OPENCODE_CONFIG": f"/workspaces/{spec.reference}/.fleet/opencode.json",
        },
        "postCreateCommand": "bash .fleet/bootstrap.sh",
        "onCreateCommand": "bash .fleet/bootstrap.sh",
    }
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(devcontainer, indent=2))
    return definition_dir
