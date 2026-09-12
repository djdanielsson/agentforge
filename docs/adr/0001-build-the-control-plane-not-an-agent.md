# ADR 0001 — Build the control plane, not another agent

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Coding agents already exist and are good: OpenHands, Codex, Claude Code. Remote
development environments exist too: Coder, DevPod, code-server. What does not
exist is a self-hosted system for running *many* agents across *many* projects
and supervising them as first-class objects — including agents that can block,
ask for clarification, or request permission, with a human answering from one
dashboard.

The differentiating problem is not the agent loop. It is everything around it:
projects as objects, one isolated workspace per project, a durable task queue,
agents as addressable entities with status and history, a permission and
clarification flow a human can answer, and an event stream that other systems can
subscribe to.

## Decision

We build the supervision and control plane, and consume every layer below it.

- **Execution layer:** the OpenHands Agent Server, consumed over REST and
  WebSocket. Not embedded, not forked. See [ADR 0002](0002-openhands-agent-server-as-execution-layer.md).
- **Workspace isolation:** one Kubernetes namespace per project containing a pod
  and a PersistentVolumeClaim mounted at `/workspace`, behind a
  `WorkspaceProvider` interface. See [ADR 0003](0003-workspace-providers.md).
- **Editor:** code-server, embedded in the dashboard. We do not write an editor.
- **Model routing:** LiteLLM as an OpenAI-compatible gateway, so an agent's
  `model` field is a logical alias.
- **Credentials:** by reference only. See [ADR 0004](0004-secrets-by-reference.md).
- **Surface:** one versioned API for every client. See
  [ADR 0005](0005-api-first-control-plane.md).

## Consequences

**Good.** The one thing that does not exist elsewhere is the thing we spend
effort on. The agent engine, the editor and the model provider all stay
swappable behind narrow interfaces, so a weak choice in any of them is
recoverable without a rewrite. The isolation model — one namespace per project,
never mount the host — is one we already know how to operate.

**Bad.** We inherit the upstream agent server's API shape and its release
cadence, which is why exactly one file is allowed to know that shape. We take on
Kubernetes as an operational dependency, justified only because the target
environment already runs it. And a control plane is only as good as its
execution layer: if the agent runtime is unreliable, the supervision layer
faithfully reports unreliable work.

## Alternatives rejected

- **Fork OpenHands and add orchestration inside it.** Upstream drift becomes a
  permanent tax, and a multi-project supervision model does not belong inside an
  agent server.
- **Build on Coder directly.** Coder's model is one workspace per human. Ours is
  many ephemeral workspaces per project, driven by machines.
- **Write our own agent.** Out of scope and already solved; the interesting work
  is the supervision layer.
- **Build a good chat UI over a single agent.** That is the thing that already
  exists, and it does not scale to a fleet.
