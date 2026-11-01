"""Persistence model for ArchGraph.

Design notes
------------
* PostgreSQL holds both metadata and the architecture graph. The graph is stored
  as typed nodes/edges with provenance columns; traversal uses recursive CTEs.
  A dedicated graph database was deliberately avoided: at the scale ArchGraph
  targets (10^4-10^6 nodes) indexed adjacency tables plus recursive SQL are fast
  enough, keep deployment to one datastore, and make atomic graph replacement
  trivial.
* Every node/edge carries provenance (repository, revision, file, line,
  detection method, confidence) and a lifecycle status.
* AI output never lives in these tables. It lives in ``enrichments`` and can
  only attach to analysis via INFERRED edges.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Identity & access
# --------------------------------------------------------------------------- #
ROLES = ("SYSTEM_ADMIN", "WORKSPACE_ADMIN", "ARCHITECT", "DEVELOPER", "VIEWER")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="DEVELOPER", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_ws_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="DEVELOPER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


# --------------------------------------------------------------------------- #
# Repositories & scans
# --------------------------------------------------------------------------- #
class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_repo_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(24), default="local")  # local|github|gitlab
    default_branch: Mapped[str] = mapped_column(String(160), default="main")
    branch: Mapped[str] = mapped_column(String(160), default="main")
    credential_enc: Mapped[str | None] = mapped_column(Text)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text)
    last_commit: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    auto_sync: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str | None] = mapped_column(String(40))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(36))
    trigger: Mapped[str] = mapped_column(String(40), default="manual")  # manual|webhook|schedule|cli
    mode: Mapped[str] = mapped_column(String(20), default="full")  # full|incremental
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    previous_commit: Mapped[str | None] = mapped_column(String(64))
    branch: Mapped[str | None] = mapped_column(String(160))
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    changed_files: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IndexedFile(Base):
    __tablename__ = "indexed_files"
    __table_args__ = (UniqueConstraint("repository_id", "path", name="uq_file_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(24))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Architecture graph
# --------------------------------------------------------------------------- #
NODE_TYPES = (
    "WORKSPACE",
    "REPOSITORY",
    "MODULE",
    "SERVICE",
    "APPLICATION",
    "PACKAGE",
    "CLASS",
    "INTERFACE",
    "METHOD",
    "FUNCTION",
    "API_ENDPOINT",
    "GRPC_SERVICE",
    "GRPC_METHOD",
    "KAFKA_TOPIC",
    "EVENT",
    "PRODUCER",
    "CONSUMER",
    "DATABASE",
    "TABLE",
    "COLUMN",
    "REDIS_CACHE",
    "BACKGROUND_JOB",
    "EXTERNAL_SYSTEM",
    "DOCUMENT",
    "JIRA_ISSUE",
    "BUSINESS_CAPABILITY",
    "BUSINESS_FLOW",
    "ARCHITECTURE_DECISION",
    "RUNTIME_TRACE",
)

EDGE_TYPES = (
    "CONTAINS",
    "DEPENDS_ON",
    "CALLS",
    "EXPOSES",
    "IMPLEMENTS",
    "PUBLISHES",
    "CONSUMES",
    "READS",
    "WRITES",
    "USES_CACHE",
    "SCHEDULES",
    "ROUTES_TO",
    "DOCUMENTS",
    "IMPLEMENTS_REQUIREMENT",
    "RELATED_TO",
    "OBSERVED_IN",
    "INFERRED_FROM",
    "APPROVED_BY",
)

DETECTION_METHODS = (
    "STATIC_AST",
    "CONFIG_FILE",
    "DECLARED_DEPENDENCY",
    "FRAMEWORK_INFERRED",
    "CONFIGURED_CONNECTION",
    "RUNTIME_OBSERVED",
    "AI_INFERRED",
    "IMPORTED_DOC",
    "MANUAL",
)

#: Methods that represent deterministic, scanner-derived facts. AI never writes these.
DETERMINISTIC_METHODS = (
    "STATIC_AST",
    "CONFIG_FILE",
    "DECLARED_DEPENDENCY",
    "FRAMEWORK_INFERRED",
    "CONFIGURED_CONNECTION",
)

LIFECYCLE = ("ACTIVE", "REMOVED", "UNRESOLVED", "STALE")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "natural_key", name="uq_node_key"),
        Index("ix_nodes_ws_type", "workspace_id", "type"),
        Index("ix_nodes_repo", "repository_id"),
        Index("ix_nodes_name", "workspace_id", "name"),
        Index("ix_nodes_qualified", "workspace_id", "qualified_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    natural_key: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    source_revision: Mapped[str | None] = mapped_column(String(64))
    detection_method: Mapped[str] = mapped_column(String(32), default="STATIC_AST")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_edge_key"),
        Index("ix_edges_src", "src_id", "type"),
        Index("ix_edges_dst", "dst_id", "type"),
        Index("ix_edges_ws", "workspace_id", "type"),
        Index("ix_edges_unresolved", "workspace_id", "resolved"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    src_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dst_id: Mapped[str | None] = mapped_column(String(36), index=True)
    dst_ref: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=True)
    detection_method: Mapped[str] = mapped_column(String(32), default="STATIC_AST")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    source_revision: Mapped[str | None] = mapped_column(String(64))
    file_path: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Documentation
# --------------------------------------------------------------------------- #
DOC_STATUS = ("PENDING", "SYNCED", "ERROR", "STALE")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_type", "external_id", name="uq_document"),
        Index("ix_docs_ws_source", "workspace_id", "source_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # jira|confluence|markdown|adr|openapi|asyncapi|pdf|docx|notion
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    doc_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="SYNCED")
    repository_id: Mapped[str | None] = mapped_column(String(36))
    node_id: Mapped[str | None] = mapped_column(String(36))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DocumentLink(Base):
    __tablename__ = "document_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(32))  # node|edge
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    link_type: Mapped[str] = mapped_column(String(32), default="DOCUMENTS")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    detection_method: Mapped[str] = mapped_column(String(32), default="MANUAL")
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# AI enrichment
# --------------------------------------------------------------------------- #
ENRICHMENT_STATES = (
    "NOT_ANALYZED",
    "QUEUED",
    "ANALYZING",
    "GENERATED",
    "NEEDS_REVIEW",
    "APPROVED",
    "REJECTED",
    "STALE",
    "FAILED",
)

ENRICHMENT_CATEGORIES = (
    "SERVICE_OVERVIEW",
    "SERVICE_RESPONSIBILITIES",
    "BUSINESS_CAPABILITIES",
    "BUSINESS_FLOW",
    "API_PURPOSE",
    "EVENT_SEMANTICS",
    "DATABASE_PURPOSE",
    "BUSINESS_RULES",
    "CROSS_SERVICE_FLOW",
    "ARCHITECTURE_DECISION",
    "REQUIREMENT_LINK",
    "DOCUMENTATION",
    "RISK_ANALYSIS",
    "CHANGE_IMPACT",
    "MISSING_DOCUMENTATION",
)


class Enrichment(Base):
    __tablename__ = "enrichments"
    __table_args__ = (
        Index("ix_enrich_target", "workspace_id", "target_id", "category"),
        Index("ix_enrich_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # node|edge|document|flow
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_name: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    source_revision: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AiProvider(Base):
    __tablename__ = "ai_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # openai|anthropic|ollama|mock
    base_url: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- #
# Jobs, sync, audit
# --------------------------------------------------------------------------- #
class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    worker: Mapped[str | None] = mapped_column(String(80))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    repository_id: Mapped[str | None] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    delivery_id: Mapped[str] = mapped_column(String(120), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="RECEIVED")
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ws_time", "workspace_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user|api_key|system|mcp
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # github|gitlab|jira|confluence|otel|mcp_client
    name: Mapped[str] = mapped_column(String(120), default="")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret_enc: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="CONFIGURED")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BusinessFlow(Base):
    __tablename__ = "business_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    detection_method: Mapped[str] = mapped_column(String(32), default="FRAMEWORK_INFERRED")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
