"""The DevPod Kubernetes workspace provider (SPEC §4, §6).

DevPod OSS is a *client-side* CLI: it provisions by shelling out, and its plugin
protocol is a subprocess stream rather than an API. So this provider is a
subprocess wrapper around `devpod`, and it is the only file in the system that
knows that.

What DevPod actually creates here (confirmed experimentally, see docs/FINDINGS.md):

    namespace <KUBERNETES_NAMESPACE>   one per project, via --provider-option
      pod     devpod-<context>-<hash>  image from devcontainer.json
      pvc     devpod-<context>-<hash>  <DISK_SIZE>, mounted at /workspaces/<id>

It creates no Service, no Ingress, no NetworkPolicy and no Role: a pod runs with
the namespace `default` service account and a projected token. Two of those are
gaps the control plane closes itself, because SPEC §18 and §28 require the
isolation DevPod does not provide:

* `_ensure_network_policy` writes a default-deny-ingress policy with a narrow
  egress allowlist (DNS, the LLM gateway, Git over 443/22).
* `_ensure_t3_ingress` publishes T3 Code on the project's own tailnet hostname,
  which is per-namespace and therefore per-project.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from ..config import Settings, get_settings
from .base import ExecResult, ProviderError, WorkspaceProvider, WorkspaceSpec, WorkspaceState
from .kubernetes_common import DEVPOD_POD_LABEL, Cluster, write_kubeconfig

log = logging.getLogger(__name__)

CONTAINER = "devpod"
T3_PORT = 4096
READY_TIMEOUT = 900


class DevPodKubernetesProvider(WorkspaceProvider):
    name = "devpod"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cluster: Cluster | None = None

    # --- plumbing ---------------------------------------------------------

    @property
    def cluster(self) -> Cluster:
        if self._cluster is None:
            self._cluster = Cluster()
        return self._cluster

    def available(self) -> tuple[bool, str]:
        binary = shutil.which(self.settings.devpod_binary)
        if not binary:
            return False, f"devpod binary not found ({self.settings.devpod_binary} not on PATH)"
        return True, binary

    def _env(self) -> dict[str, str]:
        self.settings.devpod_home.mkdir(parents=True, exist_ok=True)
        kubeconfig = write_kubeconfig(self.settings.kubeconfig_path)
        env = dict(os.environ)
        env["DEVPOD_HOME"] = str(self.settings.devpod_home)
        env["KUBECONFIG"] = str(kubeconfig)
        # DevPod's IDE tunnel and activity tracking are useless to us and hold
        # ports; the pod is what matters.
        env.setdefault("DEVPOD_DISABLE_DAEMON", "true")
        return env

    def _run(
        self, args: list[str], *, timeout: int = 120, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        ok, binary = self.available()
        if not ok:
            raise ProviderError(binary)
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            env=self._env(),
            timeout=timeout,
            cwd="/tmp",
        )
        if check and result.returncode != 0:
            raise ProviderError(
                f"devpod {' '.join(args[:2])} failed with exit {result.returncode}",
                detail={
                    "stderr": (result.stderr or "")[-2000:],
                    "stdout": (result.stdout or "")[-2000:],
                },
            )
        return result

    def _spawn(self, args: list[str], log_path: Path) -> subprocess.Popen:
        """Start `devpod up` without waiting for it.

        `devpod up` is a foreground client that holds tunnels and exits after an
        idle period; the workspace pod outlives it. Blocking a request thread on
        it would be wrong, so the caller polls the cluster instead.
        """
        ok, binary = self.available()
        if not ok:
            raise ProviderError(binary)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("ab")
        handle.write(f"\n=== {' '.join(shlex.quote(a) for a in args)} ===\n".encode())
        handle.flush()
        return subprocess.Popen(
            [binary, *args],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=self._env(),
            cwd="/tmp",
            start_new_session=True,
        )

    # --- lifecycle --------------------------------------------------------

    def _up_args(self, spec: WorkspaceSpec, *, force: bool = False) -> list[str]:
        args = [
            "up",
            spec.definition_dir,
            "--id",
            spec.reference,
            "--provider",
            "kubernetes",
            "--provider-option",
            f"KUBERNETES_NAMESPACE={spec.reference}",
            "--provider-option",
            "CREATE_NAMESPACE=true",
            "--provider-option",
            f"DISK_SIZE={spec.storage}",
            "--devcontainer-path",
            ".devcontainer/devcontainer.json",
            "--log-output",
            "plain",
        ]
        if force:
            args.append("--reset")
        return args

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        self._ensure_namespace(spec)
        self._ensure_network_policy(spec)
        self._spawn(
            self._up_args(spec),
            self.settings.data_dir / "logs" / f"{spec.reference}-devpod-up.log",
        )
        return self.wait_ready(spec.reference, timeout=READY_TIMEOUT)

    def start(self, reference: str) -> WorkspaceState:
        """DevPod cannot pause a pod, so a stopped workspace is re-`up`ed.

        The PVC survives `devpod stop`, so `.fleet/` and the agent's repository
        come back with the pod.
        """
        definition = self.settings.data_dir / "projects" / reference / "workspace"
        if not definition.exists():
            raise ProviderError(
                f"no stored workspace definition for {reference}; recreate the workspace"
            )
        self._spawn(
            ["up", str(definition), "--id", reference, "--provider", "kubernetes",
             "--provider-option", f"KUBERNETES_NAMESPACE={reference}",
             "--provider-option", "CREATE_NAMESPACE=true",
             "--log-output", "plain"],
            self.settings.data_dir / "logs" / f"{reference}-devpod-up.log",
        )
        return self.wait_ready(reference, timeout=READY_TIMEOUT)

    def stop(self, reference: str) -> None:
        result = self._run(["stop", reference, "--provider", "kubernetes"], timeout=180, check=False)
        if result.returncode != 0:
            log.warning("devpod stop %s returned %s: %s", reference, result.returncode, result.stderr[-500:])
        # `devpod stop` removes the pod; a pod that lingers would leave the
        # status reporting Running after a stop.
        pod = self.cluster.find_pod(reference, f"{DEVPOD_POD_LABEL}=true")
        if pod:
            from kubernetes.client.exceptions import ApiException

            try:
                self.cluster.core.delete_namespaced_pod(pod, reference)
            except ApiException as exc:
                if exc.status != 404:
                    raise

    def restart(self, reference: str) -> WorkspaceState:
        self.stop(reference)
        return self.start(reference)

    def destroy(self, reference: str) -> None:
        self._run(["delete", reference, "--provider", "kubernetes"], timeout=180, check=False)
        # The namespace is the isolation boundary, so destroying the workspace
        # must take it with it.
        self.cluster.delete_namespace(reference)

    def status(self, reference: str) -> WorkspaceState:
        state = WorkspaceState(reference=reference, provider=self.name)
        if not self.cluster.namespace_exists(reference):
            state.status = "pending"
            state.detail = {"reason": "namespace not created yet"}
            return state

        pod = self.cluster.find_pod(reference, f"{DEVPOD_POD_LABEL}=true")
        if not pod:
            state.status = "stopped"
            state.detail = {"reason": "no devpod pod in namespace"}
            return state

        state.pod_name = pod
        state.pvc_name = pod
        state.service_name = f"{reference}-ws"
        phase = self.cluster.pod_phase(reference, pod)
        ready = self.cluster.pod_ready(reference, pod)
        state.ready = ready
        state.status = "ready" if ready else ("failed" if phase == "Failed" else "provisioning")
        if state.status == "failed":
            state.error = self.cluster.pod_logs(reference, pod, tail=20)
        state.detail = {"phase": phase, "namespace": reference, "container": CONTAINER}

        state.url = f"http://{reference}-ws.{reference}.svc.cluster.local:{T3_PORT}"
        if self.settings.t3_enabled:
            state.t3_url = f"https://{reference}-t3-{self.settings.tailnet_domain}"
        return state

    def wait_ready(self, reference: str, timeout: int = READY_TIMEOUT) -> WorkspaceState:
        deadline = time.monotonic() + timeout
        state = self.status(reference)
        while time.monotonic() < deadline:
            state = self.status(reference)
            if state.ready or state.status == "failed":
                if state.ready:
                    # Publish and isolate only once the pod exists: an Ingress or
                    # a NetworkPolicy with no backend to select is a lie.
                    self._ensure_service(reference, state.pod_name)
                    if self.settings.t3_enabled:
                        self._ensure_t3_ingress(reference)
                return state
            time.sleep(3)
        state.status = state.status if state.ready else "provisioning"
        return state

    def connect(self, reference: str) -> str:
        state = self.status(reference)
        return state.t3_url or state.url

    # --- execution --------------------------------------------------------

    def execute(
        self, reference: str, command: list[str], *, container: str | None = None
    ) -> ExecResult:
        pod = self.cluster.find_pod(reference, f"{DEVPOD_POD_LABEL}=true")
        if not pod:
            return ExecResult(" ".join(command), 127, stderr=f"no workspace pod in {reference}")
        try:
            output = self.cluster.exec(reference, pod, command, container=container or CONTAINER)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed command
            return ExecResult(" ".join(command), 1, stderr=f"{type(exc).__name__}: {exc}")
        return ExecResult(" ".join(command), 0, stdout=output)

    def get_logs(self, reference: str, *, tail: int = 200) -> str:
        pod = self.cluster.find_pod(reference, f"{DEVPOD_POD_LABEL}=true")
        if not pod:
            return "<no workspace pod>"
        return self.cluster.pod_logs(reference, pod, container=CONTAINER, tail=tail)

    # --- the pieces DevPod does not provide -------------------------------

    def _ensure_namespace(self, spec: WorkspaceSpec) -> None:
        self.cluster.ensure_namespace(
            spec.reference,
            {
                **self.settings.labels,
                "fleet.io/project": spec.project_name,
                "fleet.io/project-id": spec.project_id,
                "pod-security.kubernetes.io/enforce": "baseline",
            },
        )

    def _ensure_network_policy(self, spec: WorkspaceSpec) -> None:
        """Default-deny ingress, narrow egress.

        Without this a workspace reaches every service in the cluster, including
        other projects' workspaces and the API servers of unrelated apps.
        """
        from kubernetes import client
        from kubernetes.client.exceptions import ApiException

        labels = {**self.settings.labels, "fleet.io/project": spec.project_name}
        gateway_ns, _, _ = self._gateway_parts()
        body = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(
                name=f"{spec.reference}-egress", namespace=spec.reference, labels=labels
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
                                    match_labels={"kubernetes.io/metadata.name": gateway_ns}
                                )
                            )
                        ],
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=4000)],
                    ),
                    # The control plane has to be reachable too: the agent's LLM
                    # calls go through it so they can be attributed (SPEC §13).
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
                f"{spec.reference}-egress", spec.reference, body
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.cluster.networking.create_namespaced_network_policy(spec.reference, body)

    def _gateway_parts(self) -> tuple[str, str, int]:
        """Split the configured gateway URL into namespace/service/port."""
        url = self.settings.llm_gateway_url
        host = url.split("//", 1)[-1].split("/", 1)[0]
        authority, _, port = host.partition(":")
        service, _, namespace = authority.partition(".")
        return namespace or "agentforge", service or "agentforge-llm", int(port or 4000)

    def _ensure_service(self, reference: str, pod: str) -> None:
        self.cluster.apply_service(
            reference,
            f"{reference}-ws",
            {DEVPOD_POD_LABEL: "true"},
            {"t3": T3_PORT},
            {**self.settings.labels, "fleet.io/role": "workspace"},
        )

    def _ensure_t3_ingress(self, reference: str) -> None:
        self.cluster.apply_ingress(
            reference,
            f"{reference}-t3",
            f"{reference}-ws",
            T3_PORT,
            {**self.settings.labels, "fleet.io/role": "t3"},
        )

    def capabilities(self) -> dict[str, object]:
        ok, detail = self.available()
        return {
            "provider": self.name,
            "component": "devpod CLI + devcontainer",
            "available": ok,
            "binary": detail if ok else "",
            "reason": "" if ok else detail,
            "isolation": "one Kubernetes namespace per project (via --provider-option)",
            "create": True,
            "start_stop": True,
            "exec": True,
            "persistent_volumes": True,
            "network_policy": "added by the control plane, not by DevPod",
            "t3": self.settings.t3_enabled,
            "notes": (
                "DevPod does not create a Service, Ingress, NetworkPolicy or Role; "
                "the control plane creates the first three for isolation and reachability."
            ),
        }
