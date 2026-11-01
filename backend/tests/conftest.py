"""Test bootstrap.

Integration tests run against a real PostgreSQL database (no mocked persistence)
and a real FastAPI app. The only external services replaced are the LLM provider
(explicitly the labelled ``mock`` provider) and outbound Git hosts (local git
repositories created per test).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

BASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://archgraph:archgraph@localhost:5434/archgraph_test")
ADMIN_DB = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://archgraph:archgraph@localhost:5434/postgres")

os.environ["DATABASE_URL"] = "postgresql+psycopg://" + BASE_URL.split("://", 1)[1]
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("REPO_STORAGE_DIR", "/tmp/archgraph-test-repos")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ensure_database() -> None:
    with psycopg.connect(ADMIN_DB, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname='archgraph_test'").fetchone()
        if not exists:
            conn.execute("CREATE DATABASE archgraph_test")


_ensure_database()

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.models import (  # noqa: E402
    AuditEvent, Document, DocumentLink, Edge, Enrichment, Job, Node, Repository,
    ScanRun, User, WebhookEvent, Workspace, WorkspaceMember, ApiKey, AiProvider,
    BusinessFlow, Integration, IndexedFile,
)
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

TABLES = [
    AuditEvent, DocumentLink, Document, Enrichment, Edge, Node, IndexedFile,
    ScanRun, Job, WebhookEvent, ApiKey, AiProvider, Integration, BusinessFlow,
    Repository, WorkspaceMember, Workspace, User,
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    migrate()
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def clean_db():
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE " + ", ".join(t.__tablename__ for t in TABLES) + " RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    response = client.post("/api/auth/bootstrap", json={
        "email": "admin@test.dev", "name": "Test Admin", "password": "test-password-123",
    })
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"token": token, "headers": {"Authorization": f"Bearer {token}"}, "user": response.json()["user"]}


@pytest.fixture
def workspace(client, admin):
    response = client.post("/api/workspaces", headers=admin["headers"],
                           json={"name": "Test System", "description": "fixture"})
    assert response.status_code == 201, response.text
    return response.json()


def make_local_repo(tmp_path: Path, files: dict[str, str], name: str = "svc") -> str:
    import shutil
    import uuid as _uuid

    repo = tmp_path / f"{name}-{_uuid.uuid4().hex[:8]}"
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=repo, check=True)
    return str(repo)


def commit(repo: str, message: str = "change") -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", "commit", "-q", "-m", message],
                   cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()
