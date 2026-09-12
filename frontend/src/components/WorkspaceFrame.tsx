import type { ProjectDetail } from "../types";

// MVP #1 embeds code-server. Monaco replaces this in MVP #3 — the surrounding
// layout is deliberately the same shape so the swap is invisible to users.
export function WorkspaceFrame({ project }: { project: ProjectDetail }) {
  const workspace = project.workspace;

  if (!workspace) {
    return <Empty text="No workspace record for this project." />;
  }
  if (workspace.status !== "ready") {
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
  if (!workspace.code_server_url) {
    return <Empty text="Workspace is ready but has no code-server URL." />;
  }

  return (
    <iframe
      className="pane-fill"
      title="workspace"
      src={workspace.code_server_url}
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
    />
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-8 text-center text-sm text-neutral-500">
      {text}
    </div>
  );
}
