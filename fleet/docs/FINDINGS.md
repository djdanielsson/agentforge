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
(unit tests). **Open:** a full `opencode run` through the proxy producing
attributable rows — verified after deployment, see `README.md`.

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
