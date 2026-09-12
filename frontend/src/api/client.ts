// Thin typed wrapper over the control-plane REST API.

import type {
  Agent,
  Event,
  GitDiff,
  Project,
  ProjectDetail,
  Task,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

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
  stopAgent: (agentId: string) => request<Agent>(`/agents/${agentId}/stop`, { method: "POST" }),
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
