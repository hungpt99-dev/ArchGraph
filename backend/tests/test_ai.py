"""AI enrichment workflow: selective, evidence-linked, reviewable, never destructive."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.jobs import claim_next, run_job
from app.models import Edge, Enrichment, Node, Repository
from app.scanner.pipeline import scan_repository
from conftest import make_local_repo
from test_pipeline import PAYMENT_SERVICE


def _scanned(client, admin, workspace, db):
    path = make_local_repo(Path("/tmp/archgraph-ai"), PAYMENT_SERVICE, "payment-ai")
    repo = Repository(workspace_id=workspace["id"], name="payment-service", url=path,
                      provider="local", branch="main", default_branch="main", settings={})
    db.add(repo)
    db.commit()
    scan_repository(db, repo, trigger="test", mode="full")
    service = db.scalar(select(Node).where(Node.workspace_id == workspace["id"],
                                           Node.type == "SERVICE"))
    return service


def _provider(client, admin, workspace):
    response = client.post(f"/api/workspaces/{workspace['id']}/ai/providers",
                           headers=admin["headers"],
                           json={"name": "Offline mock", "kind": "mock", "model": "archgraph-mock-v1"})
    assert response.status_code == 201, response.text
    return response.json()


def test_ai_disabled_without_provider(client, admin, workspace, db):
    service = _scanned(client, admin, workspace, db)
    status = client.get(f"/api/workspaces/{workspace['id']}/ai/status", headers=admin["headers"]).json()
    assert status["ai_enabled"] is False and status["provider_configured"] is False
    response = client.post(f"/api/workspaces/{workspace['id']}/ai/enrich", headers=admin["headers"],
                           json={"targets": [{"type": "node", "id": service.id}],
                                 "categories": ["SERVICE_OVERVIEW"]})
    assert response.status_code == 409
    assert "AI is disabled" in response.json()["detail"]


def test_selective_enrichment_estimate_generate_review_approve(client, admin, workspace, db):
    service = _scanned(client, admin, workspace, db)
    _provider(client, admin, workspace)
    nodes_before = db.scalar(select(func.count(Node.id)).where(Node.workspace_id == workspace["id"]))
    edges_before = db.scalar(select(func.count(Edge.id)).where(Edge.workspace_id == workspace["id"]))

    estimate = client.post(f"/api/workspaces/{workspace['id']}/ai/estimate", headers=admin["headers"],
                           json={"targets": [{"type": "node", "id": service.id}],
                                 "categories": ["SERVICE_OVERVIEW", "RISK_ANALYSIS"]}).json()
    assert estimate["estimated_total_tokens"] > 0
    assert estimate["provider_kind"] == "mock"
    assert estimate["targets"] == 1

    response = client.post(f"/api/workspaces/{workspace['id']}/ai/enrich", headers=admin["headers"],
                           json={"targets": [{"type": "node", "id": service.id}],
                                 "categories": ["SERVICE_OVERVIEW", "RISK_ANALYSIS"]})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] == 2 and body["job_id"]

    job = claim_next(db, "test-worker")
    run_job(db, job)

    enrichments = client.get(f"/api/workspaces/{workspace['id']}/ai/enrichments",
                             headers=admin["headers"]).json()
    assert len(enrichments) == 2
    overview = [e for e in enrichments if e["category"] == "SERVICE_OVERVIEW"][0]
    assert overview["status"] == "NEEDS_REVIEW"
    assert overview["provider"] == "mock" and overview["source_revision"]
    detail = client.get(f"/api/ai/enrichments/{overview['id']}", headers=admin["headers"]).json()
    assert detail["content"]["summary"]
    assert detail["evidence"], "AI output must carry evidence references"
    assert any(e.get("file") for e in detail["evidence"])
    assert detail["tokens_in"] > 0

    approved = client.post(f"/api/ai/enrichments/{overview['id']}/approve", headers=admin["headers"],
                           json={"note": "looks right"})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"

    other = [e for e in enrichments if e["category"] == "RISK_ANALYSIS"][0]
    rejected = client.post(f"/api/ai/enrichments/{other['id']}/reject", headers=admin["headers"],
                           json={"note": "too speculative"})
    assert rejected.json()["status"] == "REJECTED"

    # AI never mutates the deterministic graph
    nodes_after = db.scalar(select(func.count(Node.id)).where(Node.workspace_id == workspace["id"]))
    edges_after = db.scalar(select(func.count(Edge.id)).where(Edge.workspace_id == workspace["id"]))
    assert (nodes_before, edges_before) == (nodes_after, edges_after)
    assert db.scalar(select(func.count(Edge.id)).where(Edge.detection_method == "AI_INFERRED")) == 0

    usage = client.get(f"/api/workspaces/{workspace['id']}/ai/usage", headers=admin["headers"]).json()
    assert usage["tokens_in"] > 0 and usage["by_status"]["APPROVED"]["count"] == 1


def test_reenrich_resets_status_and_policy_is_enforced(client, admin, workspace, db):
    service = _scanned(client, admin, workspace, db)
    _provider(client, admin, workspace)
    created = client.post(f"/api/workspaces/{workspace['id']}/ai/enrich", headers=admin["headers"],
                          json={"targets": [{"type": "node", "id": service.id}],
                                "categories": ["SERVICE_OVERVIEW"]}).json()
    job = claim_next(db, "test-worker")
    run_job(db, job)
    enrichment_id = created["enrichment_ids"][0]
    assert client.post(f"/api/ai/enrichments/{enrichment_id}/approve", headers=admin["headers"],
                       json={}).json()["status"] == "APPROVED"
    again = client.post(f"/api/ai/enrichments/{enrichment_id}/reenrich", headers=admin["headers"])
    assert again.status_code == 202
    db.expire_all()
    assert db.get(Enrichment, enrichment_id).status == "QUEUED"

    # category policy narrows what can be requested
    client.patch(f"/api/workspaces/{workspace['id']}/ai/policy", headers=admin["headers"],
                 json={"ai_allowed_categories": ["DOCUMENTATION"]})
    denied = client.post(f"/api/workspaces/{workspace['id']}/ai/enrich", headers=admin["headers"],
                         json={"targets": [{"type": "node", "id": service.id}],
                               "categories": ["SERVICE_OVERVIEW"]})
    assert denied.status_code == 400
