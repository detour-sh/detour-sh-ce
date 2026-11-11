import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The portal is served by app/server.py. In dev, Vite proxies the API to a running Detour.
const api = process.env.DETOUR_API || "http://127.0.0.1:3787";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Keep every asset a real file so the strict Content-Security-Policy (no data: fonts) holds.
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1500,
  },
  css: {
    preprocessorOptions: {
      scss: { quietDeps: true, silenceDeprecations: ["mixed-decls", "global-builtin", "import"] },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: api,
        changeOrigin: true,
        configure: (proxy) => proxy.on("proxyReq", (req) => req.removeHeader("origin")),
      },
    },
  },
});
