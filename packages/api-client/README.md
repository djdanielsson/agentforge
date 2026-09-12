# @agentforge/api-client

Typed TypeScript client for the AgentForge control-plane API.

It mirrors the FastAPI surface in `apps/api` (REST under `/api/v1`, plus the
project event WebSocket) and is intended to be the shared source of truth for
the dashboard, the CLI and any external orchestrator.

- One `AgentForgeClient` class; every method returns a typed promise.
- camelCase domain types. The wire format is snake_case, and the client maps it
  explicitly, so no snake_case key reaches your code.
- `X-API-Key` auth when a key is configured.
- A typed `ApiError` (carrying `status` and `detail`) on every non-2xx response.
- An optional `AbortSignal` on every call, including the WebSocket subscription.

## Install

```bash
npm install @agentforge/api-client
```

## Usage

```ts
import { AgentForgeClient, ApiError, type Task } from "@agentforge/api-client";

const client = new AgentForgeClient({
  baseUrl: "/api/v1",            // default
  apiKey: process.env.AGENTFORGE_API_KEY, // optional; sent as X-API-Key
});

// Create a project and wait for its workspace.
const project = await client.createProject({
  name: "ComplianceFlow",
  repositoryUrl: "https://github.com/example/compliance-flow",
  defaultBranch: "main",
});

// Queue some work and watch the agent.
const agent = await client.createAgent(project.id, { name: "scout" });
const task = await client.createTask(project.id, {
  prompt: "Add a /healthz endpoint and a test for it",
  agentId: agent.id,
  priority: "high",
});

const summary = await client.getAgentSummary(agent.id);
console.log(summary.status, summary.progress, summary.filesChanged);

// Cancel a call.
const controller = new AbortController();
await client.listTasks({ projectId: project.id, signal: controller.signal });

// Stream a project's events (history is backfilled on connect, oldest first).
const unsubscribe = client.subscribeToProjectEvents(
  project.id,
  {
    onEvent: (event) => console.log(event.type, event.payload),
    onOpen: () => console.log("connected"),
    onClose: () => console.log("disconnected"),
    onError: (error) => console.error("stream error", error),
  },
);
// ... later
unsubscribe();
```

Failures are typed, so callers can branch on the status without re-reading the body:

```ts
try {
  await client.getProject("does-not-exist");
} catch (error) {
  if (error instanceof ApiError) {
    console.error(error.status, error.detail); // 404, "project not found"
  }
}
```

## Notes

- **Response shapes.** The server's pydantic schemas are the contract; the
  camelCase interfaces in `src/types.ts` are the intended view of it.
- **Asynchronous lifecycle.** `createWorkspace` and `deleteWorkspace` answer
  `202` with a `WorkspaceActionAccepted` acknowledgement, not the resulting
  workspace — the orchestrator converges on the desired state. Watch
  `subscribeToProjectEvents` for `workspace.ready` / `workspace.destroyed`.
- **Event payloads.** `WorkbenchEvent.payload` keeps its wire keys
  (`files_changed`, `request_id`, …) because the payload schema varies by event
  type. `payloadFields` from `eventCatalog()` lists them per type.
- **WebSockets.** Browsers cannot set headers on a WebSocket, and the server's
  stream routes are unauthenticated today; the subscription opens a plain
  `ws://`/`wss://` URL derived from `baseUrl`.
- **Targets bundlers.** `moduleResolution: bundler`, `.js`-suffixed relative
  import specifiers (resolvable by Node ESM and bundlers alike), ESM output in
  `dist/`.
- **Coverage.** This package covers projects, workspaces, agents, tasks,
  actions, git, keys, webhooks and events. Not yet covered — because nothing
  consumes them yet — are `GET /providers`, the `/secrets` endpoints,
  `GET /agents/{id}/permissions`, `GET /version` and the nested
  `GET /projects/{id}/agents/{agent_id}/status` alias.

## Development

```bash
npm install
npm run typecheck
npm run build
```
