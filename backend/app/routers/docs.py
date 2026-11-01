from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, ensure_workspace_access, get_principal
from ..docs.connectors import (
    extract_upload,
    link_documents,
    sync_confluence,
    sync_jira,
    sync_repository_docs,
    upsert_document,
)
from ..jobs import enqueue
from ..models import AuditEvent, Document, DocumentLink, Integration, Node, Repository
from ..queries import document_detail, documents_search
from ..security import decrypt_secret

router = APIRouter(prefix="/api", tags=["documents"])


class SyncRequest(BaseModel):
    targets: list[str] = []  # repositories|jira|confluence
    repository_id: str | None = None
    integration_ids: list[str] = []


class LinkCreate(BaseModel):
    target_id: str
    target_type: str = "node"
    link_type: str = "DOCUMENTS"


@router.get("/workspaces/{workspace_id}/documents")
def list_documents(workspace_id: str, q: str | None = None, source_type: str | None = None,
                   limit: int = Query(100, le=500),
                   principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return documents_search(db, workspace_id, q, source_type, limit)


@router.get("/workspaces/{workspace_id}/documents/stats")
def document_stats(workspace_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows = db.execute(
        select(Document.source_type, func.count(Document.id))
        .where(Document.workspace_id == workspace_id).group_by(Document.source_type)
    ).all()
    links = db.scalar(select(func.count(DocumentLink.id)).where(DocumentLink.workspace_id == workspace_id)) or 0
    return {"by_source": {s: int(c) for s, c in rows},
            "total": sum(int(c) for _, c in rows), "links": int(links)}


@router.get("/documents/{document_id}")
def get_document(document_id: str, principal: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_workspace_access(db, principal, doc.workspace_id)
    return document_detail(db, doc)


@router.post("/workspaces/{workspace_id}/documents/sync", status_code=202)
def sync_documents(workspace_id: str, payload: SyncRequest,
                   principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "DEVELOPER")
    targets = payload.targets or ["repositories"]
    if "repositories" in targets and payload.repository_id:
        repo = db.get(Repository, payload.repository_id)
        if not repo or repo.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Repository not found")
        sync_repository_docs(db, repo)
        links = link_documents(db, workspace_id)
        db.commit()
        return {"status": "COMPLETED", "targets": ["repositories"],
                "repository_id": payload.repository_id, "links": links}
    job = enqueue(db, "sync_documents",
                  {"workspace_id": workspace_id, "targets": targets,
                   "integration_ids": payload.integration_ids},
                  workspace_id=workspace_id, priority=6)
    db.commit()
    return {"job_id": job.id, "status": "QUEUED", "targets": targets}


@router.post("/workspaces/{workspace_id}/documents/upload", status_code=201)
async def upload_document(
    workspace_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    ensure_workspace_access(db, principal, workspace_id, "DEVELOPER")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")
    try:
        text = extract_upload(file.filename or "upload.txt", data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Cannot extract text: {exc}") from exc
    source = "pdf" if (file.filename or "").endswith(".pdf") else "docx" if (file.filename or "").endswith(".docx") else "markdown"
    doc, created = upsert_document(
        db, workspace_id, source, f"upload:{file.filename}:{len(data)}",
        title or file.filename or "Uploaded document", text,
        metadata={"filename": file.filename, "size": len(data)},
    )
    link_documents(db, workspace_id)
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="document.uploaded", object_type="document", object_id=doc.id,
                      detail={"filename": file.filename}))
    db.commit()
    return {"id": doc.id, "title": doc.title, "source_type": doc.source_type, "created": created}


@router.post("/documents/{document_id}/links", status_code=201)
def add_link(document_id: str, payload: LinkCreate,
             principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_workspace_access(db, principal, doc.workspace_id, "DEVELOPER")
    target = db.get(Node, payload.target_id)
    if not target or target.workspace_id != doc.workspace_id:
        raise HTTPException(status_code=404, detail="Target node not found")
    link = DocumentLink(
        workspace_id=doc.workspace_id, document_id=doc.id, target_type="node",
        target_id=target.id, link_type=payload.link_type, confidence=1.0,
        detection_method="MANUAL", approved=True,
    )
    db.add(link)
    db.commit()
    return {"id": link.id, "target": target.name, "approved": True}


@router.post("/document-links/{link_id}/approve")
def approve_link(link_id: str, principal: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    link = db.get(DocumentLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    ensure_workspace_access(db, principal, link.workspace_id, "ARCHITECT")
    link.approved = True
    db.commit()
    return {"id": link.id, "approved": True}


@router.delete("/document-links/{link_id}", status_code=204)
def delete_link(link_id: str, principal: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    link = db.get(DocumentLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    ensure_workspace_access(db, principal, link.workspace_id, "ARCHITECT")
    db.delete(link)
    db.commit()


@router.get("/workspaces/{workspace_id}/integrations")
def list_integrations(workspace_id: str, principal: Principal = Depends(get_principal),
                      db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows = db.scalars(select(Integration).where(
        (Integration.workspace_id == workspace_id) | (Integration.workspace_id.is_(None))
    ).order_by(Integration.kind)).all()
    return [
        {"id": r.id, "kind": r.kind, "name": r.name, "config": r.config, "enabled": r.enabled,
         "status": r.status, "last_error": r.last_error,
         "secret_configured": bool(r.secret_enc),
         "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None}
        for r in rows
    ]


@router.post("/workspaces/{workspace_id}/integrations", status_code=201)
def create_integration(workspace_id: str, payload: dict,
                       principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    kind = payload.get("kind")
    if kind not in ("github", "gitlab", "jira", "confluence", "otel", "mcp_client"):
        raise HTTPException(status_code=400, detail="Unsupported integration kind")
    from ..security import encrypt_secret

    record = Integration(
        workspace_id=workspace_id, kind=kind, name=payload.get("name") or f"{kind} integration",
        config={k: v for k, v in (payload.get("config") or {}).items() if "token" not in k.lower() and "secret" not in k.lower()},
        secret_enc=encrypt_secret(payload.get("secret")),
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(record)
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="integration.created", object_type="integration", object_id=record.id,
                      detail={"kind": kind}))
    db.commit()
    return {"id": record.id, "kind": record.kind, "status": record.status}


@router.post("/workspaces/{workspace_id}/integrations/{integration_id}/test")
def test_integration(workspace_id: str, integration_id: str,
                     principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id, "WORKSPACE_ADMIN")
    record = db.get(Integration, integration_id)
    if not record or record.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Integration not found")
    secret = decrypt_secret(record.secret_enc)
    try:
        if record.kind == "jira":
            from ..docs.connectors import JiraConnector

            connector = JiraConnector(record.config, secret)
            with connector._client() as client:
                resp = client.get("/rest/api/2/myself")
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                data = resp.json()
            result = {"ok": True, "account": data.get("displayName") or data.get("accountId")}
        elif record.kind == "confluence":
            import httpx

            base = (record.config.get("base_url") or "").rstrip("/")
            auth = (record.config.get("email"), secret) if record.config.get("email") else None
            with httpx.Client(timeout=20, auth=auth) as client:
                resp = client.get(f"{base}/wiki/rest/api/space", params={"limit": 1})
            result = {"ok": resp.status_code < 400, "status": resp.status_code}
        else:
            result = {"ok": True, "note": "No connectivity probe for this integration kind"}
        record.status = "CONNECTED" if result.get("ok") else "ERROR"
        record.last_error = None if result.get("ok") else str(result)[:300]
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)[:300]}
        record.status = "ERROR"
        record.last_error = str(exc)[:300]
    record.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    return result
