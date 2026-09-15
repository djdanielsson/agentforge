"""Kubernetes plumbing shared by the workspace providers.

Deliberately not a provider itself: both the DevPod provider and the native
provider need the same cluster handle, the same kubeconfig generation and the
same "which pod is this workspace" lookup.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.config.config_exception import ConfigException
from kubernetes.stream import stream

from .base import ProviderError

log = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

# DevPod labels every pod it creates; this is how we find a workspace's pod
# without depending on DevPod's internal naming scheme.
DEVPOD_POD_LABEL = "devpod.sh/created"

#: How long to wait for a deleted namespace to finish terminating.
NAMESPACE_TERMINATE_TIMEOUT = 180

#: Ceiling on one exec into a workspace. The stream has no EOF signal, so the
#: only way to bound a command is by time.
EXEC_TIMEOUT = 120


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
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    server = server or f"https://{host}:{port}"
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


_cluster: Cluster | None = None


def get_cluster() -> Cluster:
    """The process-wide cluster handle.

    A single accessor rather than a bare constructor, so a test can substitute a
    fake and the modules that need Kubernetes do not each build their own client.
    """
    global _cluster
    if _cluster is None:
        _cluster = Cluster()
    return _cluster


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

    def namespace_terminating(self, name: str) -> bool:
        from kubernetes.client.exceptions import ApiException

        try:
            return self.core.read_namespace(name).metadata.deletion_timestamp is not None
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise

    def ensure_namespace(self, name: str, labels: dict[str, str]) -> None:
        """Create the namespace, or wait for a terminating one to go.

        Deleting a project and recreating it immediately is ordinary, and the
        namespace from the first one is still terminating: anything created
        inside it is refused with `403 ... is forbidden: unable to create new
        content in namespace X because it is being terminated`.
        """
        from kubernetes.client.exceptions import ApiException

        try:
            self.core.create_namespace(
                client.V1Namespace(metadata=client.V1ObjectMeta(name=name, labels=labels))
            )
            return
        except ApiException as exc:
            if exc.status != 409:
                raise

        deadline = time.monotonic() + NAMESPACE_TERMINATE_TIMEOUT
        while time.monotonic() < deadline:
            if not self.namespace_terminating(name):
                break
            time.sleep(2)
        else:
            raise ProviderError(
                f"namespace {name} is still terminating after "
                f"{NAMESPACE_TERMINATE_TIMEOUT}s; delete the project or try again"
            )

        # The namespace is gone now, so it still has to be created: returning
        # here without creating left the project with no boundary at all, and
        # the credential copy then failed with a 404 for a namespace that never
        # existed.
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

    def pod_logs(
        self, namespace: str, pod: str, *, container: str | None = None, tail: int = 200
    ) -> str:
        from kubernetes.client.exceptions import ApiException

        try:
            return self.core.read_namespaced_pod_log(
                pod, namespace, container=container, tail_lines=tail
            )
        except ApiException as exc:
            return f"<logs unavailable: {exc.status} {exc.reason}>"

    def exec(
        self,
        namespace: str,
        pod: str,
        command: list[str],
        *,
        container: str | None = None,
        stdin_data: str | None = None,
        timeout: int = EXEC_TIMEOUT,
    ) -> str:
        """Run a command and return its combined output.

        Raising is left to the caller: `execute` wants the exit status, not an
        exception, because "the agent's command failed" is normal.

        `stdin_data` is written after the stream opens, which is how a secret
        reaches a file without ever appearing in `argv` — argv is visible in the
        pod's process list and in the API server's audit log.
        """
        if stdin_data is None:
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

        handle = stream(
            self.core.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            command=command,
            container=container,
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        handle.write_stdin(stdin_data)
        chunks: list[str] = []
        # A deadline, because the stream has no end-of-input signal: a remote
        # command that waits for EOF waits forever, and an unbounded loop here
        # hung a provisioning thread with no error anywhere.
        deadline = time.monotonic() + timeout
        while handle.is_open() and time.monotonic() < deadline:
            handle.update(timeout=1)
            if handle.peek_stdout():
                chunks.append(handle.read_stdout())
            if handle.peek_stderr():
                chunks.append(handle.read_stderr())
        timed_out = handle.is_open()
        handle.close()
        if timed_out:
            chunks.append(f"\n<fleet: exec timed out after {timeout}s>")
        return "".join(chunks)

    def apply_service(
        self,
        namespace: str,
        name: str,
        selector: dict[str, str],
        ports: dict[str, int],
        labels: dict[str, str],
    ) -> None:
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
