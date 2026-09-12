# Architecture

This document describes the architecture and, for every part of it, whether it
exists in the repository today. Where code is scaffolding that does not run yet,
it says so.

## The shape in one paragraph

AgentForge is a control plane. It owns projects, workspaces, agents, tasks,
permissions, secrets and events as database rows, exposes them over one versioned
REST and WebSocket API, and reconciles desired state against reality. The reality
is a Kubernetes namespace per project containing a pod and a PersistentVolumeClaim.
The pod runs code-server and an OpenHands Agent Server. The Agent Server is the
execution layer: it holds the agent conversation, tools, terminal and Git
operations, and AgentForge consumes it over HTTP rather than embedding or forking
it. Models sit behind a LiteLLM gateway, so an agent's `model` field is a logical
alias.

## Layers

### API — the single control plane

`apps/api` (package `agentforge_api`). A FastAPI application. It owns the REST
and WebSocket contract. It does not provision anything and it does not run
agents: it writes desired state, appends events, and enqueues work.

Consequences of that rule:

- A request never waits on a pod or an agent turn. Workspace lifecycle actions
  are accepted (`202`) and reconciled by the orchestrator, not performed inline.
- Every client — web UI, CLI, Hermes, CI — uses the same surface. There is no
  privileged client.
- The API is the thing worth attacking, so API keys are hashed at rest, may be
  scoped (`read` / `write` / `admin`), may be pinned to one project, and the
  plaintext is shown exactly once.

Status: implemented and covered by tests.

### Orchestrator — reconciliation

`apps/orchestrator` (package `agentforge_orchestrator`). One process running one
loop with several responsibilities:

- **Task queue** — lease a queued task, run it, record the outcome. Leases
  expire, so a crashed worker's task is re-queued; a max-attempt limit stops
  poison tasks.
- **Workspace manager** — build a `WorkspaceSpec` from the project and the
  agent's policy, resolve the secret references in scope, ask the provider to
  create the workspace, inject secrets, then observe readiness and emit the
  workspace lifecycle events.
- **Agent manager** — hand a task to the agent runtime, stream results back as
  events, commit what the agent changed, and emit the kind-specific outcome event
  (`test.completed`, `review.completed`, `deploy.completed`).
- **Webhook dispatcher** — turn matching events into delivery rows and deliver
  them with HMAC signatures and retries.

Status: the queue, the webhook dispatcher and the workspace manager are
implemented and tested. The agent manager speaks OpenHands 0.59's real contract
(see below) and emits only catalogued event types. It has **not been exercised
against a live server yet**: no task has been executed by an agent end to end, and
the permission flow is dormant — OpenHands 0.59 does not expose the
request/decision API this code was written for, so `agent.permission_required` is
never emitted today.

### Workspace providers — isolation

`packages/workspaces` (package `agentforge_workspaces`). A provider turns a
`WorkspaceSpec` into a running, isolated workspace. The interface is small and
provider-agnostic:

```
create · start · stop · destroy · exec · get_status · inject_secrets
```

Implementations, selected by `AGENTFORGE_WORKSPACE_PROVIDER` through a registry:

| Provider | Isolation | Secrets | Network policy | Volumes |
|---|---|---|---|---|
| `kubernetes` | namespace + pod | yes | yes | yes |
| `podman` | container + volume | yes | no (only `--network`) | yes |
| `local` | none — development only | refused | no | no |

Each provider advertises its capabilities honestly through `GET /api/v1/providers`
and the `capabilities()` method, so a UI can disable a control a backend cannot
honour. `local` refuses to start outside `development`/`test` and refuses to
handle secrets, because there is nowhere to keep them that the agent cannot read.

For Kubernetes, one project is one namespace named from the configured prefix plus
the project slug (`af-demo`), containing:

- a PersistentVolumeClaim mounted at `/workspace`;
- a pod with code-server and the OpenHands Agent Server, plus a git-clone init
  container when the project has a repository;
- a service exposing code-server and the Agent Server ports;
- a NetworkPolicy implementing `network.mode`.

Status: implemented. The Kubernetes path is verified at the manifest and provider
level, and the Local provider is exercised end to end against a real directory.
Nothing has been provisioned against a live cluster from this repository.

### Execution layer — OpenHands Agent Server

Each workspace pod runs an OpenHands Agent Server next to code-server. AgentForge
talks to it over REST and WebSocket. It already exposes the file, Git, terminal,
editor and agent operations a workspace needs, so AgentForge does not reimplement
them.

An agent's `model` field is a logical alias (`local-coder`, `fast`, `smart`).
The LiteLLM gateway decides where that alias goes, and owns routing, fallbacks
and cost.

`packages/agent-server` (package `agentforge_agent_server`) is the only place
that knows the Agent Server's API. It covers health, workspaces, agents,
conversations, events, files, Git, terminal and the VS Code URL, and it consumes
the event stream over WebSocket.

Two decisions make upstream drift cheap:

- every endpoint path lives in a single `Routes` dataclass, so a release that
  moves a path is a one-dataclass change rather than a hunt through the codebase;
- errors distinguish "the agent said no" from "the agent is not there", because
  a task outcome and an infrastructure failure need different retry behaviour.

The event mapper tolerates the alternate key names different Agent Server
releases emit (`type` vs `event`, `content` vs `message`), and the client accepts
an injected `httpx.Client`, which is how the test suite pins the contract against
a mock transport.

Status: **pinned against the running server, not yet exercised end to end.** The
paths and payload shapes come from the `/openapi.json` of a live OpenHands 0.59
pod, not from documentation. Two consequences are worth knowing:

- a conversation *is* the agent session, so `create_conversation` replaces
  "create an agent", and a turn is not request/response: `send_message` is queued
  and the answer arrives through the events endpoint, which `wait_for_reply`
  follows;
- the model is a server-level setting (`configure_model`), so an agent's `model`
  alias is applied to the workspace's server before its conversation starts.

The tests still use a mock transport, so they pin our reading rather than proving
the server agrees — dispatching a real task is what would prove that. Note also
that OpenHands answers *every* unknown path with its single-page app and HTTP
200, so a wrong path fails silently rather than loudly; the pinned paths are what
stands between us and that.

## Isolation and policy

Isolation is a property of the pod spec and the provider, so it is testable
without a cluster. Every rule below maps to something a provider does; a policy
that is only documented is not a policy.

| Concern | Rule | Where it is enforced |
|---|---|---|
| Workspace filesystem | `/workspace` only | pod volume mounts |
| Host filesystem | never mounted; a policy asking for it is rejected | `build_pod` raises; no `hostPath` volumes |
| Other projects | never visible; a policy asking for it is rejected | one namespace per project; `build_pod` raises |
| Kubernetes API | no access unless `kubernetes.enabled` | `automountServiceAccountToken` follows the policy (default false) |
| Egress | `network.mode` — `none`, `restricted` or `open` | NetworkPolicy applied by the Kubernetes provider |
| Privileges | no escalation, no capabilities, non-root | pod security context; `drop: ["ALL"]` |
| Secrets | only the references in scope, and only if `secrets.enabled` | provider `inject_secrets` |

See [permissions.md](permissions.md) and [secrets.md](secrets.md).

## Data model

```
Project 1──1 Workspace      repository_url, default_branch, namespace, status, provider
Project 1──N Agent          name, model (alias), branch, status, policy, session_id
Project 1──N Task           description, kind, status, attempts, lease, commits
Agent  1──N Task
SecretRef                   name, scope, project_id, agent_id, provider,
                            secret_name, key, env_var, required   (no value column)
Event                       project_id, agent_id, task_id, type, payload, created_at
ApiKey                      name, prefix, key_hash, scopes, project_id
Webhook 1──N WebhookDelivery
```

`Agent.policy` holds the serialised permission policy; `SecretRef` has
deliberately no column that could hold a secret value.

## Events

Events are append-only rows and are the integration point of the whole system.
The WebSocket stream and the outbound webhooks use the same wire format, so a
subscriber never needs to know which one it is attached to. The catalog is
served from `GET /api/v1/events/catalog`; see [api.md](api.md) for the list.

Design rules that follow from this:

- If the dashboard needs to show it, it is an event row, not a log line.
- An event is committed in the same transaction as the state change it
  describes, so the timeline cannot disagree with the state.
- Webhook delivery is idempotent: the unique `(webhook_id, event_id)` pair is
  the dedupe key, and retry state lives in `webhook_deliveries`.

Known gap: the catalog lists 23 types and the `EventType` enum adds
`agent.status`, but the unfinished agent manager references four types that exist
in neither (`TASK_OUTPUT`, `PERMISSION_REQUEST`, `AGENT_QUESTION`,
`GIT_COMMIT`). Those code paths raise. The fix is to emit the catalogued types
(`task.status`, `agent.message`, `agent.waiting`, `agent.permission_required`,
`commit.created`) instead.

## Model routing

LiteLLM sits between agents and model providers as an OpenAI-compatible gateway.
It is configured in `deploy/litellm/config.yaml`, where an alias maps to a
provider and model:

```yaml
model_list:
  - model_name: local-coder
    litellm_params:
      model: ollama/qwen2.5-coder:32b
      api_base: http://ollama:11434
  - model_name: smart
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: os.environ/ANTHROPIC_API_KEY
```

Nothing in AgentForge changes if a provider is swapped, because the alias is the
only thing an agent stores.

## Deployment

`deploy/helm/agentforge` is a Helm chart with deployments for the API, the
orchestrator and the web UI, a Postgres StatefulSet, a service account with the
cluster role the workspace provider needs, an ingress and the configuration. It
is a deployment artifact, not a tested installation.

## Rules that keep this maintainable

1. **Only a workspace provider talks to Kubernetes.** If the API starts
   importing `kubernetes`, the abstraction has failed.
2. **The API never does slow work.** It writes a row and enqueues; the
   orchestrator reconciles.
3. **Everything that happens is an event.**
4. **Reconcilers are idempotent.** `create` and `destroy` converge.
5. **Never mount the host.** A workspace sees `/workspace` and nothing else.
6. **Secrets move by reference.** The API can name a credential; it can never
   return its value.
