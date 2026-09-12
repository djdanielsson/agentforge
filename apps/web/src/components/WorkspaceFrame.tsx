import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Editor from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";

import { api } from "../api/client";
import type { FileEntry, ProjectDetail } from "../types";
import { Terminal } from "./Terminal";
import "../monaco";

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  css: "css",
  go: "go",
  html: "html",
  java: "java",
  js: "javascript",
  json: "json",
  jsx: "javascript",
  md: "markdown",
  py: "python",
  rb: "ruby",
  rs: "rust",
  sh: "shell",
  sql: "sql",
  ts: "typescript",
  tsx: "typescript",
  yml: "yaml",
  yaml: "yaml",
};

function languageOf(path: string): string {
  const extension = path.split(".").pop() ?? "";
  return LANGUAGE_BY_EXTENSION[extension.toLowerCase()] ?? "plaintext";
}

function parentOf(dir: string): string {
  const parts = dir.split("/").filter(Boolean);
  parts.pop();
  return parts.length ? `/${parts.join("/")}` : "/";
}

// The workspace editor and terminal, native to the app.
//
// The workspace pod is not reachable from a browser — `code_server_url` is a
// cluster-internal Service name — so everything here goes through the API, which
// execs into the pod. That is also why the tools are part of this app rather
// than a second app inside a frame.
export function WorkspaceFrame({ project }: { project: ProjectDetail }) {
  const queryClient = useQueryClient();
  const workspace = project.workspace;
  const ready = workspace?.status === "ready";

  const [dir, setDir] = useState("/workspace");
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [showTerminal, setShowTerminal] = useState(true);

  const listing = useQuery({
    queryKey: ["files", project.id, dir],
    queryFn: () => api.listFiles(project.id, dir),
    enabled: ready,
  });

  const file = useQuery({
    queryKey: ["file", project.id, openPath],
    queryFn: () => api.readFile(project.id, openPath!),
    enabled: ready && Boolean(openPath),
  });

  // Seed the editor from the server whenever a different file arrives.
  useEffect(() => {
    setDraft(file.data?.encoding === "utf8" ? file.data.content : null);
  }, [file.data]);

  const save = useMutation({
    mutationFn: () => api.writeFile(project.id, openPath!, draft ?? ""),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["file", project.id, openPath] }),
  });

  const dirty = draft !== null && draft !== file.data?.content;
  const entries: FileEntry[] = listing.data?.entries ?? [];
  const root = listing.data?.root ?? "/workspace";

  const editorTheme = useMemo(() => "vs-dark", []);

  if (!workspace) {
    return <Empty text="No workspace record for this project." />;
  }
  if (!ready) {
    return (
      <Empty
        text={
          workspace.error
            ? `Workspace error: ${workspace.error}`
            : `Workspace is ${workspace.status}. The orchestrator is provisioning it.`
        }
      />
    );
  }

  return (
    <div className="flex h-full">
      <div className="flex w-64 shrink-0 flex-col border-r border-surface-border">
        <div className="flex items-center gap-1 border-b border-surface-border px-2 py-1 text-[10px] text-neutral-500">
          <button
            onClick={() => setDir(parentOf(dir))}
            disabled={dir === root}
            className="rounded px-1 hover:bg-neutral-800 disabled:opacity-30"
            title="Parent directory"
          >
            ..
          </button>
          <span className="truncate" title={dir}>
            {dir}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto p-1">
          {listing.isLoading && <p className="px-2 py-1 text-xs text-neutral-600">Loading…</p>}
          {listing.error && (
            <p className="px-2 py-1 text-xs text-red-400">{String(listing.error)}</p>
          )}
          {listing.data && entries.length === 0 && (
            <p className="px-2 py-1 text-xs text-neutral-600">Empty directory.</p>
          )}
          {entries.map((entry) => (
            <button
              key={entry.path}
              onClick={() =>
                entry.type === "dir" ? setDir(entry.path) : setOpenPath(entry.path)
              }
              className={
                "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs " +
                (entry.path === openPath
                  ? "bg-neutral-800 text-neutral-100"
                  : "text-neutral-400 hover:bg-neutral-900")
              }
            >
              <span className="w-3 text-neutral-600">
                {entry.type === "dir" ? "/" : entry.type === "symlink" ? "@" : "·"}
              </span>
              <span className="truncate">{entry.name}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-surface-border px-3 py-1 text-[11px] text-neutral-500">
          <span className="truncate text-neutral-300">
            {openPath ?? "Select a file"}
          </span>
          {dirty && <span className="text-amber-400">unsaved</span>}
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800 disabled:opacity-40"
          >
            {save.isPending ? "saving…" : "save"}
          </button>
          {save.isError && <span className="text-red-400">{String(save.error)}</span>}
          <button
            onClick={() => setShowTerminal((shown) => !shown)}
            className="ml-auto rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800"
          >
            {showTerminal ? "hide terminal" : "show terminal"}
          </button>
        </div>

        <div className="min-h-0 flex-1">
          {openPath ? (
            file.data?.encoding === "base64" ? (
              <Empty text="Binary file — the editor only shows text." />
            ) : (
              <Editor
                path={openPath}
                language={languageOf(openPath)}
                theme={editorTheme}
                value={draft ?? ""}
                onChange={(value) => setDraft(value ?? "")}
                onMount={(editor, monaco: typeof Monaco) => {
                  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () =>
                    save.mutate(),
                  );
                }}
                options={{
                  minimap: { enabled: false },
                  fontSize: 13,
                  scrollBeyondLastLine: false,
                  renderWhitespace: "selection",
                }}
              />
            )
          ) : (
            <Empty text="Pick a file from the tree to edit it here." />
          )}
        </div>

        {showTerminal && (
          <div className="h-64 shrink-0 border-t border-surface-border">
            <Terminal url={api.terminalUrl(project.id)} />
          </div>
        )}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-8 text-center text-sm text-neutral-500">
      {text}
    </div>
  );
}
