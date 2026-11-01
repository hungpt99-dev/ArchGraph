"""Job handler registrations (imported by the worker and by tests)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.enrich import run_enrichment
from .docs.connectors import link_documents, sync_confluence, sync_jira, sync_repository_docs
from .jobs import register
from .models import Enrichment, Integration, Job, Repository
from .scanner.pipeline import scan_repository as run_scan
from .security import decrypt_secret

log = logging.getLogger("archgraph.tasks")


@register("scan_repository")
def handle_scan_repository(db: Session, job: Job, progress) -> dict:
    repo = db.get(Repository, job.payload.get("repository_id"))
    if not repo:
        raise RuntimeError("Repository no longer exists")
    result = run_scan(db, repo, trigger=job.payload.get("trigger", "manual"),
                      mode=job.payload.get("mode", "full"), job_id=job.id, progress=progress)
    job.result = result
    db.commit()
    return result


@register("scan_workspace")
def handle_scan_workspace(db: Session, job: Job, progress) -> dict:
    workspace_id = job.payload.get("workspace_id") or job.workspace_id
    repos = list(db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)).all())
    results = []
    for index, repo in enumerate(repos):
        def sub_progress(pct: int, message: str = "", _i=index):
            base = int(90 * _i / max(1, len(repos)))
            progress(base + int(pct * 0.9 / max(1, len(repos))), f"{repo.name}: {message}")

        try:
            results.append(run_scan(db, repo, trigger="manual",
                                    mode=job.payload.get("mode", "full"), job_id=job.id,
                                    progress=sub_progress))
        except Exception as exc:  # noqa: BLE001
            results.append({"repository_id": repo.id, "status": "FAILED", "error": str(exc)[:300]})
    progress(100, "workspace scan complete")
    return {"repositories": len(repos), "results": results}


@register("sync_documents")
def handle_sync_documents(db: Session, job: Job, progress) -> dict:
    workspace_id = job.workspace_id or job.payload.get("workspace_id")
    targets = job.payload.get("targets") or ["repositories"]
    results: dict = {}
    if "repositories" in targets:
        repos = list(db.scalars(select(Repository).where(Repository.workspace_id == workspace_id)).all())
        docs = 0
        for repo in repos:
            try:
                out = sync_repository_docs(db, repo)
                docs += out.get("documents", 0) + out.get("specs", 0)
            except Exception as exc:  # noqa: BLE001
                results.setdefault("errors", []).append(f"{repo.name}: {exc}")
        results["repositories"] = docs
        progress(40, "repository documents synced")
    integrations = list(db.scalars(
        select(Integration).where(Integration.workspace_id == workspace_id,
                                  Integration.enabled.is_(True),
                                  Integration.kind.in_(["jira", "confluence"]))
    ).all())
    if job.payload.get("integration_ids"):
        integrations = [i for i in integrations if i.id in job.payload["integration_ids"]]
    for integration in integrations:
        if integration.kind not in targets and targets != ["all"]:
            continue
        try:
            secret = decrypt_secret(integration.secret_enc)
            if integration.kind == "jira":
                out = sync_jira(db, workspace_id, integration.config, secret)
            else:
                out = sync_confluence(db, workspace_id, integration.config, secret)
            integration.last_sync_at = datetime.now(timezone.utc)
            integration.status = "CONNECTED"
            integration.last_error = None
            results[integration.kind] = out
        except Exception as exc:  # noqa: BLE001
            integration.status = "ERROR"
            integration.last_error = str(exc)[:400]
            results.setdefault("errors", []).append(f"{integration.kind}: {exc}")
    links = link_documents(db, workspace_id)
    results["links"] = links
    progress(100, "documents synced")
    return results


@register("run_enrichment")
def handle_run_enrichment(db: Session, job: Job, progress) -> dict:
    ids = job.payload.get("enrichment_ids") or []
    total = max(1, len(ids))
    done = 0
    failed = 0
    for index, enrichment_id in enumerate(ids):
        record = db.get(Enrichment, enrichment_id)
        if not record or record.status not in ("QUEUED", "ANALYZING"):
            continue
        progress(int(100 * index / total), f"analyzing {record.category} / {record.target_name}")
        run_enrichment(db, record)
        if record.status == "FAILED":
            failed += 1
        done += 1
    progress(100, "enrichment complete")
    return {"processed": done, "failed": failed}


@register("relink_documents")
def handle_relink(db: Session, job: Job, progress) -> dict:
    return link_documents(db, job.workspace_id or job.payload.get("workspace_id"))
