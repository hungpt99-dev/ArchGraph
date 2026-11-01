import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { ADMIN_EMAIL, ADMIN_PASSWORD, apiLogin, demoWorkspace, gotoWorkspace, signIn } from "./helpers";

/**
 * Evidence capture. Every screenshot is taken from the running application
 * (real API + PostgreSQL + worker) with Playwright — never mocked up.
 */

const EVIDENCE = resolve(process.cwd(), "../artifacts/evidence");
const dir = (name: string) => {
  const path = resolve(EVIDENCE, name);
  mkdirSync(path, { recursive: true });
  return path;
};

let token = "";
let workspace: any;
let serviceId = "";
let apiNodeId = "";
let documentId = "";

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
  workspace = await demoWorkspace(request, token);
  const services = await (await request.get(`/api/workspaces/${workspace.id}/services`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  serviceId = (services.find((s: any) => s.name === "payment-service") || services[0]).id;
  const apis = await (await request.get(`/api/workspaces/${workspace.id}/apis`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  apiNodeId = apis[0].id;
  const docs = await (await request.get(`/api/workspaces/${workspace.id}/documents?source_type=jira`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  documentId = docs[0]?.id || "";
});

test("capture login and dashboard", async ({ page, request }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
  await page.screenshot({ path: `${dir("01-dashboard")}/login.png`, fullPage: true });

  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Workspaces" })).toBeVisible();
  await page.screenshot({ path: `${dir("01-dashboard")}/workspaces.png`, fullPage: true });

  await gotoWorkspace(page, workspace.id);
  await expect(page.getByRole("heading", { name: workspace.name })).toBeVisible();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${dir("01-dashboard")}/workspace-overview.png`, fullPage: true });
});

test("capture services and repositories", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "services");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("02-workspaces")}/services-list.png`, fullPage: true });

  await page.goto(`/w/${workspace.id}/services/${serviceId}`);
  await expect(page.getByRole("heading", { name: "payment-service" })).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("02-workspaces")}/service-detail.png`, fullPage: true });

  await page.goto(`/w/${workspace.id}/sync`);
  await expect(page.getByRole("heading", { name: "Synchronization" })).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("03-repositories")}/repositories.png`, fullPage: true });
  await page.getByRole("tab", { name: /Scan runs/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("03-repositories")}/scan-history.png`, fullPage: true });
});

test("capture scanner evidence and graph", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "explorer");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${dir("04-scanner")}/graph-system-view.png`, fullPage: true });

  await page.goto(`/w/${workspace.id}/explorer?node=${serviceId}`);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${dir("05-architecture-graph")}/graph-service-neighbourhood.png`, fullPage: true });
});

test("capture source navigation", async ({ page, request }) => {
  await signIn(page, request);
  await page.goto(`/w/${workspace.id}/services/${serviceId}`);
  await page.getByRole("tab", { name: "Code" }).click();
  await page.getByRole("button", { name: /PaymentService/ }).first().click();
  await expect(page.getByText(/verified at indexed revision/)).toBeVisible();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("06-source-navigation")}/source-viewer.png`, fullPage: true });
  await page.keyboard.press("Escape");
});

test("capture Kafka analysis", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "events");
  await page.getByRole("tab", { name: /Kafka topics/ }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${dir("07-kafka-analysis")}/topics.png`, fullPage: true });
  await page.getByRole("button", { name: /Trace event flow/ }).first().click();
  await expect(page.getByText("Producer services")).toBeVisible();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${dir("07-kafka-analysis")}/event-trace.png`, fullPage: true });
  await page.keyboard.press("Escape");

  await gotoWorkspace(page, workspace.id, "flows");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${dir("07-kafka-analysis")}/business-flows.png`, fullPage: true });
});

test("capture API analysis", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "events");
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${dir("08-api-analysis")}/rest-apis.png`, fullPage: true });
  await page.getByRole("tab", { name: /Data stores/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("08-api-analysis")}/tables.png`, fullPage: true });
  await page.getByRole("tab", { name: /Unresolved/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("08-api-analysis")}/unresolved.png`, fullPage: true });
});

test("capture documentation and Jira fixtures", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "docs");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("09-documentation")}/documents.png`, fullPage: true });

  if (documentId) {
    await page.goto(`/w/${workspace.id}/docs?doc=${documentId}`);
    await page.getByPlaceholder("Search documents…").fill("HOTEL-142");
    await page.waitForTimeout(600);
    await page.getByText(/HOTEL-142/).first().click();
    await expect(page.getByText(/Linked architecture/)).toBeVisible();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${dir("10-jira")}/requirement-linked.png`, fullPage: true });
  }

  await page.goto(`/w/${workspace.id}/integrations`);
  await page.getByRole("tab", { name: "Jira & Confluence" }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${dir("11-confluence")}/documentation-integrations.png`, fullPage: true });
});

test("capture AI enrichment and approval", async ({ page, request }) => {
  await signIn(page, request);
  await gotoWorkspace(page, workspace.id, "ai");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("12-ai-enrichment")}/ai-center.png`, fullPage: true });

  const checkbox = page.locator("div.card").filter({ hasText: "Choose targets" }).locator('input[type="checkbox"]').first();
  await checkbox.check();
  await page.getByRole("button", { name: "Estimate" }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${dir("12-ai-enrichment")}/estimate.png`, fullPage: true });

  await page.getByRole("tab", { name: /Review queue/ }).click();
  await page.waitForTimeout(600);
  const row = page.locator("div.divide-y button").first();
  if (await row.isVisible()) {
    await row.click();
    await expect(page.getByText(/Evidence referenced/)).toBeVisible();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${dir("13-ai-approval")}/review-modal.png`, fullPage: true });
  }
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: /Review queue/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("13-ai-approval")}/review-queue.png`, fullPage: true });
});

test("capture webhooks and incremental sync", async ({ page, request }) => {
  await signIn(page, request);
  await page.goto(`/w/${workspace.id}/integrations`);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${dir("14-git-webhooks")}/webhook-configuration.png`, fullPage: true });

  await page.getByRole("button", { name: /Rotate secret|Create secret/ }).first().click();
  await expect(page.getByRole("heading", { name: /Webhook secret/ })).toBeVisible();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${dir("14-git-webhooks")}/webhook-secret.png`, fullPage: true });
  await page.keyboard.press("Escape");

  await page.goto(`/w/${workspace.id}/sync`);
  await page.getByRole("tab", { name: /Webhooks/ }).click();
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${dir("15-incremental-sync")}/webhook-deliveries.png`, fullPage: true });
  await page.getByRole("tab", { name: /Scan runs/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("15-incremental-sync")}/incremental-scans.png`, fullPage: true });
});

test("capture MCP integration", async ({ page, request }) => {
  await signIn(page, request);
  await page.goto(`/w/${workspace.id}/integrations`);
  await page.getByRole("tab", { name: "MCP" }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${dir("16-mcp")}/mcp-tools.png`, fullPage: true });
});

test("capture security and settings", async ({ page, request }) => {
  await signIn(page, request);
  await page.goto(`/w/${workspace.id}/settings`);
  await page.getByRole("tab", { name: "Access" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("17-security")}/roles.png`, fullPage: true });
  await page.getByRole("tab", { name: "Audit" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${dir("17-security")}/audit-log.png`, fullPage: true });
  await page.getByRole("tab", { name: "API keys" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${dir("17-security")}/api-keys.png`, fullPage: true });
  await page.getByRole("tab", { name: "General" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${dir("18-settings")}/workspace-settings.png`, fullPage: true });
});

test("capture mobile responsive layout", async ({ page, request }) => {
  await signIn(page, request);
  await page.setViewportSize({ width: 390, height: 844 });
  await gotoWorkspace(page, workspace.id);
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${dir("19-mobile-responsive")}/mobile-overview.png`, fullPage: true });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${dir("19-mobile-responsive")}/mobile-navigation.png`, fullPage: true });
});
