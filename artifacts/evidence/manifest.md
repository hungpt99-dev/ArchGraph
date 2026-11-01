# Evidence manifest

Snapshot: 2026-11-01 · Docker Compose stack (`archgraph`), demo workspace *Hotel Management Platform*.
All screenshots were captured by Playwright from the running application
(`frontend/tests/screenshots.spec.ts`); raw logs are in [logs/](logs/).

Environment at capture time:

* API `http://localhost:8000` behind nginx `http://localhost:8080`, MCP `:8787`
* PostgreSQL 16-alpine, worker container running the real job loop
* Demo: 6 repositories → 8 services, 28 endpoints, 6 Kafka topics, 6 tables,
  5 databases, 3 caches, 2 scheduled jobs, 10 documents, 93 document↔code links
* Graph: **266 nodes / 402 edges / 98 unresolved references** (17 node types)

Legend: **Complete** = implementation + automated test + captured evidence.
**Complete (mock provider)** = workflow proven with the labelled offline mock
provider; no external LLM credentials were available. **Not verified** = code path
exists but could not be exercised without external credentials.

| # | Feature | Status | Test command | Result | Screenshots | Evidence notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Admin dashboard & login | Complete | `pytest tests/test_api.py` · `playwright … 01` | 8 passed · 15 E2E passed | `01-dashboard/login.png`, `workspaces.png`, `workspace-overview.png` | Real login form + JWT; counters come from the graph API |
| 2 | Workspace management | Complete | `pytest tests/test_api.py::test_workspace_crud` | passed | `02-workspaces/services-list.png`, `service-detail.png` | Create/update/delete, membership, owner scoping |
| 3 | Multi-repository management | Complete | `pytest tests/test_api.py::test_repository_add_queues_scan_and_worker_indexes_it` | passed | `03-repositories/repositories.png`, `scan-history.png` | 6 repos, one repo hosts 3 services |
| 4 | Deterministic scanner (Java/TS/Py/Go/SQL) | Complete | `pytest tests/test_scanner_java.py tests/test_scanner_other.py` | 17 passed | `04-scanner/graph-system-view.png` | 87% line coverage on `java.py`; no LLM involved |
| 5 | Architecture graph + explorer | Complete | `pytest tests/test_pipeline.py` · E2E 04 | 6 passed | `05-architecture-graph/graph-service-neighbourhood.png` | Progressive expansion, filters, path finder, impact |
| 6 | Source navigation | Complete | E2E 05 · `test_api.py` source assertion | passed | `06-source-navigation/source-viewer.png` | Verified against indexed revision; git history shown |
| 7 | Kafka analysis | Complete | `test_scanner_java.py::test_kafka_*` · E2E 06 | passed | `07-kafka-analysis/topics.png`, `event-trace.png`, `business-flows.png` | Producer/consumer resolution across repositories |
| 8 | API analysis & unresolved refs | Complete | E2E 06 · `test_api.py` | passed | `08-api-analysis/rest-apis.png`, `tables.png`, `unresolved.png` | 98 unresolved refs surfaced, not invented |
| 9 | Documentation ingestion | Complete | `pytest tests/test_docs.py` | 5 passed | `09-documentation/documents.png` | Markdown, ADR, OpenAPI; idempotent by content hash |
| 10 | Jira ingestion | **Complete (fixtures only)** | `test_docs.py::test_jira_fixture_document_and_requirement_link` | passed | `10-jira/requirement-linked.png` | REST connector implemented; live tenant untested (no credentials) |
| 11 | Confluence ingestion | **Not verified (no tenant)** | `test_docs.py` (connector unit paths) | — | `11-confluence/documentation-integrations.png` | Form + encrypted secret + test probe implemented; no live sync proof |
| 12 | AI enrichment (selective) | **Complete (mock provider)** | `pytest tests/test_ai.py` · E2E 08 | 3 passed | `12-ai-enrichment/ai-center.png`, `estimate.png` | Estimate → queue → evidence-linked record; tokens recorded |
| 13 | AI review & approval | **Complete (mock provider)** | `test_ai.py::test_selective_…_approve` · E2E 08 | passed | `13-ai-approval/review-modal.png`, `review-queue.png` | Approve/reject/edit/reenrich; transcript in audit |
| 14 | Git webhooks | Complete | `pytest tests/test_webhooks.py` · E2E 11 | 7 passed | `14-git-webhooks/webhook-configuration.png`, `webhook-secret.png` | HMAC verify, replay → duplicate, branch scoping |
| 15 | Incremental sync | Complete | `pytest tests/test_incremental.py` · E2E 12 | 2 passed | `15-incremental-sync/incremental-scans.png`, `webhook-deliveries.png` | Changed file re-indexed, old node tombstoned, enrichment → STALE |
| 16 | MCP server | Complete | `pytest tests/test_mcp.py` (real MCP client, stdio) + HTTP probe | 4 passed | `16-mcp/mcp-tools.png` | 37 tools; HTTP 401 unauthenticated; workspace isolation proven |
| 17 | Security model | Complete | `pytest tests/test_security.py` · E2E 15 | 8 passed | `17-security/roles.png`, `audit-log.png`, `api-keys.png` | RBAC, SSRF guard, redaction, encrypted credentials |
| 18 | Settings & AI policy | Complete | E2E 09/10 · `test_ai.py::test_reenrich…policy` | passed | `18-settings/workspace-settings.png` | Policy enforcement returns 400 for disallowed category |
| 19 | Responsive layout | Complete | E2E 14 | passed | `19-mobile-responsive/mobile-overview.png`, `mobile-navigation.png` | 390×844 viewport |

## Test execution summary

| Suite | Command | Result |
| --- | --- | --- |
| Backend unit + integration | `cd backend && ../.venv/bin/python -m pytest -q` | **59 passed**, 288 s, 64% line coverage (87% `scanner/java.py`, 89% `pipeline.py`) |
| E2E (real services) | `cd frontend && npx playwright test tests/e2e.spec.ts` | **15 passed**, 1.9 min |
| Evidence capture | `npx playwright test tests/screenshots.spec.ts` | **12 passed**, 34 screenshots |
| MCP stdio client | `tests/test_mcp.py` | tools 37, isolation + audit + invalid key |
| MCP HTTP client | manual real client against `:8787/mcp` | `401` unauthenticated, `list_workspaces` OK with key |

Raw logs: [logs/backend-tests.log](logs/backend-tests.log),
[logs/e2e.log](logs/e2e.log).

## No-AI proof

* `test_ai.py::test_ai_disabled_without_provider` scans a repository with **no
  provider rows** and asserts `/ai/status` reports `ai_enabled: false` while graph
  endpoints continue to serve.
* `test_mcp.py::test_mcp_ai_tools_respect_disabled_state` shows read tools working
  and `request_enrichment` failing closed.
* The demo's AI provider is the **mock** adapter; every record is labelled `MOCK`.

## Response-time measurements (demo estate, 6 repositories)

| Operation | Measurement |
| --- | --- |
| Full scan, `payment-service` (15 files) | 3.7 s |
| Full scan, `order-inventory` (13 files) | 3.1 s |
| Full scan, `booking-platform` (18 files) | 2.5 s |
| Full scan, `notification-service` | 1.0 s |
| Graph neighbourhood (16 nodes) | < 100 ms server time (API log) |
| Workspace overview aggregate | < 150 ms |

These are small demo repositories; they are **not** benchmarks for large estates.

## Honest gaps

1. **Jira/Confluence live sync** — no tenant available. Connector code, credential
   storage, probe endpoint and fixture ingestion are tested; live sync is not.
2. **Real LLM provider** — no API key was available. OpenAI/Anthropic/Ollama
   adapters are implemented and unit-level, but enrichment evidence uses the mock.
3. **Runtime telemetry** — OpenTelemetry ingestion is not implemented (roadmap).
4. **Rate limiting** is in-process, so multi-replica APIs need an external limiter.
5. **gRPC** is modelled in the graph schema but no proto extractor ships.
6. Coverage is 64% overall; untested areas are mostly the worker loop, CLI glue and
   error branches of routers.
