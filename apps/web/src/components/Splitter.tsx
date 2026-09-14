import { useCallback, useRef, useState } from "react";

// Panes you can drag and collapse, without a dependency for it.
//
// The size is a plain number of pixels kept in state; a drag listens on the
// window rather than the divider so the pointer can leave the handle, which is
// the difference between a pane that resizes and one that loses the drag the
// moment you move faster than the element.

export type Axis = "x" | "y";

export function useDragSize({
  initial,
  min,
  max,
  axis,
  invert = false,
}: {
  initial: number;
  min: number;
  max: number;
  axis: Axis;
  /** True when the pane sits after the divider, so dragging right shrinks it. */
  invert?: boolean;
}) {
  const [size, setSize] = useState(initial);
  const [collapsed, setCollapsed] = useState(false);
  const lastExpanded = useRef(initial);

  const start = useCallback(
    (event: React.PointerEvent) => {
      event.preventDefault();
      const origin = axis === "x" ? event.clientX : event.clientY;
      const startSize = size;

      const move = (moveEvent: PointerEvent) => {
        const delta =
          (axis === "x" ? moveEvent.clientX : moveEvent.clientY) - origin;
        const next = invert ? startSize - delta : startSize + delta;
        setSize(Math.min(max, Math.max(min, next)));
        setCollapsed(false);
      };
      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
        document.body.style.userSelect = "";
      };

      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
    },
    [axis, invert, max, min, size],
  );

  const toggle = useCallback(() => {
    setCollapsed((was) => {
      if (!was) lastExpanded.current = size;
      else setSize(lastExpanded.current);
      return !was;
    });
  }, [size]);

  return { size, collapsed, start, toggle };
}

export function Divider({
  axis,
  onPointerDown,
  className = "",
}: {
  axis: Axis;
  onPointerDown: (event: React.PointerEvent) => void;
  className?: string;
}) {
  const base =
    axis === "x"
      ? "w-1 cursor-col-resize hover:bg-neutral-700"
      : "h-1 cursor-row-resize hover:bg-neutral-700";
  return (
    <div
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      onPointerDown={onPointerDown}
      className={`shrink-0 bg-surface-border transition-colors ${base} ${className}`}
    />
  );
}
