import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync } from "node:fs";

const backend = process.env.OVERWATCH_BACKEND ?? "http://127.0.0.1:8000";
const cert = process.env.OVERWATCH_HTTPS_CERT;
const key = process.env.OVERWATCH_HTTPS_KEY;
if (Boolean(cert) !== Boolean(key)) throw new Error("Set both OVERWATCH_HTTPS_CERT and OVERWATCH_HTTPS_KEY");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5188,
    https: cert && key ? { cert: readFileSync(cert), key: readFileSync(key) } : undefined,
    proxy: {
      "/api": backend,
      "/streams": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
