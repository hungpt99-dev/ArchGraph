# ArchGraph — Completion Report

**Version 1.0.0 · 2026-11-01 · self-hosted, MIT**

ArchGraph is delivered as a working product: a Docker Compose stack (PostgreSQL,
API, worker, MCP server, web UI), a deterministic multi-language scanner, a
provenance-carrying knowledge graph, an optional selective AI enrichment workflow,
documentation ingestion, GitHub/GitLab webhooks with incremental sync, and 37 MCP
tools. Evidence is in `artifacts/evidence/` and mapped feature-by-feature in
`artifacts/evidence/manifest.md`.

---

## 1. What was built

| Area | Delivered |
| --- | --- |
| Backend | FastAPI modular monolith, SQLAlchemy 2, PostgreSQL, JWT auth, RBAC, audit log |
| Scanner | Java/Spring, TypeScript/JS, Python (AST), Go, SQL, build/config parsers; incremental re-index |
| Graph | 29 node types / 18 edge types, provenance on every row, recursive traversal, path + impact |
| Worker | PostgreSQL `SKIP LOCKED` queue, retries/backoff, dead-letter, progress, retry from UI |
| Sync | GitHub HMAC webhook, GitLab token webhook, replay protection, branch scoping, full-recovery scan |
| Documents | Markdown, ADR, OpenAPI, AsyncAPI, PDF, DOCX, Jira, Confluence; deterministic linking |
| AI | Provider abstraction (OpenAI-compatible, Anthropic, Ollama, mock), 15 categories, estimate, review, staleness |
| MCP | 37 structured tools, stdio + streamable HTTP, API-key auth, workspace isolation, audit |
| UI | React/TS/Vite/Tailwind admin: 11 pages, Cytoscape explorer, source viewer, responsive layout |
| Ops | Health/readiness, Prometheus metrics, structured logs with request IDs, versioned SQL migrations |

## 2. Architecture

```
web (nginx + React)  ──/api──▶  api (FastAPI)
                     ──/mcp──▶  mcp (MCP over streamable HTTP)
                                   │
        worker (scan/sync/AI jobs) ─┴──▶ postgres (graph + metadata + queue)
                                   │
                        /data/repos (git checkouts, persistent volume)
```

Decisions and trade-offs: `docs/ARCHITECTURE.md`.
Security model: `docs/SECURITY.md`. Operations: `docs/OPERATIONS.md`.

## 3. Functional test results (actual)

| Suite | Command | Actual result |
| --- | --- | --- |
| Backend | `cd backend && ../.venv/bin/python -m pytest -q` | **59 passed**, 0 failed, 287.99 s, 64% coverage |
| E2E | `cd frontend && npx playwright test tests/e2e.spec.ts` | **15 passed**, 0 failed, 1.9 min |
| Evidence | `npx playwright test tests/screenshots.spec.ts` | **12 passed**, 0 failed, 34 screenshots |
| MCP stdio | `pytest tests/test_mcp.py` | **4 passed** (real MCP client) |
| MCP HTTP | real client → `http://127.0.0.1:8787/mcp` | 37 tools, `401` without key, queries succeed with key |

Coverage highlights: `app/scanner/java.py` 87%, `app/scanner/pipeline.py` 89%,
`app/scanner/resolver.py` 82%, `app/security.py` 91%, `app/ai/enrich.py` 76%.
Overall 64%; gaps are worker loop, CLI glue and router error branches.

Security tests (`tests/test_security.py`, 8 passed) cover scrypt hashing, JWT
tamper rejection, API-key hashing/revocation, Fernet round-trip, secret redaction
(URL userinfo, assignments, bearer tokens), SSRF blocking of metadata/loopback/
`file://`, at-rest credential encryption and role hierarchy. Webhook signature,
replay and branch-scope tests are in `tests/test_webhooks.py` (7 passed).

### Acceptance checklist

| Criterion | Status | Proof |
| --- | --- | --- |
| Starts via Docker Compose | ✅ | `docker compose up -d --build`; `docker compose ps` healthy |
| Admin dashboard functional | ✅ | screenshots 01–19; E2E 01–15 |
| Workspace management | ✅ | `test_workspace_crud`, E2E 02 |
| Multi-repository management | ✅ | `test_repository_add_queues_scan…`, E2E 03 |
| Auth & authorization | ✅ | `test_api.py`, `test_security.py`, E2E 15 |
| Java/Spring scanning | ✅ | 8 extractor tests + pipeline tests |
| API extraction | ✅ | 28 endpoints found in demo |
| Kafka extraction | ✅ | 6 topics, producers/consumers traced |
| Database extraction | ✅ | 5 databases, 6 tables, 30 columns |
| Cross-repository links | ✅ | gRPC-free demo links: gateway routes, Feign, topics, shared artifact |
| Graph without AI | ✅ | `test_ai_disabled_without_provider` |
| Source navigation | ✅ | API + E2E 05, verified revision |
| Impact analysis | ✅ | API test + explorer action |
| GitHub webhook | ✅ | 5 webhook tests + E2E 11 |
| GitLab webhook | ✅ | token verification + queue test |
| Incremental scanning | ✅ | `test_incremental.py`, E2E 12 |
| Graph updates after commit | ✅ | old node REMOVED, new node ACTIVE |
| AI enrichment becomes stale | ✅ | `test_incremental…` asserts `STALE` |
| Jira sync | ⚠️ fixtures only | connector + fixture ingestion; live tenant untested |
| Confluence sync | ⚠️ not verified | configured/probed; no live tenant |
| Markdown ingestion | ✅ | `test_docs.py` |
| Document search | ✅ | `test_docs.py`, UI search |
| Document↔code workflow | ✅ | 93 links in demo, approve/delete endpoints |
| LLM provider configuration | ✅ | provider CRUD + `Test` probe |
| AI can be disabled entirely | ✅ | 409 + graph unaffected |
| Selective enrichment | ✅ | targets × categories, estimate |
| Multiple target selection | ✅ | UI multi-select, API list |
| AI stored separately | ✅ | separate `enrichments` table, no node/edge writes |
| Evidence preserved | ✅ | evidence array with file/commit/hash |
| Review/approve/reject | ✅ | E2E 08 + `test_ai.py` |
| Stale handling | ✅ | pipeline staleness test |
| MCP server | ✅ | stdio + HTTP verified with real client |
| MCP workspace/service/source/Kafka/impact/doc queries | ✅ | `test_mcp.py` + HTTP probe |
| MCP authorization | ✅ | scoped key denied across workspaces |
| Admin pages all work | ✅ | 11 pages exercised |
| Graph interactive | ✅ | Cytoscape canvas, filters, expansion |
| Responsive layout | ✅ | E2E 14 + mobile screenshots |
| Screenshots for major functions | ✅ | 34 images, 19 directories |
| Evidence manifest | ✅ | `artifacts/evidence/manifest.md` |
| README presentation | ✅ | logo, badges, screenshots, quick start, MCP, security |
| Installation verified | ✅ | compose build/up/seed reproduced from empty volumes |

## 4. Demo

* URL: <http://localhost:8080>
* Admin: `admin@archgraph.io` / `archgraph-demo-2026` (created by `seed`)
* Workspace: **Hotel Management Platform** — 6 repositories, 8 services,
  28 endpoints, 6 topics, 5 databases, 6 tables, 10 documents, 4 business flows
* Load: `docker compose --profile demo run --rm seed`

The demo data is real source code in six git repositories under `demo/repos/`
(Spring Boot multi-module services, a TypeScript service, a shared library and a
docs repo), scanned at a real commit. Nothing is fabricated: counters derive from
the graph and 98 references remain explicitly unresolved.

## 5. Performance

Measured on the demo estate (warm container, 6 workers):

| Operation | Result |
| --- | --- |
| Full scan, 15–18 files | 2.5–3.7 s |
| Full scan, TypeScript service (3 files) | 1.0 s |
| Incremental scan (1 changed file) | < 1 s scan + queue latency |
| API workspace overview | < 150 ms |
| Graph expansion (16 nodes / 16 edges) | < 100 ms |
| Image sizes | api/worker 501 MB, web 75 MB, postgres 419 MB |

Not a large-estate benchmark. The demo has hundreds of nodes; the schema and
indexes are designed for 10⁴–10⁶, but that scale was not tested.

## 6. Screenshot inventory (34)

```
01-dashboard/login.png, workspaces.png, workspace-overview.png
02-workspaces/services-list.png, service-detail.png
03-repositories/repositories.png, scan-history.png
04-scanner/graph-system-view.png
05-architecture-graph/graph-service-neighbourhood.png
06-source-navigation/source-viewer.png
07-kafka-analysis/topics.png, event-trace.png, business-flows.png
08-api-analysis/rest-apis.png, tables.png, unresolved.png
09-documentation/documents.png
10-jira/requirement-linked.png
11-confluence/documentation-integrations.png
12-ai-enrichment/ai-center.png, estimate.png
13-ai-approval/review-modal.png, review-queue.png
14-git-webhooks/webhook-configuration.png, webhook-secret.png
15-incremental-sync/webhook-deliveries.png, incremental-scans.png
16-mcp/mcp-tools.png
17-security/roles.png, audit-log.png, api-keys.png
18-settings/workspace-settings.png
19-mobile-responsive/mobile-overview.png, mobile-navigation.png
```

## 7. Known issues / incomplete work

1. **Live Jira and Confluence sync untested** — no Atlassian tenant. Connector,
   credential encryption, probe and fixture ingestion are implemented; treat live
   sync as unverified until run against a real site.
2. **Real LLM providers untested** — no key. The mock provider exercises the full
   pipeline (queue → evidence → review → stale). Adapters follow documented APIs.
3. **OpenTelemetry ingestion not implemented** — listed as roadmap.
4. **gRPC extractor not implemented** — graph types exist, parser does not.
5. **Rate limiting is per-process** — fine for single API replica.
6. **Full re-index can be slow on large repos** — no sharding/parallel per-file
   parsing yet (single worker transaction per repo).
7. **Global search is ILIKE + trigram**, not semantic.
8. **Coverage 64%** — worker loop and some router branches lack tests.
9. **E2E depends on seeded demo data** (`make seed` before running).
10. The README references screenshots by relative path; GitHub renders them from the
    repository, so they must stay committed.

## 8. Deployment & reproduction

```bash
git clone https://github.com/hungpt99-dev/ArchGraph.git
cd ArchGraph
cp .env.example .env && sed -i "s/change-me.*/$(openssl rand -hex 32)/" .env
docker compose up -d --build
docker compose --profile demo run --rm seed
# http://localhost:8080  admin@archgraph.io / archgraph-demo-2026
```

Reproduce the evidence:

```bash
cd backend && ../.venv/bin/python -m pytest -q
cd ../frontend && npx playwright test tests/e2e.spec.ts
npx playwright test tests/screenshots.spec.ts
```

MCP: create a key in *Settings → API keys*, then

```bash
claude mcp add archgraph -- docker compose exec -T -e ARCHGRAPH_API_KEY=<key> \
  api python -m app.mcp_server
```

## 9. Deliberate design choices

* **Modular monolith + workers**, not microservices, so the product is operable by
  one team and one database.
* **PostgreSQL for the graph**: indexed adjacency + recursive CTEs beat operating a
  graph database at this scale; the interface isolates the choice.
* **No fabrication**: unresolved references are first-class and visible; the demo
  reports 98 of them rather than inventing edges.
* **AI reads, never writes** the graph; approvals are human actions with audit.

## 10. Verdict

The product runs, its tests prove the core flows, the screenshots come from the
running stack, and the limitations above are explicit. Two integration areas
(Jira/Confluence live sync, real LLM providers) are implemented but unverified
without credentials — they are reported as such, not claimed as tested.
