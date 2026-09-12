// Thin typed wrapper over the control-plane REST API.

import type {
  Agent,
  Event,
  FileContent,
  FileListing,
  GitDiff,
  Project,
  ProjectDetail,
  Task,
} from "../types";

// Everything lives under the versioned prefix. The dev proxy and the nginx in
// the Helm chart both forward /api unchanged, so this must include /v1.
const BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),

  listProjects: () => request<Project[]>("/projects"),
  getProject: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  createProject: (body: {
    name: string;
    repository_url?: string;
    default_branch?: string;
    description?: string;
  }) => request<ProjectDetail>("/projects", { method: "POST", body: JSON.stringify(body) }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),

  createAgent: (projectId: string, body: { name: string; model?: string; branch?: string }) =>
    request<Agent>(`/projects/${projectId}/agents`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listAgents: (projectId: string) => request<Agent[]>(`/projects/${projectId}/agents`),
  updateAgent: (agentId: string, body: { name?: string; model?: string; branch?: string }) =>
    request<Agent>(`/agents/${agentId}`, { method: "PATCH", body: JSON.stringify(body) }),
  restartAgent: (agentId: string) => request<Agent>(`/agents/${agentId}/restart`, { method: "POST" }),
  stopAgent: (agentId: string) => request<Agent>(`/agents/${agentId}/stop`, { method: "POST" }),

  // Aliases an agent can run on. The gateway owns the list; the API falls back
  // to the aliases this deployment was configured with when it is not up.
  listModels: () =>
    request<{ models: string[]; source: "gateway" | "config"; default: string }>("/models"),
  sendMessage: (agentId: string, content: string) =>
    request<Agent>(`/agents/${agentId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  conversation: (agentId: string) =>
    request<{ agent_id: string; messages: { role: string; content: string }[] }>(
      `/agents/${agentId}/conversation`,
    ),
  decidePermission: (agentId: string, requestId: string, decision: string) =>
    request<Agent>(`/agents/${agentId}/permissions`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, decision }),
    }),

  listTasks: (projectId: string) => request<Task[]>(`/tasks?project_id=${projectId}`),
  createTask: (projectId: string, description: string, agentId?: string) =>
    request<Task>(`/projects/${projectId}/tasks`, {
      method: "POST",
      body: JSON.stringify({ description, agent_id: agentId }),
    }),
  cancelTask: (taskId: string) =>
    request<Task>(`/tasks/${taskId}/cancel`, { method: "POST" }),
  taskEvents: (taskId: string) =>
    request<{ task_id: string; events: Event[] }>(`/tasks/${taskId}/events`),

  // The workspace pod is not reachable from the browser, so the editor and the
  // terminal both go through the API.
  listFiles: (projectId: string, path: string) =>
    request<FileListing>(`/projects/${projectId}/files?path=${encodeURIComponent(path)}`),
  readFile: (projectId: string, path: string) =>
    request<FileContent>(`/projects/${projectId}/file?path=${encodeURIComponent(path)}`),
  writeFile: (projectId: string, path: string, content: string) =>
    request<{ path: string; size: number }>(`/projects/${projectId}/file`, {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    }),
  terminalUrl: (projectId: string) => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${BASE}/projects/${projectId}/terminal`;
  },

  gitDiff: (projectId: string) => request<GitDiff>(`/projects/${projectId}/git/diff`),
  gitStatus: (projectId: string) =>
    request<{ status: string; branch: string; log: string }>(
      `/projects/${projectId}/git/status`,
    ),

  projectEventsUrl: (projectId: string) => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${BASE}/projects/${projectId}/events`;
  },
};
