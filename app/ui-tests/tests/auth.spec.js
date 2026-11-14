import { expect, test } from "@playwright/test";

// Nothing here has a session. These tests check that the portal and MCP endpoint keep strangers out.
test.use({ storageState: { cookies: [], origins: [] } });

const MCP_URL = process.env.DETOUR_MCP_URL || "http://localhost:3788/mcp";
const TOKEN = process.env.DETOUR_PORTAL_TOKEN;
const LIST_TOOLS = { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} };
const MCP_HEADERS = { Accept: "application/json, text/event-stream", "Content-Type": "application/json" };

test("the API refuses a visitor who hasn't signed in", async ({ request }) => {
  for (const path of ["/api/routes", "/api/keys", "/api/export.json", "/api/events", "/api/proposals"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(401);
  }
  const create = await request.post("/api/keys", { data: { name: "intruder" } });
  expect(create.status()).toBe(401);
  const session = await (await request.get("/api/session")).json();
  expect(session).toEqual({ signed_in: false, cloud: false });
});

test("the MCP endpoint refuses calls without a valid key", async ({ request }) => {
  expect((await request.post(MCP_URL, { headers: MCP_HEADERS, data: LIST_TOOLS })).status()).toBe(401);
  const wrong = await request.post(MCP_URL, { headers: { ...MCP_HEADERS, Authorization: "Bearer dtr_not-a-real-key" }, data: LIST_TOOLS });
  expect(wrong.status()).toBe(401);
});

test("pages carry the security headers", async ({ request }) => {
  const headers = (await request.get("/")).headers();
  expect(headers["content-security-policy"]).toContain("default-src 'self'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["strict-transport-security"]).toBeUndefined(); // local mode is plain HTTP on this computer
});

test("a visitor signs in with the token, and signs out again", async ({ page, context }) => {
  expect(TOKEN, "Set DETOUR_PORTAL_TOKEN").toBeTruthy();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to Detour" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review" })).toHaveCount(0);

  await page.getByLabel("Sign-in token").fill("not-the-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("that token isn't right")).toBeVisible();

  await page.getByLabel("Sign-in token").fill(TOKEN);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("link", { name: "Review" }).first()).toBeVisible();

  const cookie = (await context.cookies()).find((item) => item.name === "detour_session");
  expect(cookie, "session cookie").toBeTruthy();
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe("Strict");

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Security" })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).last().click();
  await expect(page.getByRole("heading", { name: "Sign in to Detour" })).toBeVisible();
  expect((await page.request.get("/api/routes")).status()).toBe(401);
});
