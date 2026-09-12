/**
 * Domain types for the AgentForge control-plane API.
 *
 * The server serialises snake_case JSON — the pydantic schemas in
 * `packages/shared/src/agentforge_shared/schemas.py` are the wire contract.
 * These types are the idiomatic camelCase view: `client.ts` owns the explicit
 * field-by-field mapping, so no snake_case key ever reaches a caller.
 *
 * Optional wire fields are nullable rather than absent, matching the server,
 * which always emits the key.
 */

/** Any decoded JSON object, before it is mapped into a domain type. */
export type Raw = Record<string, unknown>;

// --- lifecycle enums --------------------------------------------------------
// String unions mirror the StrEnums in agentforge_shared/enums.py exactly; the
// server sends the lowercase value, never the Python member name.

/** Lifecycle of a project. */
export type ProjectStatus = "creating" | "ready" | "error" | "archived";

/** Lifecycle of a project's single workspace. */
export type WorkspaceStatus =
  | "pending"
  | "provisioning"
  | "cloning"
  | "ready"
  | "stopped"
  | "error"
  | "deleting"
  | "destroyed";

/** Lifecycle of an agent. `blocked` and `awaiting_approval` both want a human. */
export type AgentStatus =
  | "starting"
  | "idle"
  | "working"
  | "blocked"
  | "awaiting_approval"
  | "stopped"
  | "error";

/** Lifecycle of a unit of work. `leased` means claimed but not yet running. */
export type TaskStatus =
  | "queued"
  | "leased"
  | "running"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled";

/** What kind of work a task represents. */
export type TaskKind = "implement" | "review" | "test" | "deploy" | "maintain";

/** Queue priority of a task. */
export type Priority = "low" | "normal" | "high" | "urgent";

/** The answer a human gives to an agent's permission request. */
export type PermissionDecision = "allow_once" | "allow_project" | "deny";

// --- meta -------------------------------------------------------------------

/** Payload of `GET /api/v1/health`. */
export interface HealthStatus {
  status: string;
  version: string;
  /** Present on the versioned health endpoint; absent on the bare probe. */
  environment?: string;
}

// --- workspace --------------------------------------------------------------

/** The isolated environment a project's agents run in. */
export interface Workspace {
  id: string;
  projectId: string;
  namespace: string;
  pvcName: string | null;
  podName: string | null;
  serviceName: string | null;
  status: WorkspaceStatus;
  /** Backend that owns this workspace, e.g. `kubernetes` or `podman`. */
  provider: string;
  image: string | null;
  /** code-server URL, present once the workspace is ready. */
  codeServerUrl: string | null;
  /** Agent-server URL for this workspace, when the backend exposes one. */
  agentServerUrl: string | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * Acknowledgement of a workspace lifecycle request.
 *
 * Provision and destroy are asynchronous: the API records the desired state and
 * the orchestrator converges on it, so both endpoints answer 202 with this
 * envelope rather than the resulting workspace.
 */
export interface WorkspaceActionAccepted {
  projectId: string;
  /** `provision` or `destroy`. */
  action: string;
  /** Workspace status at the time the request was accepted. */
  status: string;
  detail: string | null;
}

// --- project ----------------------------------------------------------------

/** A project as returned by the list endpoint. */
export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  repositoryUrl: string | null;
  defaultBranch: string;
  status: ProjectStatus;
  settings: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

/** A project plus its workspace and agents, as returned by the detail endpoint. */
export interface ProjectDetail extends Project {
  workspace: Workspace | null;
  agents: Agent[];
}

// --- agents -----------------------------------------------------------------

/** A single autonomous coding agent bound to a project and a branch. */
export interface Agent {
  id: string;
  projectId: string;
  workspaceId: string | null;
  name: string;
  model: string;
  branch: string | null;
  status: AgentStatus;
  currentTaskId: string | null;
  /** Effective permission policy, resolved with defaults. */
  policy: Record<string, unknown>;
  /** Runtime session id; cleared when the agent is restarted. */
  sessionId: string | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * The "what is it doing, is it stuck" view of one agent.
 *
 * Produced by the summary endpoint, which joins the agent's latest progress,
 * waiting, permission and test signals into one call.
 */
export interface AgentSummary {
  agentId: string;
  name: string;
  status: AgentStatus;
  /** Description of the task the agent is running, when it has one. */
  task: string | null;
  /** Latest `agent.progress` message. */
  progress: string | null;
  filesChanged: number;
  branch: string | null;
  commits: string[];
  /** Raw `test.completed` payload, when the project has run tests. */
  tests: Record<string, unknown> | null;
  /** Why the agent is blocked: the question, or the command awaiting approval. */
  blockedReason: string | null;
  /** The human-facing question, for a blocked or awaiting-approval agent. */
  question: string | null;
  workspaceUrl: string | null;
  updatedAt: string | null;
}

/** One turn in an agent's conversation. */
export interface ConversationMessage {
  role: string;
  content: string;
  /** Runtime-generated fields the server stored alongside the turn. */
  [key: string]: unknown;
}

/** The full conversation transcript for an agent. */
export interface Conversation {
  agentId: string;
  messages: ConversationMessage[];
}

// --- tasks ------------------------------------------------------------------

/** A queued unit of work. */
export interface Task {
  id: string;
  projectId: string;
  /** Null until the task queue assigns an agent. */
  agentId: string | null;
  description: string;
  kind: TaskKind;
  priority: Priority;
  status: TaskStatus;
  position: number;
  attempts: number;
  result: string | null;
  error: string | null;
  commits: string[];
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** One event in a single task's timeline, as returned by the task events endpoint. */
export interface TaskEvent {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  createdAt: string | null;
}

/** Response of the task events endpoint. */
export interface TaskEvents {
  taskId: string;
  events: TaskEvent[];
}

// --- git --------------------------------------------------------------------

/** One changed file in a diff. */
export interface GitFileChange {
  path: string;
  /** `added` | `modified` | `deleted` | `renamed`. */
  status: string;
  additions: number;
  deletions: number;
}

/** A branch diff against a base ref, with per-file statistics. */
export interface GitDiff {
  branch: string;
  base: string;
  diff: string;
  files: GitFileChange[];
}

/** Porcelain status, current branch and recent log for a workspace. */
export interface GitStatus {
  status: string;
  branch: string;
  log: string;
}

/** Result of a git commit. */
export interface GitCommitResult {
  commit: string;
  output: string;
}

/** Result of a git merge. */
export interface GitMergeResult {
  target: string;
  output: string;
}

// --- api keys ---------------------------------------------------------------

/** A stored API key. The plaintext is only ever returned at creation. */
export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  /** Set when the key is pinned to one project. */
  projectId: string | null;
  active: boolean;
  lastUsedAt: string | null;
  createdAt: string;
}

/** An API key together with its plaintext, returned exactly once at creation. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

// --- webhooks ---------------------------------------------------------------

/** An outbound event subscription. */
export interface Webhook {
  id: string;
  url: string;
  description: string | null;
  /** Exact event types, `namespace.*` patterns, or `*`. */
  events: string[];
  projectId: string | null;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

/** A webhook together with its signing secret, returned once at creation. */
export interface WebhookCreated extends Webhook {
  secret: string;
}

/** One delivery attempt for a webhook. */
export interface WebhookDelivery {
  id: string;
  webhookId: string;
  eventId: string;
  status: string;
  attempts: number;
  responseCode: number | null;
  error: string | null;
  deliveredAt: string | null;
  createdAt: string;
}

// --- events -----------------------------------------------------------------

/** One subscribable event type, as listed by the catalog. */
export interface EventTypeInfo {
  type: string;
  description: string;
  payloadFields: string[];
}

/**
 * An event exactly as the WebSocket stream and webhook payloads emit it.
 *
 * `payload` keys stay in their wire form (`files_changed`, `request_id`, …):
 * the payload schema varies per event type, so mapping it here would invent a
 * contract the server does not promise.
 */
export interface WorkbenchEvent {
  id: string;
  type: string;
  projectId: string | null;
  agentId: string | null;
  taskId: string | null;
  payload: Record<string, unknown>;
  createdAt: string | null;
}

// --- request bodies ---------------------------------------------------------

/** Body of `createProject`. */
export interface CreateProjectInput {
  name: string;
  repositoryUrl?: string;
  defaultBranch?: string;
  description?: string;
}

/** Body of `createAgent`. */
export interface CreateAgentInput {
  name: string;
  model?: string;
  branch?: string;
  /** Omit to get the server's restrictive default policy. */
  policy?: Record<string, unknown>;
}

/** Body of `createTask` and `createAgentTask`. */
export interface CreateTaskInput {
  /** The instruction the agent receives. Required by the server. */
  prompt?: string;
  agentId?: string;
  kind?: TaskKind;
  priority?: Priority;
  branch?: string;
}

/** Body of `review`. */
export interface ReviewInput {
  branch?: string;
  base?: string;
  agentId?: string;
}

/** Body of `test`. */
export interface TestInput {
  branch?: string;
  command?: string;
  agentId?: string;
}

/** Body of `deploy`. */
export interface DeployInput {
  branch?: string;
  /** Defaults to `staging` server-side. */
  environment?: string;
  agentId?: string;
}

/** Body of `gitCommit`. */
export interface GitCommitInput {
  message: string;
  /** Paths to stage; the whole tree is staged when omitted. */
  paths?: string[];
}

/** Body of `gitMerge`. */
export interface GitMergeInput {
  sourceBranch: string;
  /** Defaults to the project's default branch server-side. */
  targetBranch?: string;
  /** Git merge strategy flag; defaults to `merge`. */
  strategy?: string;
}

/** Body of `createApiKey`. */
export interface CreateApiKeyInput {
  name: string;
  /** Defaults to `["read", "write"]` server-side. */
  scopes?: string[];
  projectId?: string;
}

/** Body of `createWebhook`. */
export interface CreateWebhookInput {
  url: string;
  description?: string;
  /** Defaults to `["*"]` server-side. */
  events?: string[];
  projectId?: string;
  /** Omitted means the server generates one and returns it once. */
  secret?: string;
}

/** Body of `updateWebhook`. Only the supplied fields change. */
export interface UpdateWebhookInput {
  url?: string;
  description?: string;
  events?: string[];
  active?: boolean;
}

// --- client plumbing --------------------------------------------------------

/** Anything `fetch` accepts. Injectable so the client works in tests and non-browser runtimes. */
export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** Constructor options for the client. */
export interface AgentForgeClientOptions {
  /** Root of the REST API. Defaults to `/api/v1`. */
  baseUrl?: string;
  /** Sent as the `X-API-Key` header when non-empty. */
  apiKey?: string;
  /** Extra `fetch` options merged into every request (credentials, keepalive, …). */
  fetchOptions?: RequestInit;
  /** Custom fetch implementation. Defaults to the global `fetch`. */
  fetch?: FetchLike;
}

/** Per-call options accepted by every method. */
export interface RequestOptions {
  /** Cancels the request, or closes the WebSocket when aborted. */
  signal?: AbortSignal;
}

/** Options for `listProjects`. */
export interface ListProjectsOptions extends RequestOptions {
  /** Page size. Server default is 100. */
  limit?: number;
  offset?: number;
}

/** Options for `listAllAgents`. */
export interface ListAllAgentsOptions extends RequestOptions {
  /** Page size. Server default is 200. */
  limit?: number;
}

/** Options for `listTasks`. */
export interface ListTasksOptions extends RequestOptions {
  /** Restricts the list to one project. */
  projectId?: string;
  /** Page size. Server default is 200. */
  limit?: number;
}

/** Callbacks for {@link AgentForgeClient.subscribeToProjectEvents}. All are optional. */
export interface ProjectEventHandlers {
  /** A decoded event. Backfilled history is delivered first, oldest first. */
  onEvent?: (event: WorkbenchEvent) => void;
  onOpen?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
}
