"""Workspace resolver: binds unresolved references across repositories.

This is what turns per-repository scans into one system graph: Feign clients and
HTTP calls bind to services in other repositories, Kafka topics bind producers
to consumers, shared Maven/npm artifacts bind modules, and tables bind to the
database declared in configuration. Everything it creates is deterministic and
carries confidence + evidence; nothing is fabricated.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BusinessFlow, Edge, Node, Repository, Workspace
from ..graphstore import dedupe_key, new_id


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


class WorkspaceResolver:
    def __init__(self, db: Session, workspace_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.nodes = list(
            db.scalars(
                select(Node).where(Node.workspace_id == workspace_id, Node.status == "ACTIVE")
            ).all()
        )
        self.by_id = {n.id: n for n in self.nodes}
        self.by_key = {n.natural_key: n for n in self.nodes}
        self.services = [n for n in self.nodes if n.type == "SERVICE"]
        self.classes = [n for n in self.nodes if n.type in ("CLASS", "INTERFACE")]
        self.methods = [n for n in self.nodes if n.type in ("METHOD", "FUNCTION")]
        self.tables = [n for n in self.nodes if n.type == "TABLE"]
        self.modules = [n for n in self.nodes if n.type == "MODULE"]
        self.repos = list(db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)).all())
        self.repo_by_id = {r.id: r for r in self.repos}
        self.resolved = 0

    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        self._link_repositories()
        self._resolve_edges()
        self._gateway_routes()
        self._database_containment()
        self._module_dependencies()
        self._service_dependencies()
        self._business_flows()
        self.db.commit()
        return {"resolved_edges": self.resolved}

    # ------------------------------------------------------------------ #
    def _service_index(self) -> dict[str, Node]:
        idx: dict[str, Node] = {}
        for s in self.services:
            for key in {
                s.name,
                s.qualified_name,
                (s.properties or {}).get("artifact"),
                (s.properties or {}).get("main_class"),
            }:
                if key:
                    idx.setdefault(_norm(str(key)), s)
        return idx

    def _link_repositories(self) -> None:
        for repo in self.repos:
            repo_key = f"repository:{repo.id}"
            repository = self.by_key.get(repo_key)
            if repository is None:
                repository = Node(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    repository_id=None,
                    natural_key=repo_key,
                    type="REPOSITORY",
                    name=repo.name,
                    qualified_name=repo.url or repo.name,
                    properties={"url": repo.url, "provider": repo.provider, "branch": repo.branch},
                    detection_method="CONFIG_FILE",
                    confidence=1.0,
                    evidence={"repository_id": repo.id, "commit": repo.last_commit},
                )
                self.db.add(repository)
                self.db.flush()
                self.by_id[repository.id] = repository
                self.by_key[repo_key] = repository
                self.nodes.append(repository)
            # repo CONTAINS its repo-scoped nodes (only top-level types)
            children = [
                n
                for n in self.nodes
                if n.repository_id == repo.id
                and n.type in ("SERVICE", "MODULE", "API_ENDPOINT", "CLASS", "KAFKA_TOPIC", "DATABASE")
                and n.parent_id is None
            ]
            for child in children:
                self._ensure_edge(repository.id, child.id, "CONTAINS", "CONFIG_FILE", 0.95,
                                  properties={"scope": "repository"})

    def _resolve_edges(self) -> None:
        svc_idx = self._service_index()
        pending = list(
            self.db.scalars(
                select(Edge).where(
                    Edge.workspace_id == self.workspace_id,
                    Edge.resolved.is_(False),
                    Edge.status == "ACTIVE",
                )
            ).all()
        )
        for edge in pending:
            ref = edge.dst_ref or ""
            target: Node | None = None
            if ref.startswith("service:"):
                target = svc_idx.get(_norm(ref.split(":", 1)[1]))
            elif ref.startswith("class:"):
                simple = ref.split(":", 1)[1]
                target = self._find_class(simple, edge.repository_id)
            elif ref.startswith("call:"):
                call = ref.split(":", 1)[1]
                if "." in call:
                    owner, method = call.rsplit(".", 1)
                    target = self._find_method(owner, method, edge.repository_id)
            elif ref.startswith("table-by-entity:"):
                entity = ref.split(":", 1)[1]
                target = self._find_table(entity)
            elif ref.startswith("artifact:"):
                _, group, artifact = (ref.split(":", 2) + ["", ""])[:3]
                target = next(
                    (m for m in self.modules
                     if _norm(str((m.properties or {}).get("artifact"))) == _norm(artifact)
                     and m.repository_id != edge.repository_id),
                    None,
                )
            elif ref.startswith("unresolved-topic:"):
                topic = ref.split(":", 1)[1]
                target = next(
                    (n for n in self.nodes if n.type == "KAFKA_TOPIC" and n.name == topic), None
                )
            if target is None:
                continue
            new_key = dedupe_key(edge.src_id, edge.type, target.id, None)
            duplicate = self.db.scalar(
                select(Edge).where(Edge.workspace_id == self.workspace_id,
                                   Edge.dedupe_key == new_key, Edge.id != edge.id)
            )
            if duplicate is not None:
                self.db.delete(edge)
                self.db.flush()
                continue
            edge.dst_id = target.id
            edge.resolved = True
            edge.dst_ref = None
            edge.dedupe_key = new_key
            edge.properties = {
                **(edge.properties or {}),
                "resolution": "workspace-linker",
                "resolved_to": target.qualified_name or target.name,
            }
            edge.confidence = max(edge.confidence, 0.75)
            self.resolved += 1
            if edge.type == "CALLS" and target.repository_id and target.repository_id != edge.repository_id:
                self._service_dep_for(edge.src_id, target.repository_id, "cross-repository-call", 0.65)
        self.db.flush()

    def _find_class(self, simple: str, repo_id: str | None) -> Node | None:
        same = [c for c in self.classes if c.repository_id == repo_id]
        for node in same + self.classes:
            if node.name == simple or (node.qualified_name or "").endswith("." + simple):
                return node
        return None

    def _find_method(self, owner: str, method: str, repo_id: str | None) -> Node | None:
        candidates = [
            m for m in self.methods
            if m.name == method and (m.repository_id == repo_id or repo_id is None)
        ]
        for node in candidates:
            cls = str((node.properties or {}).get("class", ""))
            if cls.endswith("." + owner) or cls == owner or owner == "self":
                return node
        return None

    def _find_table(self, entity: str) -> Node | None:
        for t in self.tables:
            props = t.properties or {}
            if str(props.get("entity", "")).endswith(entity) or t.name == _snake(entity):
                return t
        return None

    def _gateway_routes(self) -> None:
        svc_idx = self._service_index()
        for repo in self.repos:
            routes = (repo.settings or {}).get("gateway_routes") or []
            gateway_service = next(
                (s for s in self.services
                 if s.repository_id == repo.id and "gateway" in s.name.lower()), None
            ) or next((s for s in self.services if s.repository_id == repo.id), None)
            if gateway_service is None:
                continue
            for route in routes:
                uri = str(route.get("uri", ""))
                if uri.startswith("lb://"):
                    target = svc_idx.get(_norm(uri[5:]))
                else:
                    host = re.sub(r"^https?://", "", uri).split("/")[0].split(":")[0]
                    target = svc_idx.get(_norm(host))
                if not target:
                    continue
                self._ensure_edge(
                    gateway_service.id, target.id, "ROUTES_TO", "CONFIGURED_CONNECTION", 0.95,
                    properties={"paths": route.get("paths", []), "route_id": route.get("id"),
                                "config": "spring.cloud.gateway.routes"},
                )
                for path in route.get("paths", []) or []:
                    ep_key = f"api:GW:{path}"
                    ep = next((n for n in self.nodes if n.natural_key == ep_key), None)
                    if ep is None:
                        ep = Node(
                            id=new_id(), workspace_id=self.workspace_id,
                            repository_id=repo.id, natural_key=ep_key,
                            type="API_ENDPOINT", name=f"GET {path}",
                            qualified_name=f"gateway {path}",
                            properties={"http_method": "GET", "path": path, "gateway": True,
                                        "target_service": target.name},
                            detection_method="CONFIGURED_CONNECTION", confidence=0.9,
                            evidence={"repository_id": repo.id, "file": "gateway configuration"},
                        )
                        self.db.add(ep)
                        self.db.flush()
                        self.nodes.append(ep)
                        repository_node = self.by_key.get(f"repository:{repo.id}")
                        if repository_node is not None:
                            self._ensure_edge(repository_node.id, ep.id, "CONTAINS",
                                              "CONFIG_FILE", 0.95,
                                              properties={"scope": "repository"})
                    self._ensure_edge(gateway_service.id, ep.id, "EXPOSES", "CONFIGURED_CONNECTION", 0.9)
                    self._ensure_edge(ep.id, target.id, "ROUTES_TO", "CONFIGURED_CONNECTION", 0.9,
                                      properties={"path": path})

    def _database_containment(self) -> None:
        for repo in self.repos:
            db_urls = (repo.settings or {}).get("declared_databases") or []
            tables = [t for t in self.tables if t.repository_id == repo.id]
            for db_key in db_urls:
                database = next((n for n in self.nodes if n.natural_key == db_key), None)
                if not database:
                    continue
                for table in tables:
                    if (table.properties or {}).get("entity") or (table.properties or {}).get("source") in (
                        "jpa-entity", "sqlalchemy-model", "sql-migration"
                    ):
                        self._ensure_edge(database.id, table.id, "CONTAINS", "FRAMEWORK_INFERRED", 0.85,
                                          properties={"inference": "table belongs to declared datasource"})

    def _module_dependencies(self) -> None:
        artifacts = {
            _norm(str((m.properties or {}).get("artifact"))): m
            for m in self.modules
            if (m.properties or {}).get("artifact")
        }
        pending = list(
            self.db.scalars(
                select(Edge).where(
                    Edge.workspace_id == self.workspace_id,
                    Edge.status == "ACTIVE",
                    Edge.type == "DECLARED_DEPENDENCY",
                )
            ).all()
        )
        for edge in pending:
            ref = edge.dst_ref or ""
            if not ref.startswith("artifact:"):
                continue
            _, group, artifact = (ref.split(":", 2) + ["", ""])[:3]
            target = artifacts.get(_norm(artifact))
            if not target or target.repository_id == edge.repository_id:
                continue
            edge.dst_id = target.id
            edge.resolved = True
            edge.dst_ref = None
            edge.confidence = 0.95
            edge.properties = {**(edge.properties or {}), "resolution": "shared-artifact"}
            self.resolved += 1
            src_service = next((s for s in self.services if s.repository_id == edge.repository_id), None)
            dst_service = next((s for s in self.services if s.repository_id == target.repository_id), None)
            if src_service and dst_service:
                self._ensure_edge(
                    src_service.id, dst_service.id, "DEPENDS_ON", "DECLARED_DEPENDENCY", 0.9,
                    properties={"mechanism": "shared-library", "artifact": artifact},
                )

    def _service_dep_for(self, src_node_id: str, dst_repo_id: str, mechanism: str, confidence: float):
        src = self.by_id.get(src_node_id)
        if src is None:
            return
        src_service = next(
            (s for s in self.services if s.repository_id == src.repository_id), None
        )
        dst_service = next((s for s in self.services if s.repository_id == dst_repo_id), None)
        if src_service and dst_service and src_service.id != dst_service.id:
            self._ensure_edge(src_service.id, dst_service.id, "DEPENDS_ON", "STATIC_AST", confidence,
                              properties={"mechanism": mechanism})

    def _service_dependencies(self) -> None:
        # derive service-level dependencies from resolved outbound HTTP edges
        for edge in self.db.scalars(
            select(Edge).where(
                Edge.workspace_id == self.workspace_id,
                Edge.status == "ACTIVE",
                Edge.type == "DEPENDS_ON",
            )
        ).all():
            if not edge.dst_id:
                continue
            dst = self.by_id.get(edge.dst_id)
            src = self.by_id.get(edge.src_id)
            if not dst or not src or dst.type != "SERVICE" or src.type != "SERVICE":
                continue
            if src.id == dst.id:
                continue
            self._ensure_edge(src.id, dst.id, "DEPENDS_ON", edge.detection_method, edge.confidence,
                              properties={"mechanism": (edge.properties or {}).get("mechanism", "http")})

    def _business_flows(self) -> None:
        topics = [n for n in self.nodes if n.type == "KAFKA_TOPIC"]
        pub = defaultdict(set)
        cons = defaultdict(set)
        for edge in self.db.scalars(
            select(Edge).where(
                Edge.workspace_id == self.workspace_id,
                Edge.status == "ACTIVE",
                Edge.type.in_(["PUBLISHES", "CONSUMES"]),
            )
        ).all():
            src = self.by_id.get(edge.src_id)
            if not src or src.type != "SERVICE" or not edge.dst_id:
                continue
            if edge.type == "PUBLISHES":
                pub[edge.dst_id].add(src.id)
            else:
                cons[edge.dst_id].add(src.id)
        existing = {f.name: f for f in self.db.scalars(
            select(BusinessFlow).where(BusinessFlow.workspace_id == self.workspace_id)
        ).all()}
        for topic in topics:
            producers = pub.get(topic.id, set())
            consumers = cons.get(topic.id, set())
            if not producers or not consumers:
                continue
            name = f"Event flow: {topic.name}"
            steps = []
            for pid in producers:
                p = self.by_id.get(pid)
                if p:
                    steps.append({"order": len(steps) + 1, "node_id": p.id, "name": p.name,
                                  "type": "SERVICE", "action": f"publishes {topic.name}",
                                  "layer": "producer"})
            steps.append({"order": len(steps) + 1, "node_id": topic.id, "name": topic.name,
                          "type": "KAFKA_TOPIC", "action": "event transport", "layer": "transport"})
            for cid in consumers:
                c = self.by_id.get(cid)
                if c:
                    steps.append({"order": len(steps) + 1, "node_id": c.id, "name": c.name,
                                  "type": "SERVICE", "action": f"consumes {topic.name}",
                                  "layer": "consumer"})
            flow = existing.get(name)
            if flow is None:
                self.db.add(BusinessFlow(
                    id=new_id(), workspace_id=self.workspace_id, name=name,
                    description=f"Deterministically derived from Kafka topic {topic.name}: "
                                f"{len(producers)} producer(s), {len(consumers)} consumer(s).",
                    steps=steps, detection_method="FRAMEWORK_INFERRED", confidence=0.7,
                ))
            else:
                flow.steps = steps

    # ------------------------------------------------------------------ #
    def _ensure_edge(self, src_id: str, dst_id: str, type_: str, detection: str,
                     confidence: float, properties: dict | None = None) -> None:
        dk = dedupe_key(src_id, type_, dst_id, None)
        existing = self.db.scalar(
            select(Edge).where(Edge.workspace_id == self.workspace_id, Edge.dedupe_key == dk)
        )
        if existing:
            existing.status = "ACTIVE"
            existing.last_seen = datetime.now(timezone.utc)
            return
        edge = Edge(
            id=new_id(), workspace_id=self.workspace_id, dedupe_key=dk, type=type_,
            src_id=src_id, dst_id=dst_id, resolved=True, detection_method=detection,
            confidence=confidence, status="ACTIVE", properties=properties or {},
            evidence={"resolved_by": "workspace-resolver"},
        )
        self.db.add(edge)
        self.db.flush()
        return
