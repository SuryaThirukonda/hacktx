import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const backend = process.env.OVERWATCH_BACKEND ?? "http://127.0.0.1:8000";
const cert = process.env.OVERWATCH_HTTPS_CERT;
const key = process.env.OVERWATCH_HTTPS_KEY;
if (Boolean(cert) !== Boolean(key)) throw new Error("Set both OVERWATCH_HTTPS_CERT and OVERWATCH_HTTPS_KEY");

// Pages the app routes itself (src/main.tsx, src/lib/router.ts). A copy of index.html at each lets a static host (the
// Vercel dashboard service, which has no SPA fallback) serve deep links such as /responder or /ops/drone.
const PAGES = ["dashboard", "responder", "responder/sensors", "3d", "ops", "ops/drone", "ops/phone", "ops/map", "command",
  "drive", "demo"];

function pageCopies(): Plugin {
  let outDir = "";
  return {
    name: "overwatch-page-copies",
    apply: "build",
    configResolved(cfg) {
      outDir = resolve(cfg.root, cfg.build.outDir);
    },
    closeBundle() {
      for (const page of PAGES) {
        mkdirSync(resolve(outDir, page), { recursive: true });
        copyFileSync(resolve(outDir, "index.html"), resolve(outDir, page, "index.html"));
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), pageCopies()],
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
