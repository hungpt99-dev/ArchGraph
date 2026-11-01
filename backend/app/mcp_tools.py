"""MCP tool registry.

Tools are structured architecture-graph queries — not generic search. Every tool
resolves the caller's principal, enforces workspace isolation, and writes an
audit event. Read-only tools work with no AI provider configured.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .ai.enrich import CATEGORY_SPECS, create_enrichments, estimate
from .ai.providers import get_provider
from .deps import Principal, accessible_workspace_ids, ensure_workspace_access
from .graphstore import (
    impact_analysis,
    neighbourhood,
    node_dict,
    shortest_path,
    unresolved_dependencies,
)
from .jobs import enqueue
from .models import (
    AuditEvent,
    BusinessFlow,
    Document,
    DocumentLink,
    Edge,
    Enrichment,
    Node,
    Repository,
    Workspace,
)
from .queries import (
    api_details,
    document_detail,
    find_node_by_name,
    flows,
    list_apis,
    list_services,
    search_symbols,
    service_architecture,
    service_dependencies,
    symbol_detail,
    tables,
    topic_links,
    topics,
    trace_event_flow,
)

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, schema: dict):
    def wrapper(fn):
        TOOLS[name] = {"name": name, "description": description, "inputSchema": schema,
                       "handler": fn}
        return fn

    return wrapper


def _str(desc: str = "", required: bool = True) -> dict:
    prop = {"type": "string", "description": desc}
    return prop


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Build a JSON Schema. Only workspace_id (and explicitly listed args) are required,
    so clients can omit optional filters without validation errors."""
    if required is None:
        required = [k for k in properties if k == "workspace_id"]
    return {"type": "object", "properties": properties,
            "required": required,
            "additionalProperties": False}


WS = {"workspace_id": _str("Workspace id (from list_workspaces)")}


def _ws(args: dict) -> str:
    ws = args.get("workspace_id")
    if not ws:
        raise ValueError("workspace_id is required")
    return ws


def _node(db: Session, workspace_id: str, node_id: str | None = None, name: str | None = None,
          types: list[str] | None = None) -> Node:
    node = db.get(Node, node_id) if node_id else None
    if node is None and name:
        node = find_node_by_name(db, workspace_id, name, types)
    if node is None or node.workspace_id != workspace_id:
        raise ValueError("Node not found in this workspace")
    return node


# --------------------------------------------------------------------------- #
# Workspaces & repositories
# --------------------------------------------------------------------------- #
@tool("list_workspaces", "List workspaces visible to the authenticated principal.",
      obj({}))
def list_workspaces(db: Session, principal: Principal, args: dict) -> dict:
    ids = accessible_workspace_ids(db, principal)
    rows = db.scalars(select(Workspace).where(Workspace.id.in_(ids)).order_by(Workspace.name)).all() if ids else []
    return {"workspaces": [{"id": w.id, "name": w.name, "slug": w.slug,
                            "description": w.description} for w in rows]}


@tool("get_workspace", "Get workspace details and architecture counters.",
      obj({"workspace_id": _str()}))
def get_workspace(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ws = ensure_workspace_access(db, principal, ws_id)
    from .graphstore import workspace_stats

    return {"workspace": {"id": ws.id, "name": ws.name, "slug": ws.slug,
                          "description": ws.description},
            "stats": workspace_stats(db, ws_id)}


@tool("list_repositories", "List repositories in a workspace with commit and scan status.",
      obj({"workspace_id": _str()}))
def list_repositories(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    repos = db.scalars(select(Repository).where(Repository.workspace_id == ws_id)).all()
    return {"repositories": [
        {"id": r.id, "name": r.name, "provider": r.provider, "branch": r.branch,
         "last_commit": r.last_commit, "status": r.status, "language": r.language,
         "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None}
        for r in repos
    ]}


@tool("get_repository_status", "Repository scan status, language mix and last scan stats.",
      obj({"workspace_id": _str(), "repository_id": _str()}))
def get_repository_status(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    repo = db.get(Repository, args["repository_id"])
    if not repo or repo.workspace_id != ws_id:
        raise ValueError("Repository not found")
    from .models import ScanRun

    last = db.scalar(select(ScanRun).where(ScanRun.repository_id == repo.id)
                     .order_by(ScanRun.created_at.desc()).limit(1))
    return {"repository": {"id": repo.id, "name": repo.name, "status": repo.status,
                           "last_commit": repo.last_commit, "language": repo.language,
                           "settings": repo.settings},
            "last_scan": {"status": last.status, "stats": last.stats} if last else None}


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #
@tool("list_services", "List services discovered in a workspace (deterministic scan).",
      obj({"workspace_id": _str(), "query": {"type": "string", "description": "optional filter"}}))
def list_services_tool(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"services": list_services(db, ws_id, args.get("query"))}


@tool("get_service", "Get a service by id or exact name.",
      obj({"workspace_id": _str(), "service_id": {"type": "string"},
           "name": {"type": "string"}}, ["workspace_id"]))
def get_service(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args.get("service_id"), args.get("name"), ["SERVICE"])
    from .queries import service_summary

    return service_summary(db, node)


@tool("get_service_dependencies", "Services this service depends on.",
      obj({"workspace_id": _str(), "service_id": {"type": "string"}, "name": {"type": "string"}},
          ["workspace_id"]))
def get_service_dependencies(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args.get("service_id"), args.get("name"), ["SERVICE"])
    return {"service": node.name, "dependencies": service_dependencies(db, ws_id, node.id, "out")}


@tool("get_service_dependents", "Services that depend on this service.",
      obj({"workspace_id": _str(), "service_id": {"type": "string"}, "name": {"type": "string"}},
          ["workspace_id"]))
def get_service_dependents(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args.get("service_id"), args.get("name"), ["SERVICE"])
    return {"service": node.name, "dependents": service_dependencies(db, ws_id, node.id, "in")}


@tool("get_service_architecture", "Complete architecture slice for a service: APIs, topics, "
      "databases, caches, jobs, dependencies, documents and AI insights.",
      obj({"workspace_id": _str(), "service_id": {"type": "string"}, "name": {"type": "string"}},
          ["workspace_id"]))
def get_service_architecture(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args.get("service_id"), args.get("name"), ["SERVICE"])
    return service_architecture(db, node)


# --------------------------------------------------------------------------- #
# Code
# --------------------------------------------------------------------------- #
@tool("search_symbols", "Search classes, methods, functions, packages by name.",
      obj({"workspace_id": _str(), "query": _str(),
           "types": {"type": "array", "items": {"type": "string"}},
           "limit": {"type": "integer"}}))
def search_symbols_tool(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"symbols": search_symbols(db, ws_id, args["query"], args.get("types"),
                                      int(args.get("limit") or 30))}


@tool("get_symbol", "Get a symbol with incoming/outgoing relationships and AI insights.",
      obj({"workspace_id": _str(), "node_id": {"type": "string"}}, ["workspace_id", "node_id"]))
def get_symbol(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    return symbol_detail(db, node)


@tool("get_source_location", "Exact repository, revision, file and line range for a node.",
      obj({"workspace_id": _str(), "node_id": {"type": "string"}}, ["workspace_id", "node_id"]))
def get_source_location(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    repo = db.get(Repository, node.repository_id) if node.repository_id else None
    return {"node": node_dict(node), "repository": {"id": repo.id, "name": repo.name,
                                                    "url": repo.url, "commit": repo.last_commit} if repo else None,
            "location": {"file": node.file_path, "start_line": node.start_line,
                         "end_line": node.end_line, "revision": node.source_revision},
            "evidence": node.evidence}


@tool("get_method_dependencies", "Methods/classes a method calls or declares dependencies on.",
      obj({"workspace_id": _str(), "node_id": {"type": "string"}}, ["workspace_id", "node_id"]))
def get_method_dependencies(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    rows = db.scalars(select(Edge).where(Edge.workspace_id == ws_id, Edge.src_id == node.id,
                                         Edge.status == "ACTIVE",
                                         Edge.type.in_(["CALLS", "DEPENDS_ON"]))).all()
    out = []
    for edge in rows:
        target = db.get(Node, edge.dst_id) if edge.dst_id else None
        out.append({"type": edge.type, "target": target.name if target else edge.dst_ref,
                    "target_type": target.type if target else None,
                    "detection_method": edge.detection_method, "confidence": edge.confidence,
                    "evidence": edge.evidence})
    return {"method": node.name, "dependencies": out}


@tool("get_class_dependencies", "Class-level dependency edges (fields, calls, inheritance).",
      obj({"workspace_id": _str(), "node_id": {"type": "string"}}, ["workspace_id", "node_id"]))
def get_class_dependencies(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    rows = db.scalars(select(Edge).where(Edge.workspace_id == ws_id, Edge.src_id == node.id,
                                         Edge.status == "ACTIVE",
                                         Edge.type.in_(["DEPENDS_ON", "IMPLEMENTS", "CALLS"]))).all()
    names = {n.id: n for n in db.scalars(select(Node).where(Node.workspace_id == ws_id,
                                                            Node.id.in_([r.dst_id for r in rows if r.dst_id]))).all()}
    return {"class": node.qualified_name, "dependencies": [
        {"type": e.type, "target": names[e.dst_id].name if e.dst_id in names else e.dst_ref,
         "target_type": names[e.dst_id].type if e.dst_id in names else None,
         "detection_method": e.detection_method, "confidence": e.confidence}
        for e in rows
    ]}


# --------------------------------------------------------------------------- #
# APIs
# --------------------------------------------------------------------------- #
@tool("list_apis", "List REST endpoints discovered statically (or from OpenAPI).",
      obj({"workspace_id": _str(), "query": {"type": "string"},
           "service_id": {"type": "string"}}))
def list_apis_tool(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"apis": list_apis(db, ws_id, args.get("query"), args.get("service_id"))}


@tool("get_api_details", "Endpoint details including handler method and evidence.",
      obj({"workspace_id": _str(), "api_id": {"type": "string"}}, ["workspace_id", "api_id"]))
def get_api_details(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["api_id"], None, ["API_ENDPOINT"])
    return api_details(db, node)


@tool("find_api_implementation", "Resolve an HTTP method+path to its handler method and source.",
      obj({"workspace_id": _str(), "http_method": _str(),
           "path": _str()}, ["workspace_id", "http_method", "path"]))
def find_api_implementation(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    verb = args["http_method"].upper()
    path = args["path"]
    node = db.scalar(select(Node).where(Node.workspace_id == ws_id, Node.type == "API_ENDPOINT",
                                        Node.status == "ACTIVE",
                                        Node.name == f"{verb} {path}"))
    if not node:
        node = find_node_by_name(db, ws_id, f"{verb} {path}", ["API_ENDPOINT"])
    if not node:
        raise ValueError("No endpoint matches that method/path")
    return api_details(db, node)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@tool("list_kafka_topics", "List Kafka topics with producers and consumers.",
      obj({"workspace_id": _str()}))
def list_kafka_topics(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"topics": topics(db, ws_id)}


@tool("get_topic_producers", "Producer classes/services for a topic.",
      obj({"workspace_id": _str(), "topic": _str()}, ["workspace_id", "topic"]))
def get_topic_producers(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    links = topic_links(db, ws_id, args["topic"])
    if not links.get("found"):
        raise ValueError("Topic not found")
    return {"topic": args["topic"], "producers": links["producers"]}


@tool("get_topic_consumers", "Consumer classes/services for a topic.",
      obj({"workspace_id": _str(), "topic": _str()}, ["workspace_id", "topic"]))
def get_topic_consumers(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    links = topic_links(db, ws_id, args["topic"])
    if not links.get("found"):
        raise ValueError("Topic not found")
    return {"topic": args["topic"], "consumers": links["consumers"]}


@tool("trace_event_flow", "Trace a topic end-to-end: producers, transport, consumers, downstream impact.",
      obj({"workspace_id": _str(), "topic": _str()}, ["workspace_id", "topic"]))
def trace_event_flow_tool(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return trace_event_flow(db, ws_id, args["topic"])


# --------------------------------------------------------------------------- #
# Databases
# --------------------------------------------------------------------------- #
@tool("list_databases", "List databases and caches discovered from configuration.",
      obj({"workspace_id": _str()}))
def list_databases(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    rows = db.scalars(select(Node).where(Node.workspace_id == ws_id,
                                         Node.type.in_(["DATABASE", "REDIS_CACHE"]),
                                         Node.status == "ACTIVE")).all()
    return {"databases": [node_dict(n) for n in rows]}


@tool("list_tables", "List tables with accessor counts and owning services.",
      obj({"workspace_id": _str()}))
def list_tables(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"tables": tables(db, ws_id)}


@tool("find_table_accessors", "Classes/methods that read or write a table.",
      obj({"workspace_id": _str(), "table": _str()}, ["workspace_id", "table"]))
def find_table_accessors(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = find_node_by_name(db, ws_id, args["table"], ["TABLE"])
    if not node:
        raise ValueError("Table not found")
    rows = db.scalars(select(Edge).where(Edge.workspace_id == ws_id, Edge.dst_id == node.id,
                                         Edge.type.in_(["READS", "WRITES"]),
                                         Edge.status == "ACTIVE")).all()
    return {"table": node.name, "accessors": [
        {"node": node_dict(db.get(Node, e.src_id)) if db.get(Node, e.src_id) else None,
         "operation": e.type, "detection_method": e.detection_method,
         "confidence": e.confidence, "evidence": e.evidence}
        for e in rows
    ]}


# --------------------------------------------------------------------------- #
# Architecture
# --------------------------------------------------------------------------- #
@tool("query_graph", "Traverse the architecture graph from seed nodes.",
      obj({"workspace_id": _str(),
           "node_ids": {"type": "array", "items": {"type": "string"}},
           "depth": {"type": "integer"},
           "edge_types": {"type": "array", "items": {"type": "string"}},
           "node_types": {"type": "array", "items": {"type": "string"}},
           "limit": {"type": "integer"}}))
def query_graph(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    seeds = args.get("node_ids") or []
    if not seeds:
        seeds = [n.id for n in db.scalars(select(Node).where(
            Node.workspace_id == ws_id, Node.type == "SERVICE", Node.status == "ACTIVE")).all()]
    return neighbourhood(db, ws_id, seeds, int(args.get("depth") or 1),
                         args.get("edge_types"), args.get("node_types"),
                         int(args.get("limit") or 300))


@tool("find_dependency_path", "Shortest dependency path between two services/nodes.",
      obj({"workspace_id": _str(), "source_id": {"type": "string"},
           "target_id": {"type": "string"}, "source_name": {"type": "string"},
           "target_name": {"type": "string"}}))
def find_dependency_path(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    src = _node(db, ws_id, args.get("source_id"), args.get("source_name"))
    dst = _node(db, ws_id, args.get("target_id"), args.get("target_name"))
    return shortest_path(db, ws_id, src.id, dst.id)


@tool("analyze_change_impact", "Reverse dependency closure for a node (what breaks if it changes).",
      obj({"workspace_id": _str(), "node_id": {"type": "string"},
           "depth": {"type": "integer"}}))
def analyze_change_impact(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    return impact_analysis(db, ws_id, node.id, int(args.get("depth") or 2))


@tool("trace_business_flow", "Deterministic or AI-derived business flow by name or id.",
      obj({"workspace_id": _str(), "name": {"type": "string"},
           "flow_id": {"type": "string"}}))
def trace_business_flow(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    row = db.get(BusinessFlow, args["flow_id"]) if args.get("flow_id") else None
    if row is None and args.get("name"):
        row = db.scalar(select(BusinessFlow).where(BusinessFlow.workspace_id == ws_id,
                                                   BusinessFlow.name.ilike(f"%{args['name']}%")))
    if not row:
        return {"found": False, "flows": flows(db, ws_id)}
    return {"found": True, "flow": {"id": row.id, "name": row.name, "steps": row.steps,
                                    "detection_method": row.detection_method,
                                    "confidence": row.confidence,
                                    "description": row.description}}


@tool("find_unresolved_dependencies", "References the scanner could not bind (never invented).",
      obj({"workspace_id": _str()}))
def find_unresolved(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    return {"unresolved": unresolved_dependencies(db, ws_id)}


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
@tool("search_documents", "Full-text search across ingested documentation.",
      obj({"workspace_id": _str(), "query": {"type": "string"},
           "source_type": {"type": "string"}}))
def search_documents(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    from .queries import documents_search

    return {"documents": documents_search(db, ws_id, args.get("query"), args.get("source_type"))}


@tool("get_document", "Get a document with its links to architecture nodes.",
      obj({"workspace_id": _str(), "document_id": _str()}, ["workspace_id", "document_id"]))
def get_document(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    doc = db.get(Document, args["document_id"])
    if not doc or doc.workspace_id != ws_id:
        raise ValueError("Document not found")
    return document_detail(db, doc)


@tool("find_related_requirements", "Documents (Jira/Confluence/ADR) linked to a node.",
      obj({"workspace_id": _str(), "node_id": _str()}))
def find_related_requirements(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    node = _node(db, ws_id, args["node_id"])
    links = db.scalars(select(DocumentLink).where(DocumentLink.target_id == node.id)).all()
    out = []
    for link in links:
        doc = db.get(Document, link.document_id)
        if doc:
            out.append({"id": doc.id, "title": doc.title, "source_type": doc.source_type,
                        "url": doc.url, "link_type": link.link_type,
                        "confidence": link.confidence, "approved": link.approved})
    return {"node": node.name, "documents": out}


@tool("find_implementation_for_requirement", "Nodes linked to a requirement document.",
      obj({"workspace_id": _str(), "document_id": _str()}, ["workspace_id", "document_id"]))
def find_implementation_for_requirement(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    doc = db.get(Document, args["document_id"])
    if not doc or doc.workspace_id != ws_id:
        raise ValueError("Document not found")
    links = db.scalars(select(DocumentLink).where(DocumentLink.document_id == doc.id)).all()
    nodes = []
    for link in links:
        node = db.get(Node, link.target_id)
        if node:
            nodes.append({**node_dict(node), "link_type": link.link_type,
                          "confidence": link.confidence, "approved": link.approved})
    return {"document": {"id": doc.id, "title": doc.title, "source_type": doc.source_type},
            "implementation": nodes}


# --------------------------------------------------------------------------- #
# AI
# --------------------------------------------------------------------------- #
@tool("get_ai_insights", "Approved/generated AI insights for a target or workspace.",
      obj({"workspace_id": _str(), "target_id": {"type": "string"},
           "status": {"type": "string"}}))
def get_ai_insights(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    from .queries import ai_insights

    return {"insights": ai_insights(db, ws_id, args.get("status"), args.get("target_id"))}


@tool("get_enrichment_status", "AI enrichment status, provider and usage counters.",
      obj({"workspace_id": _str()}))
def get_enrichment_status(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id)
    from .ai.enrich import usage_summary

    provider = get_provider(db, ws_id)
    return {"ai_enabled": provider is not None,
            "provider": provider.kind if provider else None,
            "usage": usage_summary(db, ws_id)}


@tool("request_enrichment", "Request AI enrichment for explicit targets and categories. "
      "Respects workspace policy and never runs without a configured provider.",
      obj({"workspace_id": _str(),
           "targets": {"type": "array", "items": {"type": "object",
                                                  "properties": {"type": {"type": "string"},
                                                                 "id": {"type": "string"}},
                                                  "required": ["id"]}},
           "categories": {"type": "array", "items": {"type": "string"}}},
          ["workspace_id", "targets", "categories"]))
def request_enrichment(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ws = ensure_workspace_access(db, principal, ws_id, "ARCHITECT")
    provider = get_provider(db, ws_id)
    if provider is None:
        raise ValueError("AI is disabled: no provider configured")
    if not (ws.settings or {}).get("ai_enabled", True):
        raise ValueError("AI is disabled for this workspace by policy")
    categories = [c for c in (args.get("categories") or []) if c in CATEGORY_SPECS]
    if not categories:
        raise ValueError("No valid categories requested")
    targets = []
    for t in args.get("targets") or []:
        node = db.get(Node, t.get("id"))
        if node and node.workspace_id == ws_id:
            targets.append(("node", node.id))
    if not targets:
        raise ValueError("No valid targets")
    est = estimate(db, ws_id, targets, categories)
    records = create_enrichments(db, ws_id, targets, categories, principal.user.email)
    job = enqueue(db, "run_enrichment",
                  {"enrichment_ids": [r.id for r in records if r.status == "QUEUED"]},
                  workspace_id=ws_id, priority=4)
    db.commit()
    return {"job_id": job.id, "enrichment_ids": [r.id for r in records], "estimate": est}


@tool("review_ai_insight", "Approve or reject an AI insight (human-in-the-loop).",
      obj({"workspace_id": _str(), "enrichment_id": _str(),
           "decision": {"type": "string", "enum": ["approve", "reject"]},
           "note": {"type": "string"}},
          ["workspace_id", "enrichment_id", "decision"]))
def review_ai_insight(db: Session, principal: Principal, args: dict) -> dict:
    ws_id = _ws(args)
    ensure_workspace_access(db, principal, ws_id, "ARCHITECT")
    record = db.get(Enrichment, args["enrichment_id"])
    if not record or record.workspace_id != ws_id:
        raise ValueError("Enrichment not found")
    decision = args.get("decision")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    record.status = "APPROVED" if decision == "approve" else "REJECTED"
    record.reviewed_by = principal.user.email
    record.review_note = args.get("note", "")
    record.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": record.id, "status": record.status}


def audit_tool_call(db: Session, principal: Principal, name: str, args: dict, error: str | None = None):
    db.add(AuditEvent(
        workspace_id=args.get("workspace_id"), actor=principal.user.email, actor_type="mcp",
        action=f"mcp.{name}", object_type="mcp_tool", object_id=name,
        detail={"args": {k: v for k, v in args.items() if k != "workspace_id"},
                "error": error},
    ))
    db.commit()
