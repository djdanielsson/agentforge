// Subscribes to a project's WebSocket event stream and keeps a bounded buffer.

import { useEffect, useRef, useState } from "react";
import type { Event } from "../types";

const MAX_EVENTS = 500;

export function useEventStream(url: string | null) {
  const [events, setEvents] = useState<Event[]>([]);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!url) return;
    setEvents([]);

    let closed = false;
    let retry: number | undefined;

    const connect = () => {
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
      socket.onerror = () => socket.close();
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as Event;
          setEvents((previous) => {
            const next = [...previous, event];
            return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
          });
        } catch {
          // ignore malformed frames rather than tearing down the stream
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socketRef.current?.close();
    };
  }, [url]);

  return { events, connected };
}
