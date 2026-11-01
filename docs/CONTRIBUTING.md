# Contributing

## Setup

See the development section of the README. Backend tests need PostgreSQL reachable
at `TEST_DATABASE_URL` (defaults to the Compose instance on port 5435 in CI setups;
locally `postgresql://archgraph:archgraph@localhost:5432/archgraph_test`).

## Rules

1. **No fabricated facts.** Scanner output must come from parsed source/config.
   Ambiguity stays unresolved with a `dst_ref`.
2. **AI never mutates the graph.** Enrichment writes `enrichments` only.
3. **Every visible feature needs evidence.** Add a Playwright assertion or a
   screenshot under `artifacts/evidence/`.
4. **Extractor changes need unit tests** covering the annotation/pattern you touch.
5. **Migrations are append-only.** Add `backend/migrations/NNNN_name.sql`; never edit
   an applied file.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q
cd frontend && npx playwright test tests/e2e.spec.ts
```

## Adding a language extractor

Implement `extract(path, text, ctx: RepoContext) -> FileResult` in
`backend/app/scanner/<lang>.py`, register the extension in
`backend/app/scanner/pipeline.py`, and emit:
`SNode(key=ctx.scope(...), type=..., name=..., file_path=..., start_line=...)` and
`SEdge(type=..., src_key=..., dst_key|dst_ref=..., detection=..., confidence=...)`.
Use `ctx.scope()` for repository-local keys and leave workspace-global keys
(`topic:`, `db:`) unscoped so the resolver can merge them across repositories.

## Code style

Python 3.12, type hints on public functions, no network calls in extractors, no LLM
calls outside `app/ai/`. TypeScript strict mode; components in `src/components`,
pages in `src/pages`; API access through `src/lib/api.ts`.
