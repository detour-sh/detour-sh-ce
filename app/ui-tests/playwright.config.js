import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "/artifacts/results",
  reporter: [["list"], ["html", { outputFolder: "/artifacts/report", open: "never" }]],
  use: {
    baseURL: process.env.DETOUR_BASE_URL || "http://detour:3787",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 5"] } },
  ],
});
