import { APIRequestContext, Page, expect } from "@playwright/test";

export const ADMIN_EMAIL = process.env.DEMO_ADMIN_EMAIL || "admin@archgraph.io";
export const ADMIN_PASSWORD = process.env.DEMO_ADMIN_PASSWORD || "archgraph-demo-2026";

export async function apiLogin(request: APIRequestContext): Promise<string> {
  const response = await request.post("/api/auth/login", {
    data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  expect(response.ok(), "demo admin must exist (run `make seed`)").toBeTruthy();
  const body = await response.json();
  return body.access_token as string;
}

export async function signIn(page: Page, request: APIRequestContext): Promise<string> {
  const token = await apiLogin(request);
  await page.addInitScript((value) => localStorage.setItem("archgraph.token", value), token);
  return token;
}

export async function demoWorkspace(request: APIRequestContext, token: string): Promise<any> {
  const response = await request.get("/api/workspaces", {
    headers: { Authorization: `Bearer ${token}` },
  });
  const list = await response.json();
  const workspace = list.find((w: any) => w.slug === "hotel-platform") || list[0];
  expect(workspace, "a seeded workspace is required").toBeTruthy();
  return workspace;
}

export async function gotoWorkspace(page: Page, workspaceId: string, path = "") {
  await page.goto(`/w/${workspaceId}${path ? `/${path}` : ""}`);
  await expect(page.getByRole("link", { name: "Overview" }).first()).toBeVisible();
}
