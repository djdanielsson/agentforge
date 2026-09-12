# ADR 0001 — Build the control plane, not another agent

- **Status:** accepted
- **Date:** 2026-09-12

## Context

Coding agents already exist and are good: OpenHands, Codex, Claude Code. Remote
development environments also exist: Coder, DevPod, code-server. What does not
exist is a self-hosted system for running *many* agents across *many* projects
and supervising them as first-class objects — agents that can block, ask for
clarification, or request permission, with a human answering from one dashboard.

## Decision

We build the orchestration/UI layer and consume the rest.

- **Agent engine:** OpenHands, via its SDK/REST API. Not forked.
- **Workspace isolation:** one k3s pod + PVC per project, rendered by our own
  controller. Dev Container spec support comes later.
- **Editor:** code-server embedded first; Monaco later. We do not write an editor.
- **Models:** LiteLLM as an OpenAI-compatible gateway, so an agent's `model`
  field is a logical alias.

## Consequences

**Good.** We can ship the differentiating layer quickly. Model choice, agent
engine and editor stay swappable behind narrow interfaces. The isolation model
is one we already know how to operate.

**Bad.** We inherit OpenHands' API shape and its release cadence — hence
`orchestrator/openhands.py` is deliberately the only file that knows its shape.
We also take on Kubernetes as an operational dependency, which is justified
only because the target environment already runs it.

## Alternatives rejected

- **Fork OpenHands and add orchestration inside it.** Upstream drift becomes a
  permanent tax, and the multi-project model does not belong in an agent server.
- **Build on Coder directly.** Coder's model is "one workspace per human". Ours
  is "many ephemeral workspaces per project, driven by machines".
- **Write our own agent.** Out of scope and already solved.
