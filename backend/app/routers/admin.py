from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal, require_role
from ..models import AuditEvent, Job, User, Workspace
from ..security import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: str = "DEVELOPER"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@router.get("/users")
def list_users(principal: Principal = Depends(require_role("SYSTEM_ADMIN")),
               db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.created_at)).all()
    return [
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
         "is_active": u.is_active, "created_at": u.created_at.isoformat(),
         "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
        for u in rows
    ]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, principal: Principal = Depends(require_role("SYSTEM_ADMIN")),
                db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=payload.email.lower(), name=payload.name,
                password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.add(AuditEvent(actor=principal.user.email, action="user.created", object_type="user",
                      object_id=user.id, detail={"role": payload.role}))
    db.commit()
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate,
                principal: Principal = Depends(require_role("SYSTEM_ADMIN")),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.name:
        user.name = payload.name
    if payload.role:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    db.add(AuditEvent(actor=principal.user.email, action="user.updated", object_type="user",
                      object_id=user.id, detail={"role": user.role, "active": user.is_active}))
    db.commit()
    return {"id": user.id, "role": user.role, "is_active": user.is_active}


@router.get("/audit")
def audit(workspace_id: str | None = None, action: str | None = None,
          limit: int = Query(100, le=500),
          principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    stmt = select(AuditEvent)
    if workspace_id:
        from ..deps import ensure_workspace_access

        ensure_workspace_access(db, principal, workspace_id)
        stmt = stmt.where(AuditEvent.workspace_id == workspace_id)
    elif not principal.is_admin:
        raise HTTPException(status_code=403, detail="System-wide audit requires SYSTEM_ADMIN")
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [
        {"id": r.id, "workspace_id": r.workspace_id, "actor": r.actor, "actor_type": r.actor_type,
         "action": r.action, "object_type": r.object_type, "object_id": r.object_id,
         "detail": r.detail, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@router.get("/system")
def system_info(principal: Principal = Depends(require_role("SYSTEM_ADMIN")),
                db: Session = Depends(get_db)):
    from ..config import get_settings

    settings = get_settings()
    counts = {
        "users": db.scalar(select(func.count(User.id))) or 0,
        "workspaces": db.scalar(select(func.count(Workspace.id))) or 0,
        "jobs": db.scalar(select(func.count(Job.id))) or 0,
    }
    return {
        "version": settings.version,
        "environment": settings.env,
        "demo_mode": settings.demo_mode,
        "ai_required": False,
        "counts": counts,
        "security": {
            "password_hashing": "scrypt (n=16384,r=8,p=1)",
            "tokens": "JWT HS256",
            "credentials": "Fernet-encrypted at rest",
            "webhook_verification": "github HMAC-SHA256 / gitlab token",
            "ssrf_protection": not settings.allow_private_networks,
        },
    }
