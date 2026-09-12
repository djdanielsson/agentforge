/**
 * Public surface of `@agentforge/api-client`.
 *
 * The client, the mappers and every domain type are re-exported here so the
 * package has exactly one entry point for both values and types.
 *
 * Relative imports carry `.js` specifiers: that is what the emitted JavaScript
 * needs to resolve under Node's ESM loader, and bundlers (Vite, esbuild) map
 * the same specifier back to the TypeScript source.
 */

export { AgentForgeClient, toWebSocketUrl } from "./client.js";
export {
  mapAgent,
  mapAgentSummary,
  mapApiKey,
  mapApiKeyCreated,
  mapConversation,
  mapConversationMessage,
  mapEventTypeInfo,
  mapGitCommitResult,
  mapGitDiff,
  mapGitFileChange,
  mapGitMergeResult,
  mapGitStatus,
  mapHealthStatus,
  mapProject,
  mapProjectDetail,
  mapTask,
  mapTaskEvent,
  mapTaskEvents,
  mapWebhook,
  mapWebhookCreated,
  mapWebhookDelivery,
  mapWorkbenchEvent,
  mapWorkspace,
  mapWorkspaceActionAccepted,
} from "./client.js";
export { ApiError, isApiError } from "./errors.js";
export type { ApiErrorOptions } from "./errors.js";
export type {
  Agent,
  AgentForgeClientOptions,
  AgentStatus,
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
