from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal, require_role
from ..models import ApiKey, AuditEvent, User, Workspace, WorkspaceMember
from ..security import (
    create_access_token,
    generate_api_key,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class BootstrapRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, is_active=user.is_active)


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    has_users = db.scalar(select(User.id).limit(1)) is not None
    return {"initialized": has_users, "bootstrap_required": not has_users}


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).limit(1)):
        raise HTTPException(status_code=409, detail="ArchGraph is already initialized")
    user = User(
        email=payload.email.lower(), name=payload.name,
        password_hash=hash_password(payload.password), role="SYSTEM_ADMIN",
    )
    db.add(user)
    db.flush()
    db.add(AuditEvent(workspace_id=None, actor=user.email, action="auth.bootstrap",
                      object_type="user", object_id=user.id, ip=request.client.host if request.client else None))
    db.commit()
    token = create_access_token(user.id, user.role)
    return {"access_token": token, "token_type": "bearer", "user": user_out(user)}


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditEvent(workspace_id=None, actor=user.email, action="auth.login",
                      object_type="user", object_id=user.id,
                      ip=request.client.host if request.client else None))
    db.commit()
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer",
            "user": user_out(user)}


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    workspaces = list(db.scalars(
        select(Workspace).where(
            (Workspace.owner_id == principal.user.id)
            | (Workspace.id.in_(select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == principal.user.id)))
        )
    ).all())
    return {
        "user": user_out(principal.user),
        "auth_type": principal.auth_type,
        "workspaces": [{"id": w.id, "name": w.name, "slug": w.slug} for w in workspaces],
    }


@router.post("/api-keys")
def create_api_key(
    payload: dict,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    name = str(payload.get("name") or "MCP client")[:120]
    workspace_id = payload.get("workspace_id")
    scopes = payload.get("scopes") or ["read"]
    plaintext, prefix, key_hash = generate_api_key()
    record = ApiKey(
        user_id=principal.user.id, workspace_id=workspace_id, name=name,
        prefix=prefix, key_hash=key_hash, scopes=scopes,
    )
    db.add(record)
    db.add(AuditEvent(workspace_id=workspace_id, actor=principal.user.email,
                      action="api_key.created", object_type="api_key", object_id=record.id,
                      detail={"name": name, "scopes": scopes}))
    db.commit()
    return {"id": record.id, "name": name, "prefix": prefix, "api_key": plaintext,
            "workspace_id": workspace_id, "scopes": scopes,
            "warning": "Store this key now. It is not retrievable later."}


@router.get("/api-keys")
def list_api_keys(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApiKey).where(ApiKey.user_id == principal.user.id)).all()
    return [
        {"id": r.id, "name": r.name, "prefix": r.prefix, "workspace_id": r.workspace_id,
         "scopes": r.scopes, "created_at": r.created_at.isoformat(),
         "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
         "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None}
        for r in rows
    ]


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if not key or (key.user_id != principal.user.id and not principal.is_admin):
        raise HTTPException(status_code=404, detail="API key not found")
    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"revoked": True}
