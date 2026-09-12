import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ProjectDetail } from "../types";
import { StatusDot } from "./StatusDot";

export function AgentPanel({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();
  // Hold the id, not the row: the project query refreshes every few seconds, so
  // the panel should render the server's view of the agent rather than a copy
  // taken when it was first clicked.
  const [selectedId, setSelectedId] = useState<string | null>(project.agents[0]?.id ?? null);
  const [draft, setDraft] = useState("");
  const [newAgent, setNewAgent] = useState("");
  const [newModel, setNewModel] = useState("");
  const [modelDraft, setModelDraft] = useState("");

  const agents = project.agents;
  const selected = agents.find((agent) => agent.id === selectedId) ?? agents[0] ?? null;

  // An agent's model is a logical alias resolved by the LiteLLM gateway, so the
  // suggestions come from the API rather than a list baked into the bundle.
  const models = useQuery({
    queryKey: ["models"],
    queryFn: api.listModels,
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    setModelDraft(selected?.model ?? "");
  }, [selected?.id, selected?.model]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["project", project.id] });

  const conversation = useQuery({
    queryKey: ["conversation", selected?.id],
    queryFn: () => api.conversation(selected!.id),
    enabled: Boolean(selected),
  });

  const addAgent = useMutation({
    mutationFn: () =>
      api.createAgent(project.id, {
        name: newAgent,
        model: newModel.trim() || undefined,
      }),
    onSuccess: (agent) => {
      setNewAgent("");
      setNewModel("");
      setSelectedId(agent.id);
      refresh();
    },
  });

  const changeModel = useMutation({
    mutationFn: (model: string) => api.updateAgent(selected!.id, { model }),
    onSuccess: () => refresh(),
  });

  const restart = useMutation({
    mutationFn: () => api.restartAgent(selected!.id),
    onSuccess: () => refresh(),
  });

  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(selected!.id, content),
    onSuccess: () => {
      setDraft("");
      queryClient.invalidateQueries({ queryKey: ["conversation", selected?.id] });
    },
  });

  // Valid aliases, straight from the API — the same list it validates writes
  // against, so the picker cannot offer something the server would refuse.
  const modelOptions = models.data?.models ?? [];

  useEffect(() => {
    if (!newModel && models.data) {
      setNewModel(models.data.default ?? models.data.models[0] ?? "");
    }
  }, [models.data, newModel]);

  const pendingModel = modelDraft.trim();
  const modelChanged = Boolean(pendingModel) && pendingModel !== selected?.model;

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
              onClick={() => setSelectedId(agent.id)}
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
          className="space-y-1 border-t border-surface-border p-2"
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
          <select
            value={newModel}
            onChange={(e) => setNewModel(e.target.value)}
            disabled={modelOptions.length === 0}
            title="The model gateway resolves this alias to a real model"
            className="w-full rounded bg-neutral-900 px-2 py-1 text-xs outline-none ring-1 ring-surface-border focus:ring-neutral-600 disabled:opacity-50"
          >
            {modelOptions.length === 0 && <option value="">no models available</option>}
            {modelOptions.map((alias) => (
              <option key={alias} value={alias}>
                {alias}
              </option>
            ))}
          </select>
        </form>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <>
            <header className="flex items-center gap-3 border-b border-surface-border px-4 py-2 text-xs text-neutral-400">
              <StatusDot status={selected.status} />
              <span className="font-medium text-neutral-200">{selected.name}</span>
              <form
                className="flex items-center gap-1"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (modelChanged) changeModel.mutate(pendingModel);
                }}
              >
                <label className="text-neutral-600" htmlFor="agent-model">
                  model:
                </label>
                <select
                  id="agent-model"
                  value={modelDraft}
                  onChange={(e) => setModelDraft(e.target.value)}
                  disabled={modelOptions.length === 0}
                  title="Applies to the agent's next session — restart to pick it up now."
                  className="rounded bg-neutral-900 px-1.5 py-0.5 text-neutral-200 outline-none ring-1 ring-surface-border focus:ring-neutral-600 disabled:opacity-50"
                >
                  {/* An alias the gateway has since dropped stays visible rather
                      than being silently rewritten to something else. */}
                  {modelDraft && !modelOptions.includes(modelDraft) && (
                    <option value={modelDraft}>{modelDraft} — not served</option>
                  )}
                  {modelOptions.map((alias) => (
                    <option key={alias} value={alias}>
                      {alias}
                    </option>
                  ))}
                </select>
                {modelChanged && (
                  <button
                    type="submit"
                    disabled={changeModel.isPending}
                    className="rounded border border-surface-border px-1.5 py-0.5 hover:bg-neutral-800 disabled:opacity-50"
                  >
                    {changeModel.isPending ? "saving…" : "save"}
                  </button>
                )}
              </form>
              <span className="text-neutral-600">branch: {selected.branch}</span>
              <button
                onClick={() => restart.mutate()}
                disabled={restart.isPending}
                title="Start a new session, keeping the workspace"
                className="rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800 disabled:opacity-50"
              >
                {restart.isPending ? "restarting…" : "restart"}
              </button>
              <button
                onClick={() => api.stopAgent(selected.id)}
                className="ml-auto rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800"
              >
                stop
              </button>
            </header>

            {changeModel.isError && (
              <p className="border-b border-surface-border px-4 py-1 text-xs text-red-400">
                Could not change the model: {String(changeModel.error)}
              </p>
            )}

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
