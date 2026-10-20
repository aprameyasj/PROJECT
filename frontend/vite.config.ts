import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.JARVIS_API_URL ?? "http://127.0.0.1:8000";
const WS_BACKEND = BACKEND.replace(/^http/, "ws");

/**
 * The React dashboard is *only* a client. Every request it makes is relative
 * (`/api/...`, `/ws`) and proxied to the Python agent, so the browser never
 * needs to know where JARVIS runs — which is also what makes the sandboxed
 * live preview work without CORS juggling.
 */
const proxy = {
  "/api": { target: BACKEND, changeOrigin: true },
  "/ws": { target: WS_BACKEND, ws: true, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: Number(process.env.PORT ?? 5173),
    strictPort: false,
    allowedHosts: true,
    proxy,
  },
  preview: {
    host: "0.0.0.0",
    port: Number(process.env.PORT ?? 4173),
    allowedHosts: true,
    proxy,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
