import { defineConfig, devices } from "@playwright/test";

// In Docker the results go to /artifacts. Locally, set DETOUR_ARTIFACTS and, to skip the browser download, DETOUR_BROWSER_CHANNEL=chrome.
const artifacts = process.env.DETOUR_ARTIFACTS || "/artifacts";
const channel = process.env.DETOUR_BROWSER_CHANNEL;

export default defineConfig({
  testDir: "./tests",
  outputDir: `${artifacts}/results`,
  reporter: [["list"], ["html", { outputFolder: `${artifacts}/report`, open: "never" }]],
  use: {
    baseURL: process.env.DETOUR_BASE_URL || "http://detour:3787",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) } },
    { name: "mobile", use: { ...devices["Pixel 5"], ...(channel ? { channel } : {}) } },
  ],
});
