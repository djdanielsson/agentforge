# Validation findings

What was established **experimentally** before the control plane was built, as
SPEC §52 requires. Every "confirmed" item below has a command that produced it;
the raw command output is quoted where it is the evidence.

Everything here was run from inside the homelab cluster (a pod in the `hermes`
namespace with cluster-wide credentials), against the live k3s API server.

Legend: **confirmed** = observed directly · **assumed** = believed but not yet
observed · **open** = needs to be verified, with what would settle it.

---

## 1. DevPod on Kubernetes — confirmed

### 1.1 Installation

`v0.6.15` is the current release (GitHub API, `releases/latest`). The CLI is a
single static binary:

```
gh release download v0.6.15 → devpod-linux-amd64 (85 MB) → `devpod version` → v0.6.15
```

Providers are **not** built in at this version. `devpod provider list` is empty
until you add one, and `devpod provider add kubernetes` installs
`devpod-kubernetes v0.0.1` from a DevPod provider registry.

```
$ devpod provider add kubernetes
done Successfully installed provider kubernetes
done Successfully configured provider 'kubernetes'
$ devpod provider list
NAME       | VERSION | DEFAULT | INITIALIZED | DESCRIPTION
kubernetes | v0.0.1  | true    | true        | DevPod on Kubernetes
```

Provider state lives under `DEVPOD_HOME` (contexts, provider binaries, workspace
metadata), so it must be on a persistent volume or the CLI forgets everything
between restarts.

### 1.2 What `devpod up` actually creates

`devpod up <dir> --provider kubernetes --id fleet-p1` against a directory
containing `.devcontainer/devcontainer.json`:

| object | name | notes |
| --- | --- | --- |
| Namespace | `devpod` | `KUBERNETES_NAMESPACE` option, default `devpod` |
| Pod | `devpod-default-fl-69cf7` | `devpod-<context>-<hash>` |
| PVC | `devpod-default-fl-69cf7` | mounted at `/workspaces/fleet-p1`, `subPath: devpod/0` |

Observed pod facts:

- image: whatever the devcontainer declares (`mcr.microsoft.com/devcontainers/base:ubuntu-24.04` here)
- labels: `devpod.sh/created=true`, `devpod.sh/workspace-uid=default-fl-69cf7`
- **ServiceAccount `default`, with a projected API token** — not disabled
- **no `Service`, no `Ingress`, no `NetworkPolicy`, no `Role`, no `RoleBinding`**
- inside the container: `uid=0(root)`, `git 2.51.1`, `curl` present, **no node**

That last group is the finding that shapes the design. DevPod gives you a pod and
a volume; it does not give you isolation or reachability. SPEC §18 requires one
project to be unable to reach another, so the control plane has to add those
objects itself rather than assume the provider did.

### 1.3 One namespace per project — confirmed

`--provider-option KUBERNETES_NAMESPACE=<ns>` is per-invocation, so each project
can get its own namespace from the same DevPod install:

```
$ devpod up /opt/data/work/dp-test/p2 --id fleet-p2 \
    --provider-option KUBERNETES_NAMESPACE=fleet-proj-b --disable-daemon

namespaces: ['devpod', 'fleet-proj-b']
devpod/devpod-default-fl-69cf7          Running  mounted at ['/workspaces/fleet-p1']
fleet-proj-b/devpod-default-fl-b6808    Running  mounted at ['/workspaces/fleet-p2']
```

This is the finding the whole isolation story rests on: DevPod *can* give a
per-project namespace, so SPEC §18 is satisfiable without abandoning DevPod.

### 1.4 `devpod up` is a foreground client

It holds SSH tunnels and exits on its own:

```
fatal Stopping devpod up, because it stayed idle for a while.
      You can disable this via 'devpod context set-options -o EXIT_AFTER_TIMEOUT=false'
```

This is not a failure: the pod keeps running, and `devpod stop`/`delete` still
work from the stored context. The control plane therefore spawns `devpod up` and
*waits on the cluster*, not on the process.

### 1.5 Image handling — a real constraint

DevPod inspects the image manifest over HTTPS before provisioning. Two
consequences:

- `ghcr.io/devcontainers/base:ubuntu` does not exist, and DevPod fails with
  `DENIED: requested access to the resource is denied` rather than a clear
  "not found". Verified as a non-existent path; `mcr.microsoft.com/devcontainers/base:ubuntu-24.04`
  returns `200` from its registry API and works.
- **The in-cluster registry (`registry.registry.svc:5000`, plain HTTP) cannot be
  used as a devcontainer image**, because DevPod has no insecure-registry option.
  DevPod workspaces use a public devcontainer image and install their tooling
  through lifecycle hooks instead. The *native* Kubernetes provider has no such
  restriction and uses our own image.

### 1.6 Open — DevPod provider protocol

**open:** whether DevPod's research/other providers (`docker`, `ssh`) install
cleanly here. Only `kubernetes` was exercised, because it is the only one this
deployment needs.

---

## 2. A workspace can do the work — confirmed

Run inside the DevPod workspace pod (`fleet-proj-b/devpod-default-fl-b6808`):

```
$ git clone --depth 1 https://x-access-token:$GH_TOKEN@github.com/djdanielsson/agentforge.git
CLONE_OK
7df1d82 fix(agent-server): clamp the page size to what the server accepts
$ git add probe.txt && git commit -qm probe
COMMIT_OK
 probe.txt | 1 +
 1 file changed, 1 insertion(+)

$ curl -fsSL https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz
NODE_INSTALLED
v24.21.0
11.19.0
```

So SPEC §36's Milestone 1 loop — start, see the repository, modify files, commit —
is confirmed for *git and the filesystem*. Git authentication for a private repo
is a token in the clone URL, which is exactly what a project credential becomes.

**Note:** the token was passed to the remote shell on **stdin**, not in `argv`,
because `argv` is visible in the host process list.

**Assumed:** `/workspaces/<id>` survives `devpod stop` + `devpod up`, because it
is a PVC and `stop` only deletes the pod. **Open:** an explicit stop/start cycle
with a file written before and read after.

---

## 3. T3 Code — confirmed as a remote server, with a caveat

`pingdotgg/t3code`, Apache-licensed, "agent harness control surface". The npm
package is **`t3`** (latest published: `0.0.40`), a self-contained executable
requiring Node `^22.16 || ^23.11 || >=24.10`; the workspace installs Node
`v24.21.0`, which satisfies it.

Headless server mode, which is what SPEC §10 asks for:

```
$ t3 --help
  serve      Run the T3 Code server without opening a browser and print
             headless pairing details.
  pair       Mint a pairing token for a running T3 Code server and print it
             as a QR code.
$ t3 serve --host 0.0.0.0 --port 4096 --no-browser --mode web
```

`t3 serve --tailscale-serve` exists but needs the `tailscale` CLI inside the
workspace; the Kubernetes tailscale operator's Ingress is used instead, which is
the pattern already proven in this cluster.

**Caveat, and it matters:** T3 Code drives *other* agent CLIs — Codex, Claude
Code, Cursor, Grok Build, OpenCode, Antigravity — and is designed for a human in
a UI. There is no headless "run this prompt, give me a result" entry point.
Treating it as a task executor would be a fake integration, so the agent
provider for T3 declares `executes_tasks: false` and the API refuses to assign it
a task. Tasks go to OpenCode; T3 is where you watch and drive the workspace.

**Open:** the exact pairing/session model when the server is behind the tailscale
operator's Ingress rather than the workspace's own `tailscale serve`. Settled by
opening the URL after deployment.

---

## 4. OpenCode — confirmed as a headless coding agent

`opencode-ai` npm package, latest `1.18.31`. Installs with
`npm install -g --prefix <dir> opencode-ai` (a `--allow-scripts` prompt appears
for its `postinstall` when using a modern npm; the CLI works either way).

Non-interactive execution, from `opencode run --help`:

```
opencode run [message..]
  -m, --model     model to use in the format of provider/model
      --agent     agent to use
      --format    default (formatted) or json (raw JSON events)
      --auto      auto-approve permissions that are not explicitly denied
      --attach    attach to a running opencode server (e.g. http://localhost:4096)
```

Custom OpenAI-compatible provider, from the OpenCode docs — this is how the agent
is pointed at the fleet gateway instead of a model provider:

```json
{
  "provider": {
    "fleet": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Fleet Gateway",
      "options": {
        "baseURL": "http://<control-plane>/llm/v1",
        "apiKey": "{env:FLEET_LLM_TOKEN}",
        "headers": {"X-Fleet-Agent": "{env:FLEET_AGENT}", "X-Fleet-Task": "{env:FLEET_TASK_ID}"}
      },
      "models": {"local-coder": {"name": "local-coder"}}
    }
  }
}
```

Config path via `OPENCODE_CONFIG`; `{env:...}` substitution is supported in
values, which is what lets one workspace config serve every agent and task.

---

## 5. The LLM gateway — confirmed

The cluster already runs LiteLLM at
`http://agentforge-llm.agentforge.svc.cluster.local:4000`, OpenAI-compatible.
No second gateway was stood up (SPEC §3.1, §11).

```
$ POST /v1/chat/completions {"model":"local-coder", ...}
status 200; model= local-coder content= 'pong'
usage: {"completion_tokens": 2, "prompt_tokens": 35, "total_tokens": 37,
        "prompt_tokens_details": {"cached_tokens": 0}}
models: ['local-coder', 'fast', 'smart']
```

Master key length 43, read from Secret `agentforge/agentforge-llm` key
`LITELLM_MASTER_KEY` in-process. **It is never printed, logged or written to a
file by any tooling here**, and the control plane gets it through a
`secretKeyRef`, not a value.

Aliases resolve server-side, which is what makes SPEC §12 ("logical model names")
work without any code: an agent asks for `fleet/fast` and LiteLLM decides.

### 5.1 Usage attribution — the design decision

A shared gateway key cannot attribute spend to a project, and a workspace holding
that key would violate SPEC §11. So `FLEET_LLM_BASE_URL` points workspaces at the
*control plane's* `/llm/v1` proxy, which:

- authenticates a project-scoped HMAC token (issued by the control plane, names
  exactly one project),
- enforces the project's `llmPolicy` before spending anything,
- forwards with the real key,
- records tokens and cost against project, agent and task.

The agent/task come from `X-Fleet-Agent` / `X-Fleet-Task` headers that the
workspace's opencode config fills from environment variables set per task run.

**Confirmed:** the proxy's token round-trip, policy rejection and usage recording
(unit tests), and a full `opencode run` through the proxy producing rows
attributed to the project, the agent and the task — see §10.

---

## 6. Tailscale reachability — confirmed

The operator derives the MagicDNS name from the **namespace and the Ingress
object's name**:

```
agentforge/agentforge-ts              → agentforge-agentforge-ts-ingress.tail7f3c08.ts.net
spiritual-gifts/spiritual-gifts-ts    → spiritual-gifts-spiritual-gifts-ts-ingress.tail7f3c08.ts.net
```

So `fleet` + an Ingress named `ts` gives `fleet-ts-ingress.tail7f3c08.ts.net`.
Note that the `tailscale.com/hostname` annotation does **not** change the
hostname reported in the Ingress status (`spiritual-gifts` sets it and still
resolves to the derived name), so the deployment relies on the derived name
rather than on the annotation.

---

## 7. What the control plane had to build itself

Items where an existing tool does not cover the requirement, and the spec says
not to reinvent only where an existing tool *does*:

| requirement | provided by | who implements it |
| --- | --- | --- |
| namespace, pod, persistent volume per workspace | DevPod | DevPod (§1.3) |
| Service + Ingress on the tailnet | — | control plane |
| default-deny NetworkPolicy per project | — | control plane |
| escaping the namespace default ServiceAccount | — | control plane (native provider: `automountServiceAccountToken: false`) |
| project-scoped credentials | Kubernetes Secrets | control plane wires them; values stay in Secrets |
| LLM routing | LiteLLM | LiteLLM, unchanged (§5) |
| LLM attribution per project/agent/task | — | control plane proxy (§5.1) |
| coding agent execution | OpenCode | OpenCode (§4) |
| human control surface | T3 Code | T3 Code (§3) |
| state, policy, API, events | — | control plane |

---

## 8. Substitutions and deviations from the spec

1. **DevPod is kept, but per-project namespaces are requested explicitly.** The
   spec's example config uses DevPod defaults, which put every workspace in one
   `devpod` namespace (§1.2). That satisfies §5 but not §18, so the provider
   passes `KUBERNETES_NAMESPACE` per project (§1.3).
2. **The control plane creates the Service, Ingress and NetworkPolicy.** Not
   a substitution so much as filling a hole (§7).
3. **SQLite instead of PostgreSQL.** SPEC §32 explicitly permits this for a
   prototype if the migration is clean: the schema is portable, the engine is
   built from `FLEET_DATABASE_URL`, and no query is SQLite-specific. A second
   replica is *not* safe on a `ReadWriteOnce` volume, which the chart states.
4. **T3 Code does not execute tasks.** Documented in §3 and enforced in code.
5. **A second workspace provider (native Kubernetes)** exists behind the same
   interface, to show the boundary is real and to have a fallback when the DevPod
   binary is absent.

---

## 9. What the deployed control plane taught us

Everything in §1–§8 was learned from a throwaway `devpod up` and a shell. These
were learned only by running the real thing, which is the argument for
deploying early rather than after the code is "finished".

### 9.1 DevPod's provider plugin is not part of the CLI — confirmed

`devpod provider list` is empty on a fresh `DEVPOD_HOME`, and every command then
fails with `couldn't find default provider kubernetes`. The plugin is not in the
CLI and not in our image either, because it lives *under* `DEVPOD_HOME`, which is
a persistent volume. The provider now installs it on demand.

### 9.2 `devpod provider add` is not concurrency-safe — confirmed

Two projects provisioned at the same time both ran `provider add` against the
same fresh `DEVPOD_HOME`. One succeeded, the other exited non-zero, and the
second project's whole workspace failed with `ProviderError: devpod provider add
failed with exit 1` — while the first succeeded, which made it look flaky rather
than racy. Serialised with a lock now.

### 9.3 `--workspace-env-file` does not reach the container — confirmed

DevPod accepted and logged the flag:

```
--workspace-env-file /data/logs/fleet-verify-alpha.env
```

and the file contained the credential, but the workspace's `containerEnv` had
only the variables from `devcontainer.json`. `env` and `/proc/1/environ` inside
the pod both lacked the injected name.

**What the control plane does instead:** it writes the project's credentials into
the workspace's own volume as a `0600` `.fleet/credentials.env`, over the exec
stream's **stdin** — so the value never appears in `argv`, which the API server
audits and the pod's process list exposes — and the agent's run command sources
that file. The env-file flag is kept because it is the right mechanism if DevPod
starts honouring it, and costs nothing.

### 9.4 Provisioning order is load-bearing — confirmed

The first deployed control plane copied a project's credential into the project
namespace *before* asking the provider to create it. The namespace did not exist
yet, so every project failed with a Kubernetes `404`. This is why
`WorkspaceProvider.prepare(spec)` exists: the isolation boundary is built first,
then filled.

### 9.5 T3 Code needs a compiler in the workspace — confirmed

`npm install -g t3` failed with:

```
npm error gyp ERR! stack Error: Could not find any Python installation to use
npm error gyp ERR! cwd .../node_modules/t3/node_modules/node-pty
```

T3 Code depends on `node-pty`, which builds a native addon with `node-gyp`. The
`mcr.microsoft.com/devcontainers/base:ubuntu-24.04` image has no Python and no
compiler, so the workspace bootstrap now installs `python3`, `make` and `g++`.
The same omission broke the native provider's workspace image build.

### 9.6 `devpod up` bootstraps twice — confirmed

DevPod runs both `onCreateCommand` and `postCreateCommand`, so the bootstrap runs
twice on first provision. It is idempotent (a marker file under the workspace
volume) and the second run takes about a second, so this was left alone rather
than worked around.

### 9.7 DevPod's own pod is root with a service-account token — confirmed

Inside the workspace: `uid=0(root)`, and
`/var/run/secrets/kubernetes.io/serviceaccount/token` is a live, projected token
for the namespace's `default` ServiceAccount, whose namespace is the project's
own (`fleet-verify-alpha`). It is a per-namespace identity, not a cluster one,
and the control plane's NetworkPolicy limits what it can reach — but it is not
least privilege, and the native provider does better
(`automountServiceAccountToken: false`, a Role that can only read ConfigMaps).
Recorded as a limitation of using DevPod rather than a defect in this code.

### 9.8 The workspace egress policy blocked `apt` — confirmed

The first isolation policy allowed 443/22 plus DNS and the in-cluster gateway,
on the reasoning that "package managers use HTTPS". Ubuntu's archive and
security suites are fetched over **HTTP on port 80**:

```
W: Failed to fetch http://archive.ubuntu.com/ubuntu/dists/noble/InRelease
   Could not connect to archive.ubuntu.com:80 (185.125.190.83). - connect (111: Connection refused)
E: Package 'python3' has no installation candidate
```

The workspace could not install a compiler, so T3 Code never installed and the
failure was a warning line in a log rather than an error. The allowlist is now a
setting (`workspace_egress_ports`, default `80,443,22`), because "what a
workspace may reach" is project policy and should not be folklore.

### 9.9 A Kubernetes exec with stdin never finishes — confirmed

The stream has no end-of-input signal. A remote `cat > file` therefore waits for
an EOF that the client cannot send, and the reader loop waits for a command that
will never exit: the project sat in `provisioning` with the pod `Running`, the
bootstrap complete, and **nothing in any log**. Credentials are written with
`head -c <bytes>` now, which terminates on its own, and every exec has a deadline
so the next bug of this shape surfaces as an error instead of a hang.

### 9.10 A converging namespace is not a created namespace — confirmed

Handling "the previous project's namespace is still terminating" by waiting for
it to disappear and then *returning* left the new project with no boundary at
all; its credential copy then failed with a 404 for a namespace that never
existed. Waiting has to be followed by creating.

### 9.11 Lifecycle hooks run as `remoteUser`, not as root — confirmed

DevPod runs the devcontainer's `postCreateCommand` as the devcontainer's
`remoteUser`. With `remoteUser: vscode` that user cannot `apt-get`
(`E: List directory /var/lib/apt/lists/partial is missing. - Acquire (13: Permission denied)`),
so half the toolchain silently failed. Worse, the bootstrap then created the
project's git repository as `vscode` while the control plane execs as `root`, and
git refused to work in it:

```
fatal: detected dubious ownership in repository at '/workspaces/.../.fleet/repo'
```

which surfaced as every task failing with `NOT_A_REPO`. The workspace now runs as
root, and the agent's run sets `safe.directory` regardless.

### 9.12 `DELETE` blocked on the teardown — confirmed

`DELETE /api/v1/projects/{p}` answered 202 but destroyed the workspace
synchronously, and destroying a DevPod workspace shells out to a CLI that can
take minutes — enough that a 30-second client timed out while the project was in
fact gone. Destruction moved to a worker thread.

### 9.13 `opencode run` deadlocks as root — confirmed, and it is the last mile

An agent task failed in 2.5 seconds with

```
{'type': 'error', 'sessionID': 'ses_…',
 'error': {'name': 'UnknownError',
           'data': {'message': 'Unexpected server error. Check server logs for details.'}}}
```

and the control plane's log showed **no request to the LLM proxy at all**, so
nothing about the gateway was involved yet. Running the same command the runner
runs, in the same pod, isolated it. Three separate causes stacked up, in this
order:

**1. The workspace could not reach the proxy.** The generated config pointed at
`http://fleet-api.fleet.svc.cluster.local:8000/llm/v1`, but the Service
published port **80**:

```
$ curl -sv http://fleet-api.fleet.svc.cluster.local:8000/api/v1/health
* Trying 10.43.238.116:8000...
* connect to 10.43.238.116 port 8000 from 10.42.0.119 port 48956 failed: Connection refused
```

One number now: the Service port, the URL workspaces are given and the
NetworkPolicy port are all 8000, and the policy's port is derived from the URL
so the two cannot drift apart again.

**2. The run never loaded the fleet provider.** `OPENCODE_CONFIG` was not in the
environment the runner builds, and a Kubernetes exec does not read the
devcontainer's `remoteEnv` — the same trap the PATH comment in `_env_prefix`
already described. OpenCode's own log shows exactly what it loaded:

```
loading path=/root/.config/opencode/opencode.jsonc
# …and nothing else: the run that failed never logged
#   loading path=/workspaces/fleet-verify-alpha/.fleet/opencode.json
```

so `--model fleet/local-coder` named a provider opencode had never heard of.

**3. With the provider loaded, the run hung forever as root.** The identical
binary (same `sha256`, same version, same config, same directory) completed a
run in ~2 s as uid 1000 and never returned as uid 0:

```
$ su vscode -c '… opencode run --model fleet/local-coder …'      # uid 1000
{"type":"step_start", …}
{"type":"text", …}
{"type":"step_finish", "tokens":{"total":2088,"input":2046,"output":38}, …}
exit=0

$ setpriv --reuid=0 … opencode run --model fleet/local-coder …    # uid 0
<no output, no exit, forever>
```

It is not the gateway: the same hang reproduces against a trivial local
mock gateway that returns a canonical OpenAI SSE stream, and it reproduces only
as root. The control plane's exec always lands as root, so an agent run now
drops privileges itself:

```
setpriv --reuid=vscode --regid=vscode --init-groups env HOME=/home/vscode bash -lc …
```

and the paths the agent writes — `.fleet/repo`, `.fleet/worktrees`,
`.fleet/tasks`, `credentials.env` — are handed to that user by the worktree
step and by the workspace bootstrap. `FLEET_WORKSPACE_AGENT_USER` (default
`vscode`) is the setting; an image with no such user keeps running as root,
unchanged.

This is the one to remember: `remoteEnv` does not exist for an exec, and a
container that runs as root is not the same container for every program in it.

### 9.14 An errored run was recorded as `completed` — confirmed

`Cluster.exec` threw the exec status channel away and both workspace providers
returned `ExecResult(command, 0, stdout=…)` — **a literal zero**. So
`opencode run`'s exit status, which is the only thing that could have said
"this failed", never reached the provider, and a run whose every model call was
refused was stored as `status: completed` with the error text sitting in
`result`. A control plane that reports an error as success is worse than one
that reports nothing.

The status is now read from the channel `kubectl exec` reads it from, and `-1`
means **unknown**, which must not be able to read as success. Verified in the
live pod:

```
'echo hello; exit 0'          -> exit=0
'echo out; echo err >&2; exit 7' -> exit=7   output='out\nerr\n'
'exit 3'                      -> exit=3
```

and, independently of the process status, an `error` event in opencode's JSON
stream is a failure with the agent's own message in `error`.

---

## 10. Verified end to end

See the deployment section of [`../README.md`](../README.md) for the URL and the
commands. What was actually exercised against the running deployment, with the
HTTP status or command output as evidence:

| what | evidence |
| --- | --- |
| control plane reachable on the tailnet | Ingress `fleet/ts` → `fleet-ts-ingress.tail7f3c08.ts.net` |
| API authentication | `401` without a token, `401` with a wrong one, `200` with the real one |
| project creation | `POST /api/v1/projects` → `201` |
| credential values never returned | the value appears in no API response, and the event log was searched too |
| real routing through LiteLLM | `GET /api/v1/models` → `{"source": "gateway", "models": ["local-coder","fast","smart"]}` |
| project-scoped LLM access | `POST /llm/v1/chat/completions` with a project token → `200`, `model: local-coder`, a real completion |
| a forged token is refused | `401 unknown project token` |
| LLM policy enforced before spending | a `localOnly` project asking for `smart` → `403` |
| usage attributed to the project | `GET /api/v1/projects/{p}/usage` → `{"calls": 1, "prompt_tokens": 35, "completion_tokens": 3}` grouped by model/agent/task |
| DevPod provisions a project workspace | pod `devpod-default-fl-42410` in namespace `fleet-verify-alpha`, `Running` |
| one namespace per project | two projects → `fleet-verify-alpha` and `fleet-verify-beta`, each with its own PVC and Service |
| the toolchain installs itself in the workspace | `node v24.21.0`, `opencode 1.18.31`, and a gateway-routed `opencode.json` written by the bootstrap |
| agent identity is per project | inside the workspace, the projected ServiceAccount namespace is `fleet-verify-alpha` |
| events | `project.created → workspace.creating → workspace.ready → agent.created → task.created → task.started → task.completed → commit.created` |
| **an OpenCode task runs end to end** | `POST /api/v1/agents/agt_f4c3152ddebd/tasks` → 202; `GET /api/v1/tasks/tsk_8a00b1a42ecc` → `status: completed`, `result` the agent's answer, `completedAt` 74s later |
| **the run is attributed to the task** | `GET /api/v1/projects/verify-alpha/usage` → `by_task: {key: "tsk_8a00b1a42ecc", calls: 2, prompt_tokens: 2599, completion_tokens: 49}` |
| a second task, same path | `tsk_e88428a47f42` → `completed`, `by_task: {calls: 2, prompt_tokens: 2586, completion_tokens: 30}` |
| the run's own log is readable | `GET /api/v1/agents/{agent}/logs?task=tsk_8a00b1a42ecc` → 1159 bytes of opencode JSON events (this path was permanently empty before §9.13) |
| a failed run is stored as failed | the pre-fix reproduction was stored as `completed`; after the fix the same shape returns `failed` with the agent's message in `error` (§9.14) |
| the workspace can reach the proxy | from inside the workspace pod: `curl -o /dev/null -w %{http_code}` → `200` on `/api/v1/health` (was `Connection refused`, §9.13) |

Findings that are **not** verified and are recorded as such: T3 Code serving on
its tailnet hostname end to end (the server starts and the URL is published; the
pairing flow through an Ingress was not driven), the native Kubernetes provider
against its own image (the image build was fixed but not rebuilt and exercised),
and the `devpod stop` → `devpod up` cycle preserving workspace files.

Two things a reader should not mistake for success:

- **The agent's answer is not a file.** The prompt asked for `FLEET_PROOF.txt`;
  the local model the `localOnly` policy permits returns its tool call as *text*
  (`{"name":"write","arguments":{…}}`) instead of a `tool_calls` field, so
  opencode had nothing to execute and the worktree is unchanged. Probed directly
  through the proxy with a `tools` array and `tool_choice: "auto"`: the gateway
  answers `finish_reason: stop` and puts the JSON in `content`. The end-to-end
  proof is therefore of the control plane, the proxy and the attribution, not of
  the agent editing files.
- **`usage` counts the calls that reached the gateway**, including the session
  title and any retry, which is why one task is 2 calls and ~2.6k prompt tokens
  rather than one.

