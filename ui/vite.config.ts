/// <reference types="vitest/config" />

import { fileViewerRenderers } from "@file-viewer/vite-plugin";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built assets ship inside the Python package (see hatch_build.py); `dendrite ui` serves them.
export default defineConfig(({ mode }) => ({
  // The full preset is imported by the lazy FileViewer component. Automatic injection would
  // load every renderer on application startup, including when no artifact is selected.
  plugins: [
    react(),
    ...(mode === "test" ? [] : [fileViewerRenderers({ copyAssets: true, inject: false, chunkStrategy: "none" })]),
  ],
  base: "./",
  build: { outDir: "../src/dendrite/ui/dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  server: { proxy: { "/api": process.env.DENDRITE_API ?? "http://127.0.0.1:8454" } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
}));
