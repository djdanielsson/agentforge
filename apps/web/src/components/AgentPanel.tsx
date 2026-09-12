import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Agent, ProjectDetail } from "../types";
import { StatusDot } from "./StatusDot";

export function AgentPanel({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Agent | null>(project.agents[0] ?? null);
  const [draft, setDraft] = useState("");
  const [newAgent, setNewAgent] = useState("");

  const conversation = useQuery({
    queryKey: ["conversation", selected?.id],
    queryFn: () => api.conversation(selected!.id),
    enabled: Boolean(selected),
  });

  const addAgent = useMutation({
    mutationFn: () => api.createAgent(project.id, { name: newAgent }),
    onSuccess: (agent) => {
      setNewAgent("");
      setSelected(agent);
      queryClient.invalidateQueries({ queryKey: ["project", project.id] });
    },
  });

  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(selected!.id, content),
    onSuccess: () => {
      setDraft("");
      queryClient.invalidateQueries({ queryKey: ["conversation", selected?.id] });
    },
  });

  const agents = project.agents;

  return (
    <div className="flex h-full">
      <div className="flex w-56 shrink-0 flex-col border-r border-surface-border">
        <div className="flex-1 overflow-y-auto p-2">
          {agents.length === 0 && (
            <p className="px-2 py-1 text-xs text-neutral-500">No agents yet.</p>
          )}
          {agents.map((agent) => (
            <button
              key={agent.id}
              onClick={() => setSelected(agent)}
              className={
                "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs " +
                (agent.id === selected?.id
                  ? "bg-neutral-800 text-neutral-100"
                  : "text-neutral-400 hover:bg-neutral-900")
              }
            >
              <StatusDot status={agent.status} />
              <span className="truncate">{agent.name}</span>
            </button>
          ))}
        </div>
        <form
          className="border-t border-surface-border p-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (newAgent.trim()) addAgent.mutate();
          }}
        >
          <input
            value={newAgent}
            onChange={(e) => setNewAgent(e.target.value)}
            placeholder="+ agent name"
            className="w-full rounded bg-neutral-900 px-2 py-1 text-xs outline-none ring-1 ring-surface-border focus:ring-neutral-600"
          />
        </form>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <>
            <header className="flex items-center gap-3 border-b border-surface-border px-4 py-2 text-xs text-neutral-400">
              <StatusDot status={selected.status} />
              <span className="font-medium text-neutral-200">{selected.name}</span>
              <span className="text-neutral-600">model: {selected.model}</span>
              <span className="text-neutral-600">branch: {selected.branch}</span>
              <button
                onClick={() => api.stopAgent(selected.id)}
                className="ml-auto rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800"
              >
                stop
              </button>
            </header>

            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              {conversation.data?.messages.length === 0 && (
                <p className="text-neutral-600">
                  No messages yet — give the agent a task.
                </p>
              )}
              {conversation.data?.messages.map((message, index) => (
                <div
                  key={index}
                  className={
                    message.role === "user"
                      ? "rounded bg-neutral-800 p-2 text-neutral-100"
                      : "rounded border border-surface-border p-2 text-neutral-300"
                  }
                >
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-neutral-500">
                    {message.role}
                  </div>
                  <pre className="whitespace-pre-wrap font-sans">{message.content}</pre>
                </div>
              ))}
            </div>

            <form
              className="border-t border-surface-border p-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (draft.trim()) send.mutate(draft);
              }}
            >
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={2}
                placeholder="Ask the agent to do something…"
                className="w-full resize-none rounded bg-neutral-900 px-2 py-1.5 text-sm outline-none ring-1 ring-surface-border focus:ring-neutral-600"
              />
            </form>
          </>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-neutral-500">
            Create an agent to start working.
          </div>
        )}
      </div>
    </div>
  );
}
