"""Scan pipeline: checkout -> config intelligence -> extraction -> graph commit."""

from __future__ import annotations

import hashlib
import os
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..graphstore import GraphWriter, workspace_stats
from ..models import (
    AuditEvent,
    Enrichment,
    IndexedFile,
    Node,
    Repository,
    ScanRun,
    new_id,
)
from .base import FileResult, RepoContext
from .config_parser import ConfigParser
from .git_provider import GitSource, iter_repo_files, read_text
from .go import GoExtractor
from .java import JavaExtractor
from .python_lang import PythonExtractor
from .resolver import WorkspaceResolver
from .sql import SqlExtractor
from .typescript import TypeScriptExtractor

settings = get_settings()

EXTRACTORS = {
    ".java": JavaExtractor(),
    ".py": PythonExtractor(),
    ".ts": TypeScriptExtractor(),
    ".tsx": TypeScriptExtractor(),
    ".js": TypeScriptExtractor(),
    ".jsx": TypeScriptExtractor(),
    ".mjs": TypeScriptExtractor(),
    ".go": GoExtractor(),
    ".sql": SqlExtractor(),
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upsert_indexed_file(
    db: Session, repo: Repository, path: str, commit: str, text: str | None, language: str | None
) -> IndexedFile:
    row = db.scalar(
        select(IndexedFile).where(IndexedFile.repository_id == repo.id, IndexedFile.path == path)
    )
    content_hash = sha256_text(text) if text is not None else None
    if row is None:
        row = IndexedFile(
            id=new_id(), workspace_id=repo.workspace_id, repository_id=repo.id, path=path,
            language=language, content_hash=content_hash, commit_sha=commit,
            size_bytes=len(text.encode()) if text else 0, indexed_at=_now(),
        )
        db.add(row)
    else:
        row.language = language
        row.content_hash = content_hash
        row.commit_sha = commit
        row.size_bytes = len(text.encode()) if text else 0
        row.indexed_at = _now()
    return row


def mark_stale_enrichments(db: Session, workspace_id: str, repo_id: str, changed: dict[str, str]) -> int:
    """Mark AI output STALE when the source it was derived from changed."""
    if not changed:
        return 0
    stale = 0
    rows = db.scalars(
        select(Enrichment).where(
            Enrichment.workspace_id == workspace_id,
            Enrichment.status.in_(["GENERATED", "NEEDS_REVIEW", "APPROVED"]),
        )
    ).all()
    for enrichment in rows:
        for ev in enrichment.evidence or []:
            if ev.get("repository_id") != repo_id:
                continue
            path = ev.get("file")
            if path and path in changed:
                enrichment.status = "STALE"
                enrichment.updated_at = _now()
                stale += 1
                break
    db.flush()
    return stale


def scan_repository(
    db: Session,
    repo: Repository,
    trigger: str = "manual",
    mode: str = "full",
    job_id: str | None = None,
    progress=None,
) -> dict:
    started = time.time()
    scan = ScanRun(
        id=new_id(), workspace_id=repo.workspace_id, repository_id=repo.id, job_id=job_id,
        trigger=trigger, mode=mode, status="RUNNING", branch=repo.branch, started_at=_now(),
    )
    db.add(scan)
    db.commit()

    try:
        checkout = GitSource(repo).prepare(mode)
        scan.commit_sha = checkout.commit
        scan.previous_commit = checkout.previous_commit
        db.commit()

        if mode == "incremental" and checkout.previous_commit == checkout.commit:
            scan.status = "SKIPPED"
            scan.finished_at = _now()
            scan.stats = {"reason": "no new commits", "commit": checkout.commit}
            repo.last_synced_at = _now()
            repo.status = "READY"
            db.commit()
            return {"scan_id": scan.id, "status": "SKIPPED", "commit": checkout.commit}

        files = iter_repo_files(checkout.path)
        deleted = {c["path"] for c in checkout.changed_files if c["status"] == "D"}
        changed_map: dict[str, str] = {c["path"]: c["status"] for c in checkout.changed_files}

        if mode == "incremental" and checkout.previous_commit:
            wanted = {c["path"] for c in checkout.changed_files if c["status"] != "D"}
            config_files = [f["path"] for f in files if f["config"]]
            selected = set(wanted) | set(config_files)
            files = [f for f in files if f["path"] in selected]

        ctx = RepoContext(
            repository_id=repo.id,
            repository_name=repo.name,
            commit=checkout.commit,
            root=checkout.path,
        )
        config_parser = ConfigParser()
        results: list[FileResult] = []
        errors: list[dict] = []
        languages: dict[str, int] = {}

        config_files = [f for f in files if f["config"] and f["ext"] not in (".md",)]
        source_files = [f for f in files if f["ext"] in EXTRACTORS]

        total = max(1, len(config_files) + len(source_files))
        done = 0
        # ---- pass 1: configuration (establishes repo/service context) ----
        for f in config_files:
            text = read_text(os.path.join(checkout.path, f["path"]))
            if text is None:
                continue
            result = config_parser.parse(f["path"], text, ctx)
            if result:
                results.append(result)
                errors.extend({"file": f["path"], "error": e} for e in result.errors)
            done += 1
        if progress and done % 5 == 0:
            progress(int(60 * done / total), f"configuration {done}/{total}")

        # ---- pass 2: source extraction ----
        for f in source_files:
            full = os.path.join(checkout.path, f["path"])
            text = read_text(full)
            done += 1
            if text is None:
                continue
            extractor = EXTRACTORS[f["ext"]]
            result = extractor.extract(f["path"], text.replace("\r\n", "\n"), ctx)
            results.append(result)
            errors.extend({"file": f["path"], "error": e} for e in result.errors)
            languages[result.language] = languages.get(result.language, 0) + 1
            upsert_indexed_file(db, repo, f["path"], checkout.commit, text, result.language)
            if progress and done % 10 == 0:
                progress(int(60 * done / total), f"extracting {done}/{total}")
        db.flush()

        # ---- pass 3: commit nodes/edges ----
        nodes: dict[str, object] = {}
        edges: list[object] = []
        for result in results:
            for node in result.nodes:
                nodes[node.key] = node
            edges.extend(result.edges)

        writer = GraphWriter(db, repo.workspace_id, repo.id, checkout.commit)
        writer.load_existing()
        writer.upsert_nodes(nodes)
        writer.apply_parents()
        writer.upsert_edges(edges)
        removed = writer.mark_missing_removed(
            restrict_files=(set(changed_map) | deleted) if mode == "incremental" else None
        )

        for path in deleted:
            db.execute(delete(IndexedFile).where(
                IndexedFile.repository_id == repo.id, IndexedFile.path == path
            ))

        # ---- repo config snapshot (used by the workspace resolver) ----
        repo.settings = {
            **(repo.settings or {}),
            "app_name": ctx.app_name,
            "artifact": ctx.artifact,
            "group_id": ctx.group_id,
            "port": ctx.port,
            "gateway_routes": ctx.gateway_routes,
            "declared_databases": ctx.declared_databases,
            "kafka_bootstrap": ctx.kafka_bootstrap,
            "redis_hosts": ctx.redis_hosts,
            "languages": languages,
        }
        repo.language = max(languages, key=languages.get) if languages else repo.language
        repo.last_commit = checkout.commit
        repo.last_synced_at = _now()
        repo.status = "READY"
        repo.error = None
        db.flush()

        if progress:
            progress(70, "linking workspace")
        resolver = WorkspaceResolver(db, repo.workspace_id)
        resolver_summary = resolver.run()

        changed_hashes = {p: "changed" for p in changed_map}
        stale = mark_stale_enrichments(db, repo.workspace_id, repo.id, changed_hashes)

        duration = time.time() - started
        stats = {
            "files_scanned": len(source_files),
            "config_files": len(config_files),
            "languages": languages,
            "nodes_created": writer.created_nodes,
            "nodes_updated": writer.updated_nodes,
            "nodes_removed": removed,
            "edges_created": writer.created_edges,
            "edges_updated": writer.updated_edges,
            "unresolved_edges": writer.unresolved_edges,
            "resolved_edges": resolver_summary["resolved_edges"],
            "enrichments_marked_stale": stale,
            "duration_seconds": round(duration, 2),
            "errors": errors[:50],
            "error_count": len(errors),
            "workspace": workspace_stats(db, repo.workspace_id),
        }
        scan.status = "COMPLETED"
        scan.finished_at = _now()
        scan.stats = stats
        scan.changed_files = checkout.changed_files
        db.add(AuditEvent(
            workspace_id=repo.workspace_id, actor="scanner", actor_type="system",
            action="repository.scanned", object_type="repository", object_id=repo.id,
            detail={"commit": checkout.commit, "mode": mode, "trigger": trigger,
                    "nodes": writer.created_nodes + writer.updated_nodes},
        ))
        db.commit()
        if progress:
            progress(100, "completed")
        return {"scan_id": scan.id, "status": "COMPLETED", "stats": stats}

    except Exception as exc:
        db.rollback()
        scan = db.get(ScanRun, scan.id) or scan
        scan.status = "FAILED"
        scan.finished_at = _now()
        scan.error = str(exc)[:2000]
        repo = db.get(Repository, repo.id)
        if repo:
            repo.status = "ERROR"
            repo.error = str(exc)[:1000]
        db.add(AuditEvent(
            workspace_id=scan.workspace_id, actor="scanner", actor_type="system",
            action="repository.scan_failed", object_type="repository", object_id=repo.id if repo else None,
            detail={"error": str(exc)[:500], "trace": traceback.format_exc()[-1500:]},
        ))
        db.commit()
        raise
