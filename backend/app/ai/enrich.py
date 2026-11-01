"""Selective AI enrichment engine.

Design rules enforced here:
* Deterministic facts are *read*, never written. This module only writes
  ``enrichments`` rows.
* Context is bounded and evidence-only: the exact files/lines/edges the scanner
  observed, secrets redacted, capped by ``AI_MAX_CONTEXT_CHARS``.
* Every result records revision, provider, model, prompt version, confidence and
  evidence hashes so it can be marked STALE when the source changes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..graphstore import node_dict
from ..models import (
    Document,
    DocumentLink,
    Edge,
    Enrichment,
    IndexedFile,
    Node,
    Repository,
    new_id,
)
from ..security import redact_secrets
from .providers import AIError, get_provider

settings = get_settings()
PROMPT_VERSION = "v1"

CATEGORY_SPECS: dict[str, dict] = {
    "SERVICE_OVERVIEW": {
        "label": "Service overview",
        "focus": "what the service does, its scope, and its role in the system",
        "targets": ["SERVICE"],
    },
    "SERVICE_RESPONSIBILITIES": {
        "label": "Service responsibilities",
        "focus": "concrete responsibilities, owned data, and boundaries",
        "targets": ["SERVICE"],
    },
    "BUSINESS_CAPABILITIES": {
        "label": "Business capabilities",
        "focus": "business capabilities the implementation supports",
        "targets": ["SERVICE"],
    },
    "BUSINESS_FLOW": {
        "label": "Business flow discovery",
        "focus": "end-to-end business flows across services, with ordered steps",
        "targets": ["SERVICE", "KAFKA_TOPIC", "API_ENDPOINT"],
    },
    "API_PURPOSE": {
        "label": "API purpose",
        "focus": "purpose, contract and side effects of the endpoint",
        "targets": ["API_ENDPOINT"],
    },
    "EVENT_SEMANTICS": {
        "label": "Event semantics",
        "focus": "what the event means, who publishes/consumes it, ordering and idempotency concerns",
        "targets": ["KAFKA_TOPIC", "EVENT"],
    },
    "DATABASE_PURPOSE": {
        "label": "Database purpose",
        "focus": "what data is stored, ownership, and access patterns",
        "targets": ["DATABASE", "TABLE"],
    },
    "BUSINESS_RULES": {
        "label": "Business rules",
        "focus": "explicit business rules visible in code and configuration",
        "targets": ["SERVICE", "METHOD", "CLASS"],
    },
    "CROSS_SERVICE_FLOW": {
        "label": "Cross-service interaction",
        "focus": "how services interact for this target and what can fail",
        "targets": ["SERVICE", "API_ENDPOINT"],
    },
    "ARCHITECTURE_DECISION": {
        "label": "Architecture decision",
        "focus": "decisions implied by the implementation and their trade-offs",
        "targets": ["SERVICE", "DATABASE", "KAFKA_TOPIC"],
    },
    "REQUIREMENT_LINK": {
        "label": "Requirement to implementation",
        "focus": "which implementation elements satisfy the requirement, and gaps",
        "targets": ["DOCUMENT"],
    },
    "DOCUMENTATION": {
        "label": "Documentation generation",
        "focus": "a concise architecture document section for the target",
        "targets": ["SERVICE", "API_ENDPOINT", "KAFKA_TOPIC"],
    },
    "RISK_ANALYSIS": {
        "label": "Architecture risks",
        "focus": "concrete architectural and reliability risks supported by evidence",
        "targets": ["SERVICE", "DATABASE", "KAFKA_TOPIC"],
    },
    "CHANGE_IMPACT": {
        "label": "Change impact explanation",
        "focus": "what breaks and what must be reviewed when this element changes",
        "targets": ["SERVICE", "API_ENDPOINT", "METHOD", "CLASS"],
    },
    "MISSING_DOCUMENTATION": {
        "label": "Missing documentation",
        "focus": "which important elements lack documentation and what should be written",
        "targets": ["SERVICE"],
    },
}

SYSTEM_PROMPT = (
    "You are an architecture analyst embedded in ArchGraph, a deterministic microservice "
    "intelligence platform. You ONLY use the evidence supplied. Never invent files, classes, "
    "methods, endpoints, topics, tables or line numbers. If the evidence is insufficient, say so "
    "and lower confidence. Return a single JSON object with keys: summary (string), "
    "responsibilities (array of strings), capabilities (array of strings), business_rules "
    "(array of strings), steps (array of objects with name/action for flows), risks (array of "
    "strings), documentation (string), claims (array of objects {claim, basis} where basis cites "
    "the evidence you used), confidence (number 0..1). No markdown fences."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _node_edges(db: Session, node_id: str, limit: int = 60) -> list[Edge]:
    return list(
        db.scalars(
            select(Edge)
            .where(
                Edge.workspace_id == (db.get(Node, node_id).workspace_id if db.get(Node, node_id) else ""),
                Edge.status == "ACTIVE",
                (Edge.src_id == node_id) | (Edge.dst_id == node_id),
            )
            .limit(limit)
        ).all()
    )


def _read_source(db: Session, node: Node, max_lines: int = 60) -> dict | None:
    if not node.repository_id or not node.file_path:
        return None
    repo = db.get(Repository, node.repository_id)
    if not repo:
        return None
    root = os.path.join(settings.repo_storage_dir, repo.id)
    full = os.path.join(root, node.file_path)
    if not os.path.exists(full):
        return None
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    start = max(1, (node.start_line or 1) - 3)
    end = min(len(lines), (node.end_line or start) + 3, start + max_lines)
    snippet = "\n".join(lines[start - 1 : end])
    indexed = db.scalar(
        select(IndexedFile).where(
            IndexedFile.repository_id == repo.id, IndexedFile.path == node.file_path
        )
    )
    return {
        "file": node.file_path,
        "start_line": start,
        "end_line": end,
        "commit": node.source_revision,
        "repository": repo.name,
        "repository_id": repo.id,
        "content_hash": indexed.content_hash if indexed else None,
        "snippet": redact_secrets(snippet),
    }


def collect_context(db: Session, workspace_id: str, target_type: str, target_id: str) -> dict:
    context: dict = {"facts": [], "code": [], "edges": [], "documents": [], "evidence": []}
    node = db.get(Node, target_id) if target_type == "node" else None
    if node:
        context["target"] = node_dict(node)
        props = node.properties or {}
        context["facts"].append(f"{node.type} {node.qualified_name or node.name}")
        for key in ("http_method", "path", "stereotype", "framework", "language", "port",
                    "cron", "topic", "table", "method", "handler", "response_type"):
            if props.get(key):
                context["facts"].append(f"{key}={props[key]}")
        if node.file_path:
            context["facts"].append(f"file={node.file_path}:{node.start_line or 1}")
        source = _read_source(db, node)
        if source:
            context["code"].append(source)
            context["evidence"].append({
                "kind": "source", "repository_id": source["repository_id"],
                "file": source["file"], "start_line": source["start_line"],
                "commit": source["commit"], "content_hash": source["content_hash"],
            })
        edges = _node_edges(db, node.id)
        for edge in edges[:40]:
            other_id = edge.dst_id if edge.src_id == node.id else edge.src_id
            other = db.get(Node, other_id) if other_id else None
            if not other:
                continue
            direction = "out" if edge.src_id == node.id else "in"
            context["edges"].append({
                "type": edge.type, "direction": direction, "other": other.name,
                "other_type": other.type, "detection": edge.detection_method,
                "confidence": edge.confidence, "evidence": edge.evidence,
            })
            context["facts"].append(f"{direction}: {edge.type} {other.type} {other.name} ({edge.detection_method})")
            if other.file_path and other.repository_id:
                context["evidence"].append({
                    "kind": "related-node", "repository_id": other.repository_id,
                    "file": other.file_path, "start_line": other.start_line,
                    "commit": other.source_revision,
                })
    elif target_type == "document":
        doc = db.get(Document, target_id)
        if doc:
            context["target"] = {
                "id": doc.id, "title": doc.title, "source_type": doc.source_type,
                "external_id": doc.external_id, "version": doc.version, "url": doc.url,
            }
            context["facts"].append(f"{doc.source_type} document: {doc.title}")
            context["facts"].append(redact_secrets(doc.content[:4000]))
            context["evidence"].append({
                "kind": "document", "document_id": doc.id, "external_id": doc.external_id,
                "content_hash": doc.content_hash, "source_type": doc.source_type,
            })
            for link in db.scalars(
                select(DocumentLink).where(DocumentLink.document_id == doc.id)
            ).all():
                target = db.get(Node, link.target_id)
                if target:
                    context["facts"].append(f"linked {target.type} {target.name}")
    elif target_type == "edge":
        edge = db.get(Edge, target_id)
        if edge:
            src, dst = db.get(Node, edge.src_id), db.get(Node, edge.dst_id) if edge.dst_id else None
            context["target"] = {
                "id": edge.id, "type": edge.type,
                "src": src.name if src else edge.src_id,
                "dst": dst.name if dst else edge.dst_ref,
            }
            context["facts"].append(f"{src.name if src else ''} {edge.type} {dst.name if dst else edge.dst_ref}")
    else:
        context["target"] = {"id": target_id, "type": target_type}

    context["evidence"] = context["evidence"][:30]
    return context


def build_prompt(context: dict, category: str) -> tuple[str, str]:
    spec = CATEGORY_SPECS.get(category, {"label": category, "focus": category})
    payload = {
        "category": category,
        "focus": spec["focus"],
        "target": context.get("target"),
        "deterministic_facts": context.get("facts", [])[:60],
        "relationships": context.get("edges", [])[:30],
        "source_excerpts": [
            {"file": c["file"], "lines": f"{c['start_line']}-{c['end_line']}", "code": c["snippet"][:3000]}
            for c in context.get("code", [])
        ],
    }
    user = (
        f"Enrichment category: {spec['label']} ({category}).\n"
        f"Focus: {spec['focus']}.\n"
        "Use only the evidence below. Cite files in claims. JSON only.\n\n"
        + json.dumps(payload, indent=1)[: settings.ai_max_context_chars]
    )
    return SYSTEM_PROMPT, user


def estimate(db: Session, workspace_id: str, targets: list[tuple[str, str]], categories: list[str]) -> dict:
    total_chars = 0
    per_target = []
    for target_type, target_id in targets:
        ctx = collect_context(db, workspace_id, target_type, target_id)
        chars = len(json.dumps(ctx))
        total_chars += chars * max(1, len(categories))
        per_target.append({
            "target_type": target_type,
            "target_id": target_id,
            "target_name": (ctx.get("target") or {}).get("name")
            or (ctx.get("target") or {}).get("title", target_id),
            "context_chars": chars,
        })
    tokens_in = total_chars // 4
    tokens_out = 700 * len(targets) * max(1, len(categories))
    provider = get_provider(db, workspace_id)
    return {
        "targets": len(targets),
        "categories": categories,
        "estimated_input_tokens": tokens_in,
        "estimated_output_tokens": tokens_out,
        "estimated_total_tokens": tokens_in + tokens_out,
        "provider": provider.name if provider else None,
        "provider_kind": provider.kind if provider else None,
        "ai_enabled": provider is not None,
        "per_target": per_target[:50],
        "note": "Estimate uses 4 characters per token and 700 output tokens per analysis.",
    }


def create_enrichments(
    db: Session,
    workspace_id: str,
    targets: list[tuple[str, str]],
    categories: list[str],
    requested_by: str | None = None,
) -> list[Enrichment]:
    created: list[Enrichment] = []
    for category in categories:
        if category not in CATEGORY_SPECS:
            continue
        for target_type, target_id in targets:
            existing = db.scalar(
                select(Enrichment).where(
                    Enrichment.workspace_id == workspace_id,
                    Enrichment.target_type == target_type,
                    Enrichment.target_id == target_id,
                    Enrichment.category == category,
                    Enrichment.status.in_(["QUEUED", "ANALYZING", "GENERATED", "NEEDS_REVIEW", "APPROVED"]),
                )
            )
            if existing:
                created.append(existing)
                continue
            name = ""
            if target_type == "node":
                node = db.get(Node, target_id)
                name = node.name if node else ""
                revision = node.source_revision if node else None
            elif target_type == "document":
                doc = db.get(Document, target_id)
                name = doc.title if doc else ""
                revision = None
            else:
                edge = db.get(Edge, target_id)
                name = f"{edge.type} edge" if edge else ""
                revision = edge.source_revision if edge else None
            record = Enrichment(
                id=new_id(), workspace_id=workspace_id, target_type=target_type,
                target_id=target_id, target_name=name, category=category,
                status="QUEUED", source_revision=revision, prompt_version=PROMPT_VERSION,
                evidence=[], content={},
            )
            db.add(record)
            created.append(record)
    db.flush()
    return created


def run_enrichment(db: Session, enrichment: Enrichment) -> Enrichment:
    enrichment.status = "ANALYZING"
    enrichment.updated_at = _now()
    db.commit()
    try:
        provider = get_provider(db, enrichment.workspace_id)
        if provider is None:
            raise AIError("AI is disabled: no enabled provider is configured for this workspace")
        context = collect_context(
            db, enrichment.workspace_id, enrichment.target_type, enrichment.target_id
        )
        system, user = build_prompt(context, enrichment.category)
        result = provider.complete_json(system, user, {"summary": "string"})
        data = result.data if isinstance(result.data, dict) else {}
        summary = str(data.get("summary") or "").strip()
        if not summary:
            raise AIError("Provider response missing required 'summary' field")
        confidence = data.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else 0.5
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        enrichment.content = {
            "summary": summary[:2000],
            "responsibilities": data.get("responsibilities") or [],
            "capabilities": data.get("capabilities") or [],
            "business_rules": data.get("business_rules") or [],
            "steps": data.get("steps") or [],
            "risks": data.get("risks") or [],
            "documentation": data.get("documentation") or "",
            "claims": (data.get("claims") or [])[:30],
            "raw_provider_kind": provider.kind,
        }
        enrichment.summary = summary[:600]
        enrichment.confidence = confidence
        enrichment.provider = provider.kind
        enrichment.model = provider.model or ""
        enrichment.evidence = context.get("evidence", [])
        enrichment.tokens_in = result.tokens_in
        enrichment.tokens_out = result.tokens_out
        enrichment.status = "GENERATED" if confidence >= 0.75 else "NEEDS_REVIEW"
        enrichment.error = None
    except Exception as exc:  # noqa: BLE001
        enrichment.status = "FAILED"
        enrichment.error = f"{type(exc).__name__}: {exc}"[:1000]
    enrichment.updated_at = _now()
    db.commit()
    return enrichment


def usage_summary(db: Session, workspace_id: str) -> dict:
    rows = db.execute(
        select(
            Enrichment.status,
            func.count(Enrichment.id),
            func.coalesce(func.sum(Enrichment.tokens_in), 0),
            func.coalesce(func.sum(Enrichment.tokens_out), 0),
        )
        .where(Enrichment.workspace_id == workspace_id)
        .group_by(Enrichment.status)
    ).all()
    by_status = {r[0]: {"count": int(r[1]), "tokens_in": int(r[2]), "tokens_out": int(r[3])} for r in rows}
    return {
        "by_status": by_status,
        "tokens_in": sum(v["tokens_in"] for v in by_status.values()),
        "tokens_out": sum(v["tokens_out"] for v in by_status.values()),
        "total": sum(v["count"] for v in by_status.values()),
    }
