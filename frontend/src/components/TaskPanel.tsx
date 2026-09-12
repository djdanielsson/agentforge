import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ProjectDetail } from "../types";
import { StatusDot } from "./StatusDot";

export function TaskPanel({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState("");

  const tasks = useQuery({
    queryKey: ["tasks", project.id],
    queryFn: () => api.listTasks(project.id),
    refetchInterval: 3000,
  });

  const createTask = useMutation({
    mutationFn: () => api.createTask(project.id, description, project.agents[0]?.id),
    onSuccess: () => {
      setDescription("");
      queryClient.invalidateQueries({ queryKey: ["tasks", project.id] });
    },
  });

  const cancel = useMutation({
    mutationFn: (taskId: string) => api.cancelTask(taskId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tasks", project.id] }),
  });

  return (
    <div className="flex h-full flex-col">
      <form
        className="flex gap-2 border-b border-surface-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (description.trim()) createTask.mutate();
        }}
      >
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What should the agent build?"
          className="flex-1 rounded bg-neutral-900 px-2 py-1.5 text-sm outline-none ring-1 ring-surface-border focus:ring-neutral-600"
        />
        <button
          type="submit"
          disabled={!description.trim() || project.agents.length === 0}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
        >
          Queue task
        </button>
      </form>

      <div className="flex-1 overflow-y-auto p-3">
        {tasks.data?.length === 0 && (
          <p className="text-sm text-neutral-600">No tasks queued.</p>
        )}
        <ul className="space-y-2">
          {tasks.data?.map((task) => (
            <li
              key={task.id}
              className="flex items-start gap-3 rounded border border-surface-border p-3 text-sm"
            >
              <StatusDot status={task.status} className="mt-1.5" />
              <div className="min-w-0 flex-1">
                <p className="text-neutral-200">{task.description}</p>
                <p className="mt-1 text-[10px] text-neutral-500">
                  #{task.position} · {task.status} · attempts {task.attempts}
                  {task.commits.length > 0 && ` · ${task.commits.length} commit(s)`}
                </p>
                {task.error && <p className="mt-1 text-xs text-red-400">{task.error}</p>}
                {task.result && (
                  <pre className="mt-1 whitespace-pre-wrap text-xs text-neutral-400">
                    {task.result}
                  </pre>
                )}
              </div>
              {(task.status === "queued" || task.status === "running") && (
                <button
                  onClick={() => cancel.mutate(task.id)}
                  className="rounded border border-surface-border px-2 py-0.5 text-[10px] text-neutral-400 hover:bg-neutral-800"
                >
                  cancel
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
