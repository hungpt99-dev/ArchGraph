"""Synchronisation endpoints: GitHub/GitLab webhooks, job control, sync history.

Webhooks are verified, idempotent and never trusted for content: the payload only
tells us *which* commit to look at; the scanner then fetches the repository
through the provider adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, ensure_workspace_access, get_principal
from ..jobs import enqueue
from ..models import AuditEvent, Job, Repository, ScanRun, WebhookEvent
from ..security import decrypt_secret

router = APIRouter(prefix="/api", tags=["sync"])


class SyncAllRequest(BaseModel):
    mode: str = "incremental"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/webhooks/github/{repository_id}")
async def github_webhook(
    repository_id: str,
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    db: Session = Depends(get_db),
):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Unknown repository")
    secret = decrypt_secret(repo.webhook_secret_enc)
    if not secret:
        raise HTTPException(status_code=403, detail="Webhook is not configured for this repository")
    body = await request.body()
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")
    delivery = x_github_delivery or hashlib.sha256(body).hexdigest()[:40]
    if db.scalar(select(WebhookEvent).where(WebhookEvent.delivery_id == delivery)):
        return {"status": "duplicate", "delivery_id": delivery}
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    event_type = x_github_event or "unknown"
    record = WebhookEvent(
        workspace_id=repo.workspace_id, repository_id=repo.id, provider="github",
        delivery_id=delivery, event_type=event_type,
        payload={k: payload.get(k) for k in ("ref", "after", "before", "action", "repository") if k in payload},
    )
    db.add(record)
    db.flush()
    if event_type == "ping":
        record.status = "PING"
        record.processed_at = _now()
        db.commit()
        return {"status": "pong", "delivery_id": delivery}
    if event_type not in ("push", "pull_request"):
        record.status = "IGNORED"
        record.processed_at = _now()
        db.commit()
        return {"status": "ignored", "event": event_type}
    if event_type == "pull_request" and payload.get("action") not in ("opened", "synchronize", "closed"):
        record.status = "IGNORED"
        record.processed_at = _now()
        db.commit()
        return {"status": "ignored", "action": payload.get("action")}
    ref = str(payload.get("ref") or "")
    if ref and not ref.endswith(repo.branch) and event_type == "push":
        record.status = "IGNORED"
        record.processed_at = _now()
        db.commit()
        return {"status": "ignored", "reason": f"branch {ref} is not {repo.branch}"}
    job = enqueue(db, "scan_repository",
                  {"repository_id": repo.id, "mode": "incremental", "trigger": "webhook",
                   "commit": payload.get("after")},
                  workspace_id=repo.workspace_id, priority=2)
    record.status = "QUEUED"
    record.processed_at = _now()
    db.add(AuditEvent(workspace_id=repo.workspace_id, actor="github-webhook", actor_type="system",
                      action="webhook.push", object_type="repository", object_id=repo.id,
                      detail={"delivery": delivery, "commit": payload.get("after")}))
    db.commit()
    return {"status": "queued", "job_id": job.id, "delivery_id": delivery,
            "commit": payload.get("after")}


@router.post("/webhooks/gitlab/{repository_id}")
async def gitlab_webhook(
    repository_id: str,
    request: Request,
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
    x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    x_gitlab_event_uuid: str | None = Header(default=None, alias="X-Gitlab-Event-UUID"),
    db: Session = Depends(get_db),
):
    repo = db.get(Repository, repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Unknown repository")
    secret = decrypt_secret(repo.webhook_secret_enc)
    if not secret:
        raise HTTPException(status_code=403, detail="Webhook is not configured for this repository")
    if not x_gitlab_token or not hmac.compare_digest(secret, x_gitlab_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    body = await request.body()
    delivery = x_gitlab_event_uuid or hashlib.sha256(body).hexdigest()[:40]
    if db.scalar(select(WebhookEvent).where(WebhookEvent.delivery_id == delivery)):
        return {"status": "duplicate", "delivery_id": delivery}
    payload = json.loads(body or b"{}")
    event_type = (x_gitlab_event or "unknown").lower()
    record = WebhookEvent(
        workspace_id=repo.workspace_id, repository_id=repo.id, provider="gitlab",
        delivery_id=delivery, event_type=event_type,
        payload={k: payload.get(k) for k in ("ref", "after", "before", "object_kind", "checkout_sha") if k in payload},
    )
    db.add(record)
    db.flush()
    if event_type not in ("push hook", "merge request hook", "push"):
        record.status = "IGNORED"
        record.processed_at = _now()
        db.commit()
        return {"status": "ignored", "event": event_type}
    ref = str(payload.get("ref") or "")
    if ref and event_type != "merge request hook" and not ref.endswith(repo.branch):
        record.status = "IGNORED"
        record.processed_at = _now()
        db.commit()
        return {"status": "ignored", "reason": f"branch {ref} is not {repo.branch}"}
    job = enqueue(db, "scan_repository",
                  {"repository_id": repo.id, "mode": "incremental", "trigger": "webhook",
                   "commit": payload.get("checkout_sha") or payload.get("after")},
                  workspace_id=repo.workspace_id, priority=2)
    record.status = "QUEUED"
    record.processed_at = _now()
    db.add(AuditEvent(workspace_id=repo.workspace_id, actor="gitlab-webhook", actor_type="system",
                      action="webhook.push", object_type="repository", object_id=repo.id,
                      detail={"delivery": delivery}))
    db.commit()
    return {"status": "queued", "job_id": job.id, "delivery_id": delivery}


@router.get("/workspaces/{workspace_id}/sync/events")
def sync_events(workspace_id: str, limit: int = Query(50, le=200),
                principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    scans = list(db.scalars(select(ScanRun).where(ScanRun.workspace_id == workspace_id)
                            .order_by(ScanRun.created_at.desc()).limit(limit)).all())
    hooks = list(db.scalars(select(WebhookEvent).where(WebhookEvent.workspace_id == workspace_id)
                            .order_by(WebhookEvent.received_at.desc()).limit(limit)).all())
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == workspace_id)
                           .order_by(Job.created_at.desc()).limit(limit)).all())
    return {
        "scans": [
            {"id": s.id, "repository_id": s.repository_id, "status": s.status, "mode": s.mode,
             "trigger": s.trigger, "commit": s.commit_sha, "previous_commit": s.previous_commit,
             "changed_files": s.changed_files, "stats": s.stats, "error": s.error,
             "created_at": s.created_at.isoformat(),
             "finished_at": s.finished_at.isoformat() if s.finished_at else None}
            for s in scans
        ],
        "webhooks": [
            {"id": h.id, "provider": h.provider, "event_type": h.event_type,
             "delivery_id": h.delivery_id, "status": h.status,
             "received_at": h.received_at.isoformat(), "error": h.error}
            for h in hooks
        ],
        "jobs": [
            {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress,
             "message": j.message, "attempts": j.attempts, "error": j.error,
             "created_at": j.created_at.isoformat(),
             "finished_at": j.finished_at.isoformat() if j.finished_at else None}
            for j in jobs
        ],
    }


@router.post("/workspaces/{workspace_id}/sync/all", status_code=202)
def sync_all(workspace_id: str, payload: SyncAllRequest,
             principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "DEVELOPER")
    repos = list(db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)).all())
    if not repos:
        raise HTTPException(status_code=400, detail="No repositories in this workspace")
    job_ids = []
    for repo in repos:
        job = enqueue(db, "scan_repository",
                      {"repository_id": repo.id, "mode": payload.mode, "trigger": "manual"},
                      workspace_id=workspace_id, priority=5)
        job_ids.append({"repository_id": repo.id, "job_id": job.id})
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="workspace.sync_all", object_type="workspace", object_id=workspace_id,
                      detail={"repositories": len(repos), "mode": payload.mode}))
    db.commit()
    return {"jobs": job_ids, "count": len(job_ids)}


@router.get("/jobs")
def list_jobs(workspace_id: str | None = None, status: str | None = None,
              limit: int = Query(50, le=200),
              principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(Job)
    if workspace_id:
        ensure_workspace_access(db, principal, workspace_id)
        stmt = stmt.where(Job.workspace_id == workspace_id)
    elif not principal.is_admin:
        from ..deps import accessible_workspace_ids

        stmt = stmt.where(Job.workspace_id.in_(accessible_workspace_ids(db, principal)))
    if status:
        stmt = stmt.where(Job.status == status)
    rows = db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit)).all()
    return [
        {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress,
         "message": j.message, "attempts": j.attempts, "max_attempts": j.max_attempts,
         "error": j.error, "workspace_id": j.workspace_id, "payload": j.payload,
         "created_at": j.created_at.isoformat(),
         "finished_at": j.finished_at.isoformat() if j.finished_at else None}
        for j in rows
    ]


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, principal: Principal = Depends(get_principal),
              db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.workspace_id:
        ensure_workspace_access(db, principal, job.workspace_id, "DEVELOPER")
    job.status = "QUEUED"
    job.attempts = 0
    job.error = None
    job.run_after = _now()
    db.commit()
    return {"id": job.id, "status": "QUEUED"}
