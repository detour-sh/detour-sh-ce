import { expect, test as setup } from "@playwright/test";

// Signs in once and saves the session cookie, so the other tests start signed in.
// The token is the one the container was started with (DETOUR_PORTAL_TOKEN).
export const STATE = `${process.env.DETOUR_ARTIFACTS || "/artifacts"}/.auth/state.json`;

setup("sign in", async ({ request }) => {
  const token = process.env.DETOUR_PORTAL_TOKEN;
  expect(token, "Set DETOUR_PORTAL_TOKEN to the token the container was started with").toBeTruthy();
  const response = await request.post("/api/signin", { data: { token } });
  expect(response.ok(), `sign-in -> ${response.status()}`).toBeTruthy();
  await request.storageState({ path: STATE });
});
