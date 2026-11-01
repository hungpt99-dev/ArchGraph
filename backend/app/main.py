from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from .config import get_settings
from .db import SessionLocal, engine
from .models import Edge, Enrichment, Job, Node, Repository, ScanRun, Workspace
from .routers import admin, ai, auth, docs, graph, repositories, sync, workspaces

settings = get_settings()
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("archgraph.api")

app = FastAPI(
    title="ArchGraph",
    version=settings.version,
    description="Evidence-driven microservices intelligence platform. Deterministic-first; AI optional.",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

for module in (auth, workspaces, repositories, graph, docs, ai, sync, admin):
    app.include_router(module.router)

_RATE: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def observability_and_limits(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    started = time.time()
    path = request.url.path
    if path.startswith("/api") and not path.startswith(("/api/health", "/api/ready", "/api/metrics")):
        key = request.client.host if request.client else "unknown"
        window = _RATE[key]
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429,
                                headers={"X-Request-ID": request_id})
        window.append(now)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    duration_ms = int((time.time() - started) * 1000)
    if path.startswith("/api") and path not in ("/api/metrics",):
        log.info("request method=%s path=%s status=%s duration_ms=%s request_id=%s",
                 request.method, path, response.status_code, duration_ms, request_id)
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):  # pragma: no cover
    log.exception("unhandled error path=%s", request.url.path)
    return JSONResponse(
        {"detail": "Internal server error", "request_id": request.headers.get("X-Request-ID")},
        status_code=500,
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "archgraph-api", "version": settings.version}


@app.get("/api/ready")
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "not_ready", "error": str(exc)[:200]}, status_code=503)
    return {"status": "ready", "database": "ok"}


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics():
    db = SessionLocal()
    try:
        def count(model, *where):
            return int(db.scalar(select(func.count(model.id)).where(*where)) or 0)

        scans_completed = count(ScanRun, ScanRun.status == "COMPLETED")
        scans_failed = count(ScanRun, ScanRun.status == "FAILED")
        nodes = count(Node, Node.status == "ACTIVE")
        edges = count(Edge, Edge.status == "ACTIVE")
        unresolved = count(Edge, Edge.resolved.is_(False))
        jobs_queued = count(Job, Job.status == "QUEUED")
        jobs_dead = count(Job, Job.status == "DEAD")
        enrich_pending = count(Enrichment, Enrichment.status.in_(["QUEUED", "NEEDS_REVIEW"]))
        enrich_failed = count(Enrichment, Enrichment.status == "FAILED")
        repos = count(Repository)
        workspaces = count(Workspace)
        lines = [
            "# HELP archgraph_workspaces Total workspaces.",
            "# TYPE archgraph_workspaces gauge",
            f"archgraph_workspaces {workspaces}",
            "# HELP archgraph_repositories Total repositories.",
            "# TYPE archgraph_repositories gauge",
            f"archgraph_repositories {repos}",
            "# HELP archgraph_graph_nodes Active graph nodes.",
            "# TYPE archgraph_graph_nodes gauge",
            f"archgraph_graph_nodes {nodes}",
            "# HELP archgraph_graph_edges Active graph edges.",
            "# TYPE archgraph_graph_edges gauge",
            f"archgraph_graph_edges {edges}",
            "# HELP archgraph_unresolved_edges Edges that could not be resolved.",
            "# TYPE archgraph_unresolved_edges gauge",
            f"archgraph_unresolved_edges {unresolved}",
            "# HELP archgraph_scans_total Scan runs by status.",
            "# TYPE archgraph_scans_total counter",
            f'archgraph_scans_total{{status="completed"}} {scans_completed}',
            f'archgraph_scans_total{{status="failed"}} {scans_failed}',
            "# HELP archgraph_jobs_queued Jobs waiting in the queue.",
            "# TYPE archgraph_jobs_queued gauge",
            f"archgraph_jobs_queued {jobs_queued}",
            "archgraph_jobs_dead " + str(jobs_dead),
            "archgraph_enrichment_pending " + str(enrich_pending),
            "archgraph_enrichment_failed " + str(enrich_failed),
        ]
        return "\n".join(lines) + "\n"
    finally:
        db.close()


@app.get("/api/mcp/tools")
def mcp_tools():
    from .mcp_tools import TOOLS

    return {"server": {"name": "archgraph", "version": settings.version,
                       "transport": ["stdio", "streamable-http"]},
            "tools": [{"name": s["name"], "description": s["description"],
                       "inputSchema": s["inputSchema"]} for s in TOOLS.values()],
            "count": len(TOOLS)}


@app.get("/api/mcp/config")
def mcp_config(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "stdio": {
            "command": "docker",
            "args": ["compose", "-f", "docker-compose.yml", "exec", "-T", "-e",
                     "ARCHGRAPH_API_KEY=<your-key>", "api", "python", "-m", "app.mcp_server"],
        },
        "streamable_http": {"url": f"{base.replace('http://', 'http://')}/mcp", "auth": "Bearer <api key>"},
        "claude_code": {
            "command": "claude mcp add archgraph -- docker compose exec -T -e ARCHGRAPH_API_KEY=<key> api python -m app.mcp_server"
        },
        "note": "Create an API key in Settings -> API keys, scoped to one workspace for isolation.",
    }


@app.on_event("startup")
def startup() -> None:
    log.info("ArchGraph API %s starting (env=%s, ai_optional=true)", settings.version, settings.env)
