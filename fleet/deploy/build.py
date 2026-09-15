#!/usr/bin/env python3
"""Build the fleet images in-cluster with Kaniko.

Reuses the pattern the cluster already uses for AgentForge: an init container
clones the repository into an emptyDir, then Kaniko builds from the `fleet/`
subdirectory and pushes to the in-cluster registry with --insecure.

Usage:
    python3 build.py <revision> <tag>
"""

from __future__ import annotations

import base64
import sys
import time

sys.path.insert(0, "/opt/data/work")
from kubehelp import req  # noqa: E402

NAMESPACE = "registry"
REGISTRY = "registry.registry.svc:5000"
REPO = "djdanielsson/agentforge"
SECRET = "fleet-gh-token"

REVISION = sys.argv[1] if len(sys.argv) > 1 else "fleet"
TAG = sys.argv[2] if len(sys.argv) > 2 else "dev"

# (image name, dockerfile path relative to the fleet/ directory)
IMAGES = [
    ("fleet-api", "apps/control-plane/Dockerfile"),
    ("fleet-workspace", "deploy/images/workspace/Dockerfile"),
    # The shared T3 Code environment (docs/T3-INTEGRATION.md §A).
    ("fleet-t3", "deploy/images/t3/Dockerfile"),
]


def upsert_secret() -> None:
    with open("/opt/data/work/.ghtoken2") as handle:
        token = handle.read().strip()
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET, "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {"token": base64.b64encode(token.encode()).decode()},
    }
    existing = req(f"/api/v1/namespaces/{NAMESPACE}/secrets/{SECRET}")
    if "__error__" in existing:
        out = req(f"/api/v1/namespaces/{NAMESPACE}/secrets", method="POST", body=body)
    else:
        out = req(f"/api/v1/namespaces/{NAMESPACE}/secrets/{SECRET}", method="PUT", body=body)
    print("  credential:", "ok" if "__error__" not in out else out)


def job(name: str, image: str, dockerfile: str) -> dict:
    clone = (
        "set -eu; "
        f"git clone --depth 1 --branch {REVISION} "
        f'"https://x-access-token:${{GH_TOKEN}}@github.com/{REPO}" /work/repo; '
        f"test -f /work/repo/fleet/{dockerfile}; "
        'echo "clone OK"'
    )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": "fleet", "component": image},
        },
        "spec": {
            "backoffLimit": 1,
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": {"app": "fleet", "component": image}},
                "spec": {
                    "restartPolicy": "Never",
                    "initContainers": [
                        {
                            "name": "clone",
                            "image": "alpine/git:2.45.2",
                            "command": ["/bin/sh", "-c"],
                            "args": [clone],
                            "env": [
                                {
                                    "name": "GH_TOKEN",
                                    "valueFrom": {"secretKeyRef": {"name": SECRET, "key": "token"}},
                                }
                            ],
                            "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                        }
                    ],
                    "containers": [
                        {
                            "name": "build",
                            "image": "gcr.io/kaniko-project/executor:v1.23.2",
                            "args": [
                                # The build context is the fleet/ subdirectory:
                                # its uv workspace root is there, not at the repo root.
                                "--context=dir:///work/repo/fleet",
                                f"--dockerfile={dockerfile}",
                                f"--destination={REGISTRY}/{image}:{TAG}",
                                "--insecure",
                                "--verbosity=info",
                                "--cache=true",
                                f"--cache-repo={REGISTRY}/fleet-cache",
                            ],
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "1Gi"},
                                "limits": {"cpu": "2", "memory": "6Gi"},
                            },
                            "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                        }
                    ],
                    "volumes": [{"name": "work", "emptyDir": {}}],
                },
            },
        },
    }


def pod_logs(namespace: str, pod: str, container: str, tail: int = 60) -> str:
    """Fetch plain-text pod logs.

    `kubehelp.req` parses JSON, and the log endpoint returns text, so a failure
    inside a build showed up as a JSONDecodeError rather than the build error.
    """
    import ssl
    import urllib.request

    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    with open(f"{sa}/token") as handle:
        token = handle.read().strip()
    context = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    request = urllib.request.Request(
        f"https://kubernetes.default.svc/api/v1/namespaces/{namespace}/pods/{pod}/log"
        f"?container={container}&tailLines={tail}"
    )
    request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, context=context) as response:
            return response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return f"<{error.code}> {error.read().decode(errors='replace')[:800]}"


def wait_job(name: str, timeout: int = 1500) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job_state = req(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{name}")
        if "__error__" in job_state:
            return "missing"
        status = job_state.get("status", {})
        if status.get("succeeded"):
            return "succeeded"
        if status.get("failed"):
            return "failed"
        time.sleep(10)
    return "timeout"


def main() -> int:
    print(f"== building revision {REVISION} as tag {TAG} ==")
    upsert_secret()
    names = []
    for image, dockerfile in IMAGES:
        name = f"fleet-build-{image}"
        req(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{name}", method="DELETE")
        for _ in range(60):
            if "__error__" in req(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/{name}"):
                break
            time.sleep(2)
        out = req(
            f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs",
            method="POST",
            body=job(name, image, dockerfile),
        )
        print(f"  {name} ({image}): {'created' if '__error__' not in out else out}")
        names.append((name, image))

    failed = []
    for name, _image in names:
        outcome = wait_job(name)
        print(f"  {name}: {outcome}")
        if outcome != "succeeded":
            failed.append(name)
            pods = req(f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector=job-name%3D{name}")
            for pod in pods.get("items", []):
                for container in ("build", "clone"):
                    print(
                        f"    [{container}] "
                        + pod_logs(NAMESPACE, pod["metadata"]["name"], container, tail=40)
                    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
