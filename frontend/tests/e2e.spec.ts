import { createHmac } from "node:crypto";
import { execSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { ADMIN_EMAIL, ADMIN_PASSWORD, apiLogin, demoWorkspace, gotoWorkspace, signIn } from "./helpers";

/**
 * End-to-end acceptance suite. Runs against the real API, PostgreSQL, worker and
 * UI — nothing is mocked. Ordering is intentional: later tests reuse the seeded
 * workspace and the data created earlier.
 */

let token = "";
let workspace: any;

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
  workspace = await demoWorkspace(request, token);
});

test("01 · login page authenticates and reaches the dashboard", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: /sign in|create the administrator/i })).toBeVisible();
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in|create admin/i }).click();
  await expect(page).toHaveURL(/\/$|\/w\//);
  await expect(page.getByText("Workspaces").first()).toBeVisible();
});

test("02 · workspace overview shows indexed architecture", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id);
  await expect(page.getByRole("heading", { name: workspace.name })).toBeVisible();
  await expect(page.getByText("SERVICES", { exact: false }).first()).toBeVisible();
  const servicesCard = page.locator("text=API ENDPOINTS").first();
  await expect(servicesCard).toBeVisible();
  await expect(page.getByText(/repos synced/).first()).toBeVisible();
});

test("03 · service inventory and service detail", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "services");
  await expect(page.getByRole("heading", { name: "Services" })).toBeVisible();
  const payment = page.getByText("payment-service", { exact: true }).first();
  await expect(payment).toBeVisible();
  const services = await (await request.get(`/api/workspaces/${workspace.id}/services`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  const target = services.find((s: any) => s.name === "payment-service");
  expect(target).toBeTruthy();
  await page.goto(`/w/${workspace.id}/services/${target.id}`);
  await expect(page.getByRole("heading", { name: "payment-service" })).toBeVisible();
  await page.getByRole("tab", { name: "APIs" }).click();
  await expect(page.getByText("/api/payments/authorize")).toBeVisible();
});

test("04 · architecture explorer renders the graph and inspector", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "explorer");
  await expect(page.getByRole("heading", { name: "Architecture Explorer" })).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByText(/\d+ nodes · \d+ edges/)).toBeVisible();
  await page.getByRole("button", { name: "KAFKA TOPIC" }).click();
  await expect(page.getByText("Inspector")).toBeVisible();
});

test("05 · source navigation opens the indexed revision", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "services");
  await page.locator('a[href*="/services/"]').filter({ hasText: "reservation-service" }).first().click();
  await page.getByRole("tab", { name: "Code" }).click();
  await page.locator("button").filter({ hasText: ".java" }).first().click();
  await expect(page.getByText(/verified at indexed revision/)).toBeVisible();
  await expect(page.locator("pre")).toContainText("class ");
});

test("06 · Kafka topology and event flow trace", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "events");
  await page.getByRole("tab", { name: /Kafka topics/ }).click();
  await expect(page.getByText("payment.completed").first()).toBeVisible();
  await page.getByRole("button", { name: /Trace event flow/ }).first().click();
  await expect(page.getByText("Producer services")).toBeVisible();
  await expect(page.getByText("Consumer services")).toBeVisible();
});

test("07 · documentation search and linked requirements", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "docs");
  await page.getByPlaceholder("Search documents…").fill("ADR-001");
  await expect(page.getByText(/ADR-001/).first()).toBeVisible();
  await page.getByText(/ADR-001/).first().click();
  await expect(page.getByText(/Linked architecture/)).toBeVisible();
});

test("08 · selective AI enrichment: estimate, generate, review, approve", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "ai");
  await expect(page.getByRole("heading", { name: "AI Intelligence Center" })).toBeVisible();
  const firstService = page.locator("div.card").filter({ hasText: "Choose targets" }).locator('input[type="checkbox"]').first();
  await firstService.check();
  await page.getByRole("button", { name: "Estimate" }).click();
  await expect(page.getByText(/input/).first()).toBeVisible();
  await page.getByRole("button", { name: "Queue enrichment" }).click();
  await expect(page.getByText(/enrichment job\(s\) queued/)).toBeVisible({ timeout: 20_000 });

  const reviewTab = page.getByRole("tab", { name: /Review queue/ });
  await reviewTab.click();
  const row = page.locator("div.divide-y button").first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();
  await expect(page.getByText(/Evidence referenced/)).toBeVisible();
  const approve = page.getByRole("button", { name: /^Approve$/ });
  if (await approve.isVisible()) {
    await approve.click();
    await expect(page.getByText(/Insight approved/)).toBeVisible();
  }
});

test("09 · AI provider configuration and usage dashboard", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "ai");
  await page.getByRole("tab", { name: "Providers" }).click();
  await expect(page.getByText(/MOCK/).first()).toBeVisible();
  await page.getByRole("tab", { name: "Usage" }).click();
  await expect(page.getByText("Policy")).toBeVisible();
});

test("10 · settings: members, API key creation and audit trail", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "settings");
  await page.getByRole("tab", { name: "API keys" }).click();
  await page.getByRole("button", { name: "Create key" }).click();
  await expect(page.getByRole("heading", { name: "API key created" })).toBeVisible();
  await expect(page.getByText("agk_").first()).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("tab", { name: "Audit" }).click();
  await expect(page.getByText("api_key.created").first()).toBeVisible();
});

test("11 · GitHub webhook configuration and signed delivery", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "integrations");
  const rotate = page.getByRole("button", { name: /Rotate secret|Create secret/ }).first();
  await rotate.click();
  const secretText = await page.locator("code").first().innerText();
  expect(secretText).toMatch(/^agk_/);

  const repos = await (await request.get(`/api/workspaces/${workspace.id}/repositories`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  const repo = repos[0];
  const payload = JSON.stringify({ ref: "refs/heads/main", after: "1".repeat(40), before: "0".repeat(40) });
  const signature = "sha256=" + createHmac("sha256", secretText).update(payload).digest("hex");
  const delivery = `e2e-${Date.now()}`;
  const response = await request.post(`/api/webhooks/github/${repo.id}`, {
    data: payload,
    headers: {
      "X-GitHub-Event": "push",
      "X-GitHub-Delivery": delivery,
      "X-Hub-Signature-256": signature,
      "content-type": "application/json",
    },
  });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.status).toBe("queued");

  await page.goto(`/w/${workspace.id}/sync`);
  await page.getByRole("tab", { name: /Webhooks/ }).click();
  await expect(page.getByText(delivery).first()).toBeVisible({ timeout: 20_000 });
});

test("12 · incremental synchronization after a real commit", async ({ page, request }) => {
  test.setTimeout(120_000);
  const hostRoot = process.env.E2E_HOST_REPO_DIR || "/tmp";
  const containerRoot = process.env.E2E_CONTAINER_REPO_DIR || hostRoot;
  const repoName = `archgraph-e2e-repo-${Date.now()}`;
  const dir = `${hostRoot}/${repoName}`;
  mkdirSync(`${dir}/src/main/java/com/e2e`, { recursive: true });
  mkdirSync(`${dir}/src/main/resources`, { recursive: true });
  writeFileSync(`${dir}/pom.xml`, `<project xmlns="http://maven.apache.org/POM/4.0.0"><groupId>com.e2e</groupId><artifactId>e2e-service</artifactId></project>`);
  writeFileSync(`${dir}/src/main/resources/application.yml`, "spring:\n  application:\n    name: e2e-service\nserver:\n  port: 9099\n");
  const controller = (path: string) => `package com.e2e;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/e2e")
public class E2eController {
    @GetMapping("${path}")
    public String hello() { return "ok"; }
}
`;
  writeFileSync(`${dir}/src/main/java/com/e2e/E2eController.java`, controller("/hello"));
  const git = (cmd: string) => execSync(cmd, { cwd: dir, stdio: "pipe" });
  git("git init -q -b main && git add -A && git -c user.email=e2e@test.dev -c user.name=e2e commit -q -m init");

  const wsResponse = await request.post("/api/workspaces", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `E2E Sync ${Date.now()}` },
  });
  const ws = await wsResponse.json();
  const repoResponse = await request.post(`/api/workspaces/${ws.id}/repositories`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "e2e-service", url: `${containerRoot}/${repoName}`, scan_now: true },
  });
  expect(repoResponse.status()).toBe(201);
  const repo = await repoResponse.json();

  const waitForScan = async (timeoutMs = 90_000, previousScanId?: string) => {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const status = await (await request.get(`/api/repositories/${repo.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })).json();
      const scan = status.last_scan;
      if (scan && scan.id !== previousScanId && ["COMPLETED", "SKIPPED", "FAILED"].includes(scan.status)) {
        if (scan.status !== "COMPLETED") throw new Error(`scan ${scan.status}: ${scan.error || ""}`);
        return status;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    throw new Error("scan did not complete");
  };
  const initial = await waitForScan();

  writeFileSync(`${dir}/src/main/java/com/e2e/E2eController.java`, controller("/hello-v2"));
  git("git add -A && git -c user.email=e2e@test.dev -c user.name=e2e commit -q -m 'rename endpoint'");
  await request.post(`/api/repositories/${repo.id}/scan`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { mode: "incremental" },
  });
  const after = await waitForScan(90_000, initial.last_scan.id);
  expect(after.last_scan.mode).toBe("incremental");

  const apis = await (await request.get(`/api/workspaces/${ws.id}/apis`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  const names = apis.map((a: any) => a.name);
  expect(names).toContain("GET /api/e2e/hello-v2");
  expect(names).not.toContain("GET /api/e2e/hello");

  await signIn(page, request);
  await gotoWorkspace(page, ws.id, "sync");
  await expect(page.getByText("incremental").first()).toBeVisible();
  await request.delete(`/api/workspaces/${ws.id}`, { headers: { Authorization: `Bearer ${token}` } });
  rmSync(dir, { recursive: true, force: true });
});

test("13 · integrations expose MCP configuration and tool catalogue", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "integrations");
  await page.getByRole("tab", { name: "MCP" }).click();
  await expect(page.getByText(/structured architecture tools/)).toBeVisible();
  await expect(page.getByText("list_services").first()).toBeVisible();
  await expect(page.getByText("trace_event_flow").first()).toBeVisible();
});

test("14 · responsive layout works on a mobile viewport", async ({ page, request }) => {
  await signIn(page, request);
  await page.setViewportSize({ width: 390, height: 844 });
  await gotoWorkspace(page, workspace.id);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("link", { name: "Services" })).toBeVisible();
  await page.getByRole("link", { name: "Services" }).click();
  await expect(page.getByRole("heading", { name: "Services" })).toBeVisible();
});

test("15 · authorization: viewer cannot mutate the workspace", async ({ page, request }) => {
  const email = `viewer-${Date.now()}@archgraph.io`;
  await request.post("/api/admin/users", {
    headers: { Authorization: `Bearer ${token}` },
    data: { email, name: "Viewer User", password: "viewer-password-1", role: "VIEWER" },
  });
  const users = await (await request.get("/api/admin/users", { headers: { Authorization: `Bearer ${token}` } })).json();
  const viewer = users.find((u: any) => u.email === email);
  const me = await (await request.get("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } })).json();
  await request.post(`/api/workspaces/${workspace.id}/members`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { user_id: viewer.id, role: "VIEWER" },
  });
  const suffix = Date.now().toString().slice(-6);
  const authzWs = await (await request.post("/api/workspaces", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `RBAC ${suffix}` },
  })).json();
  await request.post(`/api/workspaces/${authzWs.id}/members`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { user_id: viewer.id, role: "VIEWER" },
  });
  const denied = await request.post(`/api/workspaces/${authzWs.id}/repositories`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `allowed-${suffix}`, url: "/tmp", scan_now: false },
  });
  // admin is allowed; the viewer is denied below through their own token
  const viewerLogin = await request.post("/api/auth/login", { data: { email, password: "viewer-password-1" } });
  const viewerToken = (await viewerLogin.json()).access_token;
  const viewerDenied = await request.post(`/api/workspaces/${authzWs.id}/repositories`, {
    headers: { Authorization: `Bearer ${viewerToken}` },
    data: { name: `forbidden-${suffix}`, url: "/tmp", scan_now: false },
  });
  expect(viewerDenied.status()).toBe(403);
  expect(me.user.role).toBe("SYSTEM_ADMIN");
  expect(denied.status()).toBe(201);
  await request.delete(`/api/workspaces/${authzWs.id}`, { headers: { Authorization: `Bearer ${token}` } });
});
