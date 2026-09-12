/**
 * The AgentForge REST + WebSocket client.
 *
 * One class, one base URL, one place where the wire format lives. Every method
 * returns a typed promise and accepts an optional AbortSignal, so the dashboard,
 * the CLI and any external orchestrator share exactly one description of the API
 * instead of three drifting copies of it.
 */

import { ApiError } from "./errors.js";
import type {
  Agent,
  AgentForgeClientOptions,
  AgentSummary,
  ApiKey,
  ApiKeyCreated,
  Conversation,
  ConversationMessage,
  CreateAgentInput,
  CreateApiKeyInput,
  CreateProjectInput,
  CreateTaskInput,
  CreateWebhookInput,
  DeployInput,
  EventTypeInfo,
  FetchLike,
  GitCommitInput,
  GitCommitResult,
  GitDiff,
  GitFileChange,
  GitMergeInput,
  GitMergeResult,
  GitStatus,
  HealthStatus,
  ListAllAgentsOptions,
  ListProjectsOptions,
  ListTasksOptions,
  PermissionDecision,
  Priority,
  Project,
  ProjectDetail,
  ProjectEventHandlers,
  ProjectStatus,
  Raw,
  RequestOptions,
  ReviewInput,
  Task,
  TaskEvent,
  TaskEvents,
  TaskKind,
  TaskStatus,
  TestInput,
  UpdateWebhookInput,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WorkbenchEvent,
  Workspace,
  WorkspaceActionAccepted,
  WorkspaceStatus,
} from "./types.js";

/** The server mounts the whole control plane below this prefix. */
const DEFAULT_BASE_URL = "/api/v1";

/**
 * The header the server reads first. It also accepts `Authorization: Bearer`,
 * but the header form is what the docs show and what survives a proxy that
 * rewrites Authorization.
 */
const API_KEY_HEADER = "X-API-Key";

type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

type QueryValue = string | number | boolean | null | undefined;

/** Everything the internal request helper needs to build and decode a call. */
interface RequestShape<T> {
  /** Decode a parsed JSON body. Takes `unknown` so list and object endpoints share one path. */
  parse: (raw: unknown) => T;
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
}

// --- wire-field readers -----------------------------------------------------
// The mappers below are the only code that touches snake_case. These helpers
// keep that code short and make the "missing key" case explicit rather than
// letting `undefined` escape into a typed field.

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readNullableString(value: unknown): string | null {
  return value === null || value === undefined ? null : String(value);
}

function readNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/** Normalise any JSON value to an object, so property access is always safe. */
function readRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** Drop `undefined` entries so an absent input never overrides a server default. */
function compact(values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) out[key] = value;
  }
  return out;
}

function seg(value: string): string {
  return encodeURIComponent(value);
}

// --- mappers ----------------------------------------------------------------
// Exported because a consumer that already holds raw JSON (an SSR payload, a
// webhook body, a cached response) should be able to reuse the same field
// mapping. Each takes `unknown` and tolerates a malformed value, so a partially
// broken response never throws a type error in the middle of a render.

/** Map a raw workspace payload. */
export function mapWorkspace(value: unknown): Workspace {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    projectId: readString(raw.project_id),
    namespace: readString(raw.namespace),
    pvcName: readNullableString(raw.pvc_name),
    podName: readNullableString(raw.pod_name),
    serviceName: readNullableString(raw.service_name),
    status: readString(raw.status) as WorkspaceStatus,
    provider: readString(raw.provider),
    image: readNullableString(raw.image),
    codeServerUrl: readNullableString(raw.code_server_url),
    agentServerUrl: readNullableString(raw.agent_server_url),
    error: readNullableString(raw.error),
    createdAt: readString(raw.created_at),
    updatedAt: readString(raw.updated_at),
  };
}

/** Map the acknowledgement returned by the workspace lifecycle endpoints. */
export function mapWorkspaceActionAccepted(value: unknown): WorkspaceActionAccepted {
  const raw = readRecord(value);
  return {
    projectId: readString(raw.project_id),
    action: readString(raw.action),
    status: readString(raw.status),
    detail: readNullableString(raw.detail),
  };
}

/** Map a raw project payload. */
export function mapProject(value: unknown): Project {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    name: readString(raw.name),
    slug: readString(raw.slug),
    description: readNullableString(raw.description),
    repositoryUrl: readNullableString(raw.repository_url),
    defaultBranch: readString(raw.default_branch),
    status: readString(raw.status) as ProjectStatus,
    settings: readRecord(raw.settings),
    createdAt: readString(raw.created_at),
    updatedAt: readString(raw.updated_at),
  };
}

/** Map a raw project-detail payload, including its workspace and agents. */
export function mapProjectDetail(value: unknown): ProjectDetail {
  const raw = readRecord(value);
  const workspace = raw.workspace;
  return {
    ...mapProject(raw),
    workspace: workspace === null || workspace === undefined ? null : mapWorkspace(workspace),
    agents: readArray(raw.agents).map(mapAgent),
  };
}

/** Map a raw agent payload. */
export function mapAgent(value: unknown): Agent {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    projectId: readString(raw.project_id),
    workspaceId: readNullableString(raw.workspace_id),
    name: readString(raw.name),
    model: readString(raw.model),
    branch: readNullableString(raw.branch),
    status: readString(raw.status) as Agent["status"],
    currentTaskId: readNullableString(raw.current_task_id),
    policy: readRecord(raw.policy),
    sessionId: readNullableString(raw.session_id),
    error: readNullableString(raw.error),
    createdAt: readString(raw.created_at),
    updatedAt: readString(raw.updated_at),
  };
}

/** Map a raw agent-summary payload. */
export function mapAgentSummary(value: unknown): AgentSummary {
  const raw = readRecord(value);
  const tests = raw.tests;
  return {
    agentId: readString(raw.agent_id),
    name: readString(raw.name),
    status: readString(raw.status) as AgentSummary["status"],
    task: readNullableString(raw.task),
    progress: readNullableString(raw.progress),
    filesChanged: readNumber(raw.files_changed),
    branch: readNullableString(raw.branch),
    commits: readStringArray(raw.commits),
    tests: tests === null || tests === undefined ? null : readRecord(tests),
    blockedReason: readNullableString(raw.blocked_reason),
    question: readNullableString(raw.question),
    workspaceUrl: readNullableString(raw.workspace_url),
    updatedAt: readNullableString(raw.updated_at),
  };
}

/** Map a raw task payload. */
export function mapTask(value: unknown): Task {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    projectId: readString(raw.project_id),
    agentId: readNullableString(raw.agent_id),
    description: readString(raw.description),
    kind: readString(raw.kind) as TaskKind,
    priority: readString(raw.priority) as Priority,
    status: readString(raw.status) as TaskStatus,
    position: readNumber(raw.position),
    attempts: readNumber(raw.attempts),
    result: readNullableString(raw.result),
    error: readNullableString(raw.error),
    commits: readStringArray(raw.commits),
    startedAt: readNullableString(raw.started_at),
    completedAt: readNullableString(raw.completed_at),
    createdAt: readString(raw.created_at),
    updatedAt: readString(raw.updated_at),
  };
}

/** Map one entry of a task's event timeline. */
export function mapTaskEvent(value: unknown): TaskEvent {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    type: readString(raw.type),
    payload: readRecord(raw.payload),
    createdAt: readNullableString(raw.created_at),
  };
}

/** Map the task-events response envelope. */
export function mapTaskEvents(value: unknown): TaskEvents {
  const raw = readRecord(value);
  return {
    taskId: readString(raw.task_id),
    events: readArray(raw.events).map(mapTaskEvent),
  };
}

/** Map one conversation turn, keeping any extra runtime fields. */
export function mapConversationMessage(value: unknown): ConversationMessage {
  const raw = readRecord(value);
  // Spread first so the declared `role`/`content` stay typed.
  return { ...raw, role: readString(raw.role), content: readString(raw.content) };
}

/** Map the conversation envelope. */
export function mapConversation(value: unknown): Conversation {
  const raw = readRecord(value);
  return {
    agentId: readString(raw.agent_id),
    messages: readArray(raw.messages).map(mapConversationMessage),
  };
}

/** Map a raw API key payload. */
export function mapApiKey(value: unknown): ApiKey {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    name: readString(raw.name),
    prefix: readString(raw.prefix),
    scopes: readStringArray(raw.scopes),
    projectId: readNullableString(raw.project_id),
    active: readBoolean(raw.active),
    lastUsedAt: readNullableString(raw.last_used_at),
    createdAt: readString(raw.created_at),
  };
}

/** Map an API key payload that also carries the one-time plaintext. */
export function mapApiKeyCreated(value: unknown): ApiKeyCreated {
  const raw = readRecord(value);
  return { ...mapApiKey(raw), key: readString(raw.key) };
}

/** Map a raw webhook payload. */
export function mapWebhook(value: unknown): Webhook {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    url: readString(raw.url),
    description: readNullableString(raw.description),
    events: readStringArray(raw.events),
    projectId: readNullableString(raw.project_id),
    active: readBoolean(raw.active),
    createdAt: readString(raw.created_at),
    updatedAt: readString(raw.updated_at),
  };
}

/** Map a webhook payload that also carries the one-time signing secret. */
export function mapWebhookCreated(value: unknown): WebhookCreated {
  const raw = readRecord(value);
  return { ...mapWebhook(raw), secret: readString(raw.secret) };
}

/** Map a raw webhook-delivery payload. */
export function mapWebhookDelivery(value: unknown): WebhookDelivery {
  const raw = readRecord(value);
  const responseCode = raw.response_code;
  return {
    id: readString(raw.id),
    webhookId: readString(raw.webhook_id),
    eventId: readString(raw.event_id),
    status: readString(raw.status),
    attempts: readNumber(raw.attempts),
    responseCode: responseCode === null || responseCode === undefined ? null : readNumber(responseCode),
    error: readNullableString(raw.error),
    deliveredAt: readNullableString(raw.delivered_at),
    createdAt: readString(raw.created_at),
  };
}

/** Map one event-catalog entry. */
export function mapEventTypeInfo(value: unknown): EventTypeInfo {
  const raw = readRecord(value);
  return {
    type: readString(raw.type),
    description: readString(raw.description),
    payloadFields: readStringArray(raw.payload_fields),
  };
}

/** Map one streamed event. `payload` is left in wire form — see WorkbenchEvent. */
export function mapWorkbenchEvent(value: unknown): WorkbenchEvent {
  const raw = readRecord(value);
  return {
    id: readString(raw.id),
    type: readString(raw.type),
    projectId: readNullableString(raw.project_id),
    agentId: readNullableString(raw.agent_id),
    taskId: readNullableString(raw.task_id),
    payload: readRecord(raw.payload),
    createdAt: readNullableString(raw.created_at),
  };
}

/** Map a raw file-change entry. */
export function mapGitFileChange(value: unknown): GitFileChange {
  const raw = readRecord(value);
  return {
    path: readString(raw.path),
    status: readString(raw.status),
    additions: readNumber(raw.additions),
    deletions: readNumber(raw.deletions),
  };
}

/** Map a raw diff payload. */
export function mapGitDiff(value: unknown): GitDiff {
  const raw = readRecord(value);
  return {
    branch: readString(raw.branch),
    base: readString(raw.base),
    diff: readString(raw.diff),
    files: readArray(raw.files).map(mapGitFileChange),
  };
}

/** Map a raw git-status payload. */
export function mapGitStatus(value: unknown): GitStatus {
  const raw = readRecord(value);
  return { status: readString(raw.status), branch: readString(raw.branch), log: readString(raw.log) };
}

/** Map a raw commit result. */
export function mapGitCommitResult(value: unknown): GitCommitResult {
  const raw = readRecord(value);
  return { commit: readString(raw.commit), output: readString(raw.output) };
}

/** Map a raw merge result. */
export function mapGitMergeResult(value: unknown): GitMergeResult {
  const raw = readRecord(value);
  return { target: readString(raw.target), output: readString(raw.output) };
}

/** Map the health payload. */
export function mapHealthStatus(value: unknown): HealthStatus {
  const raw = readRecord(value);
  const environment = readNullableString(raw.environment);
  return {
    status: readString(raw.status),
    version: readString(raw.version),
    ...(environment === null ? {} : { environment }),
  };
}

/** Map a JSON array of payloads with one mapper. */
function mapList<T>(value: unknown, mapper: (item: unknown) => T): T[] {
  return readArray(value).map(mapper);
}

// --- URL helpers ------------------------------------------------------------

function normaliseBaseUrl(value: string | undefined): string {
  const trimmed = (value ?? DEFAULT_BASE_URL).trim();
  const withoutTrailingSlash = trimmed.replace(/\/+$/, "");
  return withoutTrailingSlash.length > 0 ? withoutTrailingSlash : DEFAULT_BASE_URL;
}

function resolveFetch(explicit: FetchLike | undefined): FetchLike {
  if (explicit !== undefined) return explicit;
  const candidate = (globalThis as { fetch?: unknown }).fetch;
  if (typeof candidate === "function") return candidate as FetchLike;
  throw new Error(
    "No global fetch implementation found; pass one with `new AgentForgeClient({ fetch })`.",
  );
}

/**
 * Turn the REST base URL plus a path into a WebSocket URL.
 *
 * The browser WebSocket constructor needs an absolute `ws://`/`wss://` URL and
 * cannot carry custom headers, and the default base URL is relative, so a
 * relative base is resolved against the page origin. Called from Node (no page
 * origin) it falls back to the dev server's host.
 */
export function toWebSocketUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, "");
  if (/^wss?:\/\//i.test(base)) return `${base}${path}`;
  if (/^https?:\/\//i.test(base)) {
    const url = new URL(base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return `${url.origin}${url.pathname.replace(/\/+$/, "")}${path}`;
  }
  const prefix = base.startsWith("/") ? base : `/${base}`;
  const location = (globalThis as { location?: { protocol?: string; host?: string } }).location;
  if (location !== undefined && typeof location.host === "string" && location.host.length > 0) {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${location.host}${prefix}${path}`;
  }
  return `ws://localhost:8000${prefix}${path}`;
}

/**
 * Typed client for the AgentForge control-plane API.
 *
 * @example
 * ```ts
 * const client = new AgentForgeClient({ baseUrl: "/api/v1", apiKey: process.env.API_KEY });
 * const projects = await client.listProjects();
 * ```
 */
export class AgentForgeClient {
  /** Normalised API root, without a trailing slash. */
  readonly baseUrl: string;

  private readonly apiKey: string | undefined;
  private readonly fetchImpl: FetchLike;
  private readonly fetchOptions: RequestInit;

  constructor(options: AgentForgeClientOptions = {}) {
    this.baseUrl = normaliseBaseUrl(options.baseUrl);
    const apiKey = options.apiKey?.trim();
    this.apiKey = apiKey !== undefined && apiKey.length > 0 ? apiKey : undefined;
    this.fetchImpl = resolveFetch(options.fetch);
    this.fetchOptions = options.fetchOptions ?? {};
  }

  // --- request plumbing -----------------------------------------------------

  private buildUrl(path: string, query?: Record<string, QueryValue>): string {
    if (query === undefined) return `${this.baseUrl}${path}`;
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      search.set(key, String(value));
    }
    const suffix = search.toString();
    return suffix.length > 0 ? `${this.baseUrl}${path}?${suffix}` : `${this.baseUrl}${path}`;
  }

  private async request<T>(method: HttpMethod, path: string, shape: RequestShape<T>): Promise<T> {
    const url = this.buildUrl(path, shape.query);
    const headers = new Headers(this.fetchOptions.headers);
    headers.set("Accept", "application/json");
    // Only set a content type when there is actually a body: FastAPI rejects a
    // JSON content type on a request with no payload.
    if (shape.body !== undefined) headers.set("Content-Type", "application/json");
    if (this.apiKey !== undefined) headers.set(API_KEY_HEADER, this.apiKey);

    const init: RequestInit = {
      ...this.fetchOptions,
      method,
      headers,
      signal: shape.signal ?? this.fetchOptions.signal ?? null,
    };
    if (shape.body !== undefined) init.body = JSON.stringify(shape.body);

    const response = await this.fetchImpl(url, init);
    if (!response.ok) throw await ApiError.fromResponse(response, url);
    if (response.status === 204 || response.status === 205) return undefined as unknown as T;
    const text = await response.text();
    // A 200 with an empty body is still a success (some DELETE paths answer so).
    if (text.length === 0) return undefined as unknown as T;
    const decoded: unknown = JSON.parse(text);
    return shape.parse(decoded);
  }

  private get<T>(
    path: string,
    parse: (raw: unknown) => T,
    options: RequestOptions & { query?: Record<string, QueryValue> } = {},
  ): Promise<T> {
    return this.request<T>("GET", path, {
      parse,
      query: options.query,
      signal: options.signal,
    });
  }

  private post<T>(
    path: string,
    body: unknown,
    parse: (raw: unknown) => T,
    options: RequestOptions = {},
  ): Promise<T> {
    return this.request<T>("POST", path, { body, parse, signal: options.signal });
  }

  private patch<T>(
    path: string,
    body: unknown,
    parse: (raw: unknown) => T,
    options: RequestOptions = {},
  ): Promise<T> {
    return this.request<T>("PATCH", path, { body, parse, signal: options.signal });
  }

  private del(path: string, options: RequestOptions = {}): Promise<void> {
    return this.request<void>("DELETE", path, { parse: () => undefined, signal: options.signal });
  }

  // --- meta -----------------------------------------------------------------

  /** Liveness, version and environment for the running API. */
  health(options: RequestOptions = {}): Promise<HealthStatus> {
    return this.get("/health", mapHealthStatus, options);
  }

  // --- projects -------------------------------------------------------------

  /** Projects, newest first. */
  listProjects(options: ListProjectsOptions = {}): Promise<Project[]> {
    return this.get("/projects", (raw) => mapList(raw, mapProject), {
      query: { limit: options.limit, offset: options.offset },
      signal: options.signal,
    });
  }

  /** Register a project. The server creates its workspace row immediately. */
  createProject(input: CreateProjectInput, options: RequestOptions = {}): Promise<ProjectDetail> {
    return this.post(
      "/projects",
      compact({
        name: input.name,
        repository_url: input.repositoryUrl,
        default_branch: input.defaultBranch,
        description: input.description,
      }),
      mapProjectDetail,
      options,
    );
  }

  /** One project with its workspace and agents. */
  getProject(projectId: string, options: RequestOptions = {}): Promise<ProjectDetail> {
    return this.get(`/projects/${seg(projectId)}`, mapProjectDetail, options);
  }

  /**
   * Archive a project. The row is kept; the orchestrator tears the workspace down.
   *
   * The endpoint answers 204, so there is no body and callers must re-fetch to
   * observe the archived status.
   */
  deleteProject(projectId: string, options: RequestOptions = {}): Promise<void> {
    return this.del(`/projects/${seg(projectId)}`, options);
  }

  // --- workspace ------------------------------------------------------------

  /** The project's workspace. Rejects with a 404 ApiError until one exists. */
  getWorkspace(projectId: string, options: RequestOptions = {}): Promise<Workspace> {
    return this.get(`/projects/${seg(projectId)}/workspace`, mapWorkspace, options);
  }

  /**
   * Request (re)provisioning of a project's workspace.
   *
   * Idempotent: the orchestrator converges on the desired state, so calling
   * twice is not an error and does not create a second workspace. Answers 202
   * with an acknowledgement rather than the workspace — watch the project's
   * event stream for `workspace.ready`.
   */
  createWorkspace(
    projectId: string,
    options: RequestOptions = {},
  ): Promise<WorkspaceActionAccepted> {
    return this.post(
      `/projects/${seg(projectId)}/workspace`,
      undefined,
      mapWorkspaceActionAccepted,
      options,
    );
  }

  /**
   * Destroy the project's workspace and everything the agent could reach.
   *
   * This deletes the namespace, so uncommitted work is lost; committed work on a
   * pushed branch survives in the remote. Answers 202 with an acknowledgement.
   */
  deleteWorkspace(
    projectId: string,
    options: RequestOptions = {},
  ): Promise<WorkspaceActionAccepted> {
    return this.request<WorkspaceActionAccepted>(
      "DELETE",
      `/projects/${seg(projectId)}/workspace`,
      { parse: mapWorkspaceActionAccepted, signal: options.signal },
    );
  }

  // --- agents ---------------------------------------------------------------

  /** A project's agents, oldest first. */
  listAgents(projectId: string, options: RequestOptions = {}): Promise<Agent[]> {
    return this.get(`/projects/${seg(projectId)}/agents`, (raw) => mapList(raw, mapAgent), options);
  }

  /** Create an agent. The server assigns the branch when one is not given. */
  createAgent(
    projectId: string,
    input: CreateAgentInput,
    options: RequestOptions = {},
  ): Promise<Agent> {
    return this.post(
      `/projects/${seg(projectId)}/agents`,
      compact({ name: input.name, model: input.model, branch: input.branch, policy: input.policy }),
      mapAgent,
      options,
    );
  }

  /** One agent by id. */
  getAgent(agentId: string, options: RequestOptions = {}): Promise<Agent> {
    return this.get(`/agents/${seg(agentId)}`, mapAgent, options);
  }

  /** What the agent is doing and whether it is stuck, in one call. */
  getAgentSummary(agentId: string, options: RequestOptions = {}): Promise<AgentSummary> {
    return this.get(`/agents/${seg(agentId)}/summary`, mapAgentSummary, options);
  }

  /** Every agent across every project, newest first. */
  listAllAgents(options: ListAllAgentsOptions = {}): Promise<Agent[]> {
    return this.get("/agents", (raw) => mapList(raw, mapAgent), {
      query: { limit: options.limit },
      signal: options.signal,
    });
  }

  /** Stop an agent. Idempotent: an already-stopped agent is returned unchanged. */
  stopAgent(agentId: string, options: RequestOptions = {}): Promise<Agent> {
    return this.post(`/agents/${seg(agentId)}/stop`, undefined, mapAgent, options);
  }

  /** Restart a stopped or errored agent. */
  restartAgent(agentId: string, options: RequestOptions = {}): Promise<Agent> {
    return this.post(`/agents/${seg(agentId)}/restart`, undefined, mapAgent, options);
  }

  /** Record a human turn in the agent's conversation. Answers 202. */
  sendMessage(agentId: string, content: string, options: RequestOptions = {}): Promise<Agent> {
    return this.post(`/agents/${seg(agentId)}/messages`, { content }, mapAgent, options);
  }

  /** The agent's full conversation transcript. */
  getConversation(agentId: string, options: RequestOptions = {}): Promise<Conversation> {
    return this.get(`/agents/${seg(agentId)}/conversation`, mapConversation, options);
  }

  /** Answer a blocked agent's permission request. */
  decidePermission(
    agentId: string,
    requestId: string,
    decision: PermissionDecision,
    options: RequestOptions = {},
  ): Promise<Agent> {
    return this.post(
      `/agents/${seg(agentId)}/permissions`,
      { request_id: requestId, decision },
      mapAgent,
      options,
    );
  }

  // --- tasks ----------------------------------------------------------------

  /** Tasks, newest first, optionally filtered to one project. */
  listTasks(options: ListTasksOptions = {}): Promise<Task[]> {
    return this.get("/tasks", (raw) => mapList(raw, mapTask), {
      query: { project_id: options.projectId, limit: options.limit },
      signal: options.signal,
    });
  }

  /** Queue work for a project, optionally pinned to one of its agents. */
  createTask(
    projectId: string,
    input: CreateTaskInput,
    options: RequestOptions = {},
  ): Promise<Task> {
    return this.post(`/projects/${seg(projectId)}/tasks`, taskBody(input), mapTask, options);
  }

  /** Queue work directly against one agent. */
  createAgentTask(
    agentId: string,
    input: CreateTaskInput,
    options: RequestOptions = {},
  ): Promise<Task> {
    return this.post(`/agents/${seg(agentId)}/tasks`, taskBody(input), mapTask, options);
  }

  /** One task by id. */
  getTask(taskId: string, options: RequestOptions = {}): Promise<Task> {
    return this.get(`/tasks/${seg(taskId)}`, mapTask, options);
  }

  /** Cancel a task. Idempotent: a finished task is returned unchanged. */
  cancelTask(taskId: string, options: RequestOptions = {}): Promise<Task> {
    return this.post(`/tasks/${seg(taskId)}/cancel`, undefined, mapTask, options);
  }

  /** The event timeline for one task, oldest first. */
  getTaskEvents(taskId: string, options: RequestOptions = {}): Promise<TaskEvents> {
    return this.get(`/tasks/${seg(taskId)}/events`, mapTaskEvents, options);
  }

  // --- semantic actions -----------------------------------------------------

  /** Have an agent review a branch. Queues a task and answers 202. */
  review(projectId: string, input: ReviewInput = {}, options: RequestOptions = {}): Promise<Task> {
    return this.post(
      `/projects/${seg(projectId)}/review`,
      compact({ branch: input.branch, base: input.base, agent_id: input.agentId }),
      mapTask,
      options,
    );
  }

  /** Run the project's test suite. Queues a task and answers 202. */
  test(projectId: string, input: TestInput = {}, options: RequestOptions = {}): Promise<Task> {
    return this.post(
      `/projects/${seg(projectId)}/test`,
      compact({ branch: input.branch, command: input.command, agent_id: input.agentId }),
      mapTask,
      options,
    );
  }

  /** Deploy a branch to an environment. Queues a task and answers 202. */
  deploy(projectId: string, input: DeployInput = {}, options: RequestOptions = {}): Promise<Task> {
    return this.post(
      `/projects/${seg(projectId)}/deploy`,
      compact({
        branch: input.branch,
        environment: input.environment,
        agent_id: input.agentId,
      }),
      mapTask,
      options,
    );
  }

  // --- git ------------------------------------------------------------------

  /** Diff the workspace against a base ref (defaults to the project's branch). */
  gitDiff(projectId: string, base?: string, options: RequestOptions = {}): Promise<GitDiff> {
    return this.get(`/projects/${seg(projectId)}/git/diff`, mapGitDiff, {
      query: { base },
      signal: options.signal,
    });
  }

  /** Porcelain status, current branch and recent log for the workspace. */
  gitStatus(projectId: string, options: RequestOptions = {}): Promise<GitStatus> {
    return this.get(`/projects/${seg(projectId)}/git/status`, mapGitStatus, options);
  }

  /** Stage and commit inside the workspace. */
  gitCommit(
    projectId: string,
    input: GitCommitInput,
    options: RequestOptions = {},
  ): Promise<GitCommitResult> {
    return this.post(
      `/projects/${seg(projectId)}/git/commit`,
      compact({ message: input.message, paths: input.paths }),
      mapGitCommitResult,
      options,
    );
  }

  /** Check out the target branch and merge the source branch into it. */
  gitMerge(
    projectId: string,
    input: GitMergeInput,
    options: RequestOptions = {},
  ): Promise<GitMergeResult> {
    return this.post(
      `/projects/${seg(projectId)}/git/merge`,
      compact({
        source_branch: input.sourceBranch,
        target_branch: input.targetBranch,
        strategy: input.strategy,
      }),
      mapGitMergeResult,
      options,
    );
  }

  // --- api keys -------------------------------------------------------------

  /** Every API key, newest first. Revoked keys are listed with `active: false`. */
  listApiKeys(options: RequestOptions = {}): Promise<ApiKey[]> {
    return this.get("/keys", (raw) => mapList(raw, mapApiKey), options);
  }

  /**
   * Mint an API key.
   *
   * The plaintext `key` is present only in this response; the server stores a
   * hash, so a lost key can only be revoked and replaced.
   */
  createApiKey(input: CreateApiKeyInput, options: RequestOptions = {}): Promise<ApiKeyCreated> {
    return this.post(
      "/keys",
      compact({ name: input.name, scopes: input.scopes, project_id: input.projectId }),
      mapApiKeyCreated,
      options,
    );
  }

  /** Revoke an API key. Answers 204; the key row stays for audit. */
  revokeApiKey(keyId: string, options: RequestOptions = {}): Promise<void> {
    return this.del(`/keys/${seg(keyId)}`, options);
  }

  // --- webhooks -------------------------------------------------------------

  /** Every webhook subscription, newest first. */
  listWebhooks(options: RequestOptions = {}): Promise<Webhook[]> {
    return this.get("/webhooks", (raw) => mapList(raw, mapWebhook), options);
  }

  /** Create a subscription. Omit `secret` to have the server generate one. */
  createWebhook(input: CreateWebhookInput, options: RequestOptions = {}): Promise<WebhookCreated> {
    return this.post(
      "/webhooks",
      compact({
        url: input.url,
        description: input.description,
        events: input.events,
        project_id: input.projectId,
        secret: input.secret,
      }),
      mapWebhookCreated,
      options,
    );
  }

  /** Update a subscription in place. Only the supplied fields change. */
  updateWebhook(
    webhookId: string,
    input: UpdateWebhookInput,
    options: RequestOptions = {},
  ): Promise<Webhook> {
    return this.patch(
      `/webhooks/${seg(webhookId)}`,
      compact({
        url: input.url,
        description: input.description,
        events: input.events,
        active: input.active,
      }),
      mapWebhook,
      options,
    );
  }

  /** Delete a subscription. Answers 204. */
  deleteWebhook(webhookId: string, options: RequestOptions = {}): Promise<void> {
    return this.del(`/webhooks/${seg(webhookId)}`, options);
  }

  /**
   * Delivery history for one webhook, newest first.
   *
   * The first thing to check when a subscription looks quiet.
   */
  listWebhookDeliveries(
    webhookId: string,
    options: RequestOptions = {},
  ): Promise<WebhookDelivery[]> {
    return this.get(
      `/webhooks/${seg(webhookId)}/deliveries`,
      (raw) => mapList(raw, mapWebhookDelivery),
      options,
    );
  }

  // --- events ---------------------------------------------------------------

  /** Everything a webhook or stream consumer can subscribe to. */
  eventCatalog(options: RequestOptions = {}): Promise<EventTypeInfo[]> {
    return this.get("/events/catalog", (raw) => mapList(raw, mapEventTypeInfo), options);
  }

  /**
   * Open the project's event WebSocket and route frames to the handlers.
   *
   * The server backfills recent history on connect (oldest first) before
   * streaming live events, so a subscriber can treat the stream as a replayable
   * timeline rather than a since-now feed.
   *
   * Returns an unsubscribe function that closes the socket. Closing fires
   * `onClose`, so handlers should treat a close after unsubscribe as expected.
   */
  subscribeToProjectEvents(
    projectId: string,
    handlers: ProjectEventHandlers = {},
    options: RequestOptions = {},
  ): () => void {
    const url = toWebSocketUrl(this.baseUrl, `/projects/${seg(projectId)}/events`);
    const socket = new WebSocket(url);
    let closed = false;

    const unsubscribe = (): void => {
      if (closed) return;
      closed = true;
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        socket.close();
      }
    };

    socket.onopen = (event: Event): void => {
      handlers.onOpen?.(event);
    };
    socket.onmessage = (event: MessageEvent): void => {
      // A malformed frame must not kill the stream, so it is dropped rather than thrown.
      let decoded: unknown;
      try {
        decoded = JSON.parse(String(event.data));
      } catch {
        return;
      }
      handlers.onEvent?.(mapWorkbenchEvent(decoded));
    };
    socket.onclose = (event: CloseEvent): void => {
      closed = true;
      handlers.onClose?.(event);
    };
    socket.onerror = (event: Event): void => {
      handlers.onError?.(event);
    };

    if (options.signal !== undefined) {
      if (options.signal.aborted) unsubscribe();
      else options.signal.addEventListener("abort", unsubscribe, { once: true });
    }

    return unsubscribe;
  }
}

/** Shared body for both task-creation endpoints. */
function taskBody(input: CreateTaskInput): Record<string, unknown> {
  return compact({
    // The server accepts `prompt` or `description`; `prompt` is the high-level form.
    prompt: input.prompt,
    agent_id: input.agentId,
    kind: input.kind,
    priority: input.priority,
    branch: input.branch,
  });
}
