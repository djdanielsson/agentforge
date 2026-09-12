import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ProjectDetail } from "../types";

export function DiffPanel({ project }: { project: ProjectDetail }) {
  const ready = project.workspace?.status === "ready";
  const diff = useQuery({
    queryKey: ["diff", project.id],
    queryFn: () => api.gitDiff(project.id),
    enabled: ready,
    retry: false,
  });

  if (!ready) {
    return (
      <Centered text="Git is unavailable until the workspace is ready." />
    );
  }
  if (diff.isLoading) return <Centered text="Loading diff…" />;
  if (diff.error) return <Centered text={String(diff.error)} />;
  if (!diff.data?.diff) return <Centered text="No changes against the base branch." />;

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-surface-border px-4 py-2 text-xs text-neutral-400">
        <span className="text-neutral-200">{diff.data.branch}</span>
        <span className="text-neutral-600"> vs {diff.data.base}</span>
        <span className="ml-3 text-neutral-600">{diff.data.files.length} file(s)</span>
      </header>
      <div className="flex-1 overflow-auto p-4">
        <pre className="whitespace-pre font-mono text-xs leading-relaxed text-neutral-300">
          {diff.data.diff}
        </pre>
      </div>
    </div>
  );
}

function Centered({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-8 text-center text-sm text-neutral-500">
      {text}
    </div>
  );
}
