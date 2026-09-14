import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Editor from "@monaco-editor/react";
import * as monaco from "monaco-editor";

import { api } from "../api/client";
import { addedLines } from "../diff";
import type { FileEntry, ProjectDetail } from "../types";
import { Divider, useDragSize } from "./Splitter";
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

function workspacePath(repoPath: string): string {
  return repoPath.startsWith("/") ? repoPath : `/workspace/${repoPath}`;
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

  // Both panes are draggable and collapsible: a file tree that is always the
  // same width is wrong for every project except the one it was sized for.
  const tree = useDragSize({ initial: 256, min: 160, max: 520, axis: "x" });
  const terminal = useDragSize({ initial: 240, min: 120, max: 640, axis: "y", invert: true });

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

  // The working-tree diff is what the editor marks: a branch diff would hide
  // everything the agent has not committed yet.
  const changes = useQuery({
    queryKey: ["diff", project.id, "worktree"],
    queryFn: () => api.gitDiff(project.id, true),
    enabled: ready,
    refetchInterval: 15000,
    retry: false,
  });

  // Seed the editor from the server whenever a different file arrives.
  useEffect(() => {
    setDraft(file.data?.encoding === "utf8" ? file.data.content : null);
  }, [file.data]);

  const save = useMutation({
    mutationFn: () => api.writeFile(project.id, openPath!, draft ?? ""),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["file", project.id, openPath] });
      queryClient.invalidateQueries({ queryKey: ["diff", project.id, "worktree"] });
    },
  });

  const dirty = draft !== null && draft !== file.data?.content;
  const entries: FileEntry[] = listing.data?.entries ?? [];
  const root = listing.data?.root ?? "/workspace";

  // repo path -> summary, for the tree badges and the open file's count.
  const changedFiles = useMemo(() => {
    const map = new Map<string, { status: string; additions: number; deletions: number }>();
    for (const change of changes.data?.files ?? []) {
      map.set(workspacePath(change.path), {
        status: change.status,
        additions: change.additions,
        deletions: change.deletions,
      });
    }
    return map;
  }, [changes.data]);

  const changedLines = useMemo(() => {
    if (!openPath || !changes.data?.diff) return [];
    return addedLines(changes.data.diff, openPath);
  }, [openPath, changes.data]);

  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const decorations = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);

  // Repaint the marks whenever the file, its text or the diff moves. Monaco
  // keeps decorations in its own layer, so they survive typing but not a
  // different model — hence the dependency on the path as well.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (!decorations.current) {
      decorations.current = editor.createDecorationsCollection([]);
    }
    decorations.current.set(
      changedLines.map((line) => ({
        range: new monaco.Range(line, 1, line, 1),
        options: {
          isWholeLine: true,
          className: "diff-line-added",
          linesDecorationsClassName: "diff-line-glyph",
        },
      })),
    );
  }, [changedLines, openPath, draft]);

  const editorTheme = useMemo(() => "vs-dark", []);
  const openChange = openPath ? changedFiles.get(openPath) : undefined;

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
    <div className="flex h-full min-h-0">
      {tree.collapsed ? (
        <button
          onClick={tree.toggle}
          title="Show files"
          className="w-6 shrink-0 border-r border-surface-border text-xs text-neutral-500 hover:bg-neutral-900"
        >
          »
        </button>
      ) : (
        <>
          <div style={{ width: tree.size }} className="flex shrink-0 flex-col border-r border-surface-border">
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
              {changedFiles.size > 0 && (
                <span className="ml-auto text-emerald-400" title="Files changed since the last commit">
                  {changedFiles.size}
                </span>
              )}
              <button
                onClick={tree.toggle}
                title="Collapse files"
                className="rounded px-1 hover:bg-neutral-800"
              >
                «
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-1">
              {listing.isLoading && <p className="px-2 py-1 text-xs text-neutral-600">Loading…</p>}
              {listing.error && (
                <p className="px-2 py-1 text-xs text-red-400">{String(listing.error)}</p>
              )}
              {listing.data && entries.length === 0 && (
                <p className="px-2 py-1 text-xs text-neutral-600">Empty directory.</p>
              )}
              {entries.map((entry) => {
                const change = changedFiles.get(entry.path);
                return (
                  <button
                    key={entry.path}
                    onClick={() =>
                      entry.type === "dir" ? setDir(entry.path) : setOpenPath(entry.path)
                    }
                    className={
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs " +
                      (entry.path === openPath
                        ? "bg-neutral-800 text-neutral-100"
                        : change
                          ? "text-neutral-200 hover:bg-neutral-900"
                          : "text-neutral-400 hover:bg-neutral-900")
                    }
                  >
                    <span className="w-3 text-neutral-600">
                      {entry.type === "dir" ? "/" : entry.type === "symlink" ? "@" : "·"}
                    </span>
                    <span className="truncate">{entry.name}</span>
                    {change && (
                      <span className="ml-auto shrink-0 text-[10px] text-emerald-400">
                        +{change.additions}
                        {change.deletions > 0 && (
                          <span className="text-red-400"> −{change.deletions}</span>
                        )}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
          <Divider axis="x" onPointerDown={tree.start} />
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-surface-border px-3 py-1 text-[11px] text-neutral-500">
          <span className="truncate text-neutral-300">{openPath ?? "Select a file"}</span>
          {dirty && <span className="text-amber-400">unsaved</span>}
          {openChange && (
            <span className="text-emerald-400" title="Lines added since the last commit">
              {changedLines.length} changed
            </span>
          )}
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800 disabled:opacity-40"
          >
            {save.isPending ? "saving…" : "save"}
          </button>
          {save.isError && <span className="text-red-400">{String(save.error)}</span>}
          <button
            onClick={terminal.toggle}
            className="ml-auto rounded border border-surface-border px-2 py-0.5 hover:bg-neutral-800"
          >
            {terminal.collapsed ? "show terminal" : "hide terminal"}
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
                onMount={(editor, monacoInstance: typeof monaco) => {
                  editorRef.current = editor as monaco.editor.IStandaloneCodeEditor;
                  editor.addCommand(monacoInstance.KeyMod.CtrlCmd | monacoInstance.KeyCode.KeyS, () =>
                    save.mutate(),
                  );
                }}
                options={{
                  minimap: { enabled: false },
                  fontSize: 13,
                  scrollBeyondLastLine: false,
                  renderWhitespace: "selection",
                  glyphMargin: true,
                }}
              />
            )
          ) : (
            <Empty text="Pick a file from the tree to edit it here." />
          )}
        </div>

        {!terminal.collapsed && (
          <>
            <Divider axis="y" onPointerDown={terminal.start} />
            <div style={{ height: terminal.size }} className="shrink-0 overflow-hidden">
              {/* Re-opening the terminal starts a fresh shell: the pty dies with
                  the socket, and a stale one would be worse than a new prompt. */}
              <Terminal url={api.terminalUrl(project.id)} />
            </div>
          </>
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
