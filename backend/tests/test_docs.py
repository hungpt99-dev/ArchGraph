"""Documentation ingestion: Markdown, ADR, OpenAPI, Jira fixtures and linking."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.docs.connectors import link_documents, sync_repository_docs, upsert_document
from app.models import Document, DocumentLink, Node, Repository
from app.scanner.pipeline import scan_repository
from conftest import make_local_repo
from test_pipeline import PAYMENT_SERVICE

DOC_REPO = dict(PAYMENT_SERVICE)
DOC_REPO.update({
    "docs/architecture.md": """# Payments architecture
payment-service authorises cards and publishes payment.completed.
The payments table is owned by payment-service.
""",
    "docs/adr/ADR-001-events.md": """# ADR-001 Event driven invoicing
Status: accepted
Invoices are generated from payment.completed instead of synchronous calls.
""",
    "api/openapi.yaml": """openapi: 3.0.3
info:
  title: Payments API
  version: 1.0.0
paths:
  /api/payments/authorize:
    post:
      summary: Authorise a payment
  /api/payments/{id}:
    get:
      summary: Read a payment
""",
})


def _repo(db, workspace, files=DOC_REPO, name="payment-docs"):
    path = make_local_repo(Path("/tmp/archgraph-docs"), files, name)
    repo = Repository(workspace_id=workspace["id"], name="payment-service", url=path,
                      provider="local", branch="main", default_branch="main", settings={})
    db.add(repo)
    db.commit()
    scan_repository(db, repo, trigger="test", mode="full")
    db.refresh(repo)
    return repo


def test_markdown_adr_and_openapi_ingestion(db, workspace):
    repo = _repo(db, workspace)
    result = sync_repository_docs(db, repo)
    assert result["documents"] >= 2
    assert result["specs"] >= 1
    db.commit()
    docs = db.scalars(select(Document).where(Document.workspace_id == workspace["id"])).all()
    sources = {d.source_type for d in docs}
    assert {"markdown", "adr", "openapi"} <= sources
    openapi = [d for d in docs if d.source_type == "openapi"][0]
    assert openapi.title == "Payments API"
    assert openapi.content_hash

    # OpenAPI endpoints become API_ENDPOINT nodes with IMPORTED_DOC provenance
    spec_api = db.scalar(select(Node).where(Node.workspace_id == workspace["id"],
                                            Node.natural_key.like("%api-spec:POST:/api/payments/authorize")))
    assert spec_api is not None and spec_api.detection_method == "IMPORTED_DOC"


def test_documents_link_to_code_deterministically(db, workspace):
    repo = _repo(db, workspace)
    sync_repository_docs(db, repo)
    link_documents(db, workspace["id"])
    db.commit()
    service = db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "SERVICE"))
    links = db.scalars(select(DocumentLink).where(DocumentLink.target_id == service.id)).all()
    assert links, "markdown mentioning the service name should link to it"
    assert all(link.detection_method == "IMPORTED_DOC" for link in links)


def test_document_ingestion_is_idempotent(db, workspace):
    repo = _repo(db, workspace)
    sync_repository_docs(db, repo)
    db.commit()
    first = db.scalar(select(__import__("sqlalchemy").func.count(Document.id)))
    sync_repository_docs(db, repo)
    db.commit()
    second = db.scalar(select(__import__("sqlalchemy").func.count(Document.id)))
    assert first == second


def test_jira_fixture_document_and_requirement_link(db, workspace, client, admin):
    repo = _repo(db, workspace)
    sync_repository_docs(db, repo)
    doc, created = upsert_document(
        db, workspace["id"], "jira", "HOTEL-142",
        "HOTEL-142: Cancel and refund",
        "# HOTEL-142\n\nCancelling a reservation must refund payment-service and publish payment.completed.",
        url="https://jira.example.com/browse/HOTEL-142",
        metadata={"project": "HOTEL", "source": "fixture"},
    )
    db.commit()
    assert created is True
    link_documents(db, workspace["id"])
    db.commit()
    links = db.scalars(select(DocumentLink).where(DocumentLink.document_id == doc.id)).all()
    assert links and links[0].link_type == "IMPLEMENTS_REQUIREMENT"

    detail = client.get(f"/api/documents/{doc.id}", headers=admin["headers"]).json()
    assert detail["document"]["source_type"] == "jira"
    assert detail["links"]

    search = client.get(f"/api/workspaces/{workspace['id']}/documents?q=refund", headers=admin["headers"]).json()
    assert any(d["external_id"] == "HOTEL-142" for d in search)


def test_markdown_upload_endpoint(client, admin, workspace):
    response = client.post(
        f"/api/workspaces/{workspace['id']}/documents/upload",
        headers=admin["headers"],
        files={"file": ("runbook.md", b"# Runbook\n\nReplay payment.completed events for payment-service.")},
        data={"title": "Payments runbook"},
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/api/workspaces/{workspace['id']}/documents/stats",
                      headers=admin["headers"]).json()["total"] == 1
