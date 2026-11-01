from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey, User, Workspace, WorkspaceMember, ROLE_RANK
from .security import decode_access_token, hash_api_key, has_role


@dataclass
class Principal:
    user: User
    workspace_id: str | None = None
    auth_type: str = "user"  # user|api_key
    scopes: list[str] | None = None

    @property
    def is_admin(self) -> bool:
        return has_role(self.user.role, "SYSTEM_ADMIN")


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Principal:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token:
        payload = decode_access_token(token)
        if payload and payload.get("sub"):
            user = db.get(User, payload["sub"])
            if user and user.is_active:
                return Principal(user=user, auth_type="user")
        raise _unauthorized("Invalid or expired token")

    api_key = x_api_key or request.headers.get("X-API-Key")
    if api_key:
        record = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key)))
        if record and record.revoked_at is None:
            user = db.get(User, record.user_id)
            if user and user.is_active:
                record.last_used_at = datetime.now(timezone.utc)
                db.commit()
                return Principal(
                    user=user,
                    workspace_id=record.workspace_id,
                    auth_type="api_key",
                    scopes=record.scopes or [],
                )
        raise _unauthorized("Invalid API key")

    raise _unauthorized()


def require_role(required: str):
    def checker(principal: Principal = Depends(get_principal)) -> Principal:
        if not has_role(principal.user.role, required):
            raise HTTPException(status_code=403, detail=f"Requires role {required} or higher")
        return principal

    return checker


def get_workspace_or_404(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def ensure_workspace_access(
    db: Session, principal: Principal, workspace_id: str, required: str = "VIEWER"
) -> Workspace:
    ws = get_workspace_or_404(db, workspace_id)
    if principal.auth_type == "api_key" and principal.workspace_id:
        if principal.workspace_id != workspace_id:
            raise HTTPException(status_code=403, detail="API key is scoped to another workspace")
        return ws
    if principal.is_admin:
        return ws
    membership = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == principal.user.id,
        )
    )
    effective = membership.role if membership else ("WORKSPACE_ADMIN" if ws.owner_id == principal.user.id else None)
    if effective is None:
        raise HTTPException(status_code=403, detail="No access to this workspace")
    if ROLE_RANK.get(effective, 99) > ROLE_RANK.get(required, 0):
        raise HTTPException(status_code=403, detail=f"Requires {required} in this workspace")
    return ws


def accessible_workspace_ids(db: Session, principal: Principal) -> list[str]:
    if principal.is_admin:
        return [w.id for w in db.scalars(select(Workspace)).all()]
    if principal.auth_type == "api_key" and principal.workspace_id:
        return [principal.workspace_id]
    rows = db.scalars(
        select(Workspace.id).where(
            or_(
                Workspace.owner_id == principal.user.id,
                Workspace.id.in_(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id == principal.user.id
                    )
                ),
            )
        )
    ).all()
    return list(rows)
