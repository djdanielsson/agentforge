# Can T3 Code be extended the way we want?

**Question this answers:** is there a plugin/extension surface in T3 Code that could
add fleet-specific settings to project creation, so that one T3 Code UI manages
many projects instead of each project having its own T3 Code?

**Answer: no plugin API exists.** T3 Code v0.0.40 ships no extension mechanism for
UI or settings. What follows is the evidence, and the three shapes that are
actually available.

## What was checked

Version `0.0.40` of the npm package `t3`, unpacked and searched, plus the upstream
repository (`pingdotgg/t3code`, cloned at `main`):

- The server bundle (`dist/bin.mjs`, ~8.5 MB) contains **no** `pluginApi`,
  `extensionPoint`, or comparable symbol — `grep` counts are zero. The client
  bundle is static assets; nothing in it loads third-party UI.
- **`mcpServers` appears 41 times in the server bundle**, and MCP wiring is visible
  in `apps/server/src/provider/Layers/*Adapter.ts` (Claude, Cursor, Grok,
  Antigravity) and `apps/server/src/provider/acp/AcpSessionRuntime.ts`. MCP is the
  extension surface an agent session sees.
- Settings are built in, not extensible: `docs/user/project-settings.md` documents
  a full settings model — environment values with per-project overrides,
  inheritance, "Mixed" states, reset — but every row is defined in code
  (`packages/shared/projectSettings`, resolved in
  `apps/server/src/orchestration/Layers/ProviderCommandReactor.ts`). There is no
  row registry for third parties to add to.
- `docs/internals/devices.md` shows the pattern T3 uses for its own bolt-on
  (`device_*` MCP toolkit, installed into the T3 home, gated behind a consent
  step). That is the closest thing to "a plugin" in this codebase, and it is an
  MCP server plus a panel T3 itself implements.
- The README explicitly invites forking: *"we want you to have everything you need
  to fork and build the editor that you want."*

## Why the current build has a T3 per project

Not an accident of the build: `docs/fleet/SPEC.md` §10 and §27 specify that T3 Code
"runs inside the project workspace as a remote environment/server". T3's own
architecture makes an environment *a machine running its server*, and it drives
agent CLIs installed on that machine — so "T3 per project workspace" is what that
sentence produces.

## The three shapes available

### A. One T3 environment; projects as checkouts; fleet as an MCP server

A single T3 server (one pod, or the user's own machine) owns every project as a
directory/worktree — T3 natively models one project with several checkouts and
per-project setting overrides. The control plane provisions the directories and
the agents, and exposes fleet operations (create project, provision workspace, add
agent, submit task, read status) as an **MCP server** registered into the provider
session, so it is driven from T3's chat.

- No fork. Uses the only extension seam that exists.
- Trade-off: projects are directories on one machine, not isolated namespaces —
  the namespace/PVC/NetworkPolicy isolation in this repository stops applying.
- "Extra settings at project creation" happen through fleet (its API/MCP), not
  inside T3's create-project dialog.

### B. Fork T3 Code and add a Fleet panel

Add settings rows and a project-creation flow that calls the control-plane API.
This is the only way to get fields inside T3's own UI.

- Cost: an ongoing fork of a large TypeScript/Effect monorepo (server bundle
  ~8.5 MB, client assets ~89 MB), tracking upstream releases.
- Also the only option that can keep per-project namespaces *and* put the controls
  in T3's UI.

### C. Drop T3 from the product

Keep the isolation model (namespace per project, DevPod workspace, OpenCode agent,
LLM gateway, tasks/events/usage attribution) and let the control plane's own page
be the fleet surface. T3 stays a single instance the user runs where they like,
for driving one project at a time.

- Cheapest, and keeps every isolation property already built and verified.
- Gives up T3's UI as the fleet cockpit.

## What is already true regardless of the choice

Verified against the deployed control plane, independent of this question: project
→ DevPod workspace (own namespace, pod, PVC) → agent → task → the control plane's
LLM proxy → LiteLLM → a tool-capable model writing files and committing in its own
git worktree, with usage attributed per project, agent and task. The T3 question
is only about which UI sits on top.

---

# Option A, built

Shape A is implemented on branch `fleet` and deployed in namespace `fleet`. This
section is what exists, how to use it, and — the part worth reading first — what
was verified against the running cluster and what was not.

Nothing here changed the DevPod path: the same deployment still creates
per-project namespaces for projects whose provider is `devpod`, and the
control-plane ingress `cp` was never re-applied (its certificate was never
reissued).

## What is deployed

| object | what it is |
| --- | --- |
| Deployment `fleet-t3` (image `registry.registry.svc:5000/fleet-t3`) | node 24.21, `t3@0.0.40`, `opencode-ai@1.18.31`, git — the whole environment |
| PVC `fleet-t3-projects` (20Gi, `local-path`) | mounted at `/projects` (the checkouts) **and** `/state` (T3's own store, `T3CODE_HOME=/state/t3code`) |
| Service `fleet-t3:5733` | the environment, in-cluster |
| Ingress `t3` (tailscale) | <https://fleet-t3-ingress.tail7f3c08.ts.net> — for pairing a client |
| Deployment `fleet-api` | the control plane, now also serving `/mcp` |

One environment, one volume, one hostname — the thing the current shape could not
do, because every project had its own T3 server and its own tailnet name.

The same PVC is mounted twice rather than T3's state living in an `emptyDir`:
the project registry `t3 project add` writes has to survive a pod restart, or every
project would have to be re-registered on every deploy.

## How a project becomes visible to T3

**`t3 project add <path>` is what does it. A directory on the volume is not
enough.** That was measured, not assumed (v0.0.40, locally and then in the pod):

```
$ t3 project add /projects/checkout-alpha
Added project bcae70a2-3969-49e4-a137-0c3a253a395a (checkout-alpha) at /projects/checkout-alpha.

# the same directory, without the command — the project list is unchanged:
$ mkdir -p /tmp/t3proj/other && <GET /api/orchestration/snapshot>
[('demo', '/tmp/t3proj/demo')]
```

The `addProjectBaseDirectory` setting is not a directory scan; it is a default for
the add-project dialog, and the CLI has no `project list` at all. Visibility is
therefore proven through the server's own snapshot endpoint, which is what the
T3 UI reads:

```
$ t3 auth session issue --base-dir /state/t3code --token-only | \
    xargs -I{} curl -sS -H "Authorization: Bearer {}" \
    http://127.0.0.1:5733/api/orchestration/snapshot
{"snapshotSequence":1,"projects":[{"id":"bcae70a2-...","title":"checkout-alpha",
  "workspaceRoot":"/projects/checkout-alpha", ...}],"threads":[],...}
```

So the provider's `create` is: make the directory a git checkout, then register
it, and report the T3 project id back into the workspace's `detail`. A clone that
fails is a `failed` workspace whose `error` is git's own message — a private
repository with no project credential fails exactly there, and "provisioning" is
not a diagnosis.

## The MCP server

`POST /mcp` on the control plane, Streamable HTTP, JSON-RPC 2.0. Tools:

| tool | arguments | what it does |
| --- | --- | --- |
| `list_projects` | — | every project, its workspace provider/status and its agents |
| `create_project` | `name`, `repository_url`, `repository_branch`, `workspace_provider`, `cpu`, `memory`, `storage` | create and provision |
| `list_agents` | `project` | a project's agents |
| `create_agent` | `project`, `name`, `provider`, `role`, `model` | add an agent |
| `submit_task` | `project`, `prompt`, `agent`, `priority` | queue work for an agent |
| `task_status` | `task` | one task: status, answer, error, git branch/commit |
| `workspace_status` | `project` | the project's workspace |
| `list_tasks` | `project`, `limit` | recent tasks |

A tool that cannot do what it was asked returns `isError: true` with the reason in
the content rather than a transport error: the caller is a model, and it can act
on a readable failure.

**Why Streamable HTTP, specifically.** OpenCode 1.18.31 is the MCP client, and it
was pointed at a logging server to see what it does rather than guessing between
SSE and streamable HTTP:

```
POST /mcp accept=application/json, text/event-stream
  {"method":"initialize","params":{"protocolVersion":"2025-11-25",
   "clientInfo":{"name":"opencode","version":"1.18.31"}},...}
POST /mcp {"method":"notifications/initialized"}          -> 202, no body
GET  /mcp accept=text/event-stream                        (404/405 accepted)
POST /mcp {"method":"tools/list"}                         -> 200 JSON
```

and `opencode mcp list` reported `✓ fleet connected` against that server. The
endpoint answers exactly that: JSON in, JSON out, 202 for notifications, and 405
with `Allow: POST, DELETE` on GET.

Auth is the control plane's operator token in the same `Authorization: Bearer`
header the MCP client is configured with — and when the deployment runs with no
token (this one does, deliberately, for testing) it is open like every other
route.

## The checkout workspace mode

`workspace.provider: "checkout"` on a project selects it. Same interface, same
agent layer, different meaning of "workspace":

| | `devpod` | `checkout` |
| --- | --- | --- |
| workspace | namespace + pod + PVC + Service + NetworkPolicy | a directory under `/projects` |
| workspace reference | `fleet-<name>` (the namespace) | `<name>` (the directory) |
| isolation | one namespace per project, default-deny ingress | none of its own |
| T3 | one server per project | the one environment |
| toolchain | installed into the workspace volume | in the image, `/usr/local/bin` |

Two things had to stop being assumptions for the API to stay provider-agnostic:

* `WorkspaceProvider.layout(reference)` — where a provider keeps a workspace's
  files and its tools: the root, the fleet home, the *repository*, the worktrees,
  the task logs and the toolchain directory. The opencode agent provider now asks
  for it instead of hard-coding `/workspaces/<ref>/.fleet`.
* `service._reference(settings, slug, provider)` — the reference follows the
  provider, because a checkout's is a directory name and a DevPod workspace's is
  a namespace.

The two layouts, as the provider reports them:

| | `devpod` | `checkout` |
| --- | --- | --- |
| root | `/workspaces/<ref>` | `/projects/<name>` |
| repository | `<root>/.fleet/repo` | `<root>` — the checkout *is* the repo |
| fleet home | `<root>/.fleet` | `/projects/<name>/.fleet` |
| worktrees | `<fleet_home>/worktrees` | `/projects/.fleet/<name>/worktrees` |
| tools | `<fleet_home>/tools/bin` | `/usr/local/bin` (in the image) |

Worktrees are outside the checkout on purpose. Nested inside it they appear in
the checkout's own `git status`, and `git add -A` there stages another agent's
worktree as an embedded repository (measured against git 2.47) — one agent's
commit recording another agent's tree. `destroy` removes both.

A reference is validated as `^[a-z0-9][a-z0-9-]{0,62}$` before it is used, because
this provider is the last thing between a stored value and `rm -rf` on a shared
volume. `stop` raises rather than pretending: there is no per-project pod to stop,
and saying so beats a green status that means nothing.

Credentials are read from the control plane's own namespace, not copied into a
workspace namespace — that copy is a Kubernetes-provider idea and there is no
namespace here to copy into. `WorkspaceProvider.credentials_in_namespace` is the
seam: DevPod says yes, checkout says no, and the service layer does not decide.

## What a T3 session sees

`create` writes the opencode config in two places:

* `/projects/<name>/.fleet/opencode.json` — what a *fleet-run* task points
  `OPENCODE_CONFIG` at.
* `/projects/<name>/opencode.json` — what a *T3-driven* session in that directory
  picks up, since opencode reads a project config from the working directory
  upward. It is added to `.git/info/exclude` (local, never committed) so the
  checkout stays clean.

Both name the fleet gateway and the fleet MCP server. Neither holds a token
*value*: they reference `{env:FLEET_LLM_TOKEN}` and `{env:FLEET_API_TOKEN}`, which
the control plane puts in a run's environment. That matters more here than it does
for DevPod — every project in a shared environment runs as the same user, so a
token written to one of these files is a token every other project's agent can
read. The environment's own global config (baked in by the entrypoint) carries the
MCP server for the same reason.

## Using it

Pair a client (the environment prints a pairing link on startup, but that link has
the pod IP in it — mint one for the tailnet URL instead):

```bash
POD=$(kubectl -n fleet get pod -l app.kubernetes.io/name=fleet-t3 -o name)
kubectl -n fleet exec "$POD" -- t3 auth pairing create \
  --base-dir /state/t3code --ttl 30m --label laptop \
  --base-url https://fleet-t3-ingress.tail7f3c08.ts.net
```

That prints a `/pair#token=...` URL. Open it on a machine on the tailnet and the
web client pairs. `t3 auth session issue` mints a bearer token instead, for
headless clients: `curl -H "Authorization: Bearer <token>"
http://127.0.0.1:5733/api/orchestration/snapshot` is the whole of the read API a
script needs.

Create a project in the environment:

```bash
curl -sS -X POST https://fleet-cp-ingress.tail7f3c08.ts.net/api/v1/projects \
  -H 'Content-Type: application/json' -d '{
    "name": "checkout-alpha",
    "repository": {"url": "https://github.com/djdanielsson/agentforge", "branch": "fleet"},
    "credentials": [{"name": "github"}],
    "workspace": {"provider": "checkout"}
  }'
curl -sS -X PUT .../api/v1/projects/checkout-alpha/credentials/github -d '{"value":"ghp_..."}'
```

For a private repository the credential has to exist *before* provisioning, or the
clone fails (and now says so). The provider sources `.fleet/credentials.env` and
uses `GITHUB_TOKEN` to authenticate the clone, then resets the remote back to the
clean URL so the token is not left in `.git/config`.

## What was verified, and what was not

Against the running deployment (namespace `fleet`, image tag = the commit this
section was written at):

| what | evidence |
| --- | --- |
| T3 serves headlessly in the cluster | pod log: `T3 Code server is ready` + `Listening on http://0.0.0.0:5733`; `GET / -> 200` from inside the pod |
| a checkout project provisioned | `POST /api/v1/projects` with `workspace.provider: checkout` → workspace `ready`, `create_output`: `CLONE_OK / BRANCH=fleet / T3_REGISTERED` |
| the checkout is a real clone of the private repo | `git log --oneline` inside the pod shows the branch's commits; `git remote get-url origin` is the clean URL, and `grep -c x-access-token .git/config` is 0 |
| the project is visible to T3 | `GET /api/orchestration/snapshot` on the environment's own server lists it with `workspaceRoot: /projects/checkout-alpha` |
| the MCP server is reachable to the CLI the environment ships | `opencode mcp list` inside the pod: `✓ fleet connected  http://fleet-api.fleet.svc.cluster.local:8000/mcp` |
| the MCP tools work over the real deployment | `initialize` → `{name: fleet-control-plane}`; `tools/list` → the eight tools; `tools/call workspace_status` → the project's workspace |
| an agent runs in a checkout workspace | a task on an `opencode` agent: `completed`, git branch + commit recorded, usage attributed (`2 calls, 2586 prompt tokens`) in `GET /projects/checkout-alpha/usage` |
| DevPod is untouched | `GET /api/v1/providers` still reports `devpod` configured and available; the existing projects and workspaces are unchanged; the tailscale proxy for ingress `cp` (`ts-cp-wnwwc-0`) was never restarted (age predates this work, 0 restarts) |

Not verified, and not claimed:

* **A message typed into the T3 UI, driving fleet through the MCP tools.** Pairing
  needs a browser on the tailnet and this work does not have one. Every layer was
  verified separately — the handshake, the tool list, `opencode mcp list`
  connected, the project registered in the server the UI reads — but no session
  in the UI has called `submit_task`.
* **T3's own chat model.** A session in T3 gets the fleet *tools* from this work;
  which model its chat uses is T3's own provider configuration, and nothing here
  sets it.
* **An agent writing files in a checkout.** The task above completed and was
  attributed, but its answer was tool-call JSON in the message text rather than a
  tool call — the known limitation of the local model behind the `localOnly`
  policy (see the README). That is the model, not the checkout: the same is true
  of a DevPod workspace.

## Three traps found by running it

**A Service in this namespace rewrites your environment.** Kubernetes injects
`<SERVICENAME>_PORT=tcp://<cluster-ip>:<port>` into *every* pod in the namespace
(the docker-links compatibility variables), so the environment's own
`FLEET_T3_PORT` arrived holding a URL and `t3 serve --port` refused it:

```
FLEET_T3_PORT=tcp://10.43.34.78:5733
FLEET_T3_SERVICE_HOST=10.43.34.78
FLEET_T3_SERVICE_PORT=5733
ERROR Invalid value for flag --port: "tcp://10.43.34.78:5733"
```

Proven with a one-shot probe Job in the namespace. The T3 pod sets
`enableServiceLinks: false` and the entrypoint passes its port literally; a
rename would not have been a fix, because the injection is keyed on the Service
name, not on the variable being read.

**A single-quoted `sed` does not expand a token, and the failure is silent.**
The clone builds its URL from the credential; the first version did it with
`sed -E 's#^https://#https://x-access-token:${GITHUB_TOKEN}@#'`, whose *program*
is single-quoted, so the shell never substituted anything and GitHub answered
`fatal: Authentication failed`. It is built by parameter expansion now —
`"https://x-access-token:${GITHUB_TOKEN}@${repo_url#https://}"` — which cannot be
quoted wrong.

**`git clone` refuses a non-empty destination, and the destination is non-empty
by design.** `.fleet/credentials.env` has to exist *before* the clone, because
the clone is authenticated with the token in it. Live provisioning failed on
`fatal: destination path '/projects/checkout-alpha' already exists and is not an
empty directory`; the clone now goes to a scratch directory and is copied in.

## And one decision worth keeping

**An MCP endpoint is a path in someone else's config.** `/mcp` is served at the
root, not under `/api/v1`, because the URL is written into every checkout's
opencode config. Moving it later breaks agents that were written yesterday.

