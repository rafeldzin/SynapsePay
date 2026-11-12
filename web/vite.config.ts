import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Semua panggilan frontend ke /api/* diteruskan apa adanya ke backend FastAPI. Route backend
// sendiri berawalan /api (PRD 12), jadi path tidak ditulis ulang. Backend tidak perlu CORS.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
});
