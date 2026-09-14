import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ProjectDetail } from "../types";
import { StatusDot } from "./StatusDot";

// The queue, next to the conversation that fills it.
//
// A message is queued work now, so the two are one view: what you asked for on
// the left, what is waiting or running on the right. This replaced a separate
// tasks tab, which meant reading the same work in two places.
export function TaskSidebar({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();

  const tasks = useQuery({
    queryKey: ["tasks", project.id],
    queryFn: () => api.listTasks(project.id),
    refetchInterval: 3000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["tasks", project.id] });

  const cancel = useMutation({ mutationFn: (taskId: string) => api.cancelTask(taskId), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: (taskId: string) => api.deleteTask(taskId), onSuccess: refresh });
  const clearFinished = useMutation({
    mutationFn: () => api.clearFinishedTasks(project.id),
    onSuccess: refresh,
  });

  const items = tasks.data ?? [];
  const finished = items.filter((task) =>
    ["succeeded", "failed", "cancelled", "blocked"].includes(task.status),
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-surface-border px-3 py-1 text-[11px] text-neutral-500">
        <span>queue</span>
        <span className="tabular-nums">{items.length}</span>
        {/* Failures used to pile up here, so an old error read as a new one. */}
        {finished.length > 0 && (
          <button
            onClick={() => clearFinished.mutate()}
            disabled={clearFinished.isPending}
            title="Dismiss everything that has finished"
            className="ml-auto rounded border border-surface-border px-1.5 py-0.5 hover:bg-neutral-800 disabled:opacity-40"
          >
            clear {finished.length}
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {items.length === 0 && (
          <p className="px-1 py-2 text-xs text-neutral-600">
            Nothing queued. Send a message and it lands here.
          </p>
        )}
        <ul className="space-y-1">
          {items.map((task) => (
            <li key={task.id} className="rounded border border-surface-border p-2 text-[11px]">
              <div className="flex items-start gap-2">
                <StatusDot status={task.status} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-neutral-300" title={task.description}>
                    {task.description}
                  </p>
                  <p className="mt-0.5 text-[10px] text-neutral-500">
                    #{task.position} · {task.kind} · {task.status}
                    {task.attempts > 1 ? ` · attempt ${task.attempts}` : ""}
                  </p>
                  {task.commits.length > 0 && (
                    <p className="mt-0.5 text-[10px] text-emerald-400">
                      {task.commits.length} commit(s)
                    </p>
                  )}
                  {task.error && (
                    <p className="mt-0.5 break-words text-[10px] text-red-400">{task.error}</p>
                  )}
                  {task.result && !task.error && (
                    <p className="mt-0.5 line-clamp-3 text-[10px] text-neutral-500">
                      {task.result}
                    </p>
                  )}
                  {["queued", "leased", "running"].includes(task.status) ? (
                    <button
                      onClick={() => cancel.mutate(task.id)}
                      className="mt-1 rounded border border-surface-border px-1.5 py-0.5 text-[10px] hover:bg-neutral-800"
                    >
                      cancel
                    </button>
                  ) : (
                    <button
                      onClick={() => dismiss.mutate(task.id)}
                      title="Dismiss"
                      className="mt-1 rounded border border-surface-border px-1.5 py-0.5 text-[10px] text-neutral-500 hover:bg-neutral-800"
                    >
                      dismiss
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
