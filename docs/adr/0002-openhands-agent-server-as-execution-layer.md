# ADR 0002 — OpenHands Agent Server as the execution layer

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

The control plane needs something that can actually do the work: hold a
conversation with a model, call tools, read and write files, run a terminal, and
operate on Git. Building that loop is a large, fast-moving problem that has
already been solved several times.

We also need many of these running at once, one per project, each inside that
project's isolation boundary, and each needing to expose an editor and a terminal
to a human.

OpenHands ships an Agent Server that already exposes files, Git, terminal, VS
Code and agent operations over an API, and can run inside a workspace container.

## Decision

Each project workspace pod runs its own OpenHands Agent Server alongside
code-server. AgentForge consumes that server over REST and WebSocket. AgentForge
does **not** embed the OpenHands SDK in-process and does **not** fork the server.

- One Agent Server per project workspace: the process boundary is the isolation
  boundary.
- The control plane talks to it as a network service, the same way any other
  client would.
- Exactly one module in the repository
  (`apps/orchestrator/src/agentforge_orchestrator/openhands.py`) is allowed to
  know the Agent Server's endpoint shapes, so adapting to an upstream change is a
  one-file edit.
- An agent's `model` is a logical alias. The Agent Server sends model calls
  through the LiteLLM gateway, which owns routing and fallbacks.

## Consequences

**Good.** We do not maintain an agent loop, a tool sandbox, or an editor
integration. Running the server per workspace means a crashed or wedged agent
cannot affect another project, and the workspace provider already knows how to
create, stop and destroy that unit. Upgrading the agent runtime is a container
image change, not a code change, as long as the wire contract holds. The narrow
client boundary keeps the blast radius of an upstream API change to one file.

**Bad.** We inherit the upstream API shape and its release cadence. If the Agent
Server is unavailable or slow, the orchestrator stalls for that project and the
supervision layer can only report it. Running a full Agent Server per workspace
costs memory and start-up time per project, which is fine for a fleet that is
mostly idle and worse for many small concurrent projects. The blocking,
clarification and permission semantics we care most about must be mapped from
whatever the server exposes; where they do not map cleanly, the control plane has
to translate, and translation is where bugs live.

## Alternatives rejected

- **Embed the SDK in the control-plane process.** One process would host every
  project's agent, which throws away the per-workspace isolation boundary and
  makes a single bad agent a fleet-wide problem.
- **Fork the server and add our supervision semantics upstream-in-tree.** A
  permanent merge tax for behaviour that does not belong in an agent server.
- **Write our own agent loop.** Out of scope; the value is the supervision layer,
  not the loop.
- **One shared Agent Server for all projects.** Cheapest to run and it collapses
  the isolation model: agents in one process could observe each other's files and
  credentials.
