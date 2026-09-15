"""Test doubles.

`FakeWorkspaceProvider` is not a mock of a provider's internals: it is a real
implementation of the `WorkspaceProvider` interface that keeps its state in
memory. That it can be dropped in for DevPod — with no change above the
interface — is the point of SPEC §6.
"""

from __future__ import annotations

from fleet_core.agents.base import AgentSpec, TaskOutcome, TaskRequest
from fleet_core.agents.opencode import OpenCodeProvider
from fleet_core.workspaces.base import (
    ExecResult,
    WorkspaceProvider,
    WorkspaceSpec,
    WorkspaceState,
)


class FakeWorkspaceProvider(WorkspaceProvider):
    name = "fake"

    def __init__(self, settings=None) -> None:
        self.settings = settings
        self.workspaces: dict[str, WorkspaceState] = {}
        self.commands: list[tuple[str, list[str]]] = []
        self.destroyed: list[str] = []
        #: Ordered trace of lifecycle calls, so a test can assert that the
        #: isolation boundary is built before the pod that sits inside it.
        self.lifecycle: list[str] = []
        #: (reference, command, stdin) for every write that carried a payload.
        self.written: list[tuple[str, str, str]] = []
        #: Commands whose output the test wants to control, matched on a substring.
        #: The default answers mimic what a real workspace does for the two
        #: commands the opencode provider depends on.
        self.responses: list[tuple[str, str, int]] = [
            ("git worktree add", "WORKTREE_READY /workspaces/x/.fleet/worktrees/backend\n", 0),
            ("opencode run", '{"type":"text","text":"done"}\n', 0),
        ]

    def respond(self, needle: str, stdout: str, exit_code: int = 0) -> None:
        self.responses.insert(0, (needle, stdout, exit_code))

    def prepare(self, spec: WorkspaceSpec) -> None:
        self.lifecycle.append(f"prepare:{spec.reference}")

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        self.lifecycle.append(f"create:{spec.reference}")
        state = WorkspaceState(
            reference=spec.reference,
            provider=self.name,
            status="ready",
            ready=True,
            pod_name=f"{spec.reference}-ws",
            pvc_name=f"{spec.reference}-workspace",
            service_name=f"{spec.reference}-ws",
            url=f"http://{spec.reference}-ws.{spec.reference}.svc.cluster.local:4096",
            t3_url=f"https://{spec.reference}-t3-tail.ts.net",
            detail={"spec_image": spec.image, "environment_keys": sorted(spec.environment)},
        )
        self.workspaces[spec.reference] = state
        return state

    def status(self, reference: str) -> WorkspaceState:
        return self.workspaces.get(
            reference,
            WorkspaceState(reference=reference, provider=self.name, status="pending"),
        )

    def start(self, reference: str) -> WorkspaceState:
        state = self.status(reference)
        state.status, state.ready = "ready", True
        self.workspaces[reference] = state
        return state

    def stop(self, reference: str) -> None:
        state = self.status(reference)
        state.status, state.ready = "stopped", False
        self.workspaces[reference] = state

    def destroy(self, reference: str) -> None:
        self.destroyed.append(reference)
        self.workspaces.pop(reference, None)

    def execute(self, reference, command, *, container=None, stdin_data=None) -> ExecResult:
        if stdin_data is not None:
            self.written.append((reference, " ".join(command), stdin_data))
        self.commands.append((reference, list(command)))
        script = " ".join(command)
        for needle, stdout, exit_code in self.responses:
            if needle and needle in script:
                return ExecResult(script, exit_code, stdout=stdout)
        return ExecResult(script, 0, stdout="ok\n")

    def get_logs(self, reference: str, *, tail: int = 200) -> str:
        return "fake workspace logs\n"

    def capabilities(self) -> dict:
        return {"provider": self.name, "isolation": "in-memory", "exec": True}


class FakeAgentProvider(OpenCodeProvider):
    """OpenCode's real logic, against a fake workspace."""

    name = "opencode"


class ExplodingWorkspaceProvider(FakeWorkspaceProvider):
    """A provider that fails the way a real one does: with a typed error."""

    name = "exploding"

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        return WorkspaceState(
            reference=spec.reference,
            provider=self.name,
            status="failed",
            error="simulated scheduler rejection",
        )


class FakeKubernetesCore:
    """Just enough of the CoreV1 API for the credential path.

    `fleet_core.secrets` is the only module that touches Secrets directly, and
    this is what lets the API be tested without a cluster.
    """

    def __init__(self) -> None:
        self.secrets: dict[tuple[str, str], object] = {}

    def create_namespaced_secret(self, namespace, body):
        from kubernetes.client.exceptions import ApiException

        key = (namespace, body.metadata.name)
        if key in self.secrets:
            raise ApiException(status=409, reason="AlreadyExists")
        self.secrets[key] = body

    def replace_namespaced_secret(self, name, namespace, body):
        self.secrets[(namespace, name)] = body

    def read_namespaced_secret(self, name, namespace):
        from kubernetes.client.exceptions import ApiException

        key = (namespace, name)
        if key not in self.secrets:
            raise ApiException(status=404, reason="NotFound")
        return self.secrets[key]

    def delete_namespaced_secret(self, name, namespace):
        self.secrets.pop((namespace, name), None)


class FakeCluster:
    """A cluster handle that never leaves the process."""

    def __init__(self) -> None:
        self.core = FakeKubernetesCore()
        self.networking = None
        self.rbac = None
        self.namespaces: set[str] = set()
        #: One pod per namespace, for the providers that look one up by label.
        #: The default is non-empty because most tests care about what happens
        #: *after* a pod exists; a test that wants "no environment" empties it.
        self.pods: dict[str, str] = {}
        self.pod_is_ready = True
        self.exec_log: list[list[str]] = []

    def find_pod(self, namespace: str, label_selector: str) -> str:  # noqa: ARG002
        return self.pods.get(namespace, "fleet-t3-0")

    def pod_ready(self, namespace: str, pod: str) -> bool:  # noqa: ARG002
        return self.pod_is_ready

    def pod_logs(self, namespace: str, pod: str, **kwargs) -> str:  # noqa: ARG002
        return "fake environment logs\n"

    def exec(self, namespace, pod, command, **kwargs):  # noqa: ARG002
        from fleet_core.workspaces.kubernetes_common import ExecOutcome

        self.exec_log.append(list(command))
        return ExecOutcome(command=" ".join(command), output="", exit_code=0)

    def namespace_exists(self, name: str) -> bool:
        return name in self.namespaces

    def ensure_namespace(self, name: str, labels: dict[str, str]) -> None:
        self.namespaces.add(name)

    def delete_namespace(self, namespace: str) -> None:
        self.namespaces.discard(namespace)


def agent_spec(reference: str = "fleet-demo") -> AgentSpec:
    return AgentSpec(
        agent_id="agt_test",
        name="backend",
        project_id="prj_test",
        project_name="demo",
        workspace_reference=reference,
        model="local-coder",
    )


def task_request() -> TaskRequest:
    return TaskRequest(task_id="tsk_test", prompt="say hello")


__all__ = [
    "ExplodingWorkspaceProvider",
    "FakeAgentProvider",
    "FakeCluster",
    "FakeWorkspaceProvider",
    "TaskOutcome",
    "agent_spec",
    "task_request",
]
