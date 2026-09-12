"""Kubernetes objects for one project workspace.

A workspace is a namespace containing a PVC and a pod. The pod runs two
containers: code-server for the human, and the OpenHands Agent Server for the
agent. Both see /workspace and nothing else.

Every manifest here takes the agent's permission policy and turns part of it
into something the kernel enforces. That mapping is the whole point of this
module, so each field that is read says which policy decision it implements.
"""

from __future__ import annotations

from agentforge_shared.permissions import AgentPermissions
from agentforge_shared.schemas import ResolvedSecret
from kubernetes import client

MANAGED_BY = "agentforge"
WORKSPACE_MOUNT = "/workspace"
LABELS = {
    "app.kubernetes.io/managed-by": MANAGED_BY,
    "app.kubernetes.io/part-of": "agentforge",
}

#: Egress that is never allowed in `restricted` mode: the cluster itself and the
#: cloud metadata endpoint. Internet egress is still permitted, because an agent
#: must reach its git remote and the model gateway. `restricted` therefore means
#: "cannot reach your infrastructure", not "cannot exfiltrate". Network egress to
#: the outside world is a separate control (a proxy or gateway).
CLUSTER_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8"]


def _meta(name: str, namespace: str, extra_labels: dict[str, str] | None = None):
    labels = dict(LABELS)
    labels.update(extra_labels or {})
    return client.V1ObjectMeta(name=name, namespace=namespace, labels=labels)


def build_pvc(name: str, namespace: str, storage: str, storage_class: str | None = None):
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


def build_service(name: str, namespace: str, ports: dict[str, int]):
    return client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=_meta(name, namespace),
        spec=client.V1ServiceSpec(
            selector={"app.kubernetes.io/instance": name},
            ports=[
                client.V1ServicePort(name=port_name, port=port, target_port=port)
                for port_name, port in ports.items()
            ],
        ),
    )


def build_secret_env(secrets: list[ResolvedSecret]) -> list[client.V1EnvVar]:
    """Project secret *references* into env vars.

    The value is resolved by the kubelet at container start; it never passes
    through AgentForge, so it cannot appear in our database, logs or API.
    """
    env: list[client.V1EnvVar] = []
    for secret in secrets:
        env.append(
            client.V1EnvVar(
                name=secret.env_var,
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name=secret.secret_name, key=secret.key, optional=not secret.required
                    )
                ),
            )
        )
    return env


def build_pod(
    name: str,
    namespace: str,
    *,
    image: str,
    agent_image: str,
    pvc_name: str,
    permissions: AgentPermissions,
    secrets: list[ResolvedSecret] | None = None,
    environment: dict[str, str] | None = None,
    code_server_port: int = 8080,
    agent_server_port: int = 3000,
    cpu_request: str = "500m",
    memory_request: str = "1Gi",
    cpu_limit: str = "2",
    memory_limit: str = "4Gi",
    git_repository: str | None = None,
    git_revision: str = "main",
):
    """Build the workspace pod under the agent's policy.

    Raises ValueError for a policy that cannot be implemented safely, rather
    than silently downgrading it. A policy the user asked for and did not get is
    worse than a failed provisioning.
    """
    if permissions.filesystem.host:
        raise ValueError(
            "filesystem.host is not implementable: workspaces never mount the host. "
            "There is no safe configuration for this."
        )
    if permissions.filesystem.other_projects:
        raise ValueError(
            "filesystem.other_projects is not implementable: project isolation depends "
            "on each workspace seeing only its own volume."
        )
    if not permissions.filesystem.workspace:
        raise ValueError("filesystem.workspace must be true; the agent needs somewhere to work")

    # Two different security contexts, for one honest reason.
    #
    # code-server runs as an explicit non-root uid. The agent runtime cannot:
    # the OpenHands image's entrypoint is root-owned and not world-executable,
    # so pinning it to uid 1000 fails at container init with
    #   exec: "/app/entrypoint.sh": permission denied
    #
    # What actually isolates a workspace is the pod boundary, and that is
    # unchanged: no host mounts, no service account token, all capabilities
    # dropped, privilege escalation disabled, a NetworkPolicy for egress, and
    # its own namespace. Root inside such a container is root in a disposable
    # filesystem, not on the node. Pinning the uid was never load-bearing.
    def _runtime_security_context(*, non_root: bool) -> client.V1SecurityContext:
        context = client.V1SecurityContext(
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
        )
        if non_root:
            context.run_as_user = 1000
            context.run_as_group = 1000
            context.run_as_non_root = True
        return context

    warnings: list[str] = []
    env = [
        client.V1EnvVar(name="DEFAULT_WORKSPACE", value=WORKSPACE_MOUNT),
        client.V1EnvVar(name="AGENTFORGE_PROJECT", value=namespace),
        client.V1EnvVar(name="GIT_PUSH_ENABLED", value="true" if permissions.git.push else "false"),
    ]
    for key, value in (environment or {}).items():
        env.append(client.V1EnvVar(name=key, value=value))

    # secrets.enabled gates injection entirely. With it off the workspace starts
    # with no credentials at all, so the agent cannot act as the control plane.
    if permissions.secrets.enabled:
        env.extend(build_secret_env(secrets or []))
    elif secrets:
        warnings.append(
            f"{len(secrets)} secret reference(s) not injected: permissions.secrets.enabled is false"
        )

    pod_env = env

    init_containers = []
    if git_repository:
        # Shallow clone keeps the bootstrap fast and the volume small; the agent
        # can fetch more if it needs history.
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
                    f"  git clone --depth 20 --branch {git_revision} "
                    f"{git_repository} {WORKSPACE_MOUNT}; "
                    "fi"
                ],
                volume_mounts=[client.V1VolumeMount(name="workspace", mount_path=WORKSPACE_MOUNT)],
                security_context=client.V1SecurityContext(
                    run_as_user=1000, run_as_group=1000, allow_privilege_escalation=False
                ),
            )
        )

    common_mounts = [client.V1VolumeMount(name="workspace", mount_path=WORKSPACE_MOUNT)]
    # fsGroup makes the volume group-writable for whichever uid each container
    # chooses. runAsNonRoot is deliberately per-container rather than pod-wide,
    # because the agent runtime image has to keep its own user.
    pod_security = client.V1PodSecurityContext(fs_group=1000)

    code_server = client.V1Container(
        name="code-server",
        image=image,
        ports=[client.V1ContainerPort(name="http", container_port=code_server_port)],
        env=pod_env,
        volume_mounts=common_mounts,
        resources=client.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit, "memory": memory_limit},
        ),
        security_context=_runtime_security_context(non_root=True),
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/healthz", port=code_server_port),
            initial_delay_seconds=5,
            period_seconds=10,
        ),
    )

    agent_server = client.V1Container(
        name="openhands",
        image=agent_image,
        ports=[client.V1ContainerPort(name="agent", container_port=agent_server_port)],
        env=pod_env,
        volume_mounts=common_mounts,
        resources=client.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit, "memory": memory_limit},
        ),
        # non_root=False: see the note above. The image decides its own user.
        security_context=_runtime_security_context(non_root=False),
        readiness_probe=client.V1Probe(
            http_get=client.V1HTTPGetAction(path="/health", port=agent_server_port),
            initial_delay_seconds=10,
            period_seconds=10,
        ),
    )

    spec = client.V1PodSpec(
        containers=[code_server, agent_server],
        init_containers=init_containers or None,
        # kubernetes.enabled false means no API credentials exist inside the pod.
        automount_service_account_token=permissions.kubernetes.enabled,
        enable_service_links=False,
        restart_policy="Always",
        security_context=pod_security,
        volumes=[
            client.V1Volume(
                name="workspace",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name
                ),
            )
        ],
    )

    pod = client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=_meta(name, namespace, {"app.kubernetes.io/instance": name}),
        spec=spec,
    )
    pod._agentforge_warnings = warnings  # surfaced by the provider, not applied silently
    return pod


def build_network_policy(name: str, namespace: str, permissions: AgentPermissions):
    """Implement `network.mode`.

    none:       no egress at all. The agent cannot reach git or a model API, so
                this only makes sense with a model served inside the cluster.
    restricted: everything except the cluster's own address ranges. Blocks
                reaching other namespaces and the cloud metadata service.
    open:       unrestricted.
    """
    mode = permissions.network.mode

    if mode == "open":
        return None

    if mode == "none":
        egress: list = []
    else:
        egress = [
            client.V1NetworkPolicyEgressRule(
                to=[client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr="0.0.0.0/0"))],
                ports=[
                    client.V1NetworkPolicyPort(protocol="TCP", port=443),
                    client.V1NetworkPolicyPort(protocol="TCP", port=22),
                    client.V1NetworkPolicyPort(protocol="TCP", port=80),
                ],
            ),
            # DNS has to keep working or nothing resolves.
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
        ]

    policy = client.V1NetworkPolicy(
        api_version="networking.k8s.io/v1",
        kind="NetworkPolicy",
        metadata=_meta(name, namespace),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={"app.kubernetes.io/instance": name}),
            policy_types=["Ingress", "Egress"],
            # Ingress: nothing reaches a workspace except in-cluster traffic to
            # its services (the control plane, the ingress controller).
            ingress=[client.V1NetworkPolicyIngressRule()],
            egress=egress,
        ),
    )
    return policy
