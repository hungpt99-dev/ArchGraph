<div align="center">

<img src="docs/assets/logo.svg" width="104" alt="ArchGraph logo" />

# ArchGraph

**Understand your entire system. Not just your code.**

The evidence-driven intelligence layer for microservices.

[![tests](https://img.shields.io/badge/backend_tests-59_passing-2ea043)](#testing)
[![e2e](https://img.shields.io/badge/e2e-15_passing-2ea043)](#testing)
[![coverage](https://img.shields.io/badge/coverage-64%25-6366f1)](#testing)
[![license](https://img.shields.io/badge/license-MIT-6366f1)](LICENSE)
[![docker](https://img.shields.io/badge/deploy-docker_compose-2496ed)](#quick-start)

[Quick start](#quick-start) · [How it works](#how-it-works) · [Selective AI](#selective-ai-the-differentiator) · [MCP](#mcp-server) · [Docs](docs/ARCHITECTURE.md)

</div>

![Workspace overview](artifacts/evidence/01-dashboard/workspace-overview.png)

ArchGraph scans your repositories, builds one knowledge graph of every service,
endpoint, Kafka topic, database, table and background job, and proves where each
fact came from — repository, commit, file, line, detector and confidence.

It runs **without an LLM**. When you do want AI, you pick exactly which services,
flows or requirements to enrich, and the output is stored separately, reviewed by
a human, and marked stale when the source changes.

---

## Why ArchGraph

Most tools give you one of these: a code graph, a vector/RAG index, an AI summary,
or a diagram that is out of date the moment it is generated. ArchGraph is in the
first camp — a provenance-carrying graph, **not** a RAG system: there are no
embeddings and no vector store. Retrieval is SQL over the graph plus full-text
indexes, and AI context is assembled from that evidence on demand.
ArchGraph is built around four rules:

1. **Deterministic first.** Scanning, graph construction, impact analysis, source
   navigation and every MCP read tool work with zero AI configuration.
2. **AI is an optional layer.** Enrichment is scoped, budgeted, versioned and
   reviewable. It never overwrites a scanner fact.
3. **Evidence or it did not happen.** Every node and edge carries repository,
   revision, file, line, detection method and confidence. Unresolvable references
   are shown as *unresolved* instead of being invented.
4. **Continuously maintained.** Webhooks trigger incremental re-indexing; AI
   insights derived from changed files become `STALE` automatically.

## What you get

| Capability | Deterministic | Notes |
| --- | --- | --- |
| Java / Spring Boot scanning | ✅ | controllers, mappings, services, JPA, Kafka, Feign, scheduling, SQL |
| TypeScript / JavaScript | ✅ | NestJS, Express/Fastify routes, KafkaJS, Prisma, fetch/axios |
| Python | ✅ | full AST: FastAPI/Flask routes, SQLAlchemy, Celery, Kafka |
| Go | ✅ | structs, methods, gin/chi routes, kafka-go |
| SQL & migrations | ✅ | tables, columns, access relationships |
| Cross-repository linking | ✅ | Feign/HTTP, gateway routes, topics, shared artifacts, databases |
| API + Kafka + DB inventory | ✅ | with producer/consumer topology and event tracing |
| Architecture explorer | ✅ | progressive graph, filters, dependency paths, impact analysis |
| Source navigation | ✅ | exact indexed revision, verified against git |
| Business flows | ✅ | derived from producers/consumers (labelled deterministic) |
| Documentation ingestion | ✅ | Markdown, ADR, OpenAPI, AsyncAPI, PDF, DOCX, Jira/Confluence APIs |
| Webhooks + incremental sync | ✅ | GitHub HMAC, GitLab token, idempotent deliveries |
| MCP server | ✅ | 37 structured graph tools, workspace-scoped API keys, audited |
| AI service/flow/risk insights | optional | 15 categories, provider-agnostic, approval + staleness |
| Runtime telemetry (OTel) | planned | adapter surface defined, not shipped |

## Selective AI: the differentiator

![AI Intelligence Center](artifacts/evidence/12-ai-enrichment/ai-center.png)

A 40-service estate yields thousands of nodes. ArchGraph refuses to spend tokens on
all of them by default. You select services (or topics, APIs, documents), choose
categories, see a token estimate, then queue exactly that work:

```
Targets  ▸ payment-service, invoice-service
Categories ▸ service overview, business capabilities, risks
Estimate ▸ ~6.8k tokens · provider: your-llm
```

Every result records: target, repository revision, evidence files, prompt version,
provider, model, tokens, confidence and review state
(`QUEUED → ANALYZING → GENERATED / NEEDS_REVIEW → APPROVED / REJECTED → STALE`).
When a cited file changes in a later scan, the insight becomes `STALE` — it is never
silently presented as current.

## How it works

```
        ┌─────────────┐     webhooks      ┌──────────────────┐
 GitHub │  Git repos  │◀──────────────────│  FastAPI control │
 GitLab │  (clone)    │                   │  plane + RBAC    │
        └──────┬──────┘                   └────────┬─────────┘
               │ checkout @ commit                 │ jobs
        ┌──────▼──────────────┐            ┌───────▼────────┐
        │ Deterministic       │            │ Worker         │
        │ extractors          │            │ (Postgres      │
        │ Java·TS·Py·Go·SQL   │            │  SKIP LOCKED)  │
        └──────┬──────────────┘            └───────┬────────┘
               │ nodes + edges + evidence          │
        ┌──────▼───────────────────────────────────▼────────┐
        │ PostgreSQL: graph, evidence, docs, jobs, audit    │
        └──────┬───────────────────────┬────────────────────┘
               │                       │
        ┌──────▼──────┐         ┌──────▼───────┐
        │ React admin │         │ MCP server   │  Claude Code · Codex · Cursor
        │ dashboard   │         │ 37 tools     │
        └─────────────┘         └──────────────┘
```

PostgreSQL stores the graph as typed nodes/edges with indexed adjacency and
recursive-CTE traversal. One datastore, atomic graph updates, no graph-database
tax. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the domain model,
detection methods and resolution rules.

## Quick start

Requires Docker with Compose v2 and ~2 GB RAM.

```bash
git clone https://github.com/hungpt99-dev/ArchGraph.git
cd ArchGraph
cp .env.example .env
# set SECRET_KEY (openssl rand -hex 32)
docker compose up -d --build
docker compose --profile demo run --rm seed
```

Open <http://localhost:8080> and sign in with the demo administrator:

```
email:    admin@archgraph.io
password: archgraph-demo-2026
```

The demo workspace contains a realistic hotel platform: 8 Spring Boot/TypeScript
services across four repositories, 6 Kafka topics, 5 databases, Redis caches,
scheduled jobs, ADRs, an OpenAPI spec and Jira-shaped requirement fixtures.

> The seed also installs a **mock** AI provider so the enrichment workflow can be
> explored offline. It is labelled `MOCK` everywhere and never calls a network
> service. Configure a real provider in *AI Intelligence Center → Providers*.

### Without Docker (development)

```bash
# PostgreSQL 16 on :5432
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://archgraph:archgraph@localhost:5432/archgraph
python -m app.migrate
sh ../demo/init_repos.sh ../demo/repos   # create demo git history
DEMO_REPO_ROOT=../demo/repos python -m app.seed_demo
uvicorn app.main:app --reload --port 8000
python -m app.worker            # second terminal

cd ../frontend && npm install && npm run dev   # http://localhost:5173
```

## Configuration

All configuration is environment-based (see `.env.example`).

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | JWT signing + default credential encryption key |
| `ENCRYPTION_KEY` | Optional dedicated Fernet key for integration secrets |
| `DATABASE_URL` | PostgreSQL DSN |
| `REPO_STORAGE_DIR` | Where repositories are cloned (persistent volume) |
| `ALLOW_LOCAL_REPOS` | Allow `file://` / absolute-path repositories (self-hosted mirrors) |
| `ALLOW_PRIVATE_NETWORKS` | Allow clones/webhooks to private address space (default off = SSRF guard) |
| `GITHUB_WEBHOOK_SECRET` / `GITLAB_WEBHOOK_TOKEN` | Optional global webhook fallback secrets |
| `DEMO_ADMIN_EMAIL` / `DEMO_ADMIN_PASSWORD` | Credentials created by the seed |

## GitHub / GitLab integration

1. *Integrations → Git webhooks → Create secret* for a repository.
2. Add the webhook in the provider:
   - GitHub: `https://<host>/api/webhooks/github/<repository-id>`, content type
     `application/json`, secret = generated value, events `push` + `pull_request`.
   - GitLab: `https://<host>/api/webhooks/gitlab/<repository-id>`, secret token = generated value.
3. A push to the tracked branch queues an **incremental** scan; only changed files
   are re-parsed, removed nodes are tombstoned and affected AI insights become `STALE`.

Replay protection uses the delivery id; signatures are verified with HMAC-SHA256
(GitHub) or constant-time token comparison (GitLab). Payloads are never trusted as
source: the scanner re-fetches the commit through the Git adapter.

## Jira / Confluence

*Integrations → Jira & Confluence* stores an Atlassian base URL, email and API token
(Fernet-encrypted). **Test** probes the API without importing data; **Sync** on the
Documentation page pulls issues/pages incrementally and links them to services,
APIs and topics by deterministic name matching. AI-suggested links are separate and
require approval.

> The shipped demo uses **Jira-shaped fixture documents** (`requirements/jira-export.md`)
> because no Atlassian tenant is bundled. The connector is implemented against the
> v2 REST API; live sync is untested without credentials — see the completion report.

## AI provider setup

*AI Intelligence Center → Provider*:

| Kind | Base URL | Notes |
| --- | --- | --- |
| OpenAI-compatible | `https://api.openai.com/v1` or any gateway | JSON mode, bearer key |
| Anthropic | `https://api.anthropic.com` | v1/messages |
| Ollama | `http://localhost:11434` | fully local, `format: json` |
| Mock | — | offline deterministic demo/test provider, labelled MOCK |

Keys are encrypted at rest, never returned by the API and never included in prompts.
See [docs/SECURITY.md](docs/SECURITY.md) for the data-minimisation rules.

## MCP server

ArchGraph exposes 37 structured tools (services, code, APIs, events, databases,
architecture, documents, AI) over stdio and streamable HTTP.

```bash
# stdio (Claude Code)
claude mcp add archgraph -- docker compose exec -T \
  -e ARCHGRAPH_API_KEY=<key> api python -m app.mcp_server
```

```json
{
  "mcpServers": {
    "archgraph": {
      "command": "docker",
      "args": ["compose", "exec", "-T", "-e", "ARCHGRAPH_API_KEY=<key>",
               "api", "python", "-m", "app.mcp_server"]
    }
  }
}
```

Cursor / Codex / any MCP client can use the streamable HTTP endpoint at
`http://localhost:8787/mcp` with `Authorization: Bearer <key>`.

Create keys in *Settings → API keys*, scoped to one workspace. Every tool call is
audited with actor, tool and arguments. Full tool reference:
[docs/MCP.md](docs/MCP.md).

## Testing

```bash
make test                     # backend: unit + integration (real PostgreSQL)
cd frontend && npx playwright test tests/e2e.spec.ts     # 15 E2E flows
cd frontend && npx playwright test tests/screenshots.spec.ts  # evidence capture
```

Backend suite covers extractors (Java/TS/Python/Go/SQL/config), graph construction,
cross-repository resolution, incremental scans, webhook signatures and replay,
RBAC, secret handling, AI lifecycle and a real MCP client round-trip.
E2E runs the actual UI against the actual API and worker — no mocks.

Results and evidence: [artifacts/evidence/manifest.md](artifacts/evidence/manifest.md).

## Demo walkthrough

1. **Overview** — counters, repository health, recent scans, flow list.
2. **Services → payment-service** — endpoints, dependencies, topics, classes.
3. **Architecture** — expand a service, filter edge types, find a dependency path,
   run impact analysis, open a class at its exact revision.
4. **APIs & Events** — REST inventory, Kafka producers/consumers, trace
   `payment.completed` through invoice and notification, unresolved references.
5. **Documentation** — search ADRs and requirements, inspect links to code.
6. **AI Intelligence** — select `payment-service`, estimate, generate, review one
   insight, approve it; change a file in a repo and rescan to see `STALE`.
7. **Integrations** — create a webhook secret, send a signed push, watch the
   incremental scan appear in Synchronization.

## Security

RBAC (`SYSTEM_ADMIN → WORKSPACE_ADMIN → ARCHITECT → DEVELOPER → VIEWER`), workspace
isolation for users and API keys, scrypt password hashing, Fernet-encrypted
credentials, HMAC webhook verification with replay protection, SSRF guards on
outbound URLs, secret redaction before AI context construction, and a full audit
trail. Report vulnerabilities privately; see [docs/SECURITY.md](docs/SECURITY.md).

## Supported languages

Java 17+/Spring Boot · TypeScript/JavaScript (NestJS, Express, Fastify) · Python 3.10+
(FastAPI, Flask, SQLAlchemy) · Go (net/http, gin, chi, kafka-go) · SQL (DDL,
migrations). Extractors are pluggable: implement `extract(path, text, ctx)` and
register the extension.

## Roadmap

- [ ] OpenTelemetry trace ingestion → `RUNTIME_OBSERVED` edges (adapter surface exists)
- [ ] gRPC proto + GraphQL schema extractors
- [ ] Per-workspace encrypted Git deploy keys (currently per-repository tokens)
- [ ] Alembic-style migration generation (versioned SQL patches ship today)
- [ ] Notion connector
- [ ] Scheduled full re-index policy (daily) in addition to webhooks

These are **not** implemented; they are listed so nothing is implied.

## Limitations

- Static analysis only: no runtime call graph, no traffic percentages.
- Heuristic parsers (Java/TS/Go) handle common framework patterns; exotic
  metaprogramming can leave unresolved references — which are shown, not hidden.
- The demo AI provider is a mock; real provider calls require your own credentials.
- Live Jira/Confluence sync is implemented but untested without a tenant.
- Rate limiting is per-process in-memory (use a shared limiter for multi-replica APIs).

## Contributing

Issues and PRs welcome. Run `make test` and the Playwright suites before opening a
PR; keep new scanner behaviour covered by extractor tests and add evidence when a
feature is user-visible. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

MIT © ArchGraph contributors. Not affiliated with OpenClaw; the README borrows
presentation principles only.
