"""REST API integration tests: auth, RBAC, workspace isolation, jobs, graph endpoints."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.jobs import claim_next, run_job
from app.models import Job, Repository, User
from app.security import hash_password
from conftest import make_local_repo
from test_pipeline import PAYMENT_SERVICE


def test_bootstrap_only_once_and_login(client, admin):
    again = client.post("/api/auth/bootstrap", json={
        "email": "other@test.dev", "name": "Other", "password": "password-12345"})
    assert again.status_code == 409
    login = client.post("/api/auth/login", json={"email": "admin@test.dev", "password": "test-password-123"})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "SYSTEM_ADMIN"
    bad = client.post("/api/auth/login", json={"email": "admin@test.dev", "password": "wrong-password"})
    assert bad.status_code == 401
    me = client.get("/api/auth/me", headers=admin["headers"])
    assert me.status_code == 200 and me.json()["user"]["email"] == "admin@test.dev"


def test_unauthenticated_requests_rejected(client):
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/workspaces", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_workspace_crud(client, admin, workspace):
    listing = client.get("/api/workspaces", headers=admin["headers"]).json()
    assert len(listing) == 1 and listing[0]["name"] == "Test System"
    updated = client.patch(f"/api/workspaces/{workspace['id']}", headers=admin["headers"],
                           json={"name": "Renamed"})
    assert updated.status_code == 200 and updated.json()["name"] == "Renamed"


def test_viewer_role_cannot_add_repository(client, admin, workspace, db):
    db.add(User(email="viewer@test.dev", name="Viewer",
                password_hash=hash_password("viewer-password"), role="VIEWER"))
    db.commit()
    token = client.post("/api/auth/login", json={"email": "viewer@test.dev",
                                                 "password": "viewer-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # viewer is not a member: workspace must not leak
    assert client.get(f"/api/workspaces/{workspace['id']}/services", headers=headers).status_code == 403
    client.post(f"/api/workspaces/{workspace['id']}/members", headers=admin["headers"],
                json={"user_id": db.scalar(select(User).where(User.email == "viewer@test.dev")).id,
                      "role": "VIEWER"})
    assert client.get(f"/api/workspaces/{workspace['id']}/services", headers=headers).status_code == 200
    denied = client.post(f"/api/workspaces/{workspace['id']}/repositories", headers=headers,
                         json={"name": "x", "url": str(Path("/tmp")), "scan_now": False})
    assert denied.status_code == 403


def test_repository_add_queues_scan_and_worker_indexes_it(client, admin, workspace, db):
    repo_path = make_local_repo(Path("/tmp/archgraph-api-src"), PAYMENT_SERVICE, "payment-api")
    created = client.post(f"/api/workspaces/{workspace['id']}/repositories", headers=admin["headers"],
                          json={"name": "payment-service", "url": repo_path, "scan_now": True})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["job_id"]

    job = claim_next(db, "test-worker")
    assert job is not None and job.id == body["job_id"]
    result = run_job(db, job)
    assert result["status"] == "COMPLETED"

    services = client.get(f"/api/workspaces/{workspace['id']}/services", headers=admin["headers"]).json()
    assert any(s["name"] == "payment-service" for s in services)
    service = [s for s in services if s["name"] == "payment-service"][0]
    assert service["api_count"] == 1

    detail = client.get(f"/api/workspaces/{workspace['id']}/services/{service['id']}",
                        headers=admin["headers"]).json()
    assert detail["architecture"]["apis"][0]["properties"]["path"] == "/api/payments/authorize"

    stats = client.get(f"/api/workspaces/{workspace['id']}/stats", headers=admin["headers"]).json()
    assert stats["nodes"] > 0 and stats["edges"] > 0

    node_id = detail["architecture"]["apis"][0]["id"]
    source = client.get(f"/api/nodes/{node_id}/source", headers=admin["headers"]).json()
    assert "PaymentController" in "\n".join(l["text"] for l in source["lines"])
    assert source["verified"] is True

    topics = client.get(f"/api/workspaces/{workspace['id']}/topics", headers=admin["headers"]).json()
    assert any(t["name"] == "payment.completed" for t in topics)

    impact = client.get(f"/api/workspaces/{workspace['id']}/impact/{service['id']}",
                        headers=admin["headers"]).json()
    assert impact["max_depth"] == 2


def test_api_key_auth_and_workspace_scope(client, admin, workspace, db):
    created = client.post("/api/auth/api-keys", headers=admin["headers"],
                          json={"name": "mcp", "workspace_id": workspace["id"], "scopes": ["read"]})
    key = created.json()["api_key"]
    ok = client.get("/api/workspaces", headers={"X-API-Key": key})
    assert ok.status_code == 200
    other = client.post("/api/workspaces", headers=admin["headers"], json={"name": "Other System"})
    denied = client.get(f"/api/workspaces/{other.json()['id']}/services",
                        headers={"X-API-Key": key})
    assert denied.status_code == 403
    key_id = created.json()["id"]
    assert client.delete(f"/api/auth/api-keys/{key_id}", headers=admin["headers"]).status_code == 200
    assert client.get("/api/workspaces", headers={"X-API-Key": key}).status_code == 401


def test_health_readiness_and_metrics(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/ready").json()["status"] == "ready"
    metrics = client.get("/api/metrics").text
    assert "archgraph_graph_nodes" in metrics and "archgraph_repositories" in metrics


def test_openapi_schema_exposes_core_routes(client):
    schema = client.get("/api/openapi.json").json()
    paths = schema["paths"]
    assert "/api/workspaces/{workspace_id}/services" in paths
    assert "/api/ai/enrichments/{enrichment_id}/approve" in paths
    assert "/api/webhooks/github/{repository_id}" in paths
