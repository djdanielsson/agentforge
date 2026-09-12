# ADR 0004 — Secrets by reference

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Agents need credentials to do useful work: push to a repository, install from a
private registry, call an API. The control plane also holds credentials of its
own, such as LLM provider keys for the model gateway.

The obvious implementation is to store secrets in the control-plane database and
hand them out when a workspace starts. That turns the control plane into a
high-value target, and secret values then leak through every surface that
serialises state: API responses, database backups, event payloads, webhook
bodies, log lines, and the dashboard.

There is a second, independent problem: an agent should not automatically inherit
every credential the control plane can see. A global Git token held for
infrastructure should not become available to an arbitrary project's agent just
because both live in the same system.

## Decision

Secrets are always by reference, never by value.

- The API and the database store a reference: `name`, `scope`, the secret store
  entry, the key inside it, and the environment variable it becomes. For example
  `name gitHub`, `scope project`, store entry `github-agent`, key `token`,
  environment variable `GITHUB_TOKEN`.
- The workspace provider resolves the reference from its own secret store and
  injects the value into the workspace. The provider is the only component that
  ever holds a value.
- **The API must never be able to return a secret value.** There is no endpoint
  that reads one, no admin override, and the read schema has no value field. The
  value does not appear in events.
- Scopes are `global` (credentials the control plane itself uses, such as LLM
  provider credentials or the GitHub app), `project` (credentials a specific
  project needs) and `agent` (temporary credentials issued for one agent).
  Scope determines what a workspace is allowed to resolve.
- Injection is governed by the agent's permission policy: `secrets.enabled` is
  `false` by default.

## Consequences

**Good.** The control plane is not a secret store, so compromising it — a leaked
database, an over-broad API key, an event subscription, a backup — does not leak
credentials. Least privilege becomes expressible: an agent gets exactly the
credentials for its scope, not everything the platform can reach. Rotation
happens where the secret already lives, and AgentForge needs no change to pick it
up. Reference-only storage is also what makes per-agent injection policy
enforceable at all, because there is a thing to authorise.

**Bad.** The provider must have access to a secret store, which is real
infrastructure a local development setup otherwise would not need. Resolution is
an extra step with a new failure mode — a missing or unresolvable reference —
and a `required` reference must fail the workspace loudly rather than start a
half-configured one. Debugging is indirect: "why is this variable empty?" is
answered in the store or the provider, not in the control plane. And operators
used to seeing a value in a UI will have to adjust to seeing only a reference.

## Alternatives rejected

- **Store secrets encrypted in the control-plane database.** Better than
  plaintext, still makes the control plane the thing to steal, and still leaks
  through any code path that decrypts.
- **Pass secrets in the workspace-creation request.** They end up in request
  bodies, logs and retries, and any client of the API becomes a secret
  courier.
- **Give every workspace all global credentials.** Simplest, and exactly the
  failure the scope model exists to prevent.
- **Bake credentials into a workspace image or environment file.** Unrotatable
  without rebuilding, and present in every copy of the artifact.
