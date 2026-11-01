from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, accessible_workspace_ids, ensure_workspace_access, get_principal
from ..graphstore import workspace_stats
from ..models import (
    AuditEvent,
    Document,
    Edge,
    Enrichment,
    Node,
    Repository,
    ScanRun,
    User,
    Workspace,
    WorkspaceMember,
    new_id,
)
from ..queries import flows, list_services, workspace_health
from ..security import safe_slug

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    slug: str | None = None
    settings: dict | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    settings: dict | None = None


class MemberAdd(BaseModel):
    user_id: str
    role: str = "DEVELOPER"


def workspace_out(w: Workspace) -> dict:
    return {
        "id": w.id, "name": w.name, "slug": w.slug, "description": w.description,
        "owner_id": w.owner_id, "settings": w.settings,
        "created_at": w.created_at.isoformat(), "updated_at": w.updated_at.isoformat(),
    }


@router.get("")
def list_workspaces(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ids = accessible_workspace_ids(db, principal)
    if not ids:
        return []
    workspaces = list(db.scalars(select(Workspace).where(Workspace.id.in_(ids)).order_by(Workspace.name)).all())
    out = []
    for w in workspaces:
        stats = workspace_stats(db, w.id)
        repos = db.scalar(select(func.count(Repository.id)).where(Repository.workspace_id == w.id, Repository.status != "PENDING")) or 0
        out.append({
            **workspace_out(w),
            "repository_count": int(repos),
            "service_count": int(stats["node_counts"].get("SERVICE", 0)),
            "api_count": int(stats["node_counts"].get("API_ENDPOINT", 0)),
            "topic_count": int(stats["node_counts"].get("KAFKA_TOPIC", 0)),
        })
    return out


@router.post("", status_code=201)
def create_workspace(payload: WorkspaceCreate, principal: Principal = Depends(get_principal),
                     db: Session = Depends(get_db)):
    slug = safe_slug(payload.slug or payload.name)
    if db.scalar(select(Workspace).where(Workspace.slug == slug)):
        slug = f"{slug}-{new_id()[:4]}"
    ws = Workspace(
        name=payload.name, slug=slug, description=payload.description,
        owner_id=principal.user.id,
        settings=payload.settings or {"ai_enabled": True, "auto_sync": True},
    )
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=principal.user.id, role="WORKSPACE_ADMIN"))
    db.add(AuditEvent(workspace_id=ws.id, actor=principal.user.email, action="workspace.created",
                      object_type="workspace", object_id=ws.id, detail={"name": ws.name}))
    db.commit()
    return workspace_out(ws)


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, principal: Principal = Depends(get_principal),
                  db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id)
    return {**workspace_out(ws), "stats": workspace_stats(db, workspace_id)}


@router.patch("/{workspace_id}")
def update_workspace(workspace_id: str, payload: WorkspaceUpdate,
                     principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    if payload.name:
        ws.name = payload.name
    if payload.description is not None:
        ws.description = payload.description
    if payload.settings is not None:
        ws.settings = payload.settings
    db.add(AuditEvent(workspace_id=ws.id, actor=principal.user.email, action="workspace.updated",
                      object_type="workspace", object_id=ws.id))
    db.commit()
    return workspace_out(ws)


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str, principal: Principal = Depends(get_principal),
                     db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    db.delete(ws)
    db.commit()


@router.get("/{workspace_id}/overview")
def overview(workspace_id: str, principal: Principal = Depends(get_principal),
             db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id)
    stats = workspace_stats(db, workspace_id)
    health = workspace_health(db, workspace_id)
    services = list_services(db, workspace_id)[:8]
    enrichment_rows = db.execute(
        select(Enrichment.status, func.count(Enrichment.id))
        .where(Enrichment.workspace_id == workspace_id).group_by(Enrichment.status)
    ).all()
    recent_scans = list(db.scalars(
        select(ScanRun).where(ScanRun.workspace_id == workspace_id)
        .order_by(ScanRun.created_at.desc()).limit(6)
    ).all())
    recent_nodes = list(db.scalars(
        select(Node).where(Node.workspace_id == workspace_id, Node.status == "ACTIVE")
        .order_by(Node.last_seen.desc()).limit(8)
    ).all())
    docs = db.scalar(select(func.count(Document.id)).where(Document.workspace_id == workspace_id)) or 0
    return {
        "workspace": workspace_out(ws),
        "stats": stats,
        "health": health,
        "services": services,
        "flows": flows(db, workspace_id)[:6],
        "ai": {status: int(count) for status, count in enrichment_rows},
        "documents": int(docs),
        "recent_scans": [
            {"id": s.id, "status": s.status, "mode": s.mode, "trigger": s.trigger,
             "commit": s.commit_sha, "created_at": s.created_at.isoformat(),
             "finished_at": s.finished_at.isoformat() if s.finished_at else None,
             "stats": s.stats}
            for s in recent_scans
        ],
        "recent_changes": [
            {"id": n.id, "type": n.type, "name": n.name, "file": n.file_path,
             "seen": n.last_seen.isoformat(), "revision": n.source_revision}
            for n in recent_nodes
        ],
    }


@router.get("/{workspace_id}/members")
def members(workspace_id: str, principal: Principal = Depends(get_principal),
            db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows = db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)).all()
    out = []
    for m in rows:
        user = db.get(User, m.user_id)
        out.append({"id": m.id, "user_id": m.user_id, "role": m.role,
                    "name": user.name if user else None, "email": user.email if user else None})
    return out


@router.post("/{workspace_id}/members", status_code=201)
def add_member(workspace_id: str, payload: MemberAdd,
               principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    existing = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == payload.user_id))
    if existing:
        existing.role = payload.role
    else:
        db.add(WorkspaceMember(workspace_id=workspace_id, user_id=payload.user_id, role=payload.role))
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email, action="workspace.member_added",
                      object_type="user", object_id=payload.user_id, detail={"role": payload.role}))
    db.commit()
    return {"ok": True}


@router.delete("/{workspace_id}/members/{member_id}", status_code=204)
def remove_member(workspace_id: str, member_id: str,
                  principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    member = db.get(WorkspaceMember, member_id)
    if member and member.workspace_id == workspace_id:
        db.delete(member)
        db.commit()


@router.get("/{workspace_id}/audit")
def workspace_audit(workspace_id: str, limit: int = Query(50, le=200),
                    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows = list(db.scalars(
        select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc()).limit(limit)
    ).all())
    return [
        {"id": r.id, "actor": r.actor, "actor_type": r.actor_type, "action": r.action,
         "object_type": r.object_type, "object_id": r.object_id, "detail": r.detail,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]
