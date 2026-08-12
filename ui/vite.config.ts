import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The demo server (ui/server.py) serves ui/dist as static assets and hosts the
// API on the same origin, so the built bundle uses relative asset paths. Under
// `npm run dev` the API is proxied to the FastAPI process instead.
// `--mode edge` builds the CloudFront variant, and it writes to a DIFFERENT
// directory on purpose. ui/dist is what ui/server.py serves on localhost, so a
// deployment build that overwrote it would silently change what you see locally --
// specifically it would remove the manual "AgentCore Runtime" backend option, which
// is exactly the option you want when developing against a deployed runtime. The two
// artefacts never share a path:
//
//   npm run build                 -> ui/dist       localhost, all backends offered
//   npm run build -- --mode edge  -> ui/dist-edge   CloudFront, manual backend gone
//
// See ui/src/lib/deployment.ts for what the flag changes and why.
export default defineConfig(({ mode }) => ({
  base: "./",
  define: {
    // A boolean LITERAL, not a string compared at runtime. The condition in
    // ui/src/lib/deployment.ts folds to `false`, so Rollup drops the whole manual
    // backend branch -- inputs, labels and handlers -- instead of shipping it
    // unreachable. Compared as a string across a module boundary it survived in the
    // bundle, which is harmless but not what "removed" should mean.
    __EDGE_BUILD__: JSON.stringify(mode === "edge"),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.DEMO_API ?? "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: mode === "edge" ? "dist-edge" : "dist",
    emptyOutDir: true,
  },
}))
