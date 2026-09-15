# Can T3 Code be extended the way we want?

**Question this answers:** is there a plugin/extension surface in T3 Code that could
add fleet-specific settings to project creation, so that one T3 Code UI manages
many projects instead of each project having its own T3 Code?

**Answer: no plugin API exists.** T3 Code v0.0.40 ships no extension mechanism for
UI or settings. What follows is the evidence, and the three shapes that are
actually available.

## What was checked

Version `0.0.40` of the npm package `t3`, unpacked and searched, plus the upstream
repository (`pingdotgg/t3code`, cloned at `main`):

- The server bundle (`dist/bin.mjs`, ~8.5 MB) contains **no** `pluginApi`,
  `extensionPoint`, or comparable symbol — `grep` counts are zero. The client
  bundle is static assets; nothing in it loads third-party UI.
- **`mcpServers` appears 41 times in the server bundle**, and MCP wiring is visible
  in `apps/server/src/provider/Layers/*Adapter.ts` (Claude, Cursor, Grok,
  Antigravity) and `apps/server/src/provider/acp/AcpSessionRuntime.ts`. MCP is the
  extension surface an agent session sees.
- Settings are built in, not extensible: `docs/user/project-settings.md` documents
  a full settings model — environment values with per-project overrides,
  inheritance, "Mixed" states, reset — but every row is defined in code
  (`packages/shared/projectSettings`, resolved in
  `apps/server/src/orchestration/Layers/ProviderCommandReactor.ts`). There is no
  row registry for third parties to add to.
- `docs/internals/devices.md` shows the pattern T3 uses for its own bolt-on
  (`device_*` MCP toolkit, installed into the T3 home, gated behind a consent
  step). That is the closest thing to "a plugin" in this codebase, and it is an
  MCP server plus a panel T3 itself implements.
- The README explicitly invites forking: *"we want you to have everything you need
  to fork and build the editor that you want."*

## Why the current build has a T3 per project

Not an accident of the build: `docs/fleet/SPEC.md` §10 and §27 specify that T3 Code
"runs inside the project workspace as a remote environment/server". T3's own
architecture makes an environment *a machine running its server*, and it drives
agent CLIs installed on that machine — so "T3 per project workspace" is what that
sentence produces.

## The three shapes available

### A. One T3 environment; projects as checkouts; fleet as an MCP server

A single T3 server (one pod, or the user's own machine) owns every project as a
directory/worktree — T3 natively models one project with several checkouts and
per-project setting overrides. The control plane provisions the directories and
the agents, and exposes fleet operations (create project, provision workspace, add
agent, submit task, read status) as an **MCP server** registered into the provider
session, so it is driven from T3's chat.

- No fork. Uses the only extension seam that exists.
- Trade-off: projects are directories on one machine, not isolated namespaces —
  the namespace/PVC/NetworkPolicy isolation in this repository stops applying.
- "Extra settings at project creation" happen through fleet (its API/MCP), not
  inside T3's create-project dialog.

### B. Fork T3 Code and add a Fleet panel

Add settings rows and a project-creation flow that calls the control-plane API.
This is the only way to get fields inside T3's own UI.

- Cost: an ongoing fork of a large TypeScript/Effect monorepo (server bundle
  ~8.5 MB, client assets ~89 MB), tracking upstream releases.
- Also the only option that can keep per-project namespaces *and* put the controls
  in T3's UI.

### C. Drop T3 from the product

Keep the isolation model (namespace per project, DevPod workspace, OpenCode agent,
LLM gateway, tasks/events/usage attribution) and let the control plane's own page
be the fleet surface. T3 stays a single instance the user runs where they like,
for driving one project at a time.

- Cheapest, and keeps every isolation property already built and verified.
- Gives up T3's UI as the fleet cockpit.

## What is already true regardless of the choice

Verified against the deployed control plane, independent of this question: project
→ DevPod workspace (own namespace, pod, PVC) → agent → task → the control plane's
LLM proxy → LiteLLM → a tool-capable model writing files and committing in its own
git worktree, with usage attributed per project, agent and task. The T3 question
is only about which UI sits on top.
