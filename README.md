# AgentForge

A self-hosted **AI engineering control plane**: create projects, spin up isolated
Kubernetes workspaces, and run multiple autonomous coding agents against them —
with the human supervising from one dashboard.

> Status: **early scaffold**. MVP #1 in progress. The repo currently contains the
> service skeleton, data model, API, orchestrator loop and workspace controller.

## Why

Existing tools give you an agent (OpenHands, Codex, Claude Code) or an editor
(code-server, Cursor). Neither gives you a **control plane** for running many
agents across many projects and supervising them.

AgentForge builds the part that doesn't exist: projects, agents, tasks,
workspaces, approvals and git — as first-class objects.

## Architecture

```
                    ┌─────────────────────────────┐
                    │          WEB UI             │
                    │  Projects · Agents · Tasks  │
                    │  Editor · Terminal · Diffs  │
                    └──────────────┬──────────────┘
                                   │  REST + WebSocket
                    ┌──────────────▼──────────────┐
                    │        ORCHESTRATOR         │
                    │  project / agent / task     │
                    │  workspace / git managers   │
                    └───────┬─────────────┬───────┘
                            │             │
              ┌─────────────▼──┐      ┌───▼──────────────┐
              │ OpenHands agent│      │ Workspace        │
              │  (per agent)   │      │ Controller       │
              └────────┬───────┘      └───┬──────────────┘
                       │                  │ k8s API
                       ▼                  ▼
                ┌────────────┐    ┌──────────────────┐
                │ LLM Gateway│    │ k3s Pod + PVC    │
                │  (LiteLLM) │    │ code-server      │
                └─────┬──────┘    │ /workspace only  │
                      │           └──────────────────┘
          ┌───────────┼───────────┐
          ▼           ▼           ▼
        Ollama      OpenAI     Anthropic
```

## We build on, not from scratch

| Concern | We use | We build |
|---|---|---|
| Agent brain | OpenHands Agent SDK | orchestration around it |
| Workspace isolation | Dev Container spec, k3s pods + PVCs | the controller that renders/provisions them |
| Editor | code-server (Monaco later) | the shell that frames it |
| Model routing | LiteLLM | per-agent/per-project model policy |
| Control plane | — | **this repo** |

## Layout

```
agentforge/
├── api/                  FastAPI — the control-plane HTTP + WebSocket surface
├── orchestrator/         Task queue, agent lifecycle, git management
├── workspace-controller/ k8s provisioning of per-project workspaces
├── shared/               Shared models, schemas, settings, DB
├── frontend/             React + TypeScript dashboard
├── docs/                 Architecture + roadmap
└── deploy/               Helm / Argo CD (later)
```

## Quickstart (dev)

```bash
make install      # uv sync across the workspace
make api          # run the API on :8000  (sqlite by default)
make orchestrator # run the orchestrator loop
```

Then:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/projects \
  -H 'content-type: application/json' \
  -d '{"name":"demo","repository_url":"https://github.com/example/demo"}'
```

Interactive API docs: <http://localhost:8000/docs>

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md). Short version:

- **MVP #1** — one project, one workspace, one agent, code-server + terminal, git diff in the UI
- **MVP #2** — many agents per project, branches, task queue, approvals
- **MVP #3** — the dashboard as the centrepiece

## License

Apache-2.0
