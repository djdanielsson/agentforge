# Fleet control plane

A control plane for a fleet of AI coding agents. It manages **projects**,
**workspaces**, **agents**, **tasks** and **events**, and it treats DevPod, T3
Code, OpenCode and LiteLLM as components it orchestrates rather than as its own
architecture (SPEC §51).

This is the proof-of-concept for the specification in
[`docs/fleet/SPEC.md`](../docs/fleet/SPEC.md). What was validated experimentally
before any of it was written — and what is confirmed versus still assumed — is in
[`docs/FINDINGS.md`](docs/FINDINGS.md). Read that first if you want to know what
is real.

```
Project ──▶ Workspace ──▶ Agent ──▶ Task
  (ns)        (DevPod pod           (opencode run
              + PVC + Service         in a git worktree)
              + NetworkPolicy)              │
                                            ▼
                                    fleet LLM proxy ──▶ LiteLLM ──▶ models
                                    (attribution)        (routing)
```

## How to try it

The control plane runs in the `fleet` namespace and is published on the tailnet:

**<https://fleet-ts-ingress.tail7f3c08.ts.net>**

(Confirm the exact name with
`kubectl -n fleet get ingress ts -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'`.)

You need the API token. It is in the `fleet-api` Secret:

```bash
kubectl -n fleet get secret fleet-api -o jsonpath='{.data.api-token}' | base64 -d
```

### What to click

1. **Open the URL.** You land on a single page: a project list, a create form, a
   task form, and a live activity feed fed by the event stream.
2. **Create a project.** Enter a name (`demo-alpha`) and optionally a repository
   URL. It returns immediately with the workspace `pending`; provisioning runs in
   the background — that is the desired-state model of SPEC §34, not a hang.
   Within a minute or two the badge goes `ready`.
3. **Watch the workspace appear.** The project card shows the namespace
   (`fleet-demo-alpha`), the provider, and the agent count. `status` refreshes it,
   `logs` shows the pod's output.
4. **Add an agent.** `POST /api/v1/projects/demo-alpha/agents` — or create one from
   the API. An `opencode` agent is a coding agent; a `t3code` agent is a control
   surface.
5. **Run a task.** Pick the agent, type a prompt, submit. The activity feed shows
   `task.created → task.started → task.completed` and the task card fills in with
   the agent's answer, the git branch and the commit it made.
6. **Open T3 Code.** Once the workspace is ready, the `open T3 →` link appears on
   the project card, pointing at the project's own tailnet hostname.

### With curl

```bash
export FLEET=https://fleet-ts-ingress.tail7f3c08.ts.net
export TOKEN=$(kubectl -n fleet get secret fleet-api -o jsonpath='{.data.api-token}' | base64 -d)
alias fleet='curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"'

# what is deployed, and which provider is actually in use
fleet $FLEET/api/v1/health
fleet $FLEET/api/v1/providers | jq

# models the gateway resolves (logical names, not vendors — SPEC §12)
fleet $FLEET/api/v1/models | jq

# a project, its workspace, its agents, its tasks
fleet $FLEET/api/v1/projects | jq
fleet $FLEET/api/v1/projects/demo-alpha | jq
fleet $FLEET/api/v1/projects/demo-alpha/agents | jq
fleet $FLEET/api/v1/projects/demo-alpha/tasks | jq

# create one
fleet -X POST $FLEET/api/v1/projects -d '{
  "name": "demo-alpha",
  "repository": {"url": "https://github.com/djdanielsson/agentforge", "branch": "fleet"},
  "llmPolicy": {"mode": "localOnly"},
  "credentials": [{"name": "github"}]
}' | jq

# give it a project credential (the value goes to a Kubernetes Secret and is
# never returned by any endpoint)
fleet -X PUT $FLEET/api/v1/projects/demo-alpha/credentials/github \
      -d '{"value": "ghp_..."}' | jq

# workspace lifecycle
fleet -X POST $FLEET/api/v1/projects/demo-alpha/workspace/refresh | jq '{status, pod, t3_url}'
fleet -X POST $FLEET/api/v1/projects/demo-alpha/workspace/stop | jq '{status}'
fleet -X POST $FLEET/api/v1/projects/demo-alpha/workspace/start | jq '{status}'
fleet $FLEET/api/v1/projects/demo-alpha/workspace/logs?tail=50 | jq -r .logs

# run a command in the workspace (also how isolation is checked)
fleet -X POST $FLEET/api/v1/projects/demo-alpha/workspace/exec \
      -d '{"command": "id && ls -la /workspaces"}' | jq -r .stdout

# add an agent and assign it work
fleet -X POST $FLEET/api/v1/projects/demo-alpha/agents \
      -d '{"name": "backend", "provider": "opencode", "role": "backend", "model": "local-coder"}' | jq
fleet -X POST $FLEET/api/v1/agents/<agent-id>/tasks \
      -d '{"prompt": "Create hello.txt containing hello world, then commit it."}' | jq
fleet $FLEET/api/v1/tasks/<task-id> | jq '{status, result, git_branch, git_commit}'

# LLM usage, attributed (SPEC §13)
fleet $FLEET/api/v1/projects/demo-alpha/usage | jq
fleet $FLEET/api/v1/usage | jq

# the event log, and the live stream
fleet "$FLEET/api/v1/events?limit=20" | jq '.items[] | "\(.type)\t\(.message)"'
curl -N -H "Authorization: Bearer $TOKEN" $FLEET/api/v1/events/stream

# delete the project — this deletes its namespace, and everything in it
fleet -X DELETE $FLEET/api/v1/projects/demo-alpha | jq
```

### Two projects, and the isolation check

```bash
for name in demo-alpha demo-beta; do
  fleet -X POST $FLEET/api/v1/projects -d "{\"name\": \"$name\"}" >/dev/null
done
# wait for both to be ready, then:
fleet -X PUT $FLEET/api/v1/projects/demo-alpha/credentials/github -d '{"value":"alpha-only"}' >/dev/null
fleet -X POST $FLEET/api/v1/projects/demo-alpha/workspace/exec -d '{"command":"env | grep -c GITHUB_TOKEN"}' | jq -r .stdout
fleet -X POST $FLEET/api/v1/projects/demo-beta/workspace/exec -d '{"command":"env | grep -c GITHUB_TOKEN"}' | jq -r .stdout
# alpha sees 1, beta sees 0: credentials are project-scoped (SPEC §15, §38)
```

## The API

Resource-oriented and versioned (SPEC §22); the UI uses these same routes, so
there is no private API.

| method | path | what it does |
| --- | --- | --- |
| GET | `/api/v1/health` | liveness and configuration |
| GET | `/api/v1/providers` | every provider, its capabilities, and which is selected |
| GET | `/api/v1/models` | logical model names the gateway resolves |
| GET/POST | `/api/v1/projects` | list, create |
| GET/PATCH/DELETE | `/api/v1/projects/{project}` | read, delete |
| GET | `/api/v1/projects/{project}/workspace` | workspace state |
| POST | `/api/v1/projects/{project}/workspace/{start\|stop\|restart\|refresh\|reapply}` | lifecycle |
| POST | `/api/v1/projects/{project}/workspace/exec` | run a command in the workspace |
| GET | `/api/v1/projects/{project}/workspace/logs` | pod logs |
| GET/POST | `/api/v1/projects/{project}/agents` | agents |
| GET/POST | `/api/v1/projects/{project}/tasks` | tasks |
| GET | `/api/v1/projects/{project}/usage` | LLM usage, rolled up |
| GET | `/api/v1/projects/{project}/credentials` | credential **metadata only** |
| PUT/DELETE | `/api/v1/projects/{project}/credentials/{name}` | set, forget |
| GET | `/api/v1/agents/{agent}` | agent (ids are globally unique) |
| POST | `/api/v1/agents/{agent}/start`, `/stop` | lifecycle |
| POST | `/api/v1/agents/{agent}/tasks` | submit without naming the project (SPEC §23) |
| GET | `/api/v1/agents/{agent}/logs` | agent output |
| GET | `/api/v1/tasks/{task}` | task |
| POST | `/api/v1/tasks/{task}/cancel` | cancel |
| GET | `/api/v1/events`, `/api/v1/events/stream` | event log, SSE stream |
| POST | `/llm/v1/chat/completions` | the attributing LLM proxy (project token, not the operator token) |

Interactive docs at `/docs`.

## Layout

```
fleet/
  packages/core/src/fleet_core/       domain, persistence, providers
    config.py   models.py   db.py     projects, workspaces, agents, tasks, events, usage
    service.py                        orchestration: the control plane's own logic
    runner.py                         task execution and its lifecycle
    events.py                         event bus: durable log + SSE fan-out
    llm.py                            gateway client, policy, usage, project tokens
    secrets.py                        credentials by reference (values never leave Secrets)
    workspaces/                       WorkspaceProvider: base, devpod, kubernetes, registry
    agents/                           AgentProvider: base, opencode, t3code, shell, registry
  apps/control-plane/src/fleet_api/   FastAPI app, routers, single-page UI
  deploy/helm/fleet/                  the chart
  deploy/images/workspace/            the native provider's workspace image
  deploy/build.py deploy/deploy.py    Kaniko build, render-and-apply deploy
  tests/                              offline suite: API, providers, usage
  docs/FINDINGS.md                    what was validated, and what is assumed
```

## Running it yourself

### Tests and lint

```bash
cd fleet
uv sync --all-packages --group dev
uv run pytest -q
uv run ruff check .
```

The suite needs no cluster, no DevPod and no gateway: it runs the real API
against a fake workspace provider, which is also the demonstration that the
provider interface is real (SPEC §6).

### Deploy

```bash
# 1. build both images in-cluster (Kaniko → registry.registry.svc:5000)
python3 fleet/deploy/build.py fleet <git-sha>

# 2. render the chart and apply it (needs a helm binary; there is none in-cluster)
python3 fleet/deploy/deploy.py --tag <git-sha>
```

`deploy.py` copies the LiteLLM master key from `agentforge/agentforge-llm` into
this namespace's own Secret, so no key value is ever written to a file, a command
line or a log. It generates the API token on first run and keeps it.

## Configuration

All settings come from the environment; `deploy/helm/fleet/values.yaml` is the
single place to change them. The ones worth knowing:

| variable | default | meaning |
| --- | --- | --- |
| `FLEET_NAMESPACE` | `fleet` | where the control plane itself runs |
| `FLEET_NAMESPACE_PREFIX` | `fleet-` | project namespaces are `<prefix><project-slug>` |
| `FLEET_WORKSPACE_PROVIDER` | `devpod` | `devpod` or `kubernetes` |
| `FLEET_WORKSPACE_IMAGE` | `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` | the devcontainer image |
| `FLEET_LLM_GATEWAY_URL` | LiteLLM in `agentforge` | routing; the control plane never talks to a model vendor |
| `FLEET_LLM_MODELS` | `local-coder,fast,smart` | logical aliases workspaces may request |
| `FLEET_API_TOKEN` | generated | required for `/api/v1`; `/llm/v1` uses project tokens |
| `FLEET_T3_ENABLED` | `true` | publish each workspace's T3 Code on the tailnet |

## Security posture

- **Projects are isolated by namespace.** Each project gets its own namespace, a
  PVC, a Service and a default-deny NetworkPolicy. Egress is limited to DNS, the
  control plane's proxy and the gateway, plus 443/22 for Git and package
  registries.
- **Workspaces hold no cluster identity.** The native provider sets
  `automountServiceAccountToken: false` and a Role that can only read ConfigMaps
  in its own namespace. (DevPod's own pod still runs as the namespace `default`
  service account with a projected token — a gap, recorded in FINDINGS §1.2, and
  the reason the DePod provider adds a NetworkPolicy rather than trusting the
  provider.)
- **Credentials are project-scoped and never returned.** The API returns names,
  Secret references and environment-variable names; values live in Kubernetes
  Secrets and are copied only into the owning project's namespace.
- **Agents never hold a model-provider credential.** They hold a project-scoped
  token for the fleet proxy, which is the only thing that knows the gateway key.

## Known limits

Deliberately not built yet, per SPEC §36–§43:

- no Kubernetes operator / CRDs (SPEC §35 defers this)
- no scheduling, idle-stop, budgets or multi-agent orchestration
  (SPEC §24, §30 — "do not build complex autonomous orchestration during the
  first prototype")
- no per-user authorisation: one operator token, project-scoped tokens for
  workspaces
- SQLite, single replica
- the full dashboard of SPEC §26 — the UI is one page on purpose
