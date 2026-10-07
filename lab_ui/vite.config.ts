import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

// Built into PaperLab's static folder: the page is served at /lab by app/core/api_lab.py, the assets at /static/lab/.
// `npm run dev` proxies the read-only public API to Railway (override with PAPERLAB_API_URL).
const API = process.env["PAPERLAB_API_URL"] ?? "https://paperlab-production-919c.up.railway.app";

export default defineConfig(({ command }) => ({
  // Dev serves from "/" so http://localhost:5174/lab works with the same router basepath as production.
  base: command === "build" ? "/static/lab/" : "/",
  plugins: [tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
      routesDirectory: path.resolve(__dirname, "src/routes"),
      generatedRouteTree: path.resolve(__dirname, "src/routeTree.gen.ts"),
    }), react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: { outDir: "../app/dashboard/lab", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5174,
    proxy: { "/api/public": { target: API, changeOrigin: true }, "/public/health": { target: API, changeOrigin: true } },
  },
}));
