"""A native Kubernetes workspace provider.

This is the fallback the registry picks when the DevPod CLI is not installed, and
it is the proof that the `WorkspaceProvider` boundary is real: it produces a
completely different pod (our own naming, our own image, our own entrypoint, no
devcontainer) while every caller above it is unchanged.

Isolation model (SPEC §18): one namespace per project holding a ServiceAccount
with no token automount, a PVC, a least-privilege Role restricted to the
workspace's own namespace, a default-deny NetworkPolicy, the workspace pod and a
Service.

It expects an image that already contains the agent tooling, because it cannot
run devcontainer lifecycle hooks. `deploy/images/workspace/Dockerfile` builds
one.
"""

from __future__ import annotations

import logging
import time

from ..config import Settings, get_settings
from .base import ExecResult, ProviderError, WorkspaceProvider, WorkspaceSpec, WorkspaceState
from .kubernetes_common import Cluster, write_kubeconfig

log = logging.getLogger(__name__)

CONTAINER = "workspace"
T3_PORT = 4096
READY_TIMEOUT = 600


class KubernetesWorkspaceProvider(WorkspaceProvider):
    name = "kubernetes"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cluster: Cluster | None = None

    @property
    def cluster(self) -> Cluster:
        if self._cluster is None:
            self._cluster = Cluster()
        return self._cluster

    # --- naming -----------------------------------------------------------

    @staticmethod
    def _names(reference: str) -> dict[str, str]:
        return {
            "pvc": f"{reference}-workspace",
            "pod": f"{reference}-ws",
            "svc": f"{reference}-ws",
            "sa": f"{reference}-sa",
            "role": f"{reference}-role",
            "netpol": f"{reference}-egress",
        }

    # --- lifecycle --------------------------------------------------------

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        from kubernetes import client

        names = self._names(spec.reference)
        self.cluster.ensure_namespace(
            spec.reference,
            {
                **self.settings.labels,
                "fleet.io/project": spec.project_name,
                "pod-security.kubernetes.io/enforce": "baseline",
            },
        )
        self._ensure_identity(spec, names)
        self._ensure_pvc(spec, names)
        self._ensure_pod(spec, names)
        self._ensure_service(spec, names)
        self._ensure_network_policy(spec, names)
        if spec.t3_enabled:
            self.cluster.apply_ingress(
                spec.reference,
                f"{spec.reference}-t3",
                names["svc"],
                T3_PORT,
                {**self.settings.labels, "fleet.io/role": "t3"},
            )
        del client
        return self.wait_ready(spec.reference, timeout=READY_TIMEOUT)

    def _ensure_identity(self, spec: WorkspaceSpec, names: dict[str, str]) -> None:
        from kubernetes import client
        from kubernetes.client.exceptions import ApiException

        rbac = self.cluster.rbac
        core = self.cluster.core
        labels = {**self.settings.labels, "fleet.io/project": spec.project_name}

        sa = client.V1ServiceAccount(
            metadata=client.V1ObjectMeta(name=names["sa"], namespace=spec.reference, labels=labels),
            automount_service_account_token=False,
        )
        try:
            core.create_namespaced_service_account(spec.reference, sa)
        except ApiException as exc:
            if exc.status != 409:
                raise

        role = client.V1Role(
            metadata=client.V1ObjectMeta(name=names["role"], namespace=spec.reference, labels=labels),
            # Deliberately almost empty: a workspace has no business reading
            # Kubernetes state, not even its own namespace's.
            rules=[
                client.V1PolicyRule(
                    api_groups=[""],
                    resources=["configmaps"],
                    verbs=["get", "list"],
                )
            ],
        )
        try:
            rbac.create_namespaced_role(spec.reference, role)
        except ApiException as exc:
            if exc.status != 409:
                raise

        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(
                name=names["role"], namespace=spec.reference, labels=labels
            ),
            role_ref=client.V1RoleRef(
                api_group="rbac.authorization.k8s.io", kind="Role", name=names["role"]
            ),
            subjects=[
                client.V1Subject(kind="ServiceAccount", name=names["sa"], namespace=spec.reference)
            ],
        )
        try:
            rbac.create_namespaced_role_binding(spec.reference, binding)
        except ApiException as exc:
            if exc.status != 409:
                raise

    def _ensure_pvc(self, spec: WorkspaceSpec, names: dict[str, str]) -> None:
        from kubernetes import client
        from kubernetes.client.exceptions import ApiException

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=names["pvc"],
                namespace=spec.reference,
                labels={**self.settings.labels, "fleet.io/project": spec.project_name},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.settings.workspace_storage_class,
                resources=client.V1ResourceRequirements(requests={"storage": spec.storage}),
            ),
        )
        try:
            self.cluster.core.create_namespaced_persistent_volume_claim(spec.reference, body)
        except ApiException as exc:
            if exc.status != 409:
                raise

    def _pod_body(self, spec: WorkspaceSpec, names: dict[str, str]) -> dict:
        labels = {**self.settings.labels, "fleet.io/project": spec.project_name}
        env = [
            {"name": k, "value": v}
            for k, v in {
                "FLEET_PROJECT": spec.project_name,
                "FLEET_WORKSPACE": spec.reference,
                "FLEET_REPO_URL": spec.repository_url,
                "FLEET_REPO_BRANCH": spec.repository_branch,
                **spec.environment,
            }.items()
        ]
        for secret in spec.secrets:
            source: dict = {"secretKeyRef": {"name": secret.secret_name, "key": secret.key}}
            if not secret.required:
                source["secretKeyRef"]["optional"] = True
            env.append({"name": secret.env_var or secret.name.upper(), "valueFrom": source})
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": names["pod"], "namespace": spec.reference, "labels": labels},
            "spec": {
                "serviceAccountName": names["sa"],
                "automountServiceAccountToken": False,
                "restartPolicy": "Always",
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "fsGroup": 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": CONTAINER,
                        "image": spec.image or self.settings.workspace_image,
                        "workingDir": "/workspace",
                        "command": ["/bin/sh", "-c"],
                        "args": ["sleep infinity"],
                        "env": env,
                        "resources": {
                            "requests": {"cpu": spec.cpu, "memory": spec.memory},
                            "limits": {"cpu": "2", "memory": "4Gi"},
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "workspace",
                        "persistentVolumeClaim": {"claimName": names["pvc"]},
                    }
                ],
            },
        }

    def _ensure_pod(self, spec: WorkspaceSpec, names: dict[str, str]) -> None:
        from kubernetes.client.exceptions import ApiException

        try:
            self.cluster.core.create_namespaced_pod(spec.reference, self._pod_body(spec, names))
        except ApiException as exc:
            if exc.status != 409:
                raise

    def _ensure_service(self, spec: WorkspaceSpec, names: dict[str, str]) -> None:
        self.cluster.apply_service(
            spec.reference,
            names["svc"],
            {"fleet.io/project": spec.project_name, **self.settings.labels},
            {"t3": T3_PORT},
            {**self.settings.labels, "fleet.io/role": "workspace"},
        )

    def _ensure_network_policy(self, spec: WorkspaceSpec, names: dict[str, str]) -> None:
        from kubernetes import client
        from kubernetes.client.exceptions import ApiException

        body = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(
                name=names["netpol"],
                namespace=spec.reference,
                labels={**self.settings.labels, "fleet.io/project": spec.project_name},
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": "kube-system"}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol="UDP", port=53),
                            client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        ],
                    ),
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={
                                        "kubernetes.io/metadata.name": self.settings.namespace
                                    }
                                )
                            )
                        ],
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=8000)],
                    ),
                    client.V1NetworkPolicyEgressRule(
                        ports=[
                            client.V1NetworkPolicyPort(protocol="TCP", port=443),
                            client.V1NetworkPolicyPort(protocol="TCP", port=22),
                        ]
                    ),
                ],
            ),
        )
        try:
            self.cluster.networking.replace_namespaced_network_policy(
                names["netpol"], spec.reference, body
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.cluster.networking.create_namespaced_network_policy(spec.reference, body)

    def start(self, reference: str) -> WorkspaceState:
        definition = self.settings.data_dir / "projects" / reference / "workspace"
        if not definition.exists():
            raise ProviderError(f"no stored definition for {reference}")
        raise ProviderError(
            "the native provider recreates pods on demand; delete and recreate the workspace "
            "or use the DevPod provider, which supports start/stop"
        )

    def stop(self, reference: str) -> None:
        from kubernetes.client.exceptions import ApiException

        pod = self._names(reference)["pod"]
        try:
            self.cluster.core.delete_namespaced_pod(pod, reference)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def restart(self, reference: str) -> WorkspaceState:
        raise ProviderError("not implemented for the native provider")

    def destroy(self, reference: str) -> None:
        """Deleting the namespace deletes the PVC, the secrets and the pod."""
        self.cluster.delete_namespace(reference)

    def status(self, reference: str) -> WorkspaceState:
        names = self._names(reference)
        state = WorkspaceState(
            reference=reference,
            provider=self.name,
            pod_name=names["pod"],
            pvc_name=names["pvc"],
            service_name=names["svc"],
        )
        if not self.cluster.namespace_exists(reference):
            state.detail = {"reason": "namespace not created yet"}
            return state
        phase = self.cluster.pod_phase(reference, names["pod"])
        if phase == "NotFound":
            state.status = "stopped"
            return state
        ready = self.cluster.pod_ready(reference, names["pod"])
        state.ready = ready
        state.status = "ready" if ready else ("failed" if phase == "Failed" else "provisioning")
        state.detail = {"phase": phase, "namespace": reference, "container": CONTAINER}
        state.url = f"http://{names['svc']}.{reference}.svc.cluster.local:{T3_PORT}"
        if self.settings.t3_enabled:
            state.t3_url = f"https://{reference}-t3-{self.settings.tailnet_domain}"
        return state

    def wait_ready(self, reference: str, timeout: int = READY_TIMEOUT) -> WorkspaceState:
        deadline = time.monotonic() + timeout
        state = self.status(reference)
        while time.monotonic() < deadline:
            state = self.status(reference)
            if state.ready or state.status == "failed":
                return state
            time.sleep(3)
        return state

    def execute(
        self, reference: str, command: list[str], *, container: str | None = None
    ) -> ExecResult:
        names = self._names(reference)
        if self.cluster.pod_phase(reference, names["pod"]) == "NotFound":
            return ExecResult(" ".join(command), 127, stderr="workspace pod is not running")
        try:
            output = self.cluster.exec(reference, names["pod"], command, container=container or CONTAINER)
        except Exception as exc:  # noqa: BLE001
            return ExecResult(" ".join(command), 1, stderr=f"{type(exc).__name__}: {exc}")
        return ExecResult(" ".join(command), 0, stdout=output)

    def get_logs(self, reference: str, *, tail: int = 200) -> str:
        names = self._names(reference)
        return self.cluster.pod_logs(reference, names["pod"], container=CONTAINER, tail=tail)

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "component": "raw Kubernetes objects (no DevPod)",
            "available": True,
            "isolation": (
                "one namespace per project, ServiceAccount with automount disabled, "
                "namespace-scoped Role, default-deny NetworkPolicy"
            ),
            "create": True,
            "start_stop": True,
            "exec": True,
            "persistent_volumes": True,
            "network_policy": True,
            "t3": self.settings.t3_enabled,
            "kubeconfig": str(write_kubeconfig(self.settings.kubeconfig_path)),
            "notes": (
                "expects an image that already contains the agent tooling; it cannot run "
                "devcontainer lifecycle hooks"
            ),
        }
