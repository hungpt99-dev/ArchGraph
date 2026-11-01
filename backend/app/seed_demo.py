"""Reproducible demo seed.

Creates the Hotel Management Platform workspace, registers the six local demo
repositories, runs a full deterministic scan, ingests markdown/ADR/OpenAPI
documentation and Jira-shaped requirement fixtures, and configures a clearly
labelled *mock* AI provider (offline, deterministic) so the enrichment workflow
can be exercised without any external LLM credentials.

Run:  python -m app.seed_demo
Idempotent: re-running reuses the workspace and re-scans.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import select

from .ai.providers import provider_for
from .db import SessionLocal
from .docs.connectors import link_documents, sync_repository_docs, upsert_document
from .migrate import migrate
from .models import AiProvider, Repository, User, Workspace, WorkspaceMember
from .scanner.pipeline import scan_repository
from .security import hash_password

DEMO_ROOT = Path(os.getenv("DEMO_REPO_ROOT", "/demo/repos"))
ADMIN_EMAIL = os.getenv("DEMO_ADMIN_EMAIL", "admin@archgraph.io")
ADMIN_PASSWORD = os.getenv("DEMO_ADMIN_PASSWORD", "archgraph-demo-2026")

REPOS = [
    ("booking-platform", "booking-platform", "Multi-module Spring Boot platform: gateway, user, reservation"),
    ("payment-service", "payment-service", "Payment and invoice services (Spring Boot)"),
    ("order-inventory", "order-inventory", "Order and inventory services (Spring Boot)"),
    ("notification-service", "notification-service", "Notification worker (TypeScript, Kafka consumer)"),
    ("shared-library", "shared-library", "Shared Java library: event envelope, money value object"),
    ("platform-docs", "platform-docs", "Architecture docs, ADRs, OpenAPI, requirement fixtures"),
]


def seed() -> dict:
    applied = migrate()
    db = SessionLocal()
    summary: dict = {"migrations": applied}
    try:
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if not admin:
            admin = User(email=ADMIN_EMAIL, name="ArchGraph Admin",
                         password_hash=hash_password(ADMIN_PASSWORD), role="SYSTEM_ADMIN")
            db.add(admin)
            db.flush()
        ws = db.scalar(select(Workspace).where(Workspace.slug == "hotel-platform"))
        if not ws:
            ws = Workspace(
                name="Hotel Management Platform", slug="hotel-platform",
                description="Demo workspace: eight services across four code repositories, "
                            "Kafka event backbone, PostgreSQL per service and Redis caching.",
                owner_id=admin.id,
                settings={"ai_enabled": True, "demo": True,
                          "ai_provider_note": "Mock provider only - no external LLM calls"},
            )
            db.add(ws)
            db.flush()
            db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="WORKSPACE_ADMIN"))
        db.commit()

        provider = db.scalar(select(AiProvider).where(AiProvider.workspace_id == ws.id,
                                                     AiProvider.kind == "mock"))
        if not provider:
            provider = AiProvider(
                workspace_id=ws.id, name="Demo deterministic provider (MOCK)",
                kind="mock", model="archgraph-mock-v1", enabled=True, is_default=True,
                config={"note": "Offline deterministic provider used for demo and tests. "
                                "It is labelled MOCK everywhere and never calls a network service."},
            )
            db.add(provider)
            db.commit()

        results = []
        for name, folder, description in REPOS:
            path = DEMO_ROOT / folder
            if not path.exists():
                results.append({"repo": name, "status": "MISSING", "path": str(path)})
                continue
            repo = db.scalar(select(Repository).where(Repository.workspace_id == ws.id,
                                                     Repository.name == name))
            if not repo:
                repo = Repository(
                    workspace_id=ws.id, name=name, url=str(path), provider="local",
                    branch="main", default_branch="main", auto_sync=True, status="PENDING",
                    settings={"demo": True, "description": description},
                )
                db.add(repo)
                db.commit()
            try:
                out = scan_repository(db, repo, trigger="seed", mode="full")
                results.append({"repo": name, "status": out["status"],
                                "stats": out.get("stats", {})})
            except Exception as exc:  # noqa: BLE001
                results.append({"repo": name, "status": "FAILED", "error": str(exc)[:300]})

        # documentation from repository content (markdown, ADR, OpenAPI, AsyncAPI)
        docs = {}
        for repo in db.scalars(select(Repository).where(Repository.workspace_id == ws.id)).all():
            try:
                docs[repo.name] = sync_repository_docs(db, repo)
            except Exception as exc:  # noqa: BLE001
                docs[repo.name] = {"error": str(exc)[:200]}
        db.commit()

        # Jira-shaped requirement fixtures (clearly labelled: imported fixture, not live Jira)
        fixture = DEMO_ROOT / "platform-docs" / "requirements" / "jira-export.md"
        jira_count = 0
        if fixture.exists():
            text = fixture.read_text()
            for block in re.split(r"\n## ", text)[1:]:
                key = block.split(" - ")[0].strip()
                title = block.split("\n")[0].split(" - ", 1)[-1].strip()
                upsert_document(
                    db, ws.id, "jira", key, f"{key}: {title}", f"# {key}: {title}\n\n{block}",
                    url=f"https://jira.example.com/browse/{key}",
                    metadata={"project": "HOTEL", "source": "fixture",
                              "note": "Imported from requirements/jira-export.md (demo fixture)"},
                )
                jira_count += 1
        links = link_documents(db, ws.id)
        db.commit()

        summary.update({
            "workspace_id": ws.id, "admin_email": ADMIN_EMAIL,
            "repositories": len(REPOS), "scan_results": results,
            "documents": docs, "jira_fixtures": jira_count, "links": links,
            "ai_provider": {"kind": "mock", "label": "MOCK - offline deterministic"},
        })
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    print(json.dumps(result, indent=1, default=str))
    if any(r.get("status") == "FAILED" for r in result.get("scan_results", [])):
        sys.exit(1)
