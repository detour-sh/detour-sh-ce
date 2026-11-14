import { defineConfig, devices } from "@playwright/test";

// In Docker the results go to /artifacts. Locally, set DETOUR_ARTIFACTS and, to skip the browser download, DETOUR_BROWSER_CHANNEL=chrome.
// DETOUR_PORTAL_TOKEN must be the token the portal was started with.
const artifacts = process.env.DETOUR_ARTIFACTS || "/artifacts";
const channel = process.env.DETOUR_BROWSER_CHANNEL;

export default defineConfig({
  testDir: "./tests",
  outputDir: `${artifacts}/results`,
  reporter: [["list"], ["html", { outputFolder: `${artifacts}/report`, open: "never" }]],
  use: {
    baseURL: process.env.DETOUR_BASE_URL || "http://localhost:3787",
    trace: "retain-on-failure",
  },
  projects: [
    // Signs in once. The other projects start from its saved session cookie.
    { name: "setup", testMatch: /auth\.setup\.js/ },
    {
      name: "desktop",
      testIgnore: /auth\.setup\.js/,
      dependencies: ["setup"],
      use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}), storageState: `${artifacts}/.auth/state.json` },
    },
    {
      name: "mobile",
      testIgnore: /auth\.setup\.js/,
      dependencies: ["setup"],
      use: { ...devices["Pixel 5"], ...(channel ? { channel } : {}), storageState: `${artifacts}/.auth/state.json` },
    },
  ],
});
