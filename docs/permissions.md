# Permissions

Agent permissions are a first-class policy from day one. An agent is not trusted
by default; what it may touch is stated explicitly and enforced by the layer that
can actually enforce it — the workspace provider.

## The default policy

```json
{
  "filesystem": { "workspace": true, "host": false, "other_projects": false },
  "terminal": { "enabled": true },
  "network": { "mode": "restricted" },
  "kubernetes": { "enabled": false },
  "git": { "enabled": true, "push": false },
  "secrets": { "enabled": false }
}
```

| Key | Default | Meaning |
|---|---|---|
| `filesystem.workspace` | `true` | mount `/workspace`; the agent's entire universe |
| `filesystem.host` | `false` | mount a host path — there is no safe version of this |
| `filesystem.other_projects` | `false` | read another project's volume |
| `terminal.enabled` | `true` | run commands in the workspace; also gates provider `exec` |
| `network.mode` | `restricted` | `none` (no egress), `restricted` (HTTPS/SSH/HTTP plus DNS), or `open` |
| `kubernetes.enabled` | `false` | cluster API access from inside the workspace |
| `git.enabled` | `true` | read and commit in the workspace repository |
| `git.push` | `false` | write access to the remote; pushing is where agent output leaves the sandbox |
| `secrets.enabled` | `false` | inject the secret references in scope |

A new agent gets this policy when the caller does not specify one. The policy is
validated on assignment, so a typo such as `mode: "widopen"` is rejected rather
than treated as an unknown mode.

## Where enforcement happens

A policy is only real if something enforces it. The provider turns the policy
into concrete objects:

1. **Pod spec.** `build_pod` refuses a policy it cannot implement safely rather
   than silently downgrading it:
   - `filesystem.host: true` → `ValueError` ("workspaces never mount the host");
   - `filesystem.other_projects: true` → `ValueError` (project isolation depends
     on each workspace seeing only its own volume);
   - `filesystem.workspace: false` → `ValueError` (the agent needs somewhere to
     work).

   Because creation fails loudly, an unimplementable policy is caught at agent
   creation time instead of on the first task.
2. **Service account.** `automountServiceAccountToken` follows
   `kubernetes.enabled`. With the default `false`, there is no API credential to
   steal inside the pod.
3. **NetworkPolicy.** The Kubernetes provider applies a policy implementing
   `network.mode`: `none` blocks all egress, `restricted` allows 443/22/80 plus
   DNS and nothing to the cluster's own ranges (so a workspace cannot reach
   another namespace or the cloud metadata service), `open` is unrestricted.
4. **Secret injection.** The provider injects only the references in scope, and
   only when `secrets.enabled` is true. See [secrets.md](secrets.md).

Not every provider can honour every field. `GET /api/v1/providers` reports the
differences: Podman has no NetworkPolicy equivalent and enforces `network.mode`
with `--network` and nothing finer; the Local provider is development-only and
refuses secrets entirely. A policy is only as strong as the provider behind it.

## Permission requests during a run

An agent that wants to do something outside its policy blocks and asks. That is
the behaviour the whole supervision model exists for.

1. The agent runtime raises a request. The API records it as an event of type
   `agent.permission_required` and moves the agent to `awaiting_approval`.
2. A human answers from the dashboard, the CLI, or a webhook consumer.
3. The decision is recorded as `permission.granted` or `permission.denied`, and
   the agent resumes or idles.

```bash
curl -s -X POST localhost:8000/api/v1/agents/$AID/permissions \
  -H 'content-type: application/json' \
  -d '{"request_id":"req_7f3","decision":"allow_once"}'
```

The three decisions:

| Decision | Effect |
|---|---|
| `allow_once` | permit this single action |
| `allow_project` | permit this class of action for the rest of the project's session |
| `deny` | refuse; the agent returns to `idle` |

The request event carries `request_id`, `command` and `reason`, which is what a
conversational client such as Hermes needs in order to ask a good question.

## Reading the effective policy

A caller needs to know what the agent may actually do, not what was stored, so
the read endpoint fills in defaults:

```bash
curl -s localhost:8000/api/v1/agents/$AID/permissions
```

```json
{
  "agent_id": "b242de62-ea15-47d7-bc00-4dc07fe90327",
  "policy": {
    "filesystem": {"workspace": true, "host": false, "other_projects": false},
    "terminal": {"enabled": true},
    "network": {"mode": "restricted"},
    "kubernetes": {"enabled": false},
    "git": {"enabled": true, "push": false},
    "secrets": {"enabled": false}
  }
}
```

## What is implemented today

- `AgentPermissions` is a real model (`agentforge_shared.permissions`) with
  validated fields and a tolerant loader for rows that predate a field.
- `AgentCreate` and `AgentUpdate` accept a `policy`, the agent row stores it, and
  `AgentRead` returns it.
- `GET /agents/{id}/permissions` returns the effective policy;
  `POST /agents/{id}/permissions` records a decision and emits the event.
- The provider enforces the parts that matter: service-account token, network
  mode, secret injection, and rejection of unimplementable filesystem requests.
- Tests cover the restrictive defaults, custom policies, rejection at create
  time, network mode validation, the read endpoint, and provider capability
  reporting.

Not implemented: the OpenHands Agent Server integration that would let an agent
raise a real permission request on its own. Today a request can be recorded by
any client; the agent does not yet generate one. Enforcement is also only as deep
as the provider — a policy running on Podman or Local gives weaker guarantees
than the same policy on Kubernetes.

## Changing the policy

Per-agent overrides are passed at create or update time. The intended precedence
is agent over project over the built-in default, and an absent key keeps the
restrictive value — a partial policy loosens nothing it did not name.
