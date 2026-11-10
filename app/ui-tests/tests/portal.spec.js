import { expect, test } from "@playwright/test";

// These tests run against the real container: the portal on 3787 and MCP on 3788.
const MCP_URL = process.env.DETOUR_MCP_URL || "http://localhost:3788/mcp";
const ARTIFACTS = process.env.DETOUR_ARTIFACTS || "/artifacts";
const unique = (prefix) => `${prefix} ${Math.random().toString(36).slice(2, 8)}`;

async function api(request, method, path, data) {
  const response = await request.fetch(path, { method, data, headers: data ? { "Content-Type": "application/json" } : {} });
  expect(response.ok(), `${method} ${path} -> ${response.status()}`).toBeTruthy();
  return response.json();
}

/** Call an MCP tool the way an agent would: JSON-RPC over Streamable HTTP with a Bearer key. */
async function mcp(request, token, name, args = {}) {
  const response = await request.post(MCP_URL, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json, text/event-stream", "Content-Type": "application/json" },
    data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
  });
  expect(response.ok(), `MCP ${name} -> ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.error, JSON.stringify(body.error)).toBeUndefined();
  expect(body.result.isError, JSON.stringify(body.result.content)).toBeFalsy();
  const structured = body.result.structuredContent;
  if (structured) return "result" in structured && Object.keys(structured).length === 1 ? structured.result : structured;
  return JSON.parse(body.result.content[0].text);
}

async function setup(request, { policy = "ask" } = {}) {
  const name = unique("route");
  const folder = `/tmp/${name.replace(" ", "-")}`;
  const { route } = await api(request, "POST", "/api/routes", { name, policy, targets: [{ kind: "folder", value: folder }] });
  const { token } = await api(request, "POST", "/api/keys", { name: unique("agent"), can_propose: true });
  return { route, token, folder };
}

function watchConsole(page) {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

test("an agent's proposal is approved in Review, undone, and approved again", async ({ page, request }) => {
  const { route, token, folder } = await setup(request);
  const title = unique("Sign every release");
  const proposal = await mcp(request, token, "propose_change", { action: "add", reason: "Asked twice this week", route: route.name, kind: "rule", title, body: "Use cosign." });
  expect(proposal.outcome).toBe("waiting_for_review");

  await page.goto("/");
  const card = page.locator("article", { hasText: title });
  await expect(card.getByText("Asked twice this week")).toBeVisible();
  await card.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(card.getByText("Approved", { exact: true })).toBeVisible();

  const context = await mcp(request, token, "get_context", { folder });
  expect(context.layers.flatMap((layer) => layer.items.map((item) => item.title))).toContain(title);

  await card.getByRole("button", { name: "Undo" }).click();
  await expect(card.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  expect((await mcp(request, token, "get_proposal", { proposal_id: proposal.id })).status).toBe("pending");

  await card.getByRole("button", { name: "Edit, then approve" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title").fill(`${title} (edited)`);
  await dialog.getByRole("button", { name: "Approve" }).click();
  await expect(card.getByText("Approved", { exact: true })).toBeVisible();

  await page.goto(`/routes/${route.id}`);
  await expect(page.getByRole("heading", { level: 1, name: route.name })).toBeVisible();
  await expect(page.getByText(`${title} (edited)`, { exact: true })).toBeVisible();
});

test("a rejection and its note reach the agent", async ({ page, request }) => {
  const { route, token } = await setup(request);
  const title = unique("Expose the port");
  const proposal = await mcp(request, token, "propose_change", { action: "add", reason: "Remote agents need it", route: route.id, title });
  await page.goto("/");
  const card = page.locator("article", { hasText: title });
  await card.getByRole("button", { name: "Reject" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Note for the agent (optional)").fill("Keep Detour local");
  await dialog.getByRole("button", { name: "Reject" }).click();
  await expect(card.getByText("Keep Detour local")).toBeVisible();
  const status = await mcp(request, token, "get_proposal", { proposal_id: proposal.id });
  expect(status).toMatchObject({ status: "rejected", decision_note: "Keep Detour local" });
  const again = await mcp(request, token, "propose_change", { action: "add", reason: "Still need it", route: route.id, title });
  expect(again.outcome).toBe("previously_rejected");
});

test("notes apply on their own when the route allows it", async ({ page, request }) => {
  const { route, token } = await setup(request, { policy: "notes" });
  const title = unique("WSL 2 is required");
  const result = await mcp(request, token, "propose_change", { action: "add", reason: "Setup failed without it", route: route.id, title });
  expect(result.outcome).toBe("applied");
  await page.goto(`/routes/${route.id}`);
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByText(`Added note “${title}”`)).toBeVisible();
});

test("a route is created and managed by hand", async ({ page }) => {
  const name = unique("Home lab");
  await page.goto("/routes");
  await page.getByRole("button", { name: "New route" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Create route" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();

  await page.getByRole("button", { name: "Add context" }).first().click();
  const itemDialog = page.getByRole("dialog");
  await itemDialog.getByLabel("Title").fill("Back up the NAS weekly");
  await itemDialog.getByRole("textbox", { name: "Text" }).fill("Every Sunday.");
  await itemDialog.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Back up the NAS weekly", { exact: true }).first()).toBeVisible();

  await page.getByRole("tab", { name: "Applies to" }).click();
  await page.getByLabel("Value").fill("~/homelab");
  await page.getByRole("button", { name: "Add target" }).click();
  await expect(page.getByText("~/homelab", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Agent access" }).click();
  await page.getByText("Read only", { exact: true }).click();
  await page.getByRole("button", { name: "Pause agent access" }).click();
  await expect(page.getByText("Agent access paused")).toBeVisible();

  await page.goto("/routes");
  await expect(page.getByRole("link", { name })).toBeVisible();
});

for (const theme of ["light", "dark"]) {
  test(`every page renders cleanly in ${theme}`, async ({ page, request }, testInfo) => {
    const { route } = await setup(request);
    const errors = watchConsole(page);
    await page.addInitScript((value) => localStorage.setItem("detour-theme", value), theme);
    for (const [label, path] of [["review", "/"], ["routes", "/routes"], ["route", `/routes/${route.id}`], ["activity", "/activity"], ["connections", "/connections"], ["settings", "/settings"]]) {
      await page.goto(path);
      await expect(page.locator("main h1")).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${path} must not scroll sideways`).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `${ARTIFACTS}/${testInfo.project.name}-${theme}-${label}.png`, fullPage: true });
    }
    expect(errors, "console errors, including Content-Security-Policy violations").toEqual([]);
  });
}

test("the theme choice persists", async ({ page }) => {
  await page.goto("/settings");
  await page.getByText("Dark", { exact: true }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/cds--g100/);
  await page.getByText("Light", { exact: true }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/cds--g10(\s|$)/);
});

test("a new key is shown once and can be revoked", async ({ page }) => {
  const name = unique("Cursor");
  await page.goto("/connections");
  await page.getByLabel("Key name").fill(name);
  await page.getByRole("button", { name: "Create key" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("It won't be shown again.")).toBeVisible();
  await expect(dialog.getByText(/dtr_/).first()).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  const row = page.locator("li", { hasText: name });
  await row.getByRole("button", { name: "Revoke" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Revoke key" }).click();
  await expect(row.getByText("Revoked")).toBeVisible();
});
