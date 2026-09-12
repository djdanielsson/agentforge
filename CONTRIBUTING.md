# Contributing

## Layout

| Path | What lives there |
|---|---|
| `shared/` | domain model, schemas, settings, DB — the vocabulary everything else speaks |
| `api/` | FastAPI control plane. Desired state in, events out. No provisioning. |
| `orchestrator/` | the reconcile loop. Turns DB rows into k8s objects and agent turns. |
| `workspace-controller/` | the only code that talks to the Kubernetes API |
| `frontend/` | React dashboard |
| `docs/` | architecture and roadmap |

## Rules that keep this maintainable

1. **Only the workspace controller talks to Kubernetes.** If the API starts
   importing `kubernetes`, the abstraction has failed.
2. **The API never does slow work.** It writes a row and enqueues; the
   orchestrator reconciles. A request should never wait on a pod.
3. **Everything that happens is an event.** If the dashboard needs to show it,
   it is a row in `events` — not a log line.
4. **Reconcilers are idempotent.** `provision()` is safe to call forever.
5. **Never mount the host.** Workspaces see `/workspace` and nothing else.

## Development

```bash
make install     # uv sync --all-packages
make api         # :8000
make test        # pytest
make lint        # ruff
cd frontend && npm install && npm run dev
```

## Commits

Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
