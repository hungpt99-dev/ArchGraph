# Architecture

## 1. Product shape

ArchGraph is a **modular monolith** plus independent background workers. There is no
reason for the product itself to be a fleet of microservices: the control plane,
graph store and UI deploy as one API container, and the heavy work (cloning,
parsing, AI calls, document sync) runs in a separate worker container that scales
horizontally because jobs are claimed from PostgreSQL.

Bounded contexts live in separate Python packages:

| Context | Location |
| --- | --- |
| Identity & access | `app/security.py`, `app/deps.py`, `app/routers/auth.py`, `app/routers/admin.py` |
| Workspaces & repositories | `app/routers/workspaces.py`, `app/routers/repositories.py` |
| Source scanner | `app/scanner/*` |
| Architecture graph | `app/graphstore.py`, `app/queries.py` |
| Synchronization | `app/scanner/pipeline.py`, `app/routers/sync.py`, `app/jobs.py` |
| Documentation connectors | `app/docs/connectors.py`, `app/routers/docs.py` |
| AI enrichment | `app/ai/*`, `app/routers/ai.py` |
| MCP server | `app/mcp_server.py`, `app/mcp_tools.py` |
| Audit & evidence | `audit_events` table, provenance columns on every node/edge |

## 2. Domain model

`users`, `workspaces`, `workspace_members`, `repositories`, `scan_runs`,
`indexed_files`, `nodes`, `edges`, `documents`, `document_links`, `enrichments`,
`ai_providers`, `business_flows`, `jobs`, `webhook_events`, `audit_events`,
`integrations`, `api_keys`.

### Graph schema

Node types: WORKSPACE, REPOSITORY, MODULE, SERVICE, APPLICATION, PACKAGE, CLASS,
INTERFACE, METHOD, FUNCTION, API_ENDPOINT, GRPC_SERVICE, GRPC_METHOD, KAFKA_TOPIC,
EVENT, PRODUCER, CONSUMER, DATABASE, TABLE, COLUMN, REDIS_CACHE, BACKGROUND_JOB,
EXTERNAL_SYSTEM, DOCUMENT, JIRA_ISSUE, BUSINESS_CAPABILITY, BUSINESS_FLOW,
ARCHITECTURE_DECISION, RUNTIME_TRACE.

Edge types: CONTAINS, DEPENDS_ON, CALLS, EXPOSES, IMPLEMENTS, PUBLISHES, CONSUMES,
READS, WRITES, USES_CACHE, SCHEDULES, ROUTES_TO, DOCUMENTS, IMPLEMENTS_REQUIREMENT,
RELATED_TO, OBSERVED_IN, INFERRED_FROM, APPROVED_BY.

Every row carries: stable id, workspace, natural key (unique per workspace),
repository, file, line range, `source_revision`, `detection_method`, `confidence`,
`status` (ACTIVE/REMOVED/UNRESOLVED), `properties`, `evidence`, `first_seen`,
`last_seen`.

### Why PostgreSQL instead of a graph database

* The graph is written in batches per scan; adjacency tables plus `ix_edges_src` /
  `ix_edges_dst` give predictable traversal at demo and mid-size scale.
* Recursive CTEs implement path finding and reverse impact closure in one query.
* Atomic graph replacement per repository is a transaction, not a distributed
  consistency problem.
* One datastore to back up, encrypt and operate.
A dedicated graph store remains a drop-in option behind `graphstore.py` if a
deployment outgrows this; nothing in the API assumes SQL beyond that module.

## 3. Scanning pipeline

1. **Checkout** (`scanner/git_provider.py`) — clone/fetch with a decrypted token,
   resolve HEAD, compute `git diff --name-status` against the previous revision.
2. **Configuration pass** (`scanner/config_parser.py`) — Maven/Gradle/npm/go.mod,
   Spring YAML/properties, gateway routes → service names, ports, datasources,
   Kafka bootstrap, modules, declared artifacts, shared databases.
3. **Source pass** — one extractor per language, each returning `FileResult`
   (nodes, edges, imports, metadata, errors). Unresolvable values become
   `dst_ref` edges flagged `unresolved`; they are never guessed.
4. **Graph commit** (`graphstore.GraphWriter`) — batched upsert by natural key,
   parent linking, edge dedupe, tombstoning of nodes not seen in the re-scanned
   files, and removal of deleted files.
5. **Workspace resolution** (`scanner/resolver.py`) — binds unresolved references
   across repositories: `service:name`, `class:Type`, `call:Type.method`,
   `artifact:group:artifact`, `table-by-entity:Entity`, gateway routes, service
   level `DEPENDS_ON`, `DATABASE → TABLE` containment, and deterministic business
   flows from topic producers/consumers.
6. **Staleness** — enrichments whose evidence files changed are marked `STALE`.
7. **Evidence** — `scan_runs` stores commit before/after, changed files, counts,
   duration and per-file errors; `audit_events` records the run.

Detection methods are part of the contract:

| Method | Meaning |
| --- | --- |
| STATIC_AST | parsed structure/call in source |
| CONFIG_FILE | declared in configuration |
| DECLARED_DEPENDENCY | declared build/package dependency |
| FRAMEWORK_INFERRED | convention (Spring stereotype, JPA mapping, Feign) |
| CONFIGURED_CONNECTION | explicit URL/route/connection string |
| RUNTIME_OBSERVED | telemetry (reserved) |
| AI_INFERRED | suggested by a model; never promoted without human approval |
| IMPORTED_DOC | parsed from a spec or document |
| MANUAL | created by a human |

## 4. Incremental correctness

The scan is idempotent: identical commits produce identical node/edge counts
(covered by `test_rescan_is_idempotent`). Incremental scans restrict both
extraction *and* tombstoning to changed files, so an edited controller replaces its
old endpoint node instead of accumulating duplicates. Removing a file tombstones
every node it produced.

## 5. AI enrichment design

`app/ai/enrich.py` builds a bounded context from the graph only: facts, edges,
source excerpts (≤ `AI_MAX_CONTEXT_CHARS`), and evidence references with content
hashes. It never queries the LLM for anything the scanner already knows, and it
cannot write to `nodes`/`edges` — the only table it mutates is `enrichments`.

States: NOT_ANALYZED, QUEUED, ANALYZING, GENERATED, NEEDS_REVIEW, APPROVED,
REJECTED, STALE, FAILED. Confidence < 0.75 lands in NEEDS_REVIEW. Approval records
the reviewer; re-enrichment creates a new revision and keeps the audit trail.

## 6. Jobs

PostgreSQL-backed queue processed with `FOR UPDATE SKIP LOCKED`; exponential backoff
(`10s, 20s, 40s…`), `DEAD` after `max_attempts`, retry from the UI/API. Kinds:
`scan_repository`, `scan_workspace`, `sync_documents`, `run_enrichment`,
`relink_documents`.

## 7. Frontend

React 18 + TypeScript + Vite + Tailwind, TanStack Query for server state, Radix
primitives for accessible dialogs/tabs, Cytoscape.js for the graph. The explorer
loads the workspace view first and expands a bounded neighbourhood on demand
(server-side expansion, ≤ 800 nodes per request), which keeps large estates usable.

## 8. Failure modes and how they surface

* Clone/auth failures → scan `FAILED`, repository `ERROR`, error text in the UI and audit log.
* Extractor exceptions → recorded per file in `scan_runs.stats.errors`; the scan still completes.
* Provider errors → enrichment `FAILED` with the provider message; the graph is untouched.
* Webhook replay → 200 `duplicate`, no second job.
* Unresolved references → visible in *APIs & Events → Unresolved* and via MCP.
