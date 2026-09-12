# AgentForge

A self-hosted AI engineering control plane. It runs many autonomous coding
agents across many projects as first-class, supervised objects: an isolated
workspace per project, a durable task queue, a permission and clarification flow
that a human answers from one dashboard, and one versioned API that every client
speaks.

Status: **early**. Read [What works today](#what-works-today) before relying on
anything here.

## What works today

Verified by the pytest suite (90 tests) and by running the API.

Implemented:

- The control plane under `/api/v1`: projects, workspace records and lifecycle
  actions, agents (including restart, permissions and a supervision summary),
  tasks, semantic actions (review / test / deploy), the git proxy, API keys,
  webhook subscriptions, the event catalog, secret references and a provider
  description endpoint.
- Persistence on SQLite (development default) or PostgreSQL, via SQLAlchemy.
  Tables are created at startup; there are no migrations yet.
- A durable task queue with leases, lease expiry and a max-attempt limit.
- Events as append-only rows, WebSocket streams with backfill, HMAC-signed
  webhook delivery with retries, and an SSRF guard on webhook URLs.
- The `WorkspaceProvider` interface with three implementations — Kubernetes,
  Podman and Local — behind `create / start / stop / destroy / exec /
  get_status / inject_secrets`.
- Kubernetes workspace objects: a namespace per project, a PersistentVolumeClaim
  at `/workspace`, a service, a pod running code-server and the OpenHands Agent
  Server, and a NetworkPolicy derived from the agent's policy. The provider
  applies the NetworkPolicy.
- Agent permissions as an enforced policy: a restrictive default, per-agent
  overrides, a read endpoint, and rejection of any policy that cannot be
  implemented safely.
- Secrets by reference: an endpoint set that stores where a credential lives,
  never its value, and provider injection that resolves references at workspace
  creation.
- A React dashboard shell, a TypeScript API client package
  (`packages/api-client`), a Helm chart (`deploy/helm/agentforge`) and the
  `agentforge` CLI.

Not implemented yet — scaffolding only, described here so nothing is oversold:

- **The OpenHands execution path.** `agentforge_orchestrator/openhands.py` is
  written against an assumed endpoint shape, not the Agent Server API, and the
  dispatch code in `agent_manager.py` references event types that are not in the
  catalog. No agent has executed a task end to end yet. The workspace pod does
  now run an OpenHands Agent Server container, so the missing piece is the
  client, not the deployment.
- **A real cluster run.** Manifests, providers and the lifecycle endpoints are
  tested, and the Local provider is exercised end to end against a real
  directory, but no workspace has been provisioned against a live Kubernetes
  cluster from this repository.
- **The dashboard is not wired to the API.** `apps/web` still uses an `/api`
  base path while the API is under `/api/v1`, and the Vite proxy rewrites `/api`
  away. The UI builds and typechecks; it has not been driven against a running
  control plane.
- **Database migrations.** Tables are created from the models at startup.
- **Podman provider in CI.** Written, but the `podman` binary is not present in
  the development environment, so the provider reports itself unavailable.

## Why

Coding agents already exist and are good: OpenHands, Codex, Claude Code. Remote
development environments exist too: Coder, DevPod, code-server. What does not
exist is a self-hosted system for running *many* agents across *many* projects
and supervising them as first-class objects — including agents that block, ask
for clarification, or request permission, with a human answering from one
dashboard.

AgentForge builds that layer and consumes the rest. It does not embed or fork an
agent; it runs the OpenHands Agent Server as the execution layer and talks to it
over HTTP and WebSocket.

## Architecture

```
                 ┌───────────────────┐  ┌──────────────────┐  ┌───────────────────┐
                 │   React web UI    │  │  agentforge CLI  │  │ Hermes / external │
                 │ dashboard + chat  │  │   fleet / ask    │  │   orchestrator    │
                 └─────────┬─────────┘  └────────┬─────────┘  └─────────┬─────────┘
                           │                     │                      │
                           └──────────┬──────────┴──────────┬───────────┘
                                      │  REST /api/v1 + WebSocket — one surface
                           ┌──────────▼───────────────────────────────────┐
                           │                    API                       │
                           │  projects · agents · tasks · permissions     │
                           │  secrets · events · keys · webhooks · git    │
                           └────────┬───────────────────────────┬─────────┘
                                    │                           │
                              desired state            append-only events
                                    │                           │
                           ┌────────▼─────────┐       ┌─────────▼───────────────┐
                           │    PostgreSQL    │       │  event stream           │
                           │  (or SQLite dev) │       │  WebSocket + webhooks   │
                           └────────▲─────────┘       └─────────────────────────┘
                                    │ reconcile
                           ┌────────┴─────────────────────┐
                           │         orchestrator         │
                           │   task queue + managers      │
                           └────────┬─────────────────────┘
                                    │  WorkspaceProvider
                                    │  (kubernetes | podman | local)
                    ┌───────────────▼──────────────────────────┐
                    │  kubernetes namespace  af-<project>      │
                    │   ┌────────────┐   ┌──────────────────┐  │
                    │   │ code-server│   │ OpenHands        │  │
                    │   │            │   │ Agent Server     │  │
                    │   └────────────┘   └────────┬─────────┘  │
                    │   PVC mounted at /workspace │            │
                    │   NetworkPolicy from policy │            │
                    └─────────────────────────────┼────────────┘
                                                  │ OpenAI-compatible request
                                         ┌────────▼────────┐
                                         │     LiteLLM     │
                                         │     gateway     │
                                         └────────┬────────┘
                                                  │
                                  Ollama · OpenAI · Anthropic
```

The API is the only control plane: the web UI, the CLI and any external
orchestrator (for example the voice assistant Hermes) are all clients of the same
versioned REST + WebSocket API. No client has a private path in.

## How the decisions fit together

| Decision | ADR |
|---|---|
| Build the control plane, not another agent | [0001](docs/adr/0001-build-the-control-plane-not-an-agent.md) |
| OpenHands Agent Server as the execution layer | [0002](docs/adr/0002-openhands-agent-server-as-execution-layer.md) |
| A `WorkspaceProvider` interface (Kubernetes, Podman, Local) | [0003](docs/adr/0003-workspace-providers.md) |
| Secrets by reference, never by value | [0004](docs/adr/0004-secrets-by-reference.md) |
| API-first: every client speaks the same API | [0005](docs/adr/0005-api-first-control-plane.md) |

## Layout

```
agentforge/
├── apps/
│   ├── api/              FastAPI control plane        (agentforge_api)
│   ├── orchestrator/     reconcile loop, task queue   (agentforge_orchestrator)
│   ├── web/              React + TypeScript + Vite dashboard
│   └── cli/              the `agentforge` CLI         (agentforge_cli)
├── packages/
│   ├── shared/           models, schemas, events, permissions, secrets   (agentforge_shared)
│   ├── workspaces/       provider contract + implementations             (agentforge_workspaces)
│   │   └── providers/    kubernetes · podman · local · registry
│   ├── agent-server/     client for the OpenHands Agent Server           (agentforge_agent_server)
│   └── api-client/       TypeScript client for the API
├── deploy/
│   ├── helm/agentforge/  Helm chart (API, orchestrator, web, Postgres)
│   └── litellm/          LiteLLM gateway configuration
├── docs/                 architecture, roadmap, API, secrets, permissions, ADRs
├── tests/                pytest suite
├── docker-compose.yml    Postgres + optional LiteLLM gateway for development
└── Makefile
```

## Quickstart

Python 3.11+ and [uv](https://docs.astral.sh/uv/). Node 22 only for the web UI.

```bash
cp .env.example .env       # sqlite by default; see the file for postgres
make install               # uv sync for every workspace member
make api                   # control plane on http://localhost:8000
```

`make api` serves the versioned API and its docs:

- interactive docs: <http://localhost:8000/api/v1/docs>
- liveness (unversioned): `curl -s localhost:8000/health`

Create a project. The API writes the desired state; the orchestrator (when it is
running) reconciles the workspace:

```bash
curl -s -X POST localhost:8000/api/v1/projects \
  -H 'content-type: application/json' \
  -d '{"name":"demo","repository_url":"https://github.com/example/demo"}'
```

The response includes a workspace record whose namespace is derived from the
configured prefix and the project slug:

```json
{
  "id": "41696374-6426-40d8-a20c-72add968a95d",
  "name": "demo",
  "slug": "demo",
  "status": "creating",
  "workspace": {
    "namespace": "af-demo",
    "status": "pending",
    "provider": "kubernetes",
    "code_server_url": null
  }
}
```

Ask what a provider can actually do, and drive the workspace directly:

```bash
curl -s localhost:8000/api/v1/providers
curl -s -X POST   localhost:8000/api/v1/projects/$PID/workspace   # 202 provision
curl -s -X DELETE localhost:8000/api/v1/projects/$PID/workspace   # 202 destroy
```

Run the rest of the stack:

```bash
make orchestrator          # reconcile loop + task queue + webhook dispatcher
make web                   # Vite dev server on http://localhost:5173
make test                  # pytest
make lint                  # ruff
```

Drive it from the CLI instead of curl:

```bash
make cli ARGS="projects"
make cli ARGS="ask demo 'Add a /health endpoint'"
make cli ARGS="fleet"
make cli ARGS="watch demo"
```

Postgres and an optional LiteLLM gateway for development:

```bash
docker compose up -d postgres
docker compose --profile gateway up -d litellm
# then set AGENTFORGE_DATABASE_URL to the postgres URL in .env
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — the layers and why they are split this way
- [docs/roadmap.md](docs/roadmap.md) — the eight milestones and where each stands
- [docs/api.md](docs/api.md) — the REST + WebSocket API
- [docs/secrets.md](docs/secrets.md) — secrets by reference
- [docs/permissions.md](docs/permissions.md) — the agent permission policy
- [docs/adr/](docs/adr/) — architecture decision records

## License

Apache-2.0. See [LICENSE](LICENSE).
