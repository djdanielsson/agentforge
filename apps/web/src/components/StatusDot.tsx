import clsx from "clsx";

const COLOURS: Record<string, string> = {
  // project / workspace
  ready: "bg-emerald-500",
  creating: "bg-amber-400 animate-pulse",
  pending: "bg-neutral-500",
  provisioning: "bg-amber-400 animate-pulse",
  cloning: "bg-amber-400 animate-pulse",
  stopped: "bg-neutral-500",
  deleting: "bg-neutral-500",
  archived: "bg-neutral-600",
  // agent
  starting: "bg-amber-400 animate-pulse",
  idle: "bg-sky-500",
  working: "bg-emerald-500 animate-pulse",
  blocked: "bg-amber-400",
  awaiting_approval: "bg-orange-400",
  // task
  queued: "bg-neutral-400",
  leased: "bg-sky-400",
  running: "bg-emerald-500 animate-pulse",
  succeeded: "bg-emerald-600",
  failed: "bg-red-500",
  cancelled: "bg-neutral-600",
  error: "bg-red-500",
};

export function StatusDot({ status, className }: { status: string; className?: string }) {
  return (
    <span
      title={status}
      className={clsx(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        COLOURS[status] ?? "bg-neutral-500",
        className,
      )}
    />
  );
}
