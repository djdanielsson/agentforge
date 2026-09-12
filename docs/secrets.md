# Secrets

AgentForge never stores, transports or returns a secret value. It stores a
*reference* to one and lets the workspace provider resolve that reference into
the workspace. This document describes the model, the scope rules, and where the
implementation stands.

## The rule

Secrets are always by reference, never by value.

- The API and the database hold a pointer: *which* secret store, *which* entry,
  *which* key inside it, and *which* environment variable it should become.
- The provider — Kubernetes, Podman, or an external store — resolves the pointer
  and injects the value where it is needed.
- No endpoint can return a value. The `SecretRef` model has no value column, the
  create and read schemas have no value field, and the resolved shape that
  reaches the provider (`ResolvedSecret`) carries only `env_var`, `secret_name`,
  `key` and `required`.

This is a decision made before implementation, because retrofitting it is where
secret handling normally goes wrong. See
[ADR 0004](adr/0004-secrets-by-reference.md).

## A reference

| Field | Meaning | Example |
|---|---|---|
| `name` | a human label for the reference, unique within a scope | `github` |
| `scope` | `global`, `project`, or `agent` | `project` |
| `secret_name` | the entry in the provider's secret store | `github-agent` |
| `key` | which key inside that entry | `token` |
| `env_var` | the environment variable it becomes in the workspace | `GITHUB_TOKEN` |
| `provider` | `kubernetes`, `podman`, or `external` | `kubernetes` |
| `required` | fail the workspace if it cannot be resolved | `false` |
| `project_id` | set when the scope is `project` or `agent` | `4169...` |
| `agent_id` | set when the scope is `agent` | `88c4...` |

As JSON, as the API accepts it:

```json
{
  "name": "github",
  "scope": "project",
  "secret_name": "github-agent",
  "key": "token",
  "env_var": "GITHUB_TOKEN",
  "provider": "kubernetes",
  "required": false
}
```

And as the provider receives it — note that there is still no value:

```json
{
  "env_var": "GITHUB_TOKEN",
  "secret_name": "github-agent",
  "key": "token",
  "required": false
}
```

For Kubernetes the provider turns that into an environment variable sourced from
a `Secret`:

```yaml
env:
  - name: GITHUB_TOKEN
    valueFrom:
      secretKeyRef:
        name: github-agent
        key: token
        optional: true
```

## Scopes

| Scope | Holds | Bound to |
|---|---|---|
| `global` | credentials the control plane itself needs — LLM provider keys, the GitHub app | nothing |
| `project` | credentials a specific project's workspace needs | a `project_id` |
| `agent` | credentials issued for one agent, for example a short-lived token | an `agent_id` |

The scopes exist to answer one question sharply: **an agent does not
automatically inherit every credential the control plane can see.** A global GitHub
token existing in the platform gives no project or agent the right to use it.
Resolution is additive but not transitive: a workspace sees global references,
its own project's references, and — when provisioning for a specific agent — that
agent's references. It never sees another project's, which is the property that
stops one compromised agent from reading the whole estate.

Injection is also gated by the agent's [permission policy](permissions.md):
`secrets.enabled` is `false` by default, so a workspace starts with no
credentials at all unless it is explicitly granted some.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/secrets` | global references only |
| POST | `/api/v1/secrets` | `201`; global scope only, `400` otherwise |
| GET | `/api/v1/projects/{id}/secrets` | global + this project's references |
| POST | `/api/v1/projects/{id}/secrets` | `201`; project scope, or agent scope with `agent_id` |
| DELETE | `/api/v1/secrets/{id}` | `204`; removes the reference, not the underlying secret |

A duplicate `name` in the same scope returns `409`. An agent-scoped reference
must name an agent that belongs to the project, otherwise `400`.

```bash
curl -s -X POST localhost:8000/api/v1/projects/$PID/secrets \
  -H 'content-type: application/json' \
  -d '{"name":"github","scope":"project","secret_name":"github-agent","key":"token","env_var":"GITHUB_TOKEN"}'
```

## Resolution flow

1. A reference is registered against a project or an agent.
2. When the provider creates a workspace, the orchestrator collects the
   references in scope (`agentforge_shared.secrets.resolve_secrets`) and passes
   them into the provider's `inject_secrets`.
3. The provider projects each reference into its own secret mechanism. Kubernetes
   builds environment variables from `secretKeyRef`; Podman verifies that the
   named Podman secret exists rather than copying a value.
4. If a reference is `required` and cannot be resolved, provisioning fails rather
   than starting a half-configured workspace.

The value exists only inside the workspace process environment. It is not in the
AgentForge database, not in an API response, not in an event payload, and not in
a log line.

## What each provider does

| Provider | Secret handling |
|---|---|
| `kubernetes` | resolves refs into `secretKeyRef` environment variables |
| `podman` | verifies the named podman secret exists (same contract: no value copied) |
| `local` | refuses outright — there is nowhere to keep a secret the agent cannot read |

## What is implemented today

- The `SecretRef` model and its table. There is no column that can hold a value.
- The endpoints above, with scope enforcement and conflict detection.
- `resolve_secrets` and `effective_permissions` in `agentforge_shared.secrets`.
- `inject_secrets` on the provider contract, implemented for Kubernetes and
  Podman.
- Tests assert that the schema has no value field, that an unknown `value` field
  is dropped rather than stored, that resolution respects scope boundaries, and
  that resolution never returns a value.

Not yet proven: injection has been exercised at the manifest and provider level,
not against a live cluster, and the `external` provider is a declared option with
no implementation.

## Deliberate non-goals

- **No secret values in the API, ever** — not even for an `admin` key.
- **No secret values in events** — payloads carry names and references only.
- **No "show me the value" escape hatch.** Rotating a credential means changing it
  in the store, not through AgentForge.
- **Deleting a reference does not delete the secret.** It is someone else's, and
  other workspaces may still point at it.
