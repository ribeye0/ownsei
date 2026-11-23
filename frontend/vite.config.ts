import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Release tags and packages use the version in pyproject.toml.
const metadata = readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8");
const project = metadata.split(/^\[project\]\s*$/m)[1]?.split(/^\[/m)[0];
const version = project?.match(/^version\s*=\s*"([^"]+)"\s*$/m)?.[1];
if (!version) throw new Error("Missing project version in pyproject.toml");

export default defineConfig({
  define: { __OWNSEI_VERSION__: JSON.stringify(version) },
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8878" } },
});
