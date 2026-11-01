from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, ensure_workspace_access, get_principal
from ..jobs import enqueue
from ..models import (
    AuditEvent,
    IndexedFile,
    Job,
    Repository,
    ScanRun,
)
from ..security import decrypt_secret, encrypt_secret, generate_api_key, safe_slug, validate_outbound_url

router = APIRouter(prefix="/api", tags=["repositories"])


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = ""
    branch: str = "main"
    provider: str = "auto"
    credential: str | None = None
    auto_sync: bool = True
    scan_now: bool = True


class RepositoryUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    branch: str | None = None
    credential: str | None = None
    auto_sync: bool | None = None
    settings: dict | None = None


class ScanRequest(BaseModel):
    mode: str = "incremental"


def detect_provider(url: str) -> str:
    if not url:
        return "local"
    if "github.com" in url:
        return "github"
    if "gitlab" in url:
        return "gitlab"
    return "local"


def repo_out(repo: Repository, db: Session) -> dict:
    counts = db.execute(
        select(IndexedFile.language, func.count(IndexedFile.id))
        .where(IndexedFile.repository_id == repo.id).group_by(IndexedFile.language)
    ).all()
    last_scan = db.scalar(
        select(ScanRun).where(ScanRun.repository_id == repo.id).order_by(ScanRun.created_at.desc()).limit(1)
    )
    return {
        "id": repo.id, "workspace_id": repo.workspace_id, "name": repo.name, "url": repo.url,
        "provider": repo.provider, "branch": repo.branch, "default_branch": repo.default_branch,
        "last_commit": repo.last_commit, "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None,
        "status": repo.status, "auto_sync": repo.auto_sync, "language": repo.language,
        "error": repo.error, "settings": repo.settings,
        "credentials_configured": bool(repo.credential_enc),
        "webhook_configured": bool(repo.webhook_secret_enc),
        "languages": {lang: int(count) for lang, count in counts if lang},
        "last_scan": {
            "id": last_scan.id, "status": last_scan.status, "mode": last_scan.mode,
            "created_at": last_scan.created_at.isoformat(),
            "finished_at": last_scan.finished_at.isoformat() if last_scan.finished_at else None,
            "stats": last_scan.stats, "error": last_scan.error,
        } if last_scan else None,
        "created_at": repo.created_at.isoformat(),
    }


@router.get("/workspaces/{workspace_id}/repositories")
def list_repositories(workspace_id: str, principal: Principal = Depends(get_principal),
                      db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    repos = db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)
                       .order_by(Repository.name)).all()
    return [repo_out(r, db) for r in repos]


@router.post("/workspaces/{workspace_id}/repositories", status_code=201)
def add_repository(workspace_id: str, payload: RepositoryCreate,
                   principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "ARCHITECT")
    if payload.url and payload.url.startswith(("http://", "https://")) and "github" not in payload.url and "gitlab" not in payload.url:
        try:
            validate_outbound_url(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if db.scalar(select(Repository).where(Repository.workspace_id == workspace_id,
                                          Repository.name == payload.name)):
        raise HTTPException(status_code=409, detail="A repository with this name already exists")
    provider = payload.provider if payload.provider != "auto" else detect_provider(payload.url)
    repo = Repository(
        workspace_id=workspace_id, name=payload.name, url=payload.url, provider=provider,
        branch=payload.branch or "main", default_branch=payload.branch or "main",
        credential_enc=encrypt_secret(payload.credential), auto_sync=payload.auto_sync,
        status="PENDING", settings={},
    )
    db.add(repo)
    db.flush()
    job_id = None
    if payload.scan_now:
        job = enqueue(db, "scan_repository",
                      {"repository_id": repo.id, "mode": "full", "trigger": "initial"},
                      workspace_id=workspace_id, priority=3)
        job_id = job.id
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email, action="repository.added",
                      object_type="repository", object_id=repo.id,
                      detail={"name": repo.name, "url": repo.url, "provider": provider}))
    db.commit()
    return {**repo_out(repo, db), "job_id": job_id}


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id)
    return repo_out(repo, db)


@router.patch("/repositories/{repository_id}")
def update_repository(repository_id: str, payload: RepositoryUpdate,
                      principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id, "ARCHITECT")
    if payload.name:
        repo.name = payload.name
    if payload.url is not None:
        repo.url = payload.url
        repo.provider = detect_provider(payload.url)
    if payload.branch:
        repo.branch = payload.branch
    if payload.credential is not None:
        repo.credential_enc = encrypt_secret(payload.credential)
    if payload.auto_sync is not None:
        repo.auto_sync = payload.auto_sync
    if payload.settings is not None:
        repo.settings = {**(repo.settings or {}), **payload.settings}
    db.commit()
    return repo_out(repo, db)


@router.delete("/repositories/{repository_id}", status_code=204)
def delete_repository(repository_id: str, principal: Principal = Depends(get_principal),
                      db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id, "ARCHITECT")
    ws_id = repo.workspace_id
    db.delete(repo)
    db.add(AuditEvent(workspace_id=ws_id, actor=principal.user.email, action="repository.removed",
                      object_type="repository", object_id=repository_id))
    db.commit()


@router.post("/repositories/{repository_id}/scan")
def trigger_scan(repository_id: str, payload: ScanRequest,
                 principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id, "DEVELOPER")
    mode = payload.mode if payload.mode in ("full", "incremental") else "incremental"
    job = enqueue(db, "scan_repository",
                  {"repository_id": repo.id, "mode": mode, "trigger": "manual"},
                  workspace_id=repo.workspace_id, priority=3)
    db.add(AuditEvent(workspace_id=repo.workspace_id, actor=principal.user.email,
                      action="repository.scan_requested", object_type="repository", object_id=repo.id,
                      detail={"mode": mode}))
    db.commit()
    return {"job_id": job.id, "status": "QUEUED", "mode": mode}


@router.get("/repositories/{repository_id}/scans")
def scan_history(repository_id: str, limit: int = Query(20, le=100),
                 principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id)
    rows = db.scalars(select(ScanRun).where(ScanRun.repository_id == repository_id)
                      .order_by(ScanRun.created_at.desc()).limit(limit)).all()
    return [
        {"id": s.id, "status": s.status, "mode": s.mode, "trigger": s.trigger,
         "commit": s.commit_sha, "previous_commit": s.previous_commit,
         "stats": s.stats, "error": s.error, "changed_files": s.changed_files[:50],
         "started_at": s.started_at.isoformat() if s.started_at else None,
         "finished_at": s.finished_at.isoformat() if s.finished_at else None,
         "created_at": s.created_at.isoformat()}
        for s in rows
    ]


@router.get("/repositories/{repository_id}/files")
def repository_files(repository_id: str, q: str | None = None, limit: int = Query(100, le=500),
                     principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id)
    stmt = select(IndexedFile).where(IndexedFile.repository_id == repository_id)
    if q:
        stmt = stmt.where(IndexedFile.path.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(IndexedFile.path).limit(limit)).all()
    return [
        {"path": r.path, "language": r.language, "size_bytes": r.size_bytes,
         "node_count": r.node_count, "commit_sha": r.commit_sha,
         "indexed_at": r.indexed_at.isoformat()}
        for r in rows
    ]


@router.post("/repositories/{repository_id}/webhook-secret")
def rotate_webhook_secret(repository_id: str, principal: Principal = Depends(get_principal),
                          db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id, "WORKSPACE_ADMIN")
    secret, _, _ = generate_api_key()
    repo.webhook_secret_enc = encrypt_secret(secret)
    db.commit()
    base = "https://your-archgraph-host"
    return {
        "secret": secret,
        "warning": "Store this secret in GitHub/GitLab now; it cannot be retrieved later.",
        "github_webhook_url": f"{base}/api/webhooks/github/{repo.id}",
        "gitlab_webhook_url": f"{base}/api/webhooks/gitlab/{repo.id}",
        "events": ["push", "pull_request"],
    }


@router.get("/repositories/{repository_id}/webhook")
def webhook_info(repository_id: str, principal: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    ensure_workspace_access(db, principal, repo.workspace_id)
    return {
        "configured": bool(repo.webhook_secret_enc),
        "github_webhook_url": f"/api/webhooks/github/{repo.id}",
        "gitlab_webhook_url": f"/api/webhooks/gitlab/{repo.id}",
        "events": ["push", "pull_request"],
        "signature_header": "X-Hub-Signature-256" if repo.provider == "github" else "X-Gitlab-Token",
    }
