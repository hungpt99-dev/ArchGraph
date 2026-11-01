"""Incremental scanning: only changed files re-indexed, enrichments marked STALE."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.ai.enrich import create_enrichments, run_enrichment
from app.models import AiProvider, Edge, Enrichment, Node, Repository, ScanRun
from app.scanner.pipeline import scan_repository
from conftest import commit, make_local_repo
from test_pipeline import PAYMENT_SERVICE


def _setup(db, workspace):
    path = make_local_repo(Path("/tmp/archgraph-incr"), PAYMENT_SERVICE, "payment-incr")
    repo = Repository(workspace_id=workspace["id"], name="payment-service", url=path,
                      provider="local", branch="main", default_branch="main", settings={})
    db.add(repo)
    db.commit()
    scan_repository(db, repo, trigger="test", mode="full")
    db.refresh(repo)
    return repo, path


def test_incremental_scan_updates_graph_and_marks_enrichment_stale(db, workspace):
    repo, path = _setup(db, workspace)
    service = db.scalar(select(Node).where(Node.workspace_id == workspace["id"],
                                           Node.type == "SERVICE"))
    provider = AiProvider(workspace_id=workspace["id"], name="Mock", kind="mock",
                          model="archgraph-mock-v1", enabled=True, is_default=True)
    db.add(provider)
    db.commit()
    enrichment = create_enrichments(db, workspace["id"], [("node", service.id)],
                                    ["SERVICE_OVERVIEW"])[0]
    db.commit()
    run_enrichment(db, enrichment)
    assert enrichment.status in ("NEEDS_REVIEW", "GENERATED")
    assert enrichment.evidence and enrichment.source_revision
    assert any(e.get("file") for e in enrichment.evidence)

    old_api = db.scalar(select(Node).where(Node.workspace_id == workspace["id"],
                                           Node.type == "API_ENDPOINT"))
    assert old_api.name == "POST /api/payments/authorize"

    controller = Path(path) / "src/main/java/com/acme/pay/PaymentController.java"
    controller.write_text(controller.read_text().replace(
        '@PostMapping("/authorize")', '@PostMapping("/authorize-v2")'))
    new_file = Path(path) / "src/main/java/com/acme/pay/RefundController.java"
    new_file.write_text("""package com.acme.pay;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/refunds")
public class RefundController {
    @PostMapping
    public String refund(@RequestBody String body) { return "ok"; }
}
""")
    new_commit = commit(path, "add refund endpoint and rename authorize")

    result = scan_repository(db, repo, trigger="test", mode="incremental")
    assert result["status"] == "COMPLETED"
    stats = result["stats"]
    assert stats["files_scanned"] == 2, stats
    scan = db.get(ScanRun, result["scan_id"])
    assert scan.previous_commit and scan.previous_commit != new_commit
    assert {c["path"] for c in scan.changed_files} >= {
        "src/main/java/com/acme/pay/PaymentController.java",
        "src/main/java/com/acme/pay/RefundController.java",
    }

    refreshed_old = db.get(Node, old_api.id)
    assert refreshed_old.status == "REMOVED"
    endpoints = {n.name for n in db.scalars(select(Node).where(
        Node.workspace_id == workspace["id"], Node.type == "API_ENDPOINT",
        Node.status == "ACTIVE")).all()}
    assert "POST /api/payments/authorize-v2" in endpoints
    assert "POST /api/refunds" in endpoints
    assert "POST /api/payments/authorize" not in endpoints

    db.refresh(enrichment)
    assert enrichment.status == "STALE"
    assert stats["enrichments_marked_stale"] >= 1

    # deterministic edges were never rewritten by AI
    ai_edges = db.scalars(select(Edge).where(Edge.workspace_id == workspace["id"],
                                             Edge.detection_method == "AI_INFERRED")).all()
    assert ai_edges == []


def test_incremental_scan_skips_when_no_new_commit(db, workspace):
    repo, _ = _setup(db, workspace)
    result = scan_repository(db, repo, trigger="test", mode="incremental")
    assert result["status"] == "SKIPPED"
