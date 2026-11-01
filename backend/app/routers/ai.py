from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.enrich import (
    CATEGORY_SPECS,
    collect_context,
    create_enrichments,
    estimate,
    usage_summary,
)
from ..ai.providers import get_provider, test_provider
from ..db import get_db
from ..deps import Principal, ensure_workspace_access, get_principal
from ..jobs import enqueue
from ..models import AiProvider, AuditEvent, Document, Edge, Enrichment, Node
from ..security import encrypt_secret

router = APIRouter(prefix="/api", tags=["ai"])


class ProviderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    enabled: bool = True
    is_default: bool = True
    workspace_id: str | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class Target(BaseModel):
    type: str = "node"
    id: str


class EnrichRequest(BaseModel):
    targets: list[Target]
    categories: list[str]
    note: str = ""


class ReviewRequest(BaseModel):
    note: str = ""


def provider_out(p: AiProvider) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url, "model": p.model,
        "enabled": p.enabled, "is_default": p.is_default, "workspace_id": p.workspace_id,
        "api_key_configured": bool(p.api_key_enc), "last_status": p.last_status,
        "last_checked_at": p.last_checked_at.isoformat() if p.last_checked_at else None,
    }


def enrichment_out(e: Enrichment, include_content: bool = False) -> dict:
    data = {
        "id": e.id, "workspace_id": e.workspace_id, "target_type": e.target_type,
        "target_id": e.target_id, "target_name": e.target_name, "category": e.category,
        "status": e.status, "summary": e.summary, "confidence": e.confidence,
        "provider": e.provider, "model": e.model, "prompt_version": e.prompt_version,
        "source_revision": e.source_revision, "tokens_in": e.tokens_in, "tokens_out": e.tokens_out,
        "reviewed_by": e.reviewed_by, "review_note": e.review_note,
        "error": e.error,
        "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat(),
    }
    if include_content:
        data["content"] = e.content
        data["evidence"] = e.evidence
    return data


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
@router.get("/workspaces/{workspace_id}/ai/providers")
def list_providers(workspace_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows = db.scalars(select(AiProvider).where(
        (AiProvider.workspace_id == workspace_id) | (AiProvider.workspace_id.is_(None))
    ).order_by(AiProvider.created_at)).all()
    return [provider_out(p) for p in rows]


@router.post("/workspaces/{workspace_id}/ai/providers", status_code=201)
def create_provider(workspace_id: str, payload: ProviderCreate,
                    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    if payload.kind not in ("openai", "anthropic", "ollama", "mock"):
        raise HTTPException(status_code=400, detail="Unsupported provider kind")
    if payload.kind in ("openai", "anthropic") and not payload.api_key:
        raise HTTPException(status_code=400, detail="api_key is required for this provider kind")
    if payload.is_default:
        for existing in db.scalars(select(AiProvider).where(AiProvider.workspace_id == workspace_id)).all():
            existing.is_default = False
    record = AiProvider(
        workspace_id=workspace_id, name=payload.name, kind=payload.kind,
        base_url=payload.base_url, model=payload.model,
        api_key_enc=encrypt_secret(payload.api_key), enabled=payload.enabled,
        is_default=payload.is_default, config={},
    )
    db.add(record)
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="ai.provider_created", object_type="ai_provider", object_id=record.id,
                      detail={"kind": payload.kind, "model": payload.model}))
    db.commit()
    return provider_out(record)


@router.patch("/ai/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderUpdate,
                    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    record = db.get(AiProvider, provider_id)
    if not record:
        raise HTTPException(status_code=404, detail="Provider not found")
    ensure_workspace_access(db, principal, record.workspace_id or "", "WORKSPACE_ADMIN") if record.workspace_id else None
    if payload.name:
        record.name = payload.name
    if payload.base_url is not None:
        record.base_url = payload.base_url
    if payload.model is not None:
        record.model = payload.model
    if payload.api_key:
        record.api_key_enc = encrypt_secret(payload.api_key)
    if payload.enabled is not None:
        record.enabled = payload.enabled
    if payload.is_default:
        for existing in db.scalars(select(AiProvider).where(AiProvider.workspace_id == record.workspace_id)).all():
            existing.is_default = False
        record.is_default = True
    db.commit()
    return provider_out(record)


@router.delete("/ai/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, principal: Principal = Depends(get_principal),
                    db: Session = Depends(get_db)):
    record = db.get(AiProvider, provider_id)
    if not record:
        raise HTTPException(status_code=404, detail="Provider not found")
    if record.workspace_id:
        ensure_workspace_access(db, principal, record.workspace_id, "WORKSPACE_ADMIN")
    db.delete(record)
    db.commit()


@router.post("/ai/providers/{provider_id}/test")
def check_provider(provider_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    record = db.get(AiProvider, provider_id)
    if not record:
        raise HTTPException(status_code=404, detail="Provider not found")
    if record.workspace_id:
        ensure_workspace_access(db, principal, record.workspace_id, "WORKSPACE_ADMIN")
    result = test_provider(record)
    record.last_checked_at = datetime.now(timezone.utc)
    record.last_status = "OK" if result.get("ok") else f"ERROR: {result.get('error', '')}"[:120]
    db.commit()
    return result


# --------------------------------------------------------------------------- #
# Status / estimate / enrichment
# --------------------------------------------------------------------------- #
@router.get("/workspaces/{workspace_id}/ai/status")
def ai_status(workspace_id: str, principal: Principal = Depends(get_principal),
              db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id)
    provider = get_provider(db, workspace_id)
    usage = usage_summary(db, workspace_id)
    providers = db.scalars(select(AiProvider).where(
        (AiProvider.workspace_id == workspace_id) | (AiProvider.workspace_id.is_(None))
    )).all()
    return {
        "ai_enabled": provider is not None and bool((ws.settings or {}).get("ai_enabled", True)),
        "provider_configured": provider is not None,
        "provider": {"name": provider.name, "kind": provider.kind, "model": provider.model}
        if provider else None,
        "provider_kind": provider.kind if provider else None,
        "providers": [provider_out(p) for p in providers],
        "usage": usage,
        "policy": {
            "ai_enabled": bool((ws.settings or {}).get("ai_enabled", True)),
            "allowed_categories": (ws.settings or {}).get("ai_allowed_categories") or [],
            "require_approval": bool((ws.settings or {}).get("ai_require_approval", True)),
        },
        "categories": [{"key": k, "label": v["label"], "targets": v["targets"]}
                       for k, v in CATEGORY_SPECS.items()],
    }


@router.patch("/workspaces/{workspace_id}/ai/policy")
def update_policy(workspace_id: str, payload: dict,
                  principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    ws.settings = {**(ws.settings or {}), **{k: v for k, v in payload.items() if k.startswith("ai_")}}
    db.commit()
    return {"settings": ws.settings}


@router.post("/workspaces/{workspace_id}/ai/estimate")
def enrichment_estimate(workspace_id: str, payload: EnrichRequest,
                        principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    targets = [(t.type, t.id) for t in payload.targets]
    return estimate(db, workspace_id, targets, payload.categories)


@router.post("/workspaces/{workspace_id}/ai/enrich", status_code=202)
def request_enrichment(workspace_id: str, payload: EnrichRequest,
                       principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ws = ensure_workspace_access(db, principal, workspace_id, "ARCHITECT")
    provider = get_provider(db, workspace_id)
    if provider is None:
        raise HTTPException(status_code=409,
                            detail="AI is disabled: configure an enabled provider first (core features do not need it)")
    if not (ws.settings or {}).get("ai_enabled", True):
        raise HTTPException(status_code=409, detail="AI is disabled for this workspace")
    if not payload.targets:
        raise HTTPException(status_code=400, detail="Select at least one target")
    if not payload.categories:
        raise HTTPException(status_code=400, detail="Select at least one enrichment category")
    allowed = (ws.settings or {}).get("ai_allowed_categories") or []
    categories = [c for c in payload.categories if not allowed or c in allowed]
    if not categories:
        raise HTTPException(status_code=400, detail="None of the requested categories are allowed by policy")
    valid_targets = []
    for t in payload.targets:
        if t.type == "node" and db.get(Node, t.id):
            valid_targets.append(("node", t.id))
        elif t.type == "document" and db.get(Document, t.id):
            valid_targets.append(("document", t.id))
        elif t.type == "edge" and db.get(Edge, t.id):
            valid_targets.append(("edge", t.id))
    if not valid_targets:
        raise HTTPException(status_code=404, detail="No valid targets")
    est = estimate(db, workspace_id, valid_targets, categories)
    records = create_enrichments(db, workspace_id, valid_targets, categories, principal.user.email)
    job = enqueue(db, "run_enrichment",
                  {"enrichment_ids": [r.id for r in records if r.status == "QUEUED"],
                   "requested_by": principal.user.email},
                  workspace_id=workspace_id, priority=4)
    for record in records:
        if record.status == "QUEUED":
            record.job_id = job.id
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="ai.enrichment_requested", object_type="enrichment",
                      object_id=",".join(r.id for r in records)[:64],
                      detail={"targets": len(valid_targets), "categories": categories,
                              "estimate": est["estimated_total_tokens"]}))
    db.commit()
    return {"job_id": job.id, "enrichment_ids": [r.id for r in records], "estimate": est,
            "queued": len([r for r in records if r.status == "QUEUED"])}


@router.get("/workspaces/{workspace_id}/ai/enrichments")
def list_enrichments(workspace_id: str, status: str | None = None,
                     target_id: str | None = None, category: str | None = None,
                     limit: int = Query(100, le=400),
                     principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    stmt = select(Enrichment).where(Enrichment.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Enrichment.status == status)
    if target_id:
        stmt = stmt.where(Enrichment.target_id == target_id)
    if category:
        stmt = stmt.where(Enrichment.category == category)
    rows = db.scalars(stmt.order_by(Enrichment.updated_at.desc()).limit(limit)).all()
    return [enrichment_out(e) for e in rows]


@router.get("/ai/enrichments/{enrichment_id}")
def get_enrichment(enrichment_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    record = db.get(Enrichment, enrichment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Enrichment not found")
    ensure_workspace_access(db, principal, record.workspace_id)
    return enrichment_out(record, include_content=True)


@router.post("/ai/enrichments/{enrichment_id}/approve")
def approve(enrichment_id: str, payload: ReviewRequest,
            principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    record = db.get(Enrichment, enrichment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Enrichment not found")
    ensure_workspace_access(db, principal, record.workspace_id, "ARCHITECT")
    if record.status not in ("GENERATED", "NEEDS_REVIEW", "STALE"):
        raise HTTPException(status_code=409, detail=f"Cannot approve from status {record.status}")
    record.status = "APPROVED"
    record.reviewed_by = principal.user.email
    record.review_note = payload.note
    record.reviewed_at = datetime.now(timezone.utc)
    db.add(AuditEvent(workspace_id=record.workspace_id, actor=principal.user.email,
                      action="ai.enrichment_approved", object_type="enrichment",
                      object_id=record.id, detail={"category": record.category}))
    db.commit()
    return enrichment_out(record, include_content=True)


@router.post("/ai/enrichments/{enrichment_id}/reject")
def reject(enrichment_id: str, payload: ReviewRequest,
           principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    record = db.get(Enrichment, enrichment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Enrichment not found")
    ensure_workspace_access(db, principal, record.workspace_id, "ARCHITECT")
    record.status = "REJECTED"
    record.reviewed_by = principal.user.email
    record.review_note = payload.note
    record.reviewed_at = datetime.now(timezone.utc)
    db.add(AuditEvent(workspace_id=record.workspace_id, actor=principal.user.email,
                      action="ai.enrichment_rejected", object_type="enrichment",
                      object_id=record.id, detail={"note": payload.note}))
    db.commit()
    return enrichment_out(record)


@router.patch("/ai/enrichments/{enrichment_id}")
def edit_enrichment(enrichment_id: str, payload: dict,
                    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    record = db.get(Enrichment, enrichment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Enrichment not found")
    ensure_workspace_access(db, principal, record.workspace_id, "ARCHITECT")
    content = payload.get("content") or {}
    if not isinstance(content, dict) or not content.get("summary"):
        raise HTTPException(status_code=400, detail="content.summary is required")
    record.content = {**(record.content or {}), **content, "edited_by": principal.user.email}
    record.summary = str(content.get("summary"))[:600]
    record.status = "NEEDS_REVIEW"
    db.add(AuditEvent(workspace_id=record.workspace_id, actor=principal.user.email,
                      action="ai.enrichment_edited", object_type="enrichment", object_id=record.id))
    db.commit()
    return enrichment_out(record, include_content=True)


@router.post("/ai/enrichments/{enrichment_id}/reenrich", status_code=202)
def reenrich(enrichment_id: str, principal: Principal = Depends(get_principal),
             db: Session = Depends(get_db)):
    record = db.get(Enrichment, enrichment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Enrichment not found")
    ensure_workspace_access(db, principal, record.workspace_id, "ARCHITECT")
    record.status = "QUEUED"
    record.error = None
    job = enqueue(db, "run_enrichment", {"enrichment_ids": [record.id]},
                  workspace_id=record.workspace_id, priority=3)
    record.job_id = job.id
    db.commit()
    return {"job_id": job.id, "status": "QUEUED"}


@router.get("/workspaces/{workspace_id}/ai/usage")
def ai_usage(workspace_id: str, principal: Principal = Depends(get_principal),
             db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    usage = usage_summary(db, workspace_id)
    by_category = db.execute(
        select(Enrichment.category, func.count(Enrichment.id),
               func.coalesce(func.sum(Enrichment.tokens_in), 0),
               func.coalesce(func.sum(Enrichment.tokens_out), 0))
        .where(Enrichment.workspace_id == workspace_id).group_by(Enrichment.category)
    ).all()
    usage["by_category"] = [
        {"category": c, "count": int(n), "tokens_in": int(ti), "tokens_out": int(to)}
        for c, n, ti, to in by_category
    ]
    return usage


@router.get("/workspaces/{workspace_id}/ai/preview")
def enrichment_preview(workspace_id: str, target_type: str = "node", target_id: str = "",
                       principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    if target_type == "node" and not db.get(Node, target_id):
        raise HTTPException(status_code=404, detail="Target not found")
    ctx = collect_context(db, workspace_id, target_type, target_id)
    return {
        "target": ctx.get("target"),
        "facts": ctx.get("facts", []),
        "edges": ctx.get("edges", [])[:20],
        "source_excerpts": [
            {"file": c["file"], "lines": f"{c['start_line']}-{c['end_line']}",
             "snippet": c["snippet"][:1200]}
            for c in ctx.get("code", [])
        ],
        "evidence": ctx.get("evidence", []),
        "context_chars": len(str(ctx)),
    }
