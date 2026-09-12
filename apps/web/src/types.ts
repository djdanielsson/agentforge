// Mirrors the API schemas in packages/shared/src/agentforge_shared/schemas.py.

export type ProjectStatus = "creating" | "ready" | "error" | "archived";
export type WorkspaceStatus =
  | "pending"
  | "provisioning"
  | "cloning"
  | "ready"
  | "stopped"
  | "error"
  | "deleting";
export type AgentStatus =
  | "starting"
  | "idle"
  | "working"
  | "blocked"
  | "awaiting_approval"
  | "stopped"
  | "error";
export type TaskStatus =
  | "queued"
  | "leased"
  | "running"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface Workspace {
  id: string;
  project_id: string;
  namespace: string;
  pvc_name: string | null;
  pod_name: string | null;
  service_name: string | null;
  status: WorkspaceStatus;
  image: string | null;
  code_server_url: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  repository_url: string | null;
  default_branch: string;
  status: ProjectStatus;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  workspace: Workspace | null;
  agents: Agent[];
}

export interface Agent {
  id: string;
  project_id: string;
  workspace_id: string | null;
  name: string;
  model: string;
  branch: string | null;
  status: AgentStatus;
  current_task_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  agent_id: string | null;
  description: string;
  status: TaskStatus;
  position: number;
  attempts: number;
  result: string | null;
  error: string | null;
  commits: string[];
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Event {
  id: string;
  type: string;
  project_id: string | null;
  agent_id: string | null;
  task_id: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface GitFileChange {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface GitDiff {
  branch: string;
  base: string;
  diff: string;
  files: GitFileChange[];
}

export interface FileEntry {
  name: string;
  path: string;
  type: "dir" | "file" | "symlink" | "other";
  size: number;
}

export interface FileListing {
  path: string;
  root: string;
  entries: FileEntry[];
}

export interface FileContent {
  path: string;
  size: number;
  /** "base64" means the file is not text; the editor refuses to render it. */
  encoding: "utf8" | "base64";
  content: string;
}
