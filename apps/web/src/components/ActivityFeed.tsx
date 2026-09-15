import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ActivityItem } from "../types";

/** What an agent is doing, between the turns the transcript holds.
 *
 * Our own tables store what was said; the commands, edits and states only exist
 * in the agent server's event stream. Without this, a running task showed the
 * user nothing at all until it finished.
 */
export function ActivityFeed({ agentId }: { agentId: string }) {
  const [expanded, setExpanded] = useState(true);
  const scroller = useRef<HTMLDivElement | null>(null);

  const activity = useQuery({
    queryKey: ["activity", agentId],
    queryFn: () => api.activity(agentId),
    // Polled: the point is to watch a task work, not to read it afterwards.
    refetchInterval: 4000,
  });

  const items = activity.data?.activity ?? [];

  useEffect(() => {
    const element = scroller.current;
    // Follow the tail, but only when it is already at the tail: someone reading
    // back through the output should not be yanked forward every few seconds.
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 60;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }, [items.length]);

  return (
    <div className="border-t border-surface-border pt-2">
      <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-neutral-500">
        <span>activity</span>
        <span className="tabular-nums">{items.length}</span>
        <button
          onClick={() => setExpanded((value) => !value)}
          className="ml-auto rounded border border-surface-border px-1.5 py-0.5 text-[10px] normal-case hover:bg-neutral-800"
        >
          {expanded ? "hide" : "show"}
        </button>
      </div>

      {expanded && activity.data?.unavailable && (
        <p className="text-[11px] text-neutral-600">
          agent server unavailable: {activity.data.unavailable}
        </p>
      )}

      {expanded && (
        <div ref={scroller} className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {items.length === 0 && (
            <p className="text-[11px] text-neutral-600">
              Nothing yet — the agent has not done anything.
            </p>
          )}
          {items.map((item, index) => (
            <ActivityLine key={item.id ?? index} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function ActivityLine({ item }: { item: ActivityItem }) {
  const label = (
    <span className="mr-1 text-[10px] uppercase tracking-wide text-neutral-600">{item.kind}</span>
  );

  // Command output is the bulk of the stream and the least read, so it stays
  // collapsed until asked for. Everything else is one line.
  if (item.kind === "output") {
    return (
      <details className="text-[11px]">
        <summary
          className={
            item.ok === false
              ? "cursor-pointer font-mono text-red-400"
              : "cursor-pointer font-mono text-neutral-500"
          }
        >
          {label}
          {item.title}
        </summary>
        <pre className="mt-1 whitespace-pre-wrap rounded bg-neutral-900 p-2 font-mono text-[11px] text-neutral-400">
          {item.detail}
        </pre>
      </details>
    );
  }

  if (item.kind === "command") {
    return (
      <p className="font-mono text-[11px] text-emerald-300/80">
        {label}
        {item.title}
      </p>
    );
  }

  if (item.kind === "error") {
    return (
      <p className="text-[11px] text-red-400">
        {label}
        {item.title}
        {item.detail && <span className="text-red-300/70"> — {item.detail}</span>}
      </p>
    );
  }

  if (item.kind === "message" || item.kind === "thought" || item.kind === "finished") {
    return (
      <p className="whitespace-pre-wrap text-[11px] text-neutral-300">
        {label}
        {item.detail ?? item.title}
      </p>
    );
  }

  return (
    <p className="text-[11px] text-neutral-500">
      {label}
      {item.title ?? item.detail}
    </p>
  );
}
