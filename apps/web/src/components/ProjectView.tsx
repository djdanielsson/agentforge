import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useEventStream } from "../hooks/useEventStream";
import { AgentPanel } from "./AgentPanel";
import { DiffPanel } from "./DiffPanel";
import { StatusDot } from "./StatusDot";
import { TaskPanel } from "./TaskPanel";
import { WorkspaceFrame } from "./WorkspaceFrame";

type Tab = "editor" | "agent" | "tasks" | "diff";

export function ProjectView({ projectId }: { projectId: string }) {
  const [tab, setTab] = useState<Tab>("agent");

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    refetchInterval: 4000,
  });

  const { events, connected } = useEventStream(api.projectEventsUrl(projectId));

  if (project.isLoading) {
    return <div className="p-6 text-sm text-neutral-500">Loading project…</div>;
  }
  if (project.error || !project.data) {
    return <div className="p-6 text-sm text-red-400">{String(project.error)}</div>;
  }

  const data = project.data;

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <header className="flex items-center gap-3 border-b border-surface-border px-4 py-2.5">
        <StatusDot status={data.workspace?.status ?? "pending"} />
        <h2 className="text-sm font-semibold text-neutral-100">{data.name}</h2>
        <span className="text-xs text-neutral-500">
          {data.repository_url ?? "no repository"} · {data.default_branch}
        </span>
        <span className="ml-auto flex items-center gap-2 text-[10px] text-neutral-500">
          <span
            className={
              "inline-block h-1.5 w-1.5 rounded-full " +
              (connected ? "bg-emerald-500" : "bg-neutral-600")
            }
          />
          {connected ? "live" : "offline"}
        </span>
      </header>

      <nav className="flex gap-1 border-b border-surface-border px-3 py-1.5">
        {(["editor", "agent", "tasks", "diff"] as Tab[]).map((name) => (
          <button
            key={name}
            onClick={() => setTab(name)}
            className={
              "rounded px-3 py-1 text-xs capitalize " +
              (tab === name
                ? "bg-neutral-800 text-neutral-100"
                : "text-neutral-500 hover:text-neutral-300")
            }
          >
            {name === "editor" ? "editor / terminal" : name}
          </button>
        ))}
      </nav>

      <section className="min-h-0 flex-1">
        {tab === "editor" && <WorkspaceFrame project={data} />}
        {tab === "agent" && <AgentPanel project={data} />}
        {tab === "tasks" && <TaskPanel project={data} />}
        {tab === "diff" && <DiffPanel project={data} />}
      </section>

      <footer className="flex items-center gap-3 border-t border-surface-border px-4 py-1.5 text-[10px] text-neutral-600">
        <span>workspace: {data.workspace?.namespace ?? "—"}</span>
        <span>agents: {data.agents.length}</span>
        <span className="ml-auto">{events.length} event(s) buffered</span>
      </footer>
    </main>
  );
}
