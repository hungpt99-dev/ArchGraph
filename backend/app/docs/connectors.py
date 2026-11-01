"""Documentation ingestion connectors.

Every connector is idempotent (content hash + external id) and records version,
URL, sync status and errors. Jira/Confluence use their REST APIs with basic auth
(email + API token) or bearer tokens; markdown/OpenAPI/AsyncAPI are read from
the indexed repository revision. Nothing here requires an LLM.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Document, DocumentLink, IndexedFile, Node, Repository, new_id
from ..security import decrypt_secret, redact_secrets, validate_outbound_url

settings = get_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def upsert_document(
    db: Session,
    workspace_id: str,
    source_type: str,
    external_id: str,
    title: str,
    content: str,
    url: str | None = None,
    version: str | None = None,
    metadata: dict | None = None,
    repository_id: str | None = None,
) -> tuple[Document, bool]:
    content = redact_secrets(content or "")
    digest = _hash(content)
    doc = db.scalar(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.source_type == source_type,
            Document.external_id == external_id,
        )
    )
    created = False
    if doc is None:
        doc = Document(
            id=new_id(), workspace_id=workspace_id, source_type=source_type,
            external_id=external_id, title=title[:500], url=url, version=version,
            content=content, content_hash=digest, doc_metadata=metadata or {},
            status="SYNCED", repository_id=repository_id, synced_at=_now(),
        )
        db.add(doc)
        created = True
    else:
        if doc.content_hash != digest:
            doc.content = content
            doc.content_hash = digest
            doc.version = version or doc.version
            doc.status = "SYNCED"
        doc.title = title[:500]
        doc.url = url or doc.url
        doc.doc_metadata = {**(doc.doc_metadata or {}), **(metadata or {})}
        doc.repository_id = repository_id or doc.repository_id
        doc.synced_at = _now()
    db.flush()
    return doc, created


# --------------------------------------------------------------------------- #
# Markdown / ADR / OpenAPI / AsyncAPI (from repository checkouts)
# --------------------------------------------------------------------------- #
def sync_repository_docs(db: Session, repo: Repository) -> dict:
    root = os.path.join(settings.repo_storage_dir, repo.id)
    if not os.path.isdir(root):
        return {"error": "repository is not cloned yet", "documents": 0}
    md_count = 0
    spec_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "target", "build", "dist")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            lower = name.lower()
            if lower.endswith((".md", ".markdown", ".adoc")):
                try:
                    text = open(full, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                is_adr = bool(re.search(r"(^|/)adr", rel.lower())) or "adr" in name.lower()
                title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), name)
                upsert_document(
                    db, repo.workspace_id, "adr" if is_adr else "markdown", f"{repo.id}:{rel}",
                    title or rel, text, url=f"{repo.url}/blob/{repo.last_commit}/{rel}" if repo.url else None,
                    metadata={"path": rel, "repository": repo.name}, repository_id=repo.id,
                )
                md_count += 1
            elif lower.endswith((".yaml", ".yml", ".json")) and (
                "openapi" in lower or "swagger" in lower or "asyncapi" in lower
            ):
                try:
                    raw = yaml.safe_load(open(full, encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                if not isinstance(raw, dict):
                    continue
                if raw.get("openapi") or raw.get("swagger"):
                    upsert_document(
                        db, repo.workspace_id, "openapi", f"{repo.id}:{rel}",
                        raw.get("info", {}).get("title", name), json.dumps(raw, indent=1)[:200000],
                        metadata={"path": rel, "paths": len(raw.get("paths") or {})},
                        repository_id=repo.id,
                    )
                    _emit_spec_endpoints(db, repo, raw)
                    spec_count += 1
                elif raw.get("asyncapi"):
                    upsert_document(
                        db, repo.workspace_id, "asyncapi", f"{repo.id}:{rel}",
                        raw.get("info", {}).get("title", name), json.dumps(raw, indent=1)[:200000],
                        metadata={"path": rel, "channels": len(raw.get("channels") or {})},
                        repository_id=repo.id,
                    )
                    _emit_spec_channels(db, repo, raw)
                    spec_count += 1
    db.flush()
    return {"documents": md_count, "specs": spec_count}


def _emit_spec_endpoints(db: Session, repo: Repository, spec: dict) -> None:
    service = db.scalar(
        select(Node).where(Node.repository_id == repo.id, Node.type == "SERVICE", Node.status == "ACTIVE")
    )
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for verb, op in methods.items():
            if verb.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            key = f"repo:{repo.id}:api-spec:{verb.upper()}:{path}"
            node = db.scalar(
                select(Node).where(Node.workspace_id == repo.workspace_id, Node.natural_key == key)
            )
            if node is None:
                node = Node(
                    id=new_id(), workspace_id=repo.workspace_id, repository_id=repo.id,
                    natural_key=key, type="API_ENDPOINT", name=f"{verb.upper()} {path}",
                    qualified_name=f"{verb.upper()} {path}",
                    properties={"http_method": verb.upper(), "path": path,
                                "source": "openapi", "summary": (op or {}).get("summary")},
                    detection_method="IMPORTED_DOC", confidence=1.0,
                    evidence={"repository_id": repo.id, "file": "openapi spec"},
                )
                db.add(node)
                db.flush()
            if service:
                from ..graphstore import dedupe_key

                dk = dedupe_key(service.id, "EXPOSES", node.id, None)
                exists = db.scalar(select(Node).where(Node.id == node.id))
                from ..models import Edge

                if exists and not db.scalar(
                    select(Edge).where(Edge.workspace_id == repo.workspace_id, Edge.dedupe_key == dk)
                ):
                    db.add(Edge(
                        id=new_id(), workspace_id=repo.workspace_id, dedupe_key=dk, type="EXPOSES",
                        src_id=service.id, dst_id=node.id, resolved=True,
                        detection_method="IMPORTED_DOC", confidence=1.0,
                        evidence={"repository_id": repo.id, "file": "openapi spec"},
                    ))


def _emit_spec_channels(db: Session, repo: Repository, spec: dict) -> None:
    for channel in (spec.get("channels") or {}):
        name = str(channel).strip("/")
        if not name:
            continue
        key = f"topic:{name}"
        node = db.scalar(
            select(Node).where(Node.workspace_id == repo.workspace_id, Node.natural_key == key)
        )
        if node is None:
            db.add(Node(
                id=new_id(), workspace_id=repo.workspace_id, repository_id=None,
                natural_key=key, type="KAFKA_TOPIC", name=name, qualified_name=name,
                properties={"source": "asyncapi"}, detection_method="IMPORTED_DOC",
                confidence=1.0, evidence={"repository_id": repo.id, "file": "asyncapi spec"},
            ))
    db.flush()


# --------------------------------------------------------------------------- #
# Jira
# --------------------------------------------------------------------------- #
class JiraConnector:
    def __init__(self, config: dict, secret: str | None) -> None:
        self.base_url = (config.get("base_url") or "").rstrip("/")
        self.email = config.get("email")
        self.project = config.get("project")
        self.secret = secret
        if not self.base_url or not self.secret:
            raise ValueError("Jira requires base_url and an API token")

    def _client(self) -> httpx.Client:
        validate_outbound_url(self.base_url)
        auth = (self.email, self.secret) if self.email else None
        headers = {"Accept": "application/json"}
        if not self.email:
            headers["Authorization"] = f"Bearer {self.secret}"
        return httpx.Client(base_url=self.base_url, auth=auth, headers=headers, timeout=45)

    def fetch(self, since: str | None = None) -> list[dict]:
        jql = f"project = {self.project}" if self.project else "order by updated DESC"
        if since:
            jql = f'{jql} AND updated >= "{since}"' if self.project else f'updated >= "{since}"'
        issues: list[dict] = []
        start = 0
        with self._client() as client:
            while True:
                resp = client.get(
                    "/rest/api/2/search",
                    params={"jql": jql, "startAt": start, "maxResults": 50,
                            "fields": "summary,description,status,labels,components,issuelinks,issuetype,priority,updated,created"},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(f"Jira HTTP {resp.status_code}: {resp.text[:200]}")
                body = resp.json()
                issues.extend(body.get("issues") or [])
                if start + 50 >= int(body.get("total", 0)):
                    break
                start += 50
        return issues


def _adf_to_text(node) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return _adf_to_text(node.get("content") or [])
    return ""


def sync_jira(db: Session, workspace_id: str, config: dict, secret: str | None) -> dict:
    connector = JiraConnector(config, secret)
    issues = connector.fetch(config.get("since"))
    count = 0
    for issue in issues:
        fields = issue.get("fields") or {}
        desc = fields.get("description")
        text = _adf_to_text(desc) if isinstance(desc, (dict, list)) else (desc or "")
        labels = ", ".join(fields.get("labels") or [])
        components = ", ".join(c.get("name", "") for c in fields.get("components") or [])
        body = (
            f"# {issue.get('key')}: {fields.get('summary', '')}\n\n"
            f"Type: {(fields.get('issuetype') or {}).get('name', '')}\n"
            f"Status: {(fields.get('status') or {}).get('name', '')}\n"
            f"Labels: {labels}\nComponents: {components}\n\n{text}\n"
        )
        upsert_document(
            db, workspace_id, "jira", issue.get("key"),
            f"{issue.get('key')}: {fields.get('summary', '')}", body,
            url=f"{connector.base_url}/browse/{issue.get('key')}",
            version=str(fields.get("updated")),
            metadata={"status": (fields.get("status") or {}).get("name"),
                      "issuetype": (fields.get("issuetype") or {}).get("name"),
                      "labels": fields.get("labels") or [], "project": connector.project,
                      "updated": fields.get("updated")},
        )
        count += 1
    return {"documents": count, "source": "jira", "project": connector.project}


# --------------------------------------------------------------------------- #
# Confluence
# --------------------------------------------------------------------------- #
def sync_confluence(db: Session, workspace_id: str, config: dict, secret: str | None) -> dict:
    base_url = (config.get("base_url") or "").rstrip("/")
    space = config.get("space")
    if not base_url or not secret:
        raise ValueError("Confluence requires base_url and an API token")
    validate_outbound_url(base_url)
    auth = (config.get("email"), secret) if config.get("email") else None
    headers = {"Accept": "application/json"}
    if not config.get("email"):
        headers["Authorization"] = f"Bearer {secret}"
    count = 0
    start = 0
    with httpx.Client(base_url=base_url, auth=auth, headers=headers, timeout=45) as client:
        while True:
            resp = client.get(
                "/wiki/rest/api/content",
                params={"spaceKey": space, "type": "page", "limit": 25, "start": start,
                        "expand": "body.storage,version,space"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Confluence HTTP {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
            results = body.get("results") or []
            for page in results:
                content = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
                text = re.sub(r"<[^>]+>", " ", content)
                text = re.sub(r"\s+", " ", text).strip()
                upsert_document(
                    db, workspace_id, "confluence", str(page.get("id")),
                    page.get("title", "Untitled"),
                    f"# {page.get('title')}\n\n{text}",
                    url=f"{base_url}/wiki{page.get('_links', {}).get('webui', '')}",
                    version=str((page.get("version") or {}).get("number")),
                    metadata={"space": space, "page_id": page.get("id")},
                )
                count += 1
            if len(results) < 25:
                break
            start += 25
    return {"documents": count, "source": "confluence", "space": space}


# --------------------------------------------------------------------------- #
# File upload (PDF / DOCX / Markdown)
# --------------------------------------------------------------------------- #
def extract_upload(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader  # lazy import: optional dependency
        import io

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if lower.endswith(".docx"):
        from docx import Document as DocxDocument  # lazy import
        import io

        doc = DocxDocument(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Deterministic document <-> code linking
# --------------------------------------------------------------------------- #
def link_documents(db: Session, workspace_id: str) -> dict:
    """Link documents to nodes by exact name mentions (no LLM involved)."""
    nodes = list(
        db.scalars(
            select(Node).where(
                Node.workspace_id == workspace_id,
                Node.status == "ACTIVE",
                Node.type.in_(["SERVICE", "KAFKA_TOPIC", "API_ENDPOINT", "DATABASE", "TABLE"]),
            )
        ).all()
    )
    docs = list(db.scalars(select(Document).where(Document.workspace_id == workspace_id)).all())
    created = 0
    for doc in docs:
        content = doc.content or ""
        existing = {
            (l.target_id, l.link_type)
            for l in db.scalars(select(DocumentLink).where(DocumentLink.document_id == doc.id)).all()
        }
        for node in nodes:
            name = node.name.split()[-1] if node.type == "API_ENDPOINT" else node.name
            if len(name) < 4:
                continue
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", content):
                link_type = "IMPLEMENTS_REQUIREMENT" if doc.source_type == "jira" else "DOCUMENTS"
                if (node.id, link_type) in existing:
                    continue
                db.add(DocumentLink(
                    id=new_id(), workspace_id=workspace_id, document_id=doc.id,
                    target_type="node", target_id=node.id, link_type=link_type,
                    confidence=0.7, detection_method="IMPORTED_DOC", approved=True,
                ))
                created += 1
    db.flush()
    return {"links_created": created, "documents": len(docs)}
