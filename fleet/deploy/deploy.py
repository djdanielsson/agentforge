#!/usr/bin/env python3
"""Build and deploy the fleet control plane.

Deliberately does the three things Kubernetes tooling normally does for you,
because this environment has neither kubectl nor helm installed in the cluster:

1. render the Helm chart with a local helm binary (`helm template`),
2. apply the rendered manifests through the Kubernetes API,
3. copy the LLM gateway master key from the gateway's own namespace into ours,
   so no key value is ever written to a file, a command line or a log.

Usage:
    python3 deploy.py --tag <image-tag> [--build] [--skip-build]
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/data/work")
from kubehelp import req  # noqa: E402

HERE = Path(__file__).resolve().parent
CHART = HERE / "helm" / "fleet"

NAMESPACE = "fleet"
REGISTRY = "registry.registry.svc:5000"
IMAGE = f"{REGISTRY}/fleet-api"
GATEWAY_NAMESPACE = "agentforge"
GATEWAY_SECRET = "agentforge-llm"
HELM = "/opt/data/work/bin/helm"


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    out = req(path, method=method, body=body)
    if "__error__" in out:
        raise RuntimeError(f"{method} {path} -> {out['__error__']}: {out.get('body', '')[:400]}")
    return out


def apply(body: dict) -> str:
    """Create or replace a resource, given a manifest with apiVersion/kind/metadata."""
    kind = body["kind"]
    version = body["apiVersion"]
    name = body["metadata"]["name"]
    namespace = body["metadata"].get("namespace")

    plural = {
        "Namespace": "namespaces",
        "ServiceAccount": "serviceaccounts",
        "ClusterRole": "clusterroles",
        "ClusterRoleBinding": "clusterrolebindings",
        "PersistentVolumeClaim": "persistentvolumeclaims",
        "Deployment": "deployments",
        "Service": "services",
        "Ingress": "ingresses",
        "Secret": "secrets",
        "ClusterRoleBinding": "clusterrolebindings",
        "ConfigMap": "configmaps",
        "NetworkPolicy": "networkpolicies",
    }[kind]

    core = version in ("v1",)
    group = "" if core else version.split("/")[0] + "/" + version.split("/")[1]
    prefix = f"/api/v1/namespaces/{namespace}" if core else f"/apis/{group}/namespaces/{namespace}"

    if kind == "Namespace":
        item_path = f"/api/v1/namespaces/{name}"
        collection = "/api/v1/namespaces"
    elif kind in ("ClusterRole", "ClusterRoleBinding"):
        base = "/apis/rbac.authorization.k8s.io/v1"
        item_path = f"{base}/{plural}/{name}"
        collection = f"{base}/{plural}"
    else:
        item_path = f"{prefix}/{plural}/{name}"
        collection = f"{prefix}/{plural}"

    existing = req(item_path)
    if "__error__" not in existing:
        # replace is not implemented for every kind through this shim, so
        # delete-and-create is used for the ones that change shape.
        api(item_path, method="DELETE")
        for _ in range(60):
            if "__error__" in req(item_path):
                break
            time.sleep(1)
    try:
        api(collection, method="POST", body=body)
        return f"created {kind}/{name}"
    except RuntimeError as exc:
        return f"FAILED {kind}/{name}: {exc}"


def render(tag: str) -> list[dict]:
    result = subprocess.run(
        [
            HELM,
            "template",
            "fleet",
            str(CHART),
            "--namespace",
            NAMESPACE,
            "--set",
            f"image.tag={tag}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed:\n{result.stderr}")
    import yaml

    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def ensure_namespace() -> None:
    api(
        "/api/v1/namespaces",
        method="POST",
        body={
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {"app.kubernetes.io/managed-by": "fleet-deploy"},
            },
        },
    ) if "__error__" in req(f"/api/v1/namespaces/{NAMESPACE}") else None
    print(f"  namespace {NAMESPACE} present")


def copy_gateway_key() -> None:
    """Copy the gateway's master key into our namespace. The value is never printed."""
    source = api(f"/api/v1/namespaces/{GATEWAY_NAMESPACE}/secrets/{GATEWAY_SECRET}")
    encoded = source.get("data", {}).get("LITELLM_MASTER_KEY")
    if not encoded:
        raise RuntimeError(f"no LITELLM_MASTER_KEY in {GATEWAY_NAMESPACE}/{GATEWAY_SECRET}")
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "fleet-llm",
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": "fleet"},
        },
        "type": "Opaque",
        "data": {"LITELLM_MASTER_KEY": encoded},
    }
    apply(body)
    print(f"  secret fleet-llm present (key length {len(base64.b64decode(encoded))}, value not shown)")


def ensure_api_secret(token: str) -> None:
    import hashlib
    import secrets

    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "fleet-api", "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {
            "api-token": base64.b64encode(token.encode()).decode(),
            # A stable secret for signing project-scoped gateway tokens,
            # derived so it survives a redeploy without being stored in git.
            "token-secret": base64.b64encode(
                hashlib.sha256(f"fleet-token-secret::{token}".encode()).hexdigest().encode()
            ).decode(),
        },
    }
    apply(body)
    print("  secret fleet-api present")


def wait_rollout(timeout: int = 180) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        deployment = req(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/fleet-api")
        if "__error__" not in deployment:
            status = deployment.get("status", {})
            if status.get("readyReplicas") and status.get("readyReplicas") == status.get("replicas"):
                return "ready"
            if status.get("unavailableReplicas") and status.get("conditions"):
                for condition in status["conditions"]:
                    if condition.get("type") == "Progressing" and condition.get("reason") in (
                        "CrashLoopBackOff",
                        "ImagePullBackOff",
                    ):
                        return f"failed: {condition.get('message')}"
        time.sleep(4)
    return "timeout"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    token = args.token or open(Path("/opt/data/work/.fleet-api-token")).read().strip()

    print("== namespace and secrets ==")
    ensure_namespace()
    copy_gateway_key()
    ensure_api_secret(token)

    print("== manifests ==")
    documents = render(args.tag)
    # Secrets are managed above so their values stay out of the rendered chart.
    for document in documents:
        if document.get("kind") == "Secret":
            continue
        print("  " + apply(document))

    print("== rollout ==")
    outcome = wait_rollout()
    print("  deployment:", outcome)
    if outcome != "ready":
        pods = req(f"/api/v1/namespaces/{NAMESPACE}/pods")
        for pod in pods.get("items", []):
            print("  pod", pod["metadata"]["name"], pod["status"].get("phase"))
            for container in pod["status"].get("containerStatuses", []) or []:
                print("    ", container["name"], container.get("state"))
        logs = req(f"/api/v1/namespaces/{NAMESPACE}/pods/fleet-api/log?tailLines=40")
        print(json.dumps(logs)[:2000])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
