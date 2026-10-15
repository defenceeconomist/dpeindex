import {defineConfig} from "@playwright/test";
const port = process.env.DPE_PREVIEW_PORT || "8790";
const directory = process.env.DPE_PREVIEW_DIR;
export default defineConfig({
  testDir: "tests/browser", timeout: 120_000, workers: 1, fullyParallel: false,
  use: {baseURL: process.env.DPE_PUBLIC_URL || `http://127.0.0.1:${port}`, headless: true},
  projects: [{name: "chromium", use: {browserName: "chromium"}}, {name: "firefox", use: {browserName: "firefox"}}, {name: "webkit", use: {browserName: "webkit"}}],
  webServer: process.env.DPE_PUBLIC_URL ? undefined : {command: `python3 scripts/preview_static.py --port ${port}${directory ? ` --directory ${JSON.stringify(directory)}` : ""}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: !directory},
});
