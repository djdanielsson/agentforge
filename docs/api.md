# API

One versioned surface for every client: the web UI, the `agentforge` CLI and any
external orchestrator such as Hermes. No client has a private path in.

- Base URL: `/api/v1`
- Liveness (unversioned, for probes): `GET /health`
- Interactive docs: `/api/v1/docs` (OpenAPI JSON at `/api/v1/openapi.json`)
- Transport: JSON over HTTP, plus WebSocket for event streams

Everything below was checked against a running instance unless marked otherwise.

## Authentication

When `AGENTFORGE_AUTH_ENABLED` is true, every request must carry a key:

```
X-API-Key: aiw_<prefix>_<secret>
```

or, equivalently:

```
Authorization: Bearer aiw_<prefix>_<secret>
```

Keys are generated with `secrets`, stored only as a hash, and shown in plaintext
exactly once — at creation. A key may carry `read`, `write` and `admin` scopes,
and may be pinned to a single project: a pinned key receives `404` rather than
`403` for other projects, so it cannot probe which project ids exist.

When `AGENTFORGE_AUTH_ENABLED` is false (the development default) every request is
anonymous with full scope, and the API logs a warning at startup. Do not expose
an instance with auth off.

## Conventions

- Errors use FastAPI's shape: `{"detail": "..."}`, with the appropriate status
  code — `400` a request that contradicts itself (for example project scope at
  the global secret endpoint), `401` no key, `403` insufficient scope, `404`
  missing or hidden, `409` a duplicate name in the same scope, `422` an invalid
  body, `503` a workspace that is not ready.
- Request bodies ignore unknown fields (Pydantic's default). A field the API does
  not know is dropped, not stored, and not echoed.
- List endpoints accept `limit` (and `offset` on projects).
- IDs are UUID strings.

## Endpoints

### Meta

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | unversioned liveness; `{"status","version"}` |
| GET | `/api/v1/health` | versioned; adds `environment` |
| GET | `/api/v1/version` | `{"name":"agentforge","version":"0.1.0"}` |
| GET | `/api/v1/providers` | configured provider and each provider's capabilities |
| GET | `/api/v1/models` | model aliases an agent can run on, and the default for a new agent |

`GET /api/v1/models` asks the LiteLLM gateway (`/v1/models`) what aliases it
serves and reports `"source": "gateway"`. When the gateway does not answer — a
deployment that has not stood one up yet — it falls back to
`AGENTFORGE_AGENT_MODEL_ALIASES` and reports `"source": "config"`, so the
dashboard's model picker always has something to offer:

```json
{"models": ["local-coder", "fast", "smart"], "source": "config", "default": "local-coder"}
```

The same list is enforced, not merely advertised: creating an agent (or changing
its model) with an alias the gateway does not serve is refused with a `422` that
carries the valid ones, so a typo fails where it was typed instead of when a task
is dispatched.

```json
{"detail": {"message": "unknown model alias 'gpt-9-turbo'",
            "source": "gateway", "models": ["local-coder", "fast", "smart"]}}
```

`GET /api/v1/providers` advertises what each backend can honour, so a client can
disable a control a provider cannot implement:

```json
{
  "configured": "kubernetes",
  "providers": [
    {"provider": "kubernetes", "isolation": "namespace + pod", "secrets": true,
     "network_policy": true, "exec": true, "persistent_volumes": true},
    {"provider": "podman", "available": false,
     "error": "the podman binary is not on PATH. Install podman, or select the kubernetes provider with AGENTFORGE_WORKSPACE_PROVIDER=kubernetes."},
    {"provider": "local", "isolation": "none (development only)", "secrets": false,
     "network_policy": false, "exec": true, "persistent_volumes": false}
  ]
}
```

### Projects

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/projects` | `limit`, `offset`; returns `ProjectRead[]` |
| POST | `/api/v1/projects` | `201`; returns `ProjectDetail` including the new workspace row |
| GET | `/api/v1/projects/{id}` | `ProjectDetail` (workspace + agents) |
| PATCH | `/api/v1/projects/{id}` | name, description, default_branch, status, settings |
| DELETE | `/api/v1/projects/{id}` | `204`; archives the project and marks the workspace for teardown |

Creating a project writes a `Workspace` row in `pending` immediately, named from
the configured prefix plus the slug. The orchestrator fills in the Kubernetes
names later.

```bash
curl -s -X POST localhost:8000/api/v1/projects \
  -H 'content-type: application/json' \
  -d '{"name":"demo","repository_url":"https://github.com/example/demo"}'
```

```json
{
  "id": "41696374-6426-40d8-a20c-72add968a95d",
  "name": "demo",
  "slug": "demo",
  "default_branch": "main",
  "status": "creating",
  "workspace": {
    "namespace": "af-demo",
    "status": "pending",
    "provider": "kubernetes",
    "pvc_name": null,
    "pod_name": null,
    "code_server_url": null,
    "agent_server_url": null
  },
  "agents": []
}
```

### Workspaces

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/projects/{id}/workspace` | the project's workspace record |
| POST | `/api/v1/projects/{id}/workspace` | `202`; request (re)provisioning |
| DELETE | `/api/v1/projects/{id}/workspace` | `202`; request destruction |

Both lifecycle calls are requests, not synchronous work: they move the workspace
to `pending` / `deleting` and return `WorkspaceActionAccepted` (or `404` if the
project is unknown). The orchestrator converges. Provisioning is idempotent, and
provisioning an already-`ready` workspace is a no-op.

```bash
curl -s -X POST localhost:8000/api/v1/projects/$PID/workspace
```

```json
{
  "project_id": "d5561ec7-088b-43dd-a8eb-440b23d19801",
  "action": "provision",
  "status": "pending",
  "detail": "the orchestrator will provision this workspace"
}
```

Destroy is honest about what it costs: it deletes the namespace, so uncommitted
work is lost. Committed work on a pushed branch survives in the remote.

The workspace record carries `provider`, `namespace`, `pvc_name`, `pod_name`,
`service_name`, `code_server_url`, `agent_server_url` and `status`
(`pending`, `provisioning`, `cloning`, `ready`, `stopped`, `error`, `deleting`,
`destroyed`).

### Workspace files and terminal

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/projects/{id}/files?path=` | one directory level: name, type, size |
| GET | `/api/v1/projects/{id}/file?path=` | content; `encoding` is `utf8` or `base64` |
| PUT | `/api/v1/projects/{id}/file` | `{path, content, encoding}`; 413 past the editor limit |
| WS | `/api/v1/projects/{id}/terminal` | a pty shell in the workspace pod |

`code_server_url` is an in-cluster Service name, so a browser can never load it —
embedding it is what it is not for. These endpoints are how the dashboard's editor
and terminal work instead: the API execs into the workspace pod, so the tools are
part of the app and the only thing the browser needs is API access.

Paths are resolved inside `/workspace`; anything that escapes it is refused
rather than normalised. Reads come back base64 on the wire and are labelled
`utf8` when they decode, so the editor can refuse a binary file instead of
showing mojibake. The terminal is a real pty (prompts, colours, line editing),
with the caveat that the Kubernetes client has no resize channel, so the pty keeps
the size it was opened with.

### Agents

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/projects/{id}/agents` | agents in a project |
| POST | `/api/v1/projects/{id}/agents` | `201`; name, model alias, branch, optional `policy` |
| GET | `/api/v1/agents` | every agent (fleet view) |
| GET | `/api/v1/agents/{id}` | one agent |
| PATCH | `/api/v1/agents/{id}` | name, model, branch, status, policy |
| POST | `/api/v1/agents/{id}/stop` | stop the agent |
| POST | `/api/v1/agents/{id}/restart` | clear the agent session and start a new one |
| POST | `/api/v1/agents/{id}/messages` | `202`; append a human turn |
| GET | `/api/v1/agents/{id}/conversation` | `{"agent_id","messages":[...]}` |
| GET | `/api/v1/agents/{id}/permissions` | the effective permission policy |
| POST | `/api/v1/agents/{id}/permissions` | answer a permission request |
| GET | `/api/v1/agents/{id}/summary` | supervision summary (requires `write` today) |
| POST | `/api/v1/agents/{id}/tasks` | `201`; queue work directly against one agent |
| GET | `/api/v1/projects/{id}/agents/{agent_id}/status` | nested status alias |

An agent's `model` is a logical alias (`local-coder`), not a provider model
name. The default branch is `agent/<slugified-name>`. A new agent gets the
restrictive default [permission policy](permissions.md); a `policy` that cannot
be implemented safely is rejected at create time.

Restart clears `session_id`, `current_task_id` and `error` and moves the agent
back to `starting`. It does not touch the workspace: restarting an agent must
not throw away the work in `/workspace`.

```bash
curl -s -X POST localhost:8000/api/v1/projects/$PID/agents \
  -H 'content-type: application/json' -d '{"name":"coding"}'
```

```json
{
  "id": "88c4a404-046a-4f93-812f-9783fc024844",
  "name": "coding",
  "model": "local-coder",
  "branch": "agent/coding",
  "status": "starting",
  "policy": {}
}
```

The summary endpoint is what a conversational client polls or is pushed to:

```bash
curl -s localhost:8000/api/v1/agents/$AID/summary
```

```json
{
  "agent_id": "88c4a404-046a-4f93-812f-9783fc024844",
  "name": "coding",
  "status": "awaiting_approval",
  "task": "Add a /health endpoint",
  "progress": "edited app/main.py",
  "files_changed": 2,
  "branch": "agent/coding",
  "commits": [],
  "tests": {"passed": 90, "failed": 0, "command": "make test"},
  "blocked_reason": "npm install left-pad",
  "question": "Allow this command?",
  "workspace_url": "http://af-demo-ws.af-demo.svc.cluster.local:8080",
  "updated_at": "2026-09-12T16:52:01Z"
}
```

### Tasks

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/tasks` | optional `project_id` filter |
| GET | `/api/v1/tasks/{id}` | one task |
| POST | `/api/v1/tasks/{id}/cancel` | cancel a queued or running task |
| GET | `/api/v1/tasks/{id}/events` | the task's events, in order |
| POST | `/api/v1/projects/{id}/tasks` | `201`; queue work for a project |
| POST | `/api/v1/agents/{id}/tasks` | `201`; queue work for one agent |

A task body takes `prompt` or `description` (one is required), plus optional
`kind` (`implement`, `review`, `test`, `deploy`, `maintain`), `priority`
(`low`, `normal`, `high`, `urgent`), `agent_id` and `branch`.

```bash
curl -s -X POST localhost:8000/api/v1/projects/$PID/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"Add a /health endpoint","priority":"high"}'
```

An empty body is rejected with `422`:

```
{"detail":[{"type":"value_error","loc":["body"],"msg":"Value error, either `prompt` or `description` is required", ...}]}
```

### Semantic actions

Say *what* you want, not *how* it is done. Each queues a task and returns `202`
with the task; the kind determines the outcome event.

| Method | Path | Body | Produces |
|---|---|---|---|
| POST | `/api/v1/projects/{id}/review` | `branch`, `base`, `agent_id` | `review.completed` |
| POST | `/api/v1/projects/{id}/test` | `branch`, `command`, `agent_id` | `test.completed` |
| POST | `/api/v1/projects/{id}/deploy` | `branch`, `environment`, `agent_id` | `deploy.completed` |

If `agent_id` is omitted and the project has agents, the first agent is used. The
test command defaults to the project's `settings.test_command`, or `make test`.

### Git

Every Git command runs inside the project's workspace. The API never touches a
host filesystem. These endpoints require a `ready` workspace; otherwise they
return `503`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/projects/{id}/git/diff` | optional `base`; returns `branch`, `base`, `diff`, `files[]` |
| GET | `/api/v1/projects/{id}/git/status` | `status`, `branch`, `log` (last 20) |
| POST | `/api/v1/projects/{id}/git/commit` | `message`, optional `paths` |
| POST | `/api/v1/projects/{id}/git/merge` | `source_branch`, optional `target_branch` |

```bash
curl -s localhost:8000/api/v1/projects/$PID/git/diff
# without a ready workspace:
# HTTP 503 {"detail":"workspace is pending; git is unavailable until it is ready"}
```

### Secret references

See [secrets.md](secrets.md) for the model and the scope rules. There is no
endpoint that returns a value, and no field in which one can be supplied.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/secrets` | global references only |
| POST | `/api/v1/secrets` | `201`; global scope only, `400` otherwise |
| GET | `/api/v1/projects/{id}/secrets` | global + this project's references |
| POST | `/api/v1/projects/{id}/secrets` | `201`; project scope, or agent scope with `agent_id` |
| DELETE | `/api/v1/secrets/{id}` | `204`; removes the reference, not the secret |

```bash
curl -s -X POST localhost:8000/api/v1/projects/$PID/secrets \
  -H 'content-type: application/json' \
  -d '{"name":"github","scope":"project","secret_name":"github-agent","key":"token","env_var":"GITHUB_TOKEN"}'
```

```json
{
  "id": "3d75634b-da0e-4b22-8372-f5d4e232dc5b",
  "name": "github",
  "scope": "project",
  "project_id": "d5561ec7-088b-43dd-a8eb-440b23d19801",
  "provider": "kubernetes",
  "secret_name": "github-agent",
  "key": "token",
  "env_var": "GITHUB_TOKEN",
  "required": false
}
```

A duplicate `name` in the same scope returns `409`. An agent-scoped reference
must name an agent that belongs to the project.

### API keys

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/keys` | metadata only — never the secret |
| POST | `/api/v1/keys` | `201`; returns the plaintext key once |
| DELETE | `/api/v1/keys/{id}` | `204`; revokes (deactivates) the key |

```bash
curl -s -X POST localhost:8000/api/v1/keys \
  -H 'content-type: application/json' -d '{"name":"docs"}'
```

```json
{
  "id": "d1aa1c03-62eb-4957-a307-61f237f7d5ed",
  "name": "docs",
  "prefix": "d727e7e3",
  "scopes": ["read", "write"],
  "project_id": null,
  "active": true,
  "key": "af_d727e7e3_QzhVm1335U5JOnJ0_QExYv3kPQWzAHPjwPpcQVrzWgQ"
}
```

### Webhooks

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/webhooks` | list subscriptions |
| POST | `/api/v1/webhooks` | `201`; returns the signing secret once |
| PATCH | `/api/v1/webhooks/{id}` | url, description, events, active |
| DELETE | `/api/v1/webhooks/{id}` | `204` |
| GET | `/api/v1/webhooks/{id}/deliveries` | delivery history, newest first |

A subscription takes `url`, optional `description`, `events` (default `["*"]`)
and optional `project_id`. Without a `project_id` it receives events from every
project. Pattern matching supports exact types, `*`, and a trailing namespace
wildcard such as `agent.*`.

URLs are validated before they are stored. A URL that resolves to a private,
loopback, link-local or reserved address is refused with `422`, because a webhook
URL is a server-side request forgery primitive:

```
HTTP 422 {"detail":"webhook host resolves to a non-routable address (169.254.169.254)"}
```

### Events

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/events/catalog` | every event type, with payload fields |
| WS | `/api/v1/projects/{id}/events` | the project timeline, backfilled on connect |
| WS | `/api/v1/agents/{id}/events` | one agent's timeline, backfilled on connect |

## Event catalog

`GET /api/v1/events/catalog` returns 23 types. Each entry has a `type`, a
`description` and the `payload_fields` a subscriber can expect.

| Type | Meaning |
|---|---|
| `project.created` | a project was registered (`name`, `repository_url`) |
| `project.status` | project lifecycle changed (`status`) |
| `workspace.created` | the workspace's objects exist (`namespace`) |
| `workspace.ready` | the workspace is up (`code_server_url`) |
| `workspace.destroyed` | the workspace was destroyed (`namespace`) |
| `workspace.failed` | provisioning failed (`error`) |
| `agent.created` | an agent was created (`name`, `model`, `branch`) |
| `agent.started` | the agent runtime accepted the session (`session_id`) |
| `agent.progress` | observable progress (`message`, `files_changed`) |
| `agent.message` | a conversational turn (`role`, `content`) |
| `agent.waiting` | the agent needs a human answer (`question`) |
| `agent.permission_required` | the agent wants to run something (`request_id`, `command`, `reason`) |
| `agent.completed` | the agent finished a task (`task_id`, `result`) |
| `agent.failed` | the agent errored (`error`) |
| `agent.stopped` | the agent was stopped |
| `permission.granted` | a permission request was allowed (`request_id`, `decision`) |
| `permission.denied` | a permission request was denied (`request_id`) |
| `task.created` | work was queued (`description`, `kind`, `priority`) |
| `task.status` | task lifecycle changed (`status`, `result`, `error`) |
| `test.completed` | a test run finished (`passed`, `failed`, `command`) |
| `review.completed` | a review finished (`branch`, `files_changed`, `summary`) |
| `deploy.completed` | a deployment finished (`environment`, `branch`) |
| `commit.created` | the agent committed code (`commit`, `branch`) |

The internal `EventType` enum also defines `agent.status`, which the API emits
for raw status transitions; it is not listed in the catalog. The unfinished agent
manager additionally references four types that exist in neither place
(`TASK_OUTPUT`, `PERMISSION_REQUEST`, `AGENT_QUESTION`, `GIT_COMMIT`); those code
paths raise and will be replaced with the catalogued types.

## WebSocket streams

On connect the socket is accepted and up to 200 recent events for the topic are
sent, oldest first; live events follow. Messages are the same JSON shape as a
webhook payload's `event` field:

```json
{
  "id": "1b6e...",
  "type": "agent.progress",
  "project_id": "4169...",
  "agent_id": "88c4...",
  "task_id": "ed03...",
  "payload": {"message": "edited app/main.py", "files_changed": 1},
  "created_at": "2026-09-12T16:52:01.835635"
}
```

An unknown id closes the socket with code `4404`. The hub is in-process, so
events published by a *different* API process are not fanned out to a socket on
this one; scaling out means moving the hub to Postgres `LISTEN/NOTIFY` or a
stream, behind the same interface.

## Webhook delivery

Deliveries are made from the orchestrator. Each request is a `POST` with:

```
content-type: application/json
X-AIWorkbench-Signature: sha256=<hex hmac-sha256 of the raw body>
X-AIWorkbench-Event: agent.permission_required
```

with a body of the form:

```json
{"event": {"id": "...", "type": "...", "project_id": "...", "agent_id": "...", "task_id": "...", "payload": {}, "created_at": "..."}}
```

Verify the signature with the subscription's secret before trusting the body.
Delivery is retried with linear backoff up to `AGENTFORGE_WEBHOOK_MAX_ATTEMPTS`,
and every attempt is recorded in `webhook_deliveries`, visible via
`GET /api/v1/webhooks/{id}/deliveries`.

## CLI

The `agentforge` CLI is a client of this API, not a separate surface. Its default
base URL is `http://localhost:8000/api/v1`:

```bash
agentforge --url http://localhost:8000 projects
agentforge ask demo "Add a /health endpoint"
agentforge fleet
agentforge watch demo
```

`--url` defaults to `AGENTFORGE_API_URL`; `--key` to `AGENTFORGE_API_KEY`.
