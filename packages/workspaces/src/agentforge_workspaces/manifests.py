"""Kubernetes object builders for a project workspace.

A workspace is deliberately boring: a PVC plus a single pod containing
code-server. The agent runtime is added as an extra container in MVP #2.
"""

from __future__ import annotations

from kubernetes import client

MANAGED_BY = "agentforge"
WORKSPACE_MOUNT = "/workspace"
WORKSPACE_DIR = "/home/coder"  # code-server's default config dir
LABELS = {"app.kubernetes.io/managed-by": MANAGED_BY, "app.kubernetes.io/part-of": "agentforge"}


def _meta(
    name: str, namespace: str, extra_labels: dict[str, str] | None = None
) -> client.V1ObjectMeta:
    labels = dict(LABELS)
    labels.update(extra_labels or {})
    return client.V1ObjectMeta(name=name, namespace=namespace, labels=labels)


def build_pvc(
    name: str, namespace: str, storage: str, storage_class: str | None = None
) -> client.V1PersistentVolumeClaim:
    spec = client.V1PersistentVolumeClaimSpec(
        access_modes=["ReadWriteOnce"],
        resources=client.V1ResourceRequirements(requests={"storage": storage}),
    )
    if storage_class:
        spec.storage_class_name = storage_class
    return client.V1PersistentVolumeClaim(
        api_version="v1",
        kind="PersistentVolumeClaim",
        metadata=_meta(name, namespace),
        spec=spec,
    )


def build_service(name: str, namespace: str, port: int) -> client.V1Service:
    return client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=_meta(name, namespace),
        spec=client.V1ServiceSpec(
            selector={"app.kubernetes.io/instance": name},
            ports=[client.V1ServicePort(name="code-server", port=port, target_port=port)],
        ),
    )


def build_pod(
    name: str,
    namespace: str,
    *,
    image: str,
    pvc_name: str,
    port: int = 8080,
    cpu_request: str = "500m",
    memory_request: str = "1Gi",
    cpu_limit: str = "2",
    memory_limit: str = "4Gi",
    git_repository: str | None = None,
    git_revision: str = "main",
) -> client.V1Pod:
    """code-server + a git bootstrap init container.

    Security posture: no host mounts, no service account token mounted, all
    capabilities dropped, non-root.
    """
    env = [
        client.V1EnvVar(
            name="PASSWORD",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=f"{name}-auth", key="password", optional=True
                )
            ),
        ),
        client.V1EnvVar(name="DEFAULT_WORKSPACE", value=WORKSPACE_MOUNT),
    ]

    init_containers = []
    if git_repository:
        init_containers.append(
            client.V1Container(
                name="git-clone",
                image="alpine/git:latest",
                command=["sh", "-c"],
                args=[
                    "set -eu; "
                    f"if [ -d {WORKSPACE_MOUNT}/.git ]; then "
                    f"  cd {WORKSPACE_MOUNT} && git fetch --all --prune; "
                    "else "
                    f"  git clone --branch {git_revision} {git_repository} {WORKSPACE_MOUNT}; "
                    "fi"
                ],
                volume_mounts=[client.V1VolumeMount(name="workspace", mount_path=WORKSPACE_MOUNT)],
                security_context=client.V1SecurityContext(run_as_user=1000, run_as_group=1000),
            )
        )

    container = client.V1Container(
        name="code-server",
        image=image,
        ports=[client.V1ContainerPort(name="http", container_port=port)],
        env=env,
        volume_mounts=[client.V1VolumeMount(name="workspace", mount_path=WORKSPACE_MOUNT)],
        resources=client.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit, "memory": memory_limit},
        ),
        security_context=client.V1SecurityContext(
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
        ),
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/healthz", port=port),
            initial_delay_seconds=5,
            period_seconds=10,
        ),
    )

    spec = client.V1PodSpec(
        containers=[container],
        init_containers=init_containers or None,
        automount_service_account_token=False,
        enable_service_links=False,
        restart_policy="Always",
        security_context=client.V1PodSecurityContext(run_as_non_root=True, fs_group=1000),
        volumes=[
            client.V1Volume(
                name="workspace",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name
                ),
            )
        ],
    )
    labels = {"app.kubernetes.io/instance": name}
    return client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=_meta(name, namespace, labels),
        spec=spec,
    )


def build_network_policy(
    name: str, namespace: str, allow_egress: bool = True
) -> client.V1NetworkPolicy:
    """Deny all ingress except from the control plane; egress configurable."""
    egress = []
    if allow_egress:
        egress = [client.V1NetworkPolicyEgressRule()]  # unrestricted
    return client.V1NetworkPolicy(
        api_version="networking.k8s.io/v1",
        kind="NetworkPolicy",
        metadata=_meta(name, namespace),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={"app.kubernetes.io/instance": name}),
            policy_types=["Ingress", "Egress"],
            ingress=[client.V1NetworkPolicyIngressRule()],
            egress=egress,
        ),
    )
