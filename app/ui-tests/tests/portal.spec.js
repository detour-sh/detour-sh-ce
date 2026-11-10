import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`test pattern renders and works in ${theme}`, async ({ page }, testInfo) => {
    await page.addInitScript((value) => localStorage.setItem("detour-theme", value), theme);
    await page.goto("/test-pattern");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByTestId("pattern-ready")).toBeVisible();
    await expect(page.locator(".pattern-panel")).toHaveCount(4);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    const background = await page.locator("body").evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(background).toBe(theme === "dark" ? "rgb(17, 24, 21)" : "rgb(245, 247, 244)");

    await page.getByRole("button", { name: "Primary" }).click();
    await expect(page.getByText("Interaction passed")).toBeVisible();
    await page.getByRole("button", { name: "Test dialog" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.screenshot({ path: `/artifacts/${testInfo.project.name}-${theme}.png`, fullPage: true });
  });
}

test("theme choice persists and primary pages remain visible", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("Settings color scheme").selectOption("dark");
  await page.getByLabel("Interface accent color").selectOption("blue");
  await page.getByLabel("Interface text size").selectOption("large");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-accent", "blue");
  await expect(page.locator("html")).toHaveAttribute("data-text-size", "large");
  await expect(page.locator("body")).toHaveCSS("font-size", "16px");
  for (const path of ["/", "/artifacts", "/bundles", "/connections", "/settings"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} must not overflow horizontally`).toBeLessThanOrEqual(1);
  }
});

test("profile opens settings and desktop menu collapses", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Local user profile" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
  const menuButton = page.locator("[data-toggle-navigation]");
  if (await menuButton.isVisible()) {
    await expect(menuButton).toHaveAttribute("aria-label", "Collapse navigation");
    await menuButton.click();
    await expect(page.locator("body")).toHaveClass(/navigation-collapsed/);
    await expect(menuButton).toHaveAttribute("aria-expanded", "false");
    await expect(menuButton).toHaveAttribute("aria-label", "Expand navigation");
  }
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Workspace environment" })).toBeVisible();
  await expect(page.getByPlaceholder("NEVER_DELETE")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Global tag sets" })).toBeVisible();
  await expect(page.getByPlaceholder("Deployment stage")).toBeVisible();
});

test("Detour add menu remains operable", async ({ page }) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Add Detour" });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.getByRole("menuitem", { name: /Create Detour/ })).toBeVisible();
  await page.getByRole("menuitem", { name: /Create Detour/ }).click();
  await expect(page.getByRole("dialog", { name: "Create Detour" })).toBeVisible();
});
