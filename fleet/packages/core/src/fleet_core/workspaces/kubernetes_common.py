"""Kubernetes plumbing shared by the workspace providers.

Deliberately not a provider itself: both the DevPod provider and the native
provider need the same cluster handle, the same kubeconfig generation and the
same "which pod is this workspace" lookup.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kubernetes import client, config as kube_config
from kubernetes.config.config_exception import ConfigException
from kubernetes.stream import stream

log = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

# DevPod labels every pod it creates; this is how we find a workspace's pod
# without depending on DevPod's internal naming scheme.
DEVPOD_POD_LABEL = "devpod.sh/created"


def load_client() -> client.ApiClient:
    """In-cluster service account, or a kubeconfig. Never a silent fallback."""
    try:
        if SA_DIR.exists():
            kube_config.load_incluster_config()
        else:
            kube_config.load_kube_config()
    except ConfigException as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "no Kubernetes credentials: run in a pod with a service account, "
            "or provide a kubeconfig"
        ) from exc
    return client.ApiClient()


def write_kubeconfig(path: Path, *, server: str | None = None) -> Path:
    """Materialise a kubeconfig pointing at the in-cluster API.

    DevPod shells out to kubectl-style config loading and will not accept the
    projected service-account directory directly, so we write a config that
    references the token and CA by path.
    """
    server = server or f"https://{os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:{os.environ.get('KUBERNETES_SERVICE_PORT', '443')}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "apiVersion: v1",
                "kind: Config",
                "clusters:",
                "- name: fleet-in-cluster",
                "  cluster:",
                f"    server: {server}",
                f"    certificate-authority: {SA_DIR / 'ca.crt'}",
                "contexts:",
                "- name: fleet-in-cluster",
                "  context:",
                "    cluster: fleet-in-cluster",
                "    user: fleet",
                "    namespace: default",
                "current-context: fleet-in-cluster",
                "users:",
                "- name: fleet",
                "  user:",
                f"    tokenFile: {SA_DIR / 'token'}",
                "",
            ]
        )
    )
    return path


@dataclass
class Cluster:
    """A thin convenience wrapper over the Kubernetes client."""

    def __post_init__(self) -> None:
        self.api = load_client()
        self.core = client.CoreV1Api(self.api)
        self.networking = client.NetworkingV1Api(self.api)
        self.rbac = client.RbacAuthorizationV1Api(self.api)

    def namespace_exists(self, name: str) -> bool:
        from kubernetes.client.exceptions import ApiException

        try:
            self.core.read_namespace(name)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise

    def ensure_namespace(self, name: str, labels: dict[str, str]) -> None:
        from kubernetes.client.exceptions import ApiException

        try:
            self.core.create_namespace(
                client.V1Namespace(metadata=client.V1ObjectMeta(name=name, labels=labels))
            )
        except ApiException as exc:
            if exc.status != 409:
                raise

    def find_pod(self, namespace: str, label_selector: str) -> str:
        from kubernetes.client.exceptions import ApiException

        try:
            pods = self.core.list_namespaced_pod(namespace, label_selector=label_selector)
        except ApiException as exc:
            if exc.status == 404:
                return ""
            raise
        names = [p.metadata.name for p in pods.items]
        # A restarted workspace can briefly have two pods; the newest wins.
        if not names:
            return ""
        if len(names) > 1:
            log.warning("namespace %s has %d matching pods: %s", namespace, len(names), names)
        return sorted(names)[-1]

    def pod_ready(self, namespace: str, pod: str) -> bool:
        from kubernetes.client.exceptions import ApiException

        try:
            pod_obj = self.core.read_namespaced_pod(pod, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        conditions = pod_obj.status.conditions or []
        return any(c.type == "Ready" and c.status == "True" for c in conditions)

    def pod_phase(self, namespace: str, pod: str) -> str:
        from kubernetes.client.exceptions import ApiException

        try:
            return self.core.read_namespaced_pod(pod, namespace).status.phase
        except ApiException as exc:
            if exc.status == 404:
                return "NotFound"
            raise

    def pod_logs(self, namespace: str, pod: str, *, container: str | None = None, tail: int = 200) -> str:
        from kubernetes.client.exceptions import ApiException

        try:
            return self.core.read_namespaced_pod_log(
                pod, namespace, container=container, tail_lines=tail
            )
        except ApiException as exc:
            return f"<logs unavailable: {exc.status} {exc.reason}>"

    def exec(self, namespace: str, pod: str, command: list[str], *, container: str | None = None) -> str:
        """Run a command and return its combined output.

        Raising is left to the caller: `execute` wants the exit status, not an
        exception, because "the agent's command failed" is normal.
        """
        return stream(
            self.core.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            command=command,
            container=container,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )

    def apply_service(self, namespace: str, name: str, selector: dict[str, str], ports: dict[str, int], labels: dict[str, str]) -> None:
        from kubernetes.client.exceptions import ApiException

        body = client.V1Service(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels),
            spec=client.V1ServiceSpec(
                selector=selector,
                ports=[
                    client.V1ServicePort(name=k, port=v, target_port=v) for k, v in ports.items()
                ],
            ),
        )
        try:
            self.core.create_namespaced_service(namespace, body)
        except ApiException as exc:
            if exc.status == 409:
                self.core.replace_namespaced_service(name, namespace, body)
            else:
                raise

    def apply_ingress(
        self,
        namespace: str,
        name: str,
        service: str,
        port: int,
        labels: dict[str, str],
        *,
        hostname: str = "",
    ) -> str:
        from kubernetes.client.exceptions import ApiException

        annotations = {"tailscale.com/hostname": hostname} if hostname else {}
        body = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": {
                "ingressClassName": "tailscale",
                "defaultBackend": {"service": {"name": service, "port": {"number": port}}},
            },
        }
        paths = {
            "ingresses": f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses",
            "ingress": f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses/{name}",
        }
        try:
            self.networking.create_namespaced_ingress(namespace, body)
        except ApiException as exc:
            if exc.status == 409:
                self.networking.replace_namespaced_ingress(name, namespace, body)
            else:
                raise
        return paths["ingress"]

    def namespace_labeled(self, name: str, key: str) -> str:
        return self.core.read_namespace(name).metadata.labels.get(key, "")

    def delete_namespace(self, namespace: str) -> None:
        from kubernetes.client.exceptions import ApiException

        try:
            self.core.delete_namespace(namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def all(self, kind: str, namespace: str) -> list[dict[str, Any]]:
        """Small escape hatch used by the smoke scripts, not by request paths."""
        paths = {
            "pods": f"/api/v1/namespaces/{namespace}/pods",
            "services": f"/api/v1/namespaces/{namespace}/services",
            "persistentvolumeclaims": f"/api/v1/namespaces/{namespace}/persistentvolumeclaims",
            "secrets": f"/api/v1/namespaces/{namespace}/secrets",
        }
        rc = self.api.call_api(
            paths[kind],
            "GET",
            response_type="object",
            auth_settings=["BearerToken"],
        )
        data = rc[0] or {}
        return list(data.get("items", []))
