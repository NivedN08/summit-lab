import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Python server (python3 server.py) answers /api; without it the app replays
// the runs baked into public/runs and answers questions offline.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8790", changeOrigin: false } },
  },
});
