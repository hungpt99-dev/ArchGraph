"""Webhook security and idempotency tests (GitHub HMAC, GitLab token)."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from sqlalchemy import select

from app.models import Job, WebhookEvent
from conftest import make_local_repo
from test_pipeline import PAYMENT_SERVICE


def _repo(client, admin, workspace, name="payment-service"):
    path = make_local_repo(Path("/tmp/archgraph-webhook-src"), PAYMENT_SERVICE, name)
    response = client.post(f"/api/workspaces/{workspace['id']}/repositories", headers=admin["headers"],
                           json={"name": name, "url": path, "scan_now": False})
    assert response.status_code == 201, response.text
    return response.json()


def _secret(client, admin, repo_id):
    return client.post(f"/api/repositories/{repo_id}/webhook-secret",
                       headers=admin["headers"]).json()["secret"]


def test_github_webhook_requires_configuration(client, admin, workspace):
    repo = _repo(client, admin, workspace)
    response = client.post(f"/api/webhooks/github/{repo['id']}", json={"ref": "refs/heads/main"},
                           headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "d-1"})
    assert response.status_code == 403


def test_github_webhook_rejects_bad_signature(client, admin, workspace):
    repo = _repo(client, admin, workspace)
    _secret(client, admin, repo["id"])
    response = client.post(f"/api/webhooks/github/{repo['id']}", content=b"{}",
                           headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "d-2",
                                    "X-Hub-Signature-256": "sha256=deadbeef"})
    assert response.status_code == 401


def test_github_webhook_queues_incremental_scan_and_is_idempotent(client, admin, workspace, db):
    repo = _repo(client, admin, workspace)
    secret = _secret(client, admin, repo["id"])
    payload = json.dumps({"ref": "refs/heads/main", "after": "a" * 40, "before": "b" * 40}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-42",
               "X-Hub-Signature-256": signature, "content-type": "application/json"}
    first = client.post(f"/api/webhooks/github/{repo['id']}", content=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued"
    job = db.get(Job, first.json()["job_id"])
    assert job.kind == "scan_repository"
    assert job.payload["mode"] == "incremental" and job.payload["trigger"] == "webhook"
    assert job.payload["commit"] == "a" * 40
    second = client.post(f"/api/webhooks/github/{repo['id']}", content=payload, headers=headers)
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert len(db.scalars(select(Job)).all()) == 1
    events = db.scalars(select(WebhookEvent).where(WebhookEvent.repository_id == repo["id"])).all()
    assert len(events) == 1 and events[0].delivery_id == "delivery-42"


def test_github_webhook_ignores_other_branches(client, admin, workspace, db):
    repo = _repo(client, admin, workspace)
    secret = _secret(client, admin, repo["id"])
    payload = json.dumps({"ref": "refs/heads/feature-x", "after": "c" * 40}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    response = client.post(f"/api/webhooks/github/{repo['id']}", content=payload,
                           headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "d-3",
                                    "X-Hub-Signature-256": signature})
    assert response.json()["status"] == "ignored"
    assert len(db.scalars(select(Job)).all()) == 0


def test_github_ping_is_acknowledged_without_scan(client, admin, workspace):
    repo = _repo(client, admin, workspace)
    secret = _secret(client, admin, repo["id"])
    payload = json.dumps({"zen": "Keep it logically awesome."}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    response = client.post(f"/api/webhooks/github/{repo['id']}", content=payload,
                           headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-4",
                                    "X-Hub-Signature-256": signature})
    assert response.json()["status"] == "pong"


def test_gitlab_webhook_token_verification(client, admin, workspace, db):
    repo = _repo(client, admin, workspace, name="gitlab-service")
    secret = _secret(client, admin, repo["id"])
    payload = json.dumps({"ref": "refs/heads/main", "checkout_sha": "d" * 40}).encode()
    bad = client.post(f"/api/webhooks/gitlab/{repo['id']}", content=payload,
                      headers={"X-Gitlab-Token": "wrong", "X-Gitlab-Event": "Push Hook"})
    assert bad.status_code == 401
    good = client.post(f"/api/webhooks/gitlab/{repo['id']}", content=payload,
                       headers={"X-Gitlab-Token": secret, "X-Gitlab-Event": "Push Hook",
                                "X-Gitlab-Event-UUID": "gl-1"})
    assert good.status_code == 200 and good.json()["status"] == "queued"
    assert db.get(Job, good.json()["job_id"]).payload["mode"] == "incremental"
