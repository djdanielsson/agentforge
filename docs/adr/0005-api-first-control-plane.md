# ADR 0005 — API-first control plane

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Several very different clients need to drive AgentForge: a browser dashboard, a
command-line tool for interactive work, a voice assistant (Hermes) that submits
tasks and asks for status, and external orchestrators such as CI. They will not
all exist on day one, but they are all foreseeable.

The default way to build this is a web application whose backend serves its own
frontend, with the CLI and any automation reaching in through whatever internal
endpoints happen to exist. That produces a privileged client and a second, weaker
integration path — usually several — each with its own idea of authorisation and
its own bugs.

Supervision also has a machine half: something must be able to *watch* agents and
react when one blocks or requests permission, without a human refreshing a page.

## Decision

The API is the single control plane.

- Every capability is exposed once, under a versioned prefix (`/api/v1`), as REST
  plus WebSocket. The web UI, the `agentforge` CLI and any external orchestrator
  are all clients of that API. **No client has a private path in.**
- The API owns authorisation. Clients present an API key (`X-API-Key` or
  `Authorization: Bearer`); keys are hashed at rest, scoped, and can be pinned to
  a single project.
- Events are append-only rows and are the integration point. The WebSocket stream
  and outbound webhooks carry the **same wire format**, so a subscriber never
  needs to know which one it is attached to.
- Webhooks are signed with HMAC-SHA256 over the raw body, with retries and a
  delivery history.
- Interactions are described semantically where possible. A caller asks for a
  review, a test or a deployment; it does not need to know that a task row, an
  agent turn and a git branch are involved.
- Liveness (`GET /health`) is unversioned so a probe never breaks when the API
  version moves.

## Consequences

**Good.** Authorisation is implemented once, in the one place that is actually
attacked. The CLI and Hermes get the entire product for free, because they speak
the same surface as the dashboard. The API is testable without a browser: the
suite drives it directly. Machines can supervise machines, which is what the
webhook and WebSocket contract is for. And a new client — a chat bot, a CI job, a
different frontend — is a client, not a project.

**Bad.** A UI-first affordance can never be "just a bit of frontend logic"; it
has to be an endpoint, which is slower to build for small wins. The versioning
discipline is permanent — clients will depend on field names and event types, so
both become contracts. The event hub is in-process today, so the WebSocket fanout
does not cross API processes until it moves to Postgres `LISTEN/NOTIFY` or a
stream, behind the same interface. And designing for machine clients first makes
the API larger than any single UI strictly needs.

## Alternatives rejected

- **Server-rendered UI with its own backend.** Fastest to a demo, and it makes
  the UI the privileged client and everything else second class.
- **A private CLI path that bypasses the API.** Whatever it can do is invisible to
  the API's authorisation and to anyone else's client, and it must be rebuilt
  every time.
- **gRPC-only.** Excellent for machine clients, awkward for a browser dashboard
  without a proxy layer, and it would still need the event stream that the API
  already provides.
- **Polling instead of push.** Cheaper to build and wrong for supervision: an
  agent that blocks needs to be seen promptly, not on the next poll interval.
