from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, ensure_workspace_access, get_principal
from ..graphstore import (
    edge_dict,
    find_nodes,
    impact_analysis,
    neighbourhood,
    node_dict,
    shortest_path,
    unresolved_dependencies,
    workspace_stats,
)
from ..models import Edge, Node, Repository, new_id
from ..queries import (
    api_details,
    find_node_by_name,
    flows,
    get_node,
    list_apis,
    list_services,
    service_architecture,
    service_dependencies,
    service_summary,
    symbol_detail,
    tables,
    topic_links,
    topics,
    trace_event_flow,
)
from ..scanner.git_provider import GitSource
from ..security import has_role

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/workspaces/{workspace_id}/stats")
def stats(workspace_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return workspace_stats(db, workspace_id)


@router.get("/workspaces/{workspace_id}/nodes")
def nodes(
    workspace_id: str,
    q: str | None = None,
    type: str | None = Query(default=None, description="Comma separated node types"),
    repository_id: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    ensure_workspace_access(db, principal, workspace_id)
    types = [t.strip() for t in type.split(",") if t.strip()] if type else None
    rows, total = find_nodes(db, workspace_id, q, types, repository_id, limit, offset)
    return {"items": [node_dict(n) for n in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/workspaces/{workspace_id}/nodes/{node_id}")
def node_detail(workspace_id: str, node_id: str, principal: Principal = Depends(get_principal),
                db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    node = get_node(db, workspace_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    detail = symbol_detail(db, node)
    detail["parents"] = []
    parent_id = node.parent_id
    depth = 0
    while parent_id and depth < 8:
        parent = db.get(Node, parent_id)
        if not parent:
            break
        detail["parents"].append({"id": parent.id, "type": parent.type, "name": parent.name})
        parent_id = parent.parent_id
        depth += 1
    return detail


@router.get("/workspaces/{workspace_id}/graph")
def graph(
    workspace_id: str,
    node_id: list[str] | None = Query(default=None),
    depth: int = Query(1, ge=1, le=3),
    edge_types: str | None = None,
    node_types: str | None = None,
    limit: int = Query(300, le=800),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    ensure_workspace_access(db, principal, workspace_id)
    etypes = [e.strip() for e in edge_types.split(",") if e.strip()] if edge_types else None
    ntypes = [n.strip() for n in node_types.split(",") if n.strip()] if node_types else None
    if node_id:
        seeds = [n for n in node_id if get_node(db, workspace_id, n)]
        if not seeds:
            raise HTTPException(status_code=404, detail="Seed node not found")
        return neighbourhood(db, workspace_id, seeds, depth, etypes, ntypes, limit)
    # top-level system view: services + shared infrastructure, no leaf classes
    services = list(db.scalars(
        select(Node).where(Node.workspace_id == workspace_id, Node.status == "ACTIVE",
                           Node.type.in_(["SERVICE", "KAFKA_TOPIC", "DATABASE", "REDIS_CACHE"]))
    ).all())
    ids = [s.id for s in services][:limit]
    return neighbourhood(db, workspace_id, ids, 1, etypes or ["DEPENDS_ON", "PUBLISHES", "CONSUMES",
                                                              "ROUTES_TO", "USES_CACHE"], ntypes, limit)


@router.get("/workspaces/{workspace_id}/services")
def services(workspace_id: str, q: str | None = None,
             principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return list_services(db, workspace_id, q)


@router.get("/workspaces/{workspace_id}/services/{service_id}")
def service_detail(workspace_id: str, service_id: str,
                   principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    node = get_node(db, workspace_id, service_id)
    if not node or node.type != "SERVICE":
        raise HTTPException(status_code=404, detail="Service not found")
    return {**service_summary(db, node), "architecture": service_architecture(db, node)}


@router.get("/workspaces/{workspace_id}/services/{service_id}/dependencies")
def dependencies(workspace_id: str, service_id: str, direction: str = "out",
                 principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return service_dependencies(db, workspace_id, service_id, direction)


@router.get("/workspaces/{workspace_id}/apis")
def apis(workspace_id: str, q: str | None = None, service_id: str | None = None,
         principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return list_apis(db, workspace_id, q, service_id)


@router.get("/workspaces/{workspace_id}/apis/{api_id}")
def api_detail(workspace_id: str, api_id: str, principal: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    node = get_node(db, workspace_id, api_id)
    if not node or node.type != "API_ENDPOINT":
        raise HTTPException(status_code=404, detail="API not found")
    return api_details(db, node)


@router.get("/workspaces/{workspace_id}/topics")
def kafka_topics(workspace_id: str, principal: Principal = Depends(get_principal),
                 db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return topics(db, workspace_id)


@router.get("/workspaces/{workspace_id}/topics/{topic_name}")
def topic_detail(workspace_id: str, topic_name: str,
                 principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    result = topic_links(db, workspace_id, topic_name)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="Topic not found")
    return result


@router.get("/workspaces/{workspace_id}/topics/{topic_name}/trace")
def topic_trace(workspace_id: str, topic_name: str,
                principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return trace_event_flow(db, workspace_id, topic_name)


@router.get("/workspaces/{workspace_id}/tables")
def db_tables(workspace_id: str, principal: Principal = Depends(get_principal),
              db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return tables(db, workspace_id)


@router.get("/workspaces/{workspace_id}/flows")
def business_flows(workspace_id: str, principal: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return flows(db, workspace_id)


@router.get("/workspaces/{workspace_id}/path")
def path(workspace_id: str, src: str, dst: str,
         principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    for node_id in (src, dst):
        if not get_node(db, workspace_id, node_id):
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return shortest_path(db, workspace_id, src, dst)


@router.get("/workspaces/{workspace_id}/impact/{node_id}")
def impact(workspace_id: str, node_id: str, depth: int = Query(2, ge=1, le=5),
           principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    if not get_node(db, workspace_id, node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return impact_analysis(db, workspace_id, node_id, depth)


@router.get("/workspaces/{workspace_id}/unresolved")
def unresolved(workspace_id: str, principal: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    return unresolved_dependencies(db, workspace_id)


@router.get("/workspaces/{workspace_id}/search")
def search(workspace_id: str, q: str = Query(min_length=1), limit: int = 30,
           principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ensure_workspace_access(db, principal, workspace_id)
    rows, total = find_nodes(db, workspace_id, q, None, None, limit, 0)
    return {"items": [node_dict(n) for n in rows], "total": total}


@router.get("/nodes/{node_id}/source")
def source(node_id: str, context: int = Query(3, ge=0, le=40),
           principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    ensure_workspace_access(db, principal, node.workspace_id)
    if not node.repository_id or not node.file_path:
        raise HTTPException(status_code=400, detail="Node has no source location")
    repo = db.get(Repository, node.repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    commit = node.source_revision or repo.last_commit
    git = GitSource(repo)
    content = git.file_at(commit, node.file_path) if commit else None
    if content is None:
        raise HTTPException(status_code=404, detail="Source not available at indexed revision")
    lines = content.splitlines()
    start = max(1, (node.start_line or 1) - context)
    end = min(len(lines), (node.end_line or node.start_line or 1) + context)
    url = None
    if repo.url and "github.com" in repo.url:
        url = f"{repo.url.rstrip('.git')}/blob/{commit}/{quote(node.file_path)}#L{node.start_line or 1}"
    elif repo.url and "gitlab" in repo.url:
        url = f"{repo.url.rstrip('.git')}/-/blob/{commit}/{quote(node.file_path)}#L{node.start_line or 1}"
    return {
        "node": {"id": node.id, "name": node.name, "type": node.type,
                 "qualified_name": node.qualified_name, "properties": node.properties},
        "repository": {"id": repo.id, "name": repo.name, "url": repo.url, "branch": repo.branch},
        "revision": commit,
        "path": node.file_path,
        "start_line": start,
        "end_line": end,
        "highlight": {"start": node.start_line, "end": node.end_line},
        "lines": [{"number": i + start, "text": lines[i]} for i in range(start - 1, end)],
        "external_url": url,
        "verified": commit == node.source_revision,
        "history": git.history(node.file_path, 5),
    }


@router.get("/nodes/{node_id}/edges")
def node_edges(node_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    ensure_workspace_access(db, principal, node.workspace_id)
    rows = db.scalars(select(Edge).where(Edge.workspace_id == node.workspace_id,
                                         (Edge.src_id == node.id) | (Edge.dst_id == node.id),
                                         Edge.status == "ACTIVE").limit(400)).all()
    names = {n.id: n.name for n in db.scalars(
        select(Node).where(Node.workspace_id == node.workspace_id)).all()}
    return [edge_dict(e, names) for e in rows]


@router.post("/workspaces/{workspace_id}/nodes/{node_id}/confirm-dependency")
def confirm_dependency(workspace_id: str, node_id: str, payload: dict,
                       principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Human confirmation turns an AI-suggested dependency into an approved edge.

    The edge keeps detection_method=AI_INFERRED until a human approves it; only
    then does it get status ACTIVE_APPROVED and APPROVED_BY evidence.
    """
    ensure_workspace_access(db, principal, workspace_id, "ARCHITECT")
    target = payload.get("target_id")
    edge_type = payload.get("type", "DEPENDS_ON")
    node = get_node(db, workspace_id, node_id)
    other = get_node(db, workspace_id, target) if target else None
    if not node or not other:
        raise HTTPException(status_code=404, detail="Node not found")
    from ..graphstore import dedupe_key

    dk = dedupe_key(node.id, edge_type, other.id, None)
    edge = db.scalar(select(Edge).where(Edge.workspace_id == workspace_id, Edge.dedupe_key == dk))
    if edge is None:
        edge = Edge(id=new_id(), workspace_id=workspace_id, dedupe_key=dk, type=edge_type,
                    src_id=node.id, dst_id=other.id, resolved=True,
                    detection_method="AI_INFERRED", confidence=0.5, status="ACTIVE",
                    properties={"confirmed_by": principal.user.email})
        db.add(edge)
    edge.properties = {**(edge.properties or {}), "confirmed_by": principal.user.email}
    db.commit()
    return {"ok": True, "edge_id": edge.id}


@router.get("/ai/categories")
def ai_categories():
    from ..ai.enrich import CATEGORY_SPECS

    return [{"key": key, "label": spec["label"], "focus": spec["focus"],
             "targets": spec["targets"]} for key, spec in CATEGORY_SPECS.items()]
