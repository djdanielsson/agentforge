"""The Kubernetes provider. The reference implementation.

One project is one namespace: PVC for persistence, a pod running code-server and
the OpenHands Agent Server, a NetworkPolicy for egress, and no service account
token unless the agent's policy explicitly asks for cluster access.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agentforge_shared.config import Settings, get_settings
from agentforge_shared.enums import WorkspaceStatus
from agentforge_shared.schemas import ResolvedSecret
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream

from .. import manifests
from .base import ProviderError, WorkspaceProvider, WorkspaceSpec, WorkspaceState

log = logging.getLogger(__name__)

MANAGED_LABEL = "app.kubernetes.io/managed-by=agentforge"


def load_api_client(settings: Settings | None = None) -> client.ApiClient:
    """In-cluster service account, or a kubeconfig. Never a silent fallback."""
    from kubernetes import config as kube_config
    from kubernetes.config.config_exception import ConfigException

    settings = settings or get_settings()
    try:
        if settings.k8s_in_cluster:
            kube_config.load_incluster_config()
            log.debug("kubernetes: in-cluster credentials")
        else:
            kube_config.load_kube_config(config_file=settings.kubeconfig or None)
            log.debug("kubernetes: kubeconfig %s", settings.kubeconfig or "(default)")
    except ConfigException as exc:
        raise ProviderError(
            "no Kubernetes credentials. Set AGENTFORGE_K8S_IN_CLUSTER=true inside a pod, "
            "or point AGENTFORGE_KUBECONFIG at a kubeconfig."
        ) from exc
    return client.ApiClient()


class KubernetesProvider(WorkspaceProvider):
    name = "kubernetes"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._api: client.ApiClient | None = None
        self._core: client.CoreV1Api | None = None
        self._net: client.NetworkingV1Api | None = None

    # --- plumbing ---------------------------------------------------------

    @property
    def api(self) -> client.ApiClient:
        if self._api is None:
            self._api = load_api_client(self.settings)
        return self._api

    @property
    def v1(self) -> client.CoreV1Api:
        if self._core is None:
            self._core = client.CoreV1Api(self.api)
        return self._core

    @property
    def networking(self) -> client.NetworkingV1Api:
        if self._net is None:
            self._net = client.NetworkingV1Api(self.api)
        return self._net

    @staticmethod
    def _ignore_conflict(exc: ApiException) -> None:
        if exc.status != 409:
            raise

    def _names(self, reference: str) -> dict[str, str]:
        return {
            "pvc": f"{reference}-workspace",
            "pod": f"{reference}-ws",
            "svc": f"{reference}-ws",
            "netpol": f"{reference}-egress",
        }

    # --- lifecycle --------------------------------------------------------

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        names = self._names(spec.reference)
        warnings: list[str] = []

        self._ensure_namespace(spec.reference)
        self._apply(
            self.v1.create_namespaced_persistent_volume_claim,
            spec.reference,
            manifests.build_pvc(names["pvc"], spec.reference, spec.storage, spec.storage_class),
        )
        self._apply(
            self.v1.create_namespaced_service,
            spec.reference,
            manifests.build_service(
                names["svc"],
                spec.reference,
                {"http": spec.code_server_port, "agent": spec.agent_server_port},
            ),
        )

        netpol = manifests.build_network_policy(names["netpol"], spec.reference, spec.permissions)
        if netpol is not None:
            # Replace rather than ignore: a tightened policy must actually take
            # effect on an existing workspace, or the control is a lie.
            try:
                self.networking.delete_namespaced_network_policy(names["netpol"], spec.reference)
            except ApiException as exc:
                if exc.status != 404:
                    raise
            self._apply(self.networking.create_namespaced_network_policy, spec.reference, netpol)

        warnings.extend(self._verify_secrets(spec.reference, spec.secrets, strict=False))

        pod = manifests.build_pod(
            names["pod"],
            spec.reference,
            image=spec.image,
            agent_image=spec.agent_image,
            pvc_name=names["pvc"],
            permissions=spec.permissions,
            secrets=spec.secrets,
            environment=spec.environment,
            code_server_port=spec.code_server_port,
            agent_server_port=spec.agent_server_port,
            agent_uid=spec.agent_uid,
            agent_gid=spec.agent_gid,
            cpu_request=spec.cpu_request,
            memory_request=spec.memory_request,
            cpu_limit=spec.cpu_limit,
            memory_limit=spec.memory_limit,
            git_repository=spec.repository_url,
            git_revision=spec.revision,
        )
        warnings.extend(getattr(pod, "_agentforge_warnings", []))
        self._apply(self.v1.create_namespaced_pod, spec.reference, pod)

        state = self.get_status(spec.reference)
        state.detail["warnings"] = warnings
        return state

    def _ensure_namespace(self, reference: str) -> None:
        body = client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=reference,
                labels={
                    **manifests.LABELS,
                    # Baseline PSA blocks the obvious escapes; the pod spec does
                    # the rest by dropping all capabilities and running non-root.
                    "pod-security.kubernetes.io/enforce": "baseline",
                },
            )
        )
        self._apply(self.v1.create_namespace, None, body)

    def _apply(self, create_fn, namespace, body) -> None:
        try:
            create_fn(namespace, body) if namespace else create_fn(body)
        except ApiException as exc:
            self._ignore_conflict(exc)

    def start(self, reference: str) -> WorkspaceState:
        """Pods cannot be paused, so `stop` deletes the pod and `start` recreates
        it from the stored spec. The PVC, and therefore the agent's work, survives.

        The spec is rebuilt from the workspace row by the caller, so this method
        only clears the way.
        """
        names = self._names(reference)
        try:
            self.v1.delete_namespaced_pod(names["pod"], reference)
        except ApiException as exc:
            if exc.status != 404:
                raise
        return self.get_status(reference)

    def stop(self, reference: str) -> None:
        """Delete the pod, keep the namespace and the volume."""
        names = self._names(reference)
        try:
            self.v1.delete_namespaced_pod(names["pod"], reference)
            log.info("stopped workspace pod %s/%s", reference, names["pod"])
        except ApiException as exc:
            if exc.status != 404:
                raise

    def destroy(self, reference: str) -> None:
        """Delete the namespace. Everything the agent could reach goes with it."""
        try:
            self.v1.delete_namespace(reference)
            log.info("destroyed workspace namespace %s", reference)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def get_status(self, reference: str) -> WorkspaceState:
        names = self._names(reference)
        state = WorkspaceState(
            reference=reference,
            provider=self.name,
            status=str(WorkspaceStatus.PROVISIONING),
            pvc_name=names["pvc"],
            pod_name=names["pod"],
            service_name=names["svc"],
            code_server_url=f"http://{names['svc']}.{reference}.svc.cluster.local:"
            f"{self.settings.workspace_code_server_port}",
            agent_server_url=f"http://{names['svc']}.{reference}.svc.cluster.local:3000",
        )
        try:
            pod = self.v1.read_namespaced_pod(names["pod"], reference)
        except ApiException as exc:
            if exc.status == 404:
                state.status = str(WorkspaceStatus.PENDING)
                return state
            raise

        phase = pod.status.phase
        conditions = pod.status.conditions or []
        ready = any(c.type == "Ready" and c.status == "True" for c in conditions)

        if ready:
            state.status = str(WorkspaceStatus.READY)
            state.ready = True
        elif phase in ("Failed",):
            state.status = str(WorkspaceStatus.ERROR)
        else:
            state.status = str(WorkspaceStatus.PROVISIONING)

        state.detail = {
            "phase": phase,
            "node": pod.spec.node_name,
            "conditions": [{"type": c.type, "status": c.status} for c in conditions],
            "containers": [
                {"name": c.name, "ready": c.ready, "restarts": c.restart_count}
                for c in (pod.status.container_statuses or [])
            ],
        }
        return state

    def wait_ready(self, reference: str, timeout: int = 300) -> WorkspaceState:
        deadline = time.monotonic() + timeout
        state = self.get_status(reference)
        while time.monotonic() < deadline:
            state = self.get_status(reference)
            if state.ready:
                return state
            if state.status == str(WorkspaceStatus.ERROR):
                return state
            time.sleep(2)
        return state

    def exec(self, reference: str, command: list[str], *, container: str | None = None) -> str:
        names = self._names(reference)
        result = stream(
            self.v1.connect_get_namespaced_pod_exec,
            names["pod"],
            reference,
            command=command,
            container=container or "code-server",
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        return result if isinstance(result, str) else str(result)

    # --- secrets ----------------------------------------------------------

    def inject_secrets(self, reference: str, secrets: list[ResolvedSecret]) -> None:
        """Verify that each referenced secret can actually be resolved.

        Kubernetes resolves secretKeyRef at container start, so there is nothing
        to copy: this checks the referenced Secrets exist in the workspace
        namespace and reports the ones that do not. `required` references raise,
        optional ones are reported so the UI can warn rather than fail.

        A Secret may legitimately be absent at this point if an External Secrets
        operator is about to create it, so the check is deliberately not fatal
        for optional references.
        """
        missing = self._verify_secrets(reference, secrets, strict=True)
        if missing:
            log.warning("workspace %s has unresolved optional secrets: %s", reference, missing)

    def _verify_secrets(
        self, reference: str, secrets: list[ResolvedSecret], *, strict: bool
    ) -> list[str]:
        problems: list[str] = []
        for secret in secrets:
            try:
                found = self.v1.read_namespaced_secret(secret.secret_name, reference)
                keys = found.data or {}
                if secret.key not in keys:
                    problems.append(
                        f"{secret.env_var}: secret {secret.secret_name} has no key {secret.key}"
                    )
            except ApiException as exc:
                if exc.status != 404:
                    raise
                problems.append(
                    f"{secret.env_var}: secret {secret.secret_name} does not exist in {reference}"
                )

        if strict:
            fatal = [
                p
                for p in problems
                if any(s.env_var == p.split(":")[0] and s.required for s in secrets)
            ]
            if fatal:
                raise ProviderError(
                    "required workspace secrets could not be resolved: " + "; ".join(fatal),
                    detail={"missing": fatal},
                )
        return problems

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "isolation": "namespace + pod",
            "secrets": True,
            "network_policy": True,
            "exec": True,
            "persistent_volumes": True,
        }


def exec_in_workspace(
    namespace: str,
    pod_name: str,
    command: list[str],
    *,
    container: str = "code-server",
    provider: KubernetesProvider | None = None,
) -> str:
    """Compatibility helper for callers that already hold a namespace.

    Prefer `KubernetesProvider.exec`; this exists because the git manager and the
    git router address a workspace by namespace rather than by spec.
    """
    provider = provider or KubernetesProvider()
    result = stream(
        provider.v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=command,
        container=container,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    return result if isinstance(result, str) else str(result)
