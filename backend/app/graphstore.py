"""Graph storage: upserts, batched writes and traversal queries over PostgreSQL."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from .models import Edge, Node, new_id

GLOBAL_PREFIXES = ("topic:", "db:", "redis:", "artifact:")


def now() -> datetime:
    return datetime.now(timezone.utc)


def dedupe_key(src_id: str, type_: str, dst_id: str | None, dst_ref: str | None) -> str:
    import hashlib

    raw = f"{src_id}|{type_}|{dst_id or ''}|{dst_ref or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


class GraphWriter:
    """Batched, idempotent graph writer used by the scanner pipeline."""

    def __init__(self, db: Session, workspace_id: str, repository_id: str, commit: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.repository_id = repository_id
        self.commit = commit
        self.key_to_id: dict[str, str] = {}
        self.created_nodes = 0
        self.updated_nodes = 0
        self.created_edges = 0
        self.updated_edges = 0
        self.unresolved_edges = 0
        self.seen_keys: set[str] = set()
        self.parent_updates: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ #
    def load_existing(self) -> None:
        rows = self.db.execute(
            select(Node.id, Node.natural_key).where(Node.workspace_id == self.workspace_id)
        ).all()
        self.key_to_id = {r.natural_key: r.id for r in rows}

    def _is_global(self, key: str) -> bool:
        return not key.startswith(f"repo:{self.repository_id}:") and key.startswith(GLOBAL_PREFIXES)

    def upsert_nodes(self, nodes: dict[str, object]) -> None:
        if not self.key_to_id:
            self.load_existing()
        existing_rows = {
            n.natural_key: n
            for n in self.db.scalars(
                select(Node).where(
                    Node.workspace_id == self.workspace_id,
                    Node.natural_key.in_(list(nodes.keys())),
                )
            ).all()
        }
        ts = now()
        for key, sn in nodes.items():  # type: ignore[assignment]
            self.seen_keys.add(key)
            is_global = self._is_global(key)
            node = existing_rows.get(key)
            evidence = {
                "repository_id": self.repository_id,
                "commit": self.commit,
                "file": sn.file_path,
                "line": sn.start_line,
                "detection_method": sn.detection,
            }
            if node is None:
                node = Node(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    repository_id=None if is_global else self.repository_id,
                    natural_key=key,
                    type=sn.type,
                    name=sn.name,
                    qualified_name=sn.qualified_name,
                    file_path=sn.file_path,
                    start_line=sn.start_line,
                    end_line=sn.end_line,
                    source_revision=self.commit,
                    detection_method=sn.detection,
                    confidence=sn.confidence,
                    status="ACTIVE",
                    properties={**(sn.properties or {}), "natural_key": key},
                    evidence=evidence,
                    first_seen=ts,
                    last_seen=ts,
                )
                self.db.add(node)
                self.created_nodes += 1
            else:
                node.repository_id = node.repository_id or (None if is_global else self.repository_id)
                node.type = sn.type
                node.name = sn.name
                node.qualified_name = sn.qualified_name
                if sn.file_path:
                    node.file_path = sn.file_path
                    node.start_line = sn.start_line
                    node.end_line = sn.end_line
                node.source_revision = self.commit
                node.detection_method = sn.detection
                node.confidence = sn.confidence
                node.status = "ACTIVE"
                node.properties = {**(sn.properties or {}), "natural_key": key}
                node.evidence = evidence
                node.last_seen = ts
                self.updated_nodes += 1
            self.db.flush()
            self.key_to_id[key] = node.id
            if sn.parent_key:
                self.parent_updates.append((node.id, sn.parent_key))
        self.db.flush()

    def apply_parents(self) -> None:
        for node_id, parent_key in self.parent_updates:
            pid = self.key_to_id.get(parent_key)
            if pid and pid != node_id:
                self.db.execute(
                    text("UPDATE nodes SET parent_id = :pid WHERE id = :id AND parent_id IS DISTINCT FROM :pid"),
                    {"pid": pid, "id": node_id},
                )
        self.db.flush()

    def upsert_edges(self, edges: list[object]) -> None:
        if not edges:
            return
        ts = now()
        by_dedupe: dict[str, list] = defaultdict(list)
        for e in edges:  # type: ignore[assignment]
            src_id = self.key_to_id.get(e.src_key)
            if not src_id:
                continue
            dst_id = self.key_to_id.get(e.dst_key) if e.dst_key else None
            if e.dst_key and not dst_id:
                dst_id = self.key_to_id.get(str(e.dst_key))
            dk = dedupe_key(src_id, e.type, dst_id, None if dst_id else e.dst_ref)
            by_dedupe[dk].append((e, src_id, dst_id))

        existing = {
            row.dedupe_key: row
            for row in self.db.scalars(
                select(Edge).where(
                    Edge.workspace_id == self.workspace_id, Edge.dedupe_key.in_(list(by_dedupe))
                )
            ).all()
        }
        for dk, items in by_dedupe.items():
            e, src_id, dst_id = items[0]
            evidence = {
                "repository_id": self.repository_id,
                "commit": self.commit,
                "file": e.file_path,
                "line": e.line,
                "detection_method": e.detection,
            }
            edge = existing.get(dk)
            if edge is None:
                edge = Edge(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    repository_id=self.repository_id,
                    dedupe_key=dk,
                    type=e.type,
                    src_id=src_id,
                    dst_id=dst_id,
                    dst_ref=None if dst_id else e.dst_ref,
                    resolved=bool(dst_id),
                    detection_method=e.detection,
                    confidence=e.confidence,
                    status="ACTIVE",
                    source_revision=self.commit,
                    file_path=e.file_path,
                    start_line=e.line,
                    properties=e.properties or {},
                    evidence=evidence,
                    first_seen=ts,
                    last_seen=ts,
                )
                self.db.add(edge)
                self.created_edges += 1
            else:
                edge.repository_id = self.repository_id
                edge.dst_id = dst_id or edge.dst_id
                edge.dst_ref = None if dst_id else e.dst_ref
                edge.resolved = bool(dst_id or edge.dst_id)
                edge.detection_method = e.detection
                edge.confidence = max(edge.confidence, e.confidence)
                edge.status = "ACTIVE"
                edge.source_revision = self.commit
                edge.properties = e.properties or {}
                edge.evidence = evidence
                edge.last_seen = ts
                self.updated_edges += 1
            if not edge.resolved:
                self.unresolved_edges += 1
        self.db.flush()

    def mark_missing_removed(self, restrict_files: set[str] | None = None) -> int:
        stmt = select(Node).where(
            Node.workspace_id == self.workspace_id,
            Node.repository_id == self.repository_id,
            Node.status == "ACTIVE",
            Node.type.notin_(["REPOSITORY"]),
            Node.natural_key.notlike("api:GW:%"),
        )
        if restrict_files is not None:
            stmt = stmt.where(Node.file_path.in_(list(restrict_files)))
        count = 0
        for node in self.db.scalars(stmt).all():
            if node.natural_key not in self.seen_keys:
                node.status = "REMOVED"
                count += 1
        self.db.flush()
        return count


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def workspace_stats(db: Session, workspace_id: str) -> dict:
    node_counts = dict(
        db.execute(
            select(Node.type, func.count(Node.id))
            .where(Node.workspace_id == workspace_id, Node.status == "ACTIVE")
            .group_by(Node.type)
        ).all()
    )
    edge_counts = dict(
        db.execute(
            select(Edge.type, func.count(Edge.id))
            .where(Edge.workspace_id == workspace_id, Edge.status == "ACTIVE")
            .group_by(Edge.type)
        ).all()
    )
    unresolved = db.scalar(
        select(func.count(Edge.id)).where(
            Edge.workspace_id == workspace_id, Edge.resolved.is_(False), Edge.status == "ACTIVE"
        )
    )
    return {
        "nodes": sum(node_counts.values()),
        "edges": sum(edge_counts.values()),
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "unresolved_edges": int(unresolved or 0),
    }


def node_dict(n: Node) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "name": n.name,
        "qualified_name": n.qualified_name,
        "workspace_id": n.workspace_id,
        "repository_id": n.repository_id,
        "parent_id": n.parent_id,
        "file_path": n.file_path,
        "start_line": n.start_line,
        "end_line": n.end_line,
        "source_revision": n.source_revision,
        "detection_method": n.detection_method,
        "confidence": n.confidence,
        "status": n.status,
        "properties": n.properties,
        "evidence": n.evidence,
        "first_seen": n.first_seen.isoformat() if n.first_seen else None,
        "last_seen": n.last_seen.isoformat() if n.last_seen else None,
    }


def edge_dict(e: Edge, node_names: dict[str, str] | None = None) -> dict:
    names = node_names or {}
    return {
        "id": e.id,
        "type": e.type,
        "src_id": e.src_id,
        "dst_id": e.dst_id,
        "dst_ref": e.dst_ref,
        "src_name": names.get(e.src_id),
        "dst_name": names.get(e.dst_id or ""),
        "resolved": e.resolved,
        "detection_method": e.detection_method,
        "confidence": e.confidence,
        "status": e.status,
        "properties": e.properties,
        "evidence": e.evidence,
        "file_path": e.file_path,
        "start_line": e.start_line,
    }


def neighbourhood(
    db: Session,
    workspace_id: str,
    node_ids: list[str],
    depth: int = 1,
    edge_types: list[str] | None = None,
    node_types: list[str] | None = None,
    limit: int = 400,
) -> dict:
    """Breadth-first expansion limited to ``limit`` nodes (progressive disclosure)."""
    seed = set(node_ids)
    visited = set(node_ids)
    frontier = set(node_ids)
    edges_out: dict[str, Edge] = {}
    for _ in range(max(1, min(depth, 4))):
        if not frontier:
            break
        stmt = select(Edge).where(
            Edge.workspace_id == workspace_id,
            Edge.status == "ACTIVE",
            or_(Edge.src_id.in_(frontier), Edge.dst_id.in_(frontier)),
        )
        if edge_types:
            stmt = stmt.where(Edge.type.in_(edge_types))
        stmt = stmt.limit(limit * 8)
        new_frontier: set[str] = set()
        for edge in db.scalars(stmt).all():
            if edge.dst_id is None and edge.src_id not in frontier:
                continue
            edges_out[edge.id] = edge
            other = edge.dst_id if edge.src_id in frontier else edge.src_id
            if other and other not in visited and len(visited) < limit:
                visited.add(other)
                new_frontier.add(other)
        frontier = new_frontier

    nodes = db.scalars(
        select(Node).where(Node.id.in_(visited), Node.status == "ACTIVE")
    ).all()
    allowed = {n.id for n in nodes}
    if node_types:
        allowed = {n.id for n in nodes if n.type in node_types}
        # keep seeds regardless so the view never collapses entirely
        allowed |= seed & {n.id for n in nodes}
    names = {n.id: n.name for n in nodes}
    edges = [
        edge_dict(e, names)
        for e in edges_out.values()
        if e.src_id in allowed and (e.dst_id in allowed or e.dst_id is None)
    ]
    return {
        "nodes": [node_dict(n) for n in nodes if n.id in allowed],
        "edges": edges,
        "truncated": len(visited) >= limit,
    }


def shortest_path(db: Session, workspace_id: str, src_id: str, dst_id: str, max_depth: int = 6) -> dict:
    sql = text(
        """
        WITH RECURSIVE paths(node_id, path, depth) AS (
            SELECT CAST(:src AS varchar), ARRAY[CAST(:src AS varchar)], 0
            UNION ALL
            SELECT e.dst_id, p.path || e.dst_id, p.depth + 1
            FROM paths p
            JOIN edges e ON e.src_id = p.node_id
            WHERE e.workspace_id = :ws AND e.status = 'ACTIVE' AND e.dst_id IS NOT NULL
              AND p.depth < :max_depth AND NOT e.dst_id = ANY(p.path)
        )
        SELECT path, depth FROM paths WHERE node_id = :dst ORDER BY depth LIMIT 1
        """
    )
    row = db.execute(sql, {"src": src_id, "dst": dst_id, "ws": workspace_id, "max_depth": max_depth}).first()
    if not row:
        return {"found": False, "path": [], "edges": []}
    path: list[str] = list(row[0])
    nodes = db.scalars(select(Node).where(Node.id.in_(path))).all()
    by_id = {n.id: n for n in nodes}
    edge_rows = db.scalars(
        select(Edge).where(
            Edge.workspace_id == workspace_id,
            Edge.src_id.in_(path),
            Edge.dst_id.in_(path),
            Edge.status == "ACTIVE",
        )
    ).all()
    names = {n.id: n.name for n in nodes}
    edges = []
    for i in range(len(path) - 1):
        match = next((e for e in edge_rows if e.src_id == path[i] and e.dst_id == path[i + 1]), None)
        if match:
            edges.append(edge_dict(match, names))
    return {
        "found": True,
        "path": [node_dict(by_id[i]) for i in path if i in by_id],
        "edges": edges,
        "length": len(path) - 1,
    }


def impact_analysis(db: Session, workspace_id: str, node_id: str, depth: int = 2) -> dict:
    """Reverse dependency closure: what is affected if this node changes."""
    sql = text(
        """
        WITH RECURSIVE up(node_id, depth, via) AS (
            SELECT CAST(:node AS varchar), 0, CAST(NULL AS varchar)
            UNION
            SELECT e.src_id, up.depth + 1, e.type
            FROM up JOIN edges e ON e.dst_id = up.node_id
            WHERE e.workspace_id = :ws AND e.status = 'ACTIVE' AND e.dst_id IS NOT NULL
              AND up.depth < :depth
        )
        SELECT node_id, min(depth) AS depth FROM up GROUP BY node_id ORDER BY depth
        """
    )
    rows = db.execute(sql, {"node": node_id, "ws": workspace_id, "depth": max(1, min(depth, 5))}).all()
    ids = [r[0] for r in rows]
    nodes = {n.id: n for n in db.scalars(select(Node).where(Node.id.in_(ids))).all()}
    direct_edges = db.scalars(
        select(Edge).where(
            Edge.workspace_id == workspace_id,
            Edge.dst_id.in_(ids),
            Edge.status == "ACTIVE",
        )
    ).all()
    names = {n.id: n.name for n in nodes.values()}
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        node = nodes.get(r[0])
        if not node:
            continue
        groups[node.type].append({**node_dict(node), "depth": r[1]})
    return {
        "root": node_id,
        "impacted_count": len(ids) - 1,
        "by_type": {k: len(v) for k, v in groups.items()},
        "impacted": [item for items in groups.values() for item in items if item["id"] != node_id],
        "edges": [edge_dict(e, names) for e in direct_edges],
        "max_depth": depth,
    }


def find_nodes(
    db: Session,
    workspace_id: str,
    query: str | None = None,
    types: list[str] | None = None,
    repository_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Node], int]:
    stmt = select(Node).where(Node.workspace_id == workspace_id, Node.status == "ACTIVE")
    if types:
        stmt = stmt.where(Node.type.in_(types))
    if repository_id:
        stmt = stmt.where(Node.repository_id == repository_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(Node.name.ilike(like), Node.qualified_name.ilike(like), Node.file_path.ilike(like))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Node.type, Node.name).offset(offset).limit(limit)).all()
    return list(rows), int(total)


def unresolved_dependencies(db: Session, workspace_id: str, limit: int = 200) -> list[dict]:
    names = {
        n.id: n.name
        for n in db.scalars(select(Node).where(Node.workspace_id == workspace_id)).all()
    }
    rows = db.scalars(
        select(Edge)
        .where(Edge.workspace_id == workspace_id, Edge.resolved.is_(False), Edge.status == "ACTIVE")
        .order_by(Edge.type, Edge.dst_ref)
        .limit(limit)
    ).all()
    return [edge_dict(e, names) for e in rows]
