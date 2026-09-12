import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard talks to the control plane; in dev we proxy so there is no CORS
// dance and WebSockets Just Work.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forwarded unchanged: the API mounts everything under /api/v1, so
      // stripping the prefix here would turn a valid path into a 404.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
