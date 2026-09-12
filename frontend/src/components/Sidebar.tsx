import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { StatusDot } from "./StatusDot";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function Sidebar({ selectedId, onSelect }: Props) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");

  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

  const createProject = useMutation({
    mutationFn: () =>
      api.createProject({
        name,
        repository_url: repo || undefined,
      }),
    onSuccess: (project) => {
      setCreating(false);
      setName("");
      setRepo("");
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onSelect(project.id);
    },
  });

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-surface-border bg-surface-raised">
      <div className="flex items-center justify-between px-4 py-3">
        <h1 className="text-sm font-semibold tracking-wide text-neutral-200">
          AI WORKBENCH
        </h1>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded border border-surface-border px-2 py-0.5 text-xs text-neutral-300 hover:bg-neutral-800"
        >
          {creating ? "cancel" : "+ project"}
        </button>
      </div>

      {creating && (
        <form
          className="space-y-2 border-b border-surface-border px-4 py-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) createProject.mutate();
          }}
        >
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Project name"
            className="w-full rounded bg-neutral-900 px-2 py-1.5 text-sm outline-none ring-1 ring-surface-border focus:ring-neutral-600"
          />
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="https://github.com/org/repo (optional)"
            className="w-full rounded bg-neutral-900 px-2 py-1.5 text-xs outline-none ring-1 ring-surface-border focus:ring-neutral-600"
          />
          <button
            type="submit"
            disabled={!name.trim() || createProject.isPending}
            className="w-full rounded bg-emerald-600 px-2 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            {createProject.isPending ? "creating…" : "Create project"}
          </button>
          {createProject.error && (
            <p className="text-xs text-red-400">{String(createProject.error)}</p>
          )}
        </form>
      )}

      <nav className="flex-1 overflow-y-auto px-2 py-2">
        {projects.isLoading && (
          <p className="px-2 py-1 text-xs text-neutral-500">loading…</p>
        )}
        {projects.data?.length === 0 && (
          <p className="px-2 py-1 text-xs text-neutral-500">
            No projects yet. Create one to provision a workspace.
          </p>
        )}
        {projects.data?.map((project) => (
          <button
            key={project.id}
            onClick={() => onSelect(project.id)}
            className={
              "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition " +
              (project.id === selectedId
                ? "bg-neutral-800 text-neutral-100"
                : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200")
            }
          >
            <StatusDot status={project.status} />
            <span className="truncate">{project.name}</span>
          </button>
        ))}
      </nav>

      <div className="border-t border-surface-border px-4 py-2 text-[10px] text-neutral-600">
        {projects.data?.length ?? 0} project(s)
      </div>
    </aside>
  );
}
