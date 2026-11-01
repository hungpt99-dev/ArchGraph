# Operations

## Health and readiness

* `GET /api/health` — liveness, returns version.
* `GET /api/ready` — executes `SELECT 1`; non-200 when the database is unavailable.
* Container healthchecks use `/api/health` (API) and `pg_isready` (PostgreSQL).

## Metrics

`GET /api/metrics` (Prometheus text format):

```
archgraph_workspaces
archgraph_repositories
archgraph_graph_nodes
archgraph_graph_edges
archgraph_unresolved_edges
archgraph_scans_total{status="completed"|"failed"}
archgraph_jobs_queued
archgraph_jobs_dead
archgraph_enrichment_pending
archgraph_enrichment_failed
```

Structured JSON logs include method, path, status, duration and `X-Request-ID`.

## Backups

* PostgreSQL: `pg_dump -Fc archgraph > archgraph.dump` (all graph, jobs, audit and
  document metadata live here).
* Repository checkouts: the `repo-data` volume is a cache; it can be rebuilt by
  rescanning. Back it up only to avoid re-cloning.
* Uploaded PDFs/DOCX are stored as extracted text in `documents.content`.

## Scaling

* API: stateless; scale vertically or add replicas behind a shared rate limiter.
* Worker: run N workers; jobs are claimed with `SKIP LOCKED`.
* Graph size: indexes are in place for adjacency and trigram search. For >10^6
  nodes, partition `edges` by workspace or move traversal behind the
  `graphstore.py` interface.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Scans stuck `QUEUED` | worker container running? `docker compose logs worker` |
| Scan `FAILED` with auth error | repository token, branch name, provider reachability |
| Webhook `403` | secret not generated for the repository |
| Webhook `401` | signature/token mismatch; regenerate the secret |
| AI `FAILED` | provider `Test` button; check base URL/model; AI is optional |
| Graph missing a service | no `@SpringBootApplication`/manifest, or `spring.application.name` absent |
| Duplicate endpoint | run a full re-index for that repository |

## Upgrades

`docker compose build && docker compose up -d`. Migrations run in the one-shot
`migrate` service before API/worker start; `app.migrate` is idempotent and applies
`backend/migrations/*.sql` once.
