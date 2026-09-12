# Architecture

## Principle

We deliberately **do not** rebuild the hard parts. OpenHands is the agent brain,
Dev Containers describe environments, code-server is the editor, LiteLLM routes
models. AI Workbench is the layer that connects them and makes many agents
supervisable.

## Services

### `api/` — control plane surface

FastAPI. Owns the REST + WebSocket contract the dashboard talks to. It does not
provision anything itself; it writes desired state and enqueues work.

```
POST   /projects
GET    /projects
GET    /projects/{id}
POST   /projects/{id}/agents
GET    /projects/{id}/agents
POST   /agents/{id}/tasks
GET    /agents/{id}/events        (WebSocket)
GET    /projects/{id}/git/diff
POST   /projects/{id}/git/commit
POST   /projects/{id}/git/merge
```

### `orchestrator/` — the loop

Reconciles desired state (the DB) against actual state (k8s + OpenHands).

- **project manager** — owns a project's repo + workspace lifecycle
- **agent manager** — creates/stops agents, binds them to a branch, pumps
  conversation turns into OpenHands, writes events back
- **task queue** — durable queue of units of work; one row per task, leased by a
  worker, resumable after restart
- **workspace manager** — thin client over the workspace controller
- **git manager** — diff / commit / merge, per agent branch

### `workspace-controller/` — k8s provisioning

Turns a project into an isolated world:

```
CreateProjectWorkspace()
  → Namespace (or shared ns + NetworkPolicy)
  → PersistentVolumeClaim        /workspace
  → Pod: code-server + sidecar
  → clone git repo into /workspace
  → return workspace endpoint
```

Non-negotiables for isolation:

| Resource | Access |
|---|---|
| `/workspace` | ✓ |
| internet | configurable |
| git remote | ✓ (scoped credential) |
| Kubernetes API | ✗ |
| host filesystem | ✗ |
| other projects | ✗ |

We start with a plain Go-less controller (Python + `kubernetes` client). A CRD
+ operator is a later refinement, not MVP.

### `frontend/` — dashboard

React + TypeScript + Tailwind. MVP #1 shells code-server in an iframe and shows
the agent chat beside it. Monaco replaces the iframe from MVP #3.

## Data model

```
Project
├── repository_url, default_branch
├── workspace (1:1)
├── agents[]        Agent
├── tasks[]         Task
└── settings

Agent
├── project_id, model, branch
├── workspace_id
├── status, current_task_id
└── conversation

Task
├── project_id, agent_id
├── description, status
├── result, commits[]
└── timestamps
```

Events are append-only rows, streamed over WebSocket, so the UI is replayable.

## Why these choices

**OpenHands as the engine.** It already does file manipulation, shell execution,
tool use and agent conversations, and can run in ephemeral container workspaces.
Consuming its SDK/REST beats forking it.

**k3s pods as the unit of isolation.** One project → one pod + PVC. Cheap,
familiar, NetworkPolicy-able. Never mount the host.

**LiteLLM in the middle.** One OpenAI-compatible endpoint, so an agent's `model`
field is `local-coder` or `gpt-4o` and the gateway decides where that goes —
routing, fallbacks, virtual keys and cost tracking come free.

**A plain controller before an operator.** The reconciliation logic is the same,
but a service is debuggable on day one.
