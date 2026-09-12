# Roadmap

## MVP #1 — one project, one agent, end to end

The foundation. Deliverable: you can tell the agent *"add a /health endpoint"*
and watch it modify files in the workspace, then see `git diff` in the UI.

- [ ] Monorepo scaffold, shared models, migrations
- [ ] `POST /projects` → orchestrator creates a k3s workspace (PVC + pod)
- [ ] code-server reachable from the dashboard
- [ ] OpenHands agent container started in the workspace
- [ ] Chat panel wired to the agent
- [ ] `GET /projects/{id}/git/diff` rendered in the UI
- [ ] Terminal (WebSocket → `kubectl exec`)

## MVP #2 — many agents

- [ ] Multiple agents per project, each on its own branch
- [ ] Task queue with leases + retries
- [ ] Agent status, stop / restart / destroy
- [ ] Per-agent conversation history
- [ ] Diff viewer with merge flow
- [ ] Approval flow for risky commands (`npm install`, `rm`, …)

## MVP #3 — the control centre

- [ ] Dashboard: project list, agent fleet view, live status
- [ ] Monaco-based editor replacing the code-server iframe
- [ ] Agent supervision as a first-class object: blocked / needs-clarification,
      permission requests with Allow-once / Allow-for-project / Deny
- [ ] LiteLLM model policy per project and per agent, with cost display

## Later

- [ ] Dev Container spec support (`.devcontainer/devcontainer.json` → workspace)
- [ ] CRD + operator for workspaces
- [ ] OIDC auth, multi-user, per-project permissions
- [ ] Helm chart, Argo CD app
- [ ] NetworkPolicy egress controls surfaced in the UI
