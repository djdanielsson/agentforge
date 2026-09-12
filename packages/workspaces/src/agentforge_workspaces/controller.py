"""The reconciler.

`provision()` is idempotent: it creates the namespace, PVC, service and pod, and
waits for the pod to become Ready. Call it as often as you like.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from agentforge_shared.config import Settings, get_settings
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream

from . import manifests
from .k8s import core, load_client

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkspaceInfo:
    namespace: str
    pvc_name: str
    pod_name: str
    service_name: str
    code_server_url: str | None
    ready: bool


class WorkspaceController:
    def __init__(
        self, settings: Settings | None = None, api_client: client.ApiClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._api = api_client
        self._core: client.CoreV1Api | None = None

    # --- plumbing ---------------------------------------------------------

    @property
    def api(self) -> client.ApiClient:
        if self._api is None:
            self._api = load_client(self.settings)
        return self._api

    @property
    def v1(self) -> client.CoreV1Api:
        if self._core is None:
            self._core = core(self.api)
        return self._core

    @staticmethod
    def _ignore_exists(exc: ApiException) -> None:
        if exc.status != 409:
            raise

    # --- naming -----------------------------------------------------------

    def _names(self, namespace: str) -> dict[str, str]:
        stem = namespace
        return {"pvc": f"{stem}-workspace", "pod": f"{stem}-ws", "svc": f"{stem}-ws"}

    # --- lifecycle --------------------------------------------------------

    def ensure_namespace(self, namespace: str) -> None:
        body = client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=namespace,
                labels={**manifests.LABELS, "pod-security.kubernetes.io/enforce": "baseline"},
            )
        )
        try:
            self.v1.create_namespace(body)
            log.info("created namespace %s", namespace)
        except ApiException as exc:
            self._ignore_exists(exc)

    def provision(
        self,
        *,
        namespace: str,
        repository_url: str | None = None,
        revision: str = "main",
        wait: bool = True,
        timeout: int = 300,
    ) -> WorkspaceInfo:
        s = self.settings
        names = self._names(namespace)
        self.ensure_namespace(namespace)

        try:
            self.v1.create_namespaced_persistent_volume_claim(
                namespace,
                manifests.build_pvc(
                    names.pvc, namespace, s.workspace_storage, s.workspace_storage_class
                ),
            )
            log.info("created pvc %s/%s", namespace, names.pvc)
        except ApiException as exc:
            self._ignore_exists(exc)

        try:
            self.v1.create_namespaced_service(
                namespace,
                manifests.build_service(names.svc, namespace, s.workspace_code_server_port),
            )
            log.info("created service %s/%s", namespace, names.svc)
        except ApiException as exc:
            self._ignore_exists(exc)

        pod = manifests.build_pod(
            names.pod,
            namespace,
            image=s.workspace_image,
            pvc_name=names.pvc,
            port=s.workspace_code_server_port,
            cpu_request=s.workspace_cpu_request,
            memory_request=s.workspace_memory_request,
            git_repository=repository_url,
            git_revision=revision,
        )
        try:
            self.v1.create_namespaced_pod(namespace, pod)
            log.info("created pod %s/%s", namespace, names.pod)
        except ApiException as exc:
            self._ignore_exists(exc)

        ready = self.wait_ready(namespace, names.pod, timeout=timeout) if wait else False
        return WorkspaceInfo(
            namespace=namespace,
            pvc_name=names.pvc,
            pod_name=names.pod,
            service_name=names.svc,
            code_server_url=self._service_url(namespace, names.svc),
            ready=ready,
        )

    def _service_url(self, namespace: str, service: str) -> str:
        port = self.settings.workspace_code_server_port
        return f"http://{service}.{namespace}.svc.cluster.local:{port}"

    def wait_ready(self, namespace: str, pod_name: str, timeout: int = 300) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pod = self.v1.read_namespaced_pod(pod_name, namespace)
            phase = pod.status.phase
            if phase == "Running":
                conditions = pod.status.conditions or []
                ready = next((c for c in conditions if c.type == "Ready"), None)
                if ready is not None and ready.status == "True":
                    return True
            if phase in ("Failed", "Succeeded"):
                return False
            time.sleep(2)
        return False

    def status(self, namespace: str, pod_name: str) -> dict:
        try:
            pod = self.v1.read_namespaced_pod(pod_name, namespace)
        except ApiException as exc:
            if exc.status == 404:
                return {"exists": False}
            raise
        return {
            "exists": True,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "conditions": [
                {"type": c.type, "status": c.status} for c in (pod.status.conditions or [])
            ],
        }

    def teardown(self, namespace: str) -> None:
        """Delete the whole namespace — the workspace's entire universe."""
        try:
            self.v1.delete_namespace(namespace)
            log.info("deleting namespace %s", namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise


def exec_in_workspace(
    namespace: str,
    pod_name: str,
    command: list[str],
    *,
    container: str = "code-server",
    timeout: int = 60,
) -> str:
    """Run a command inside a workspace pod and return stdout (+stderr)."""
    controller = WorkspaceController()
    resp = stream(
        controller.v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=command,
        container=container,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _request_timeout=timeout,
    )
    return resp if isinstance(resp, str) else str(resp)


__all__ = ["WorkspaceController", "WorkspaceInfo", "exec_in_workspace"]
