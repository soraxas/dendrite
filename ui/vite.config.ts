/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built assets ship inside the Python package (see hatch_build.py); `dendrite ui` serves them.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../src/dendrite/ui/dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  server: { proxy: { "/api": process.env.DENDRITE_API ?? "http://127.0.0.1:8454" } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
