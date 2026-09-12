# ADR 0003 — Workspace providers

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Every project needs an isolated, disposable place to run an agent and an editor.
The production target is Kubernetes, because the target environment already runs
it. But a project definition that can only be realised on a cluster is painful to
develop against, impossible to test cheaply in CI, and dangerous to assume: the
whole point of isolation is that the boundary is real, and a boundary you cannot
exercise locally is a boundary you do not understand.

The project definition — repository, branch, resources, secrets, policy — should
not care which substrate eventually materialises it.

## Decision

Introduce a `WorkspaceProvider` abstraction. A provider turns a project
definition into a running, isolated workspace. The interface is deliberately
small:

```
create · start · stop · destroy · exec · get_status · inject_secrets
```

Implementations, in build order:

1. **Kubernetes** (first) — one namespace per project, named from the configured
   prefix plus the project slug (`af-demo`), containing a PersistentVolumeClaim
   mounted at `/workspace` and a pod running code-server and the OpenHands Agent
   Server.
2. **Podman** — the same project definition running in local containers, for
   development without a cluster.
3. **Local** — a development fallback that runs directly on the host. It is not
   an isolation boundary and must never be used for untrusted work.

Rules that hold for every provider:

- A workspace sees `/workspace` and nothing else. The host filesystem is never
  mounted.
- No Kubernetes API access from inside a workspace:
  `automountServiceAccountToken` is false and the API is not reachable.
- One project cannot see another project's workspace.
- `inject_secrets` resolves references into the workspace environment; it never
  receives or returns a secret value. See [ADR 0004](0004-secrets-by-reference.md).

Only a workspace provider may import a substrate's client library. The API and
the orchestrator depend on the interface, not on Kubernetes.

## Consequences

**Good.** The same project definition runs on a cluster, on Podman, or locally,
so development and CI do not need a cluster and the manifest builders can be
tested as plain objects. Isolation rules are stated once and implemented per
provider, which makes it obvious when a provider fails to honour one. The API
never imports `kubernetes`, so "the API did a slow infra thing" cannot happen by
accident. Adding a substrate — a different cloud, a different container runtime —
is a new implementation, not a rewrite.

**Bad.** The interface is the lowest common denominator of the substrates, so a
Kubernetes-only capability cannot be exposed through it without either a
capability flag or a leak. Three implementations to keep honest, and every
provider must be re-verified against the isolation rules. `Local` is genuinely
not a security boundary; a developer who forgets that and runs untrusted code on
it has no isolation at all, so its use has to be visibly discouraged. And there
is a real translation cost: the abstraction can hide a bug that is obvious in one
provider's own terms.

## Alternatives rejected

- **Kubernetes only.** Simplest to build, but it makes local development and CI
  depend on a cluster, and it couples the project model to one substrate.
- **Dev Containers as the only abstraction.** A good description of an
  environment, not a lifecycle. It does not give us create/start/stop/destroy,
  status or secret injection, and it says nothing about isolation.
- **A CRD plus an operator first.** The reconciliation logic is the same, but an
  operator is harder to debug on day one and cannot run a development workspace
  off-cluster.
- **Run agents directly in the control-plane process.** No isolation boundary at
  all, and it contradicts [ADR 0002](0002-openhands-agent-server-as-execution-layer.md).
