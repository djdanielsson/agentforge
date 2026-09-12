import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard talks to the control plane; in dev we proxy so there is no CORS
// dance and WebSockets Just Work.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        ws: true,
      },
    },
  },
});
