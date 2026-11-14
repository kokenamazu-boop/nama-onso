import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri の devUrl と合わせる（Nama Hub と同じ 1420）。
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**", "**/sidecar/**"],
    },
  },
  build: {
    target: "chrome105",
    minify: "esbuild",
    sourcemap: false,
  },
});
