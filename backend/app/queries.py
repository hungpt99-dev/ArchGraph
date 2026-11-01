"""High-level architecture queries shared by the REST API and the MCP server.

Keeping one implementation guarantees that humans and AI agents see exactly the
same graph facts (and the same provenance).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .graphstore import (
    edge_dict,
    impact_analysis,
    neighbourhood,
    node_dict,
    shortest_path,
    unresolved_dependencies,
    workspace_stats,
)
from .models import (
    BusinessFlow,
    Document,
    DocumentLink,
    Edge,
    Enrichment,
    Node,
    Repository,
    ScanRun,
)


def get_node(db: Session, workspace_id: str, node_id: str) -> Node | None:
    node = db.get(Node, node_id)
    if node and node.workspace_id == workspace_id:
        return node
    return None


def find_node_by_name(db: Session, workspace_id: str, name: str, types: list[str] | None = None) -> Node | None:
    stmt = select(Node).where(
        Node.workspace_id == workspace_id, Node.status == "ACTIVE",
        or_(Node.name == name, Node.qualified_name == name),
    )
    if types:
        stmt = stmt.where(Node.type.in_(types))
    return db.scalar(stmt.order_by(Node.type))


def service_summary(db: Session, node: Node) -> dict:
    edges = list(
        db.scalars(
            select(Edge).where(
                Edge.workspace_id == node.workspace_id,
                Edge.status == "ACTIVE",
                or_(Edge.src_id == node.id, Edge.dst_id == node.id),
            )
        ).all()
    )
    apis = [
        db.get(Node, e.dst_id) for e in edges
        if e.src_id == node.id and e.type == "EXPOSES" and e.dst_id
    ]
    deps = [
        db.get(Node, e.dst_id) for e in edges
        if e.src_id == node.id and e.type == "DEPENDS_ON" and e.dst_id
    ]
    topics_out = [
        db.get(Node, e.dst_id) for e in edges
        if e.src_id == node.id and e.type in ("PUBLISHES", "CONSUMES") and e.dst_id
    ]
    repo = db.get(Repository, node.repository_id) if node.repository_id else None
    return {
        **node_dict(node),
        "repository": {"id": repo.id, "name": repo.name, "url": repo.url,
                       "branch": repo.branch, "commit": repo.last_commit} if repo else None,
        "counts": {
            "apis": len([a for a in apis if a]),
            "dependencies": len([d for d in deps if d]),
            "topics": len({t.id for t in topics_out if t}),
        },
    }


def list_services(db: Session, workspace_id: str, query: str | None = None) -> list[dict]:
    stmt = select(Node).where(
        Node.workspace_id == workspace_id, Node.type == "SERVICE", Node.status == "ACTIVE"
    )
    if query:
        stmt = stmt.where(or_(Node.name.ilike(f"%{query}%"), Node.qualified_name.ilike(f"%{query}%")))
    services = list(db.scalars(stmt.order_by(Node.name)).all())
    api_counts = dict(
        db.execute(
            select(Edge.src_id, func.count(Edge.id))
            .where(Edge.workspace_id == workspace_id, Edge.type == "EXPOSES", Edge.status == "ACTIVE")
            .group_by(Edge.src_id)
        ).all()
    )
    dep_counts = dict(
        db.execute(
            select(Edge.src_id, func.count(Edge.id))
            .where(Edge.workspace_id == workspace_id, Edge.type == "DEPENDS_ON",
                   Edge.src_id.in_([s.id for s in services]), Edge.status == "ACTIVE")
            .group_by(Edge.src_id)
        ).all()
    )
    out = []
    for s in services:
        repo = db.get(Repository, s.repository_id) if s.repository_id else None
        out.append({
            **node_dict(s),
            "api_count": int(api_counts.get(s.id, 0)),
            "dependency_count": int(dep_counts.get(s.id, 0)),
            "repository_name": repo.name if repo else None,
            "language": (s.properties or {}).get("language"),
            "framework": (s.properties or {}).get("framework"),
            "port": (s.properties or {}).get("port"),
        })
    return out


def service_dependencies(db: Session, workspace_id: str, service_id: str, direction: str = "out") -> list[dict]:
    stmt = select(Edge).where(Edge.workspace_id == workspace_id, Edge.status == "ACTIVE",
                              Edge.type.in_(["DEPENDS_ON", "ROUTES_TO", "CALLS"]))
    stmt = stmt.where(Edge.src_id == service_id) if direction == "out" else stmt.where(Edge.dst_id == service_id)
    edges = list(db.scalars(stmt).all())
    names = {n.id: n.name for n in db.scalars(select(Node).where(Node.workspace_id == workspace_id)).all()}
    out = []
    seen = set()
    for edge in edges:
        other_id = edge.dst_id if direction == "out" else edge.src_id
        if not other_id or (other_id, edge.type) in seen:
            continue
        seen.add((other_id, edge.type))
        other = db.get(Node, other_id)
        if not other or other.type != "SERVICE":
            continue
        out.append({
            "service": node_dict(other),
            "edge": edge_dict(edge, names),
        })
    return out


def service_architecture(db: Session, node: Node) -> dict:
    ws = node.workspace_id
    edges_out = list(db.scalars(
        select(Edge).where(Edge.workspace_id == ws, Edge.src_id == node.id, Edge.status == "ACTIVE")
    ).all())
    edges_in = list(db.scalars(
        select(Edge).where(Edge.workspace_id == ws, Edge.dst_id == node.id, Edge.status == "ACTIVE")
    ).all())
    collect = lambda ids: [n for n in (db.get(Node, i) for i in ids if i) if n]
    apis = collect([e.dst_id for e in edges_out if e.type == "EXPOSES"])
    topics_pub = collect([e.dst_id for e in edges_out if e.type == "PUBLISHES"])
    topics_sub = collect([e.dst_id for e in edges_out if e.type == "CONSUMES"])
    caches = collect([e.dst_id for e in edges_out if e.type == "USES_CACHE"])
    jobs = collect([e.dst_id for e in edges_out if e.type == "SCHEDULES"])
    dbs = collect([e.dst_id for e in edges_out if e.type == "DEPENDS_ON" and (db.get(Node, e.dst_id).type == "DATABASE" if e.dst_id else False)])
    deps = [
        node_dict(n) for n in collect([e.dst_id for e in edges_out if e.type == "DEPENDS_ON"])
        if n.type == "SERVICE"
    ]
    dependents = [
        node_dict(n) for n in collect([e.src_id for e in edges_in if e.type == "DEPENDS_ON"])
        if n.type == "SERVICE"
    ]
    docs = []
    for link in db.scalars(select(DocumentLink).where(DocumentLink.target_id == node.id)).all():
        doc = db.get(Document, link.document_id)
        if doc:
            docs.append({"id": doc.id, "title": doc.title, "source_type": doc.source_type,
                         "url": doc.url, "link_type": link.link_type, "confidence": link.confidence})
    insights = [
        {"id": e.id, "category": e.category, "status": e.status,
         "summary": e.summary, "confidence": e.confidence}
        for e in db.scalars(select(Enrichment).where(Enrichment.target_id == node.id)).all()
    ]
    classes = [
        node_dict(n)
        for n in db.scalars(
            select(Node).where(Node.repository_id == node.repository_id, Node.type.in_(["CLASS", "INTERFACE"]),
                               Node.status == "ACTIVE").limit(40)
        ).all()
    ] if node.repository_id else []
    return {
        "service": node_dict(node),
        "apis": [node_dict(n) for n in apis],
        "publishes": [node_dict(n) for n in topics_pub],
        "consumes": [node_dict(n) for n in topics_sub],
        "caches": [node_dict(n) for n in caches],
        "jobs": [node_dict(n) for n in jobs],
        "databases": [node_dict(n) for n in dbs],
        "dependencies": deps,
        "dependents": dependents,
        "classes": classes,
        "documents": docs,
        "ai_insights": insights,
        "evidence": node.evidence,
    }


def list_apis(db: Session, workspace_id: str, query: str | None = None, service_id: str | None = None) -> list[dict]:
    stmt = select(Node).where(Node.workspace_id == workspace_id, Node.type == "API_ENDPOINT",
                              Node.status == "ACTIVE")
    if service_id:
        stmt = stmt.where(Node.parent_id == service_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Node.name.ilike(like), Node.qualified_name.ilike(like)))
    nodes = list(db.scalars(stmt.order_by(Node.name)).all())
    return [node_dict(n) for n in nodes]


def api_details(db: Session, node: Node) -> dict:
    handler = db.scalar(
        select(Edge).where(Edge.src_id == node.id, Edge.type == "IMPLEMENTS", Edge.status == "ACTIVE")
    )
    handler_node = db.get(Node, handler.dst_id) if handler and handler.dst_id else None
    service = db.get(Node, node.parent_id) if node.parent_id else None
    return {
        "api": node_dict(node),
        "service": node_dict(service) if service else None,
        "handler": node_dict(handler_node) if handler_node else None,
        "evidence": node.evidence,
    }


def topics(db: Session, workspace_id: str) -> list[dict]:
    nodes = list(db.scalars(
        select(Node).where(Node.workspace_id == workspace_id, Node.type == "KAFKA_TOPIC",
                           Node.status == "ACTIVE").order_by(Node.name)
    ).all())
    out = []
    for n in nodes:
        pub = db.scalars(select(Edge).where(Edge.workspace_id == workspace_id, Edge.dst_id == n.id,
                                            Edge.type == "PUBLISHES", Edge.status == "ACTIVE")).all()
        cons = db.scalars(select(Edge).where(Edge.workspace_id == workspace_id, Edge.dst_id == n.id,
                                             Edge.type == "CONSUMES", Edge.status == "ACTIVE")).all()
        producers = {db.get(Node, e.src_id).name for e in pub if db.get(Node, e.src_id)}
        consumers = {db.get(Node, e.src_id).name for e in cons if db.get(Node, e.src_id)}
        out.append({**node_dict(n), "producers": sorted(producers), "consumers": sorted(consumers),
                    "producer_count": len(producers), "consumer_count": len(consumers)})
    return out


def topic_links(db: Session, workspace_id: str, topic_name: str) -> dict:
    node = find_node_by_name(db, workspace_id, topic_name, ["KAFKA_TOPIC"])
    if not node:
        return {"found": False, "topic": topic_name}
    edges = list(db.scalars(
        select(Edge).where(Edge.workspace_id == workspace_id, Edge.dst_id == node.id,
                           Edge.status == "ACTIVE", Edge.type.in_(["PUBLISHES", "CONSUMES"]))
    ).all())
    names = {n.id: n for n in db.scalars(select(Node).where(Node.workspace_id == workspace_id)).all()}
    producers, consumers = [], []
    for e in edges:
        src = names.get(e.src_id)
        if not src:
            continue
        item = {
            "service": src.name if src.type == "SERVICE" else None,
            "node": node_dict(src),
            "properties": e.properties,
            "evidence": e.evidence,
            "detection_method": e.detection_method,
        }
        (producers if e.type == "PUBLISHES" else consumers).append(item)
    return {
        "found": True, "topic": node_dict(node),
        "producers": producers, "consumers": consumers,
        "evidence": node.evidence,
    }


def trace_event_flow(db: Session, workspace_id: str, topic_name: str) -> dict:
    links = topic_links(db, workspace_id, topic_name)
    if not links.get("found"):
        return links
    steps = []
    for producer in links["producers"]:
        if producer["service"]:
            steps.append({"stage": "produce", "service": producer["service"],
                          "node_id": producer["node"]["id"]})
    steps.append({"stage": "transport", "topic": links["topic"]["name"],
                  "node_id": links["topic"]["id"]})
    for consumer in links["consumers"]:
        if consumer["service"]:
            steps.append({"stage": "consume", "service": consumer["service"],
                          "node_id": consumer["node"]["id"]})
    affected = []
    for consumer in links["consumers"]:
        if consumer["service"]:
            svc = find_node_by_name(db, workspace_id, consumer["service"], ["SERVICE"])
            if svc:
                imp = impact_analysis(db, workspace_id, svc.id, depth=1)
                affected.extend(imp["impacted"])
    return {
        "topic": links["topic"], "producers": links["producers"], "consumers": links["consumers"],
        "steps": steps,
        "downstream_impact": affected[:50],
    }


def tables(db: Session, workspace_id: str) -> list[dict]:
    nodes = list(db.scalars(
        select(Node).where(Node.workspace_id == workspace_id, Node.type.in_(["TABLE", "DATABASE"]),
                           Node.status == "ACTIVE").order_by(Node.type, Node.name)
    ).all())
    out = []
    for n in nodes:
        accessors = db.scalars(
            select(Edge).where(Edge.workspace_id == workspace_id, Edge.dst_id == n.id,
                               Edge.type.in_(["READS", "WRITES"]), Edge.status == "ACTIVE")
        ).all()
        names = {a.src_id for a in accessors}
        services = set()
        for sid in names:
            src = db.get(Node, sid)
            if src and src.repository_id:
                svc = db.scalar(select(Node).where(Node.repository_id == src.repository_id,
                                                   Node.type == "SERVICE", Node.status == "ACTIVE"))
                if svc:
                    services.add(svc.name)
        out.append({**node_dict(n), "accessor_count": len(names), "services": sorted(services)})
    return out


def search_symbols(db: Session, workspace_id: str, query: str, types: list[str] | None = None,
                   limit: int = 30) -> list[dict]:
    like = f"%{query}%"
    stmt = select(Node).where(
        Node.workspace_id == workspace_id, Node.status == "ACTIVE",
        or_(Node.name.ilike(like), Node.qualified_name.ilike(like)),
    )
    if types:
        stmt = stmt.where(Node.type.in_(types))
    nodes = list(db.scalars(stmt.order_by(Node.type, Node.name).limit(limit)).all())
    return [node_dict(n) for n in nodes]


def symbol_detail(db: Session, node: Node) -> dict:
    out_edges = list(db.scalars(
        select(Edge).where(Edge.workspace_id == node.workspace_id, Edge.src_id == node.id,
                           Edge.status == "ACTIVE").limit(200)
    ).all())
    in_edges = list(db.scalars(
        select(Edge).where(Edge.workspace_id == node.workspace_id, Edge.dst_id == node.id,
                           Edge.status == "ACTIVE").limit(200)
    ).all())
    names = {n.id: n.name for n in db.scalars(
        select(Node).where(Node.workspace_id == node.workspace_id, Node.status == "ACTIVE")
    ).all()}
    return {
        "node": node_dict(node),
        "outgoing": [edge_dict(e, names) for e in out_edges],
        "incoming": [edge_dict(e, names) for e in in_edges],
        "enrichments": [
            {"id": e.id, "category": e.category, "status": e.status, "summary": e.summary,
             "confidence": e.confidence}
            for e in db.scalars(select(Enrichment).where(Enrichment.target_id == node.id)).all()
        ],
    }


def documents_search(db: Session, workspace_id: str, query: str | None = None,
                     source_type: str | None = None, limit: int = 50) -> list[dict]:
    stmt = select(Document).where(Document.workspace_id == workspace_id)
    if source_type:
        stmt = stmt.where(Document.source_type == source_type)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Document.title.ilike(like), Document.content.ilike(like)))
    docs = list(db.scalars(stmt.order_by(Document.updated_at.desc()).limit(limit)).all())
    return [
        {"id": d.id, "title": d.title, "source_type": d.source_type, "external_id": d.external_id,
         "url": d.url, "version": d.version, "status": d.status, "updated_at": d.updated_at.isoformat(),
         "excerpt": (d.content or "")[:280]}
        for d in docs
    ]


def document_detail(db: Session, doc: Document) -> dict:
    links = []
    for link in db.scalars(select(DocumentLink).where(DocumentLink.document_id == doc.id)).all():
        node = db.get(Node, link.target_id) if link.target_type == "node" else None
        links.append({
            "id": link.id, "target_type": link.target_type, "target_id": link.target_id,
            "target_name": node.name if node else None,
            "target_node_type": node.type if node else None,
            "link_type": link.link_type, "confidence": link.confidence,
            "detection_method": link.detection_method, "approved": link.approved,
        })
    return {
        "document": {
            "id": doc.id, "title": doc.title, "source_type": doc.source_type,
            "external_id": doc.external_id, "url": doc.url, "version": doc.version,
            "status": doc.status, "content": doc.content, "metadata": doc.doc_metadata,
            "content_hash": doc.content_hash, "updated_at": doc.updated_at.isoformat(),
        },
        "links": links,
    }


def workspace_health(db: Session, workspace_id: str) -> dict:
    stats = workspace_stats(db, workspace_id)
    repos = list(db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)).all())
    last_scan = db.scalar(
        select(ScanRun).where(ScanRun.workspace_id == workspace_id)
        .order_by(ScanRun.created_at.desc()).limit(1)
    )
    failed = db.scalar(
        select(func.count(ScanRun.id)).where(ScanRun.workspace_id == workspace_id, ScanRun.status == "FAILED")
    )
    return {
        "stats": stats,
        "repositories": [
            {"id": r.id, "name": r.name, "status": r.status, "last_commit": r.last_commit,
             "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
             "error": r.error, "language": r.language}
            for r in repos
        ],
        "last_scan": {
            "id": last_scan.id, "status": last_scan.status,
            "finished_at": last_scan.finished_at.isoformat() if last_scan.finished_at else None,
            "commit": last_scan.commit_sha, "trigger": last_scan.trigger,
        } if last_scan else None,
        "failed_scans": int(failed or 0),
    }


def flows(db: Session, workspace_id: str) -> list[dict]:
    flows = list(db.scalars(
        select(BusinessFlow).where(BusinessFlow.workspace_id == workspace_id).order_by(BusinessFlow.name)
    ).all())
    return [
        {"id": f.id, "name": f.name, "description": f.description, "steps": f.steps,
         "detection_method": f.detection_method, "confidence": f.confidence, "status": f.status}
        for f in flows
    ]


def ai_insights(db: Session, workspace_id: str, status: str | None = None,
                target_id: str | None = None) -> list[dict]:
    stmt = select(Enrichment).where(Enrichment.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Enrichment.status == status)
    if target_id:
        stmt = stmt.where(Enrichment.target_id == target_id)
    rows = list(db.scalars(stmt.order_by(Enrichment.updated_at.desc()).limit(200)).all())
    return [
        {"id": e.id, "target_type": e.target_type, "target_id": e.target_id,
         "target_name": e.target_name, "category": e.category, "status": e.status,
         "summary": e.summary, "confidence": e.confidence, "provider": e.provider,
         "model": e.model, "prompt_version": e.prompt_version,
         "tokens_in": e.tokens_in, "tokens_out": e.tokens_out,
         "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat()}
        for e in rows
    ]
