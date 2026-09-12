import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

// A real terminal, not a <pre> that pretends: the pty lives in the workspace pod
// and the API bridges the bytes, so prompts, colours and line editing are the
// shell's own.
export function Terminal({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;

    const term = new XTerm({
      convertEol: true,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 12,
      theme: {
        background: "#0b0b0c",
        foreground: "#d4d4d4",
        cursor: "#d4d4d4",
        selectionBackground: "#3f3f46",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    fit.fit();

    const socket = new WebSocket(url);
    socket.onmessage = (event) => term.write(event.data as string);
    socket.onclose = () => term.write("\r\n[disconnected]\r\n");
    term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(data);
    });

    const onResize = () => fit.fit();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      socket.close();
      term.dispose();
    };
  }, [url]);

  return <div ref={host} className="h-full w-full overflow-hidden bg-[#0b0b0c] px-2 py-1" />;
}
