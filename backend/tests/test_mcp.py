"""MCP server tests: real MCP client over stdio, tool schemas, auth and isolation."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Node, Repository
from app.scanner.pipeline import scan_repository
from conftest import make_local_repo
from test_pipeline import PAYMENT_SERVICE

BACKEND = Path(__file__).resolve().parents[1]

REQUIRED_TOOLS = {
    "list_workspaces", "get_workspace", "list_repositories", "get_repository_status",
    "list_services", "get_service", "get_service_dependencies", "get_service_dependents",
    "get_service_architecture", "search_symbols", "get_symbol", "get_source_location",
    "get_method_dependencies", "get_class_dependencies", "list_apis", "get_api_details",
    "find_api_implementation", "list_kafka_topics", "get_topic_producers", "get_topic_consumers",
    "trace_event_flow", "list_databases", "list_tables", "find_table_accessors", "query_graph",
    "find_dependency_path", "analyze_change_impact", "trace_business_flow",
    "find_unresolved_dependencies", "search_documents", "get_document",
    "find_related_requirements", "find_implementation_for_requirement", "get_ai_insights",
    "get_enrichment_status", "request_enrichment", "review_ai_insight",
}


def _env(key: str) -> dict:
    env = {
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "REPO_STORAGE_DIR": os.environ.get("REPO_STORAGE_DIR", "/tmp/archgraph-test-repos"),
        "PYTHONPATH": str(BACKEND),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "ARCHGRAPH_API_KEY": key,
    }
    return env


def _scanned(db, workspace):
    path = make_local_repo(Path("/tmp/archgraph-mcp"), PAYMENT_SERVICE, "payment-mcp")
    repo = Repository(workspace_id=workspace["id"], name="payment-service", url=path,
                      provider="local", branch="main", default_branch="main", settings={})
    db.add(repo)
    db.commit()
    scan_repository(db, repo, trigger="test", mode="full")
    return db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "SERVICE"))


def _call(api_key: str, calls: list[tuple[str, dict]]) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def runner():
        params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                       env=_env(api_key), cwd=str(BACKEND))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                results = {}
                for name, args in calls:
                    result = await session.call_tool(name, args)
                    payload = result.content[0].text if result.content else "{}"
                    results[name] = json.loads(payload)
                return [t.name for t in listed.tools], results

    return asyncio.run(runner())


def test_mcp_tool_surface_and_schemas(client, admin, workspace, db):
    service = _scanned(db, workspace)
    key = client.post("/api/auth/api-keys", headers=admin["headers"],
                      json={"name": "mcp-test", "workspace_id": workspace["id"],
                            "scopes": ["read"]}).json()["api_key"]
    names, results = _call(key, [
        ("list_workspaces", {}),
        ("list_services", {"workspace_id": workspace["id"]}),
        ("get_service_architecture", {"workspace_id": workspace["id"], "name": "payment-service"}),
        ("get_source_location", {"workspace_id": workspace["id"], "node_id": service.id}),
        ("trace_event_flow", {"workspace_id": workspace["id"], "topic": "payment.completed"}),
    ])
    assert REQUIRED_TOOLS <= set(names)
    assert len(names) == 37
    assert results["list_workspaces"]["ok"] is True
    assert results["list_workspaces"]["data"]["workspaces"][0]["id"] == workspace["id"]
    services = results["list_services"]["data"]["services"]
    assert any(s["name"] == "payment-service" for s in services)
    arch = results["get_service_architecture"]["data"]
    assert arch["service"]["name"] == "payment-service"
    assert arch["apis"][0]["properties"]["path"] == "/api/payments/authorize"
    location = results["get_source_location"]["data"]["location"]
    assert location["file"].endswith(".java") and location["start_line"]
    flow = results["trace_event_flow"]["data"]
    assert flow["topic"]["name"] == "payment.completed"
    assert flow["producers"], "producer service must be linked to the topic"


def test_mcp_enforces_workspace_isolation_and_audit(client, admin, workspace, db):
    _scanned(db, workspace)
    other = client.post("/api/workspaces", headers=admin["headers"], json={"name": "Other System"}).json()
    key = client.post("/api/auth/api-keys", headers=admin["headers"],
                      json={"name": "scoped", "workspace_id": workspace["id"]}).json()["api_key"]
    _, results = _call(key, [
        ("list_services", {"workspace_id": other["id"]}),
        ("get_workspace", {"workspace_id": workspace["id"]}),
    ])
    assert results["list_services"]["ok"] is False
    error = results["list_services"]["error"].lower()
    assert "access" in error or "scoped to another workspace" in error, results["list_services"]
    assert results["get_workspace"]["ok"] is True
    audit = client.get(f"/api/workspaces/{workspace['id']}/audit", headers=admin["headers"]).json()
    mcp_events = [a for a in audit if a["action"].startswith("mcp.")]
    assert mcp_events and mcp_events[0]["actor_type"] == "mcp"


def test_mcp_requires_valid_api_key(client, admin, workspace, db):
    _scanned(db, workspace)
    with pytest.raises(Exception):
        _call("agk_invalid_key_value", [("list_workspaces", {})])
    result = subprocess.run(
        [sys.executable, "-m", "app.mcp_server"], cwd=BACKEND, env=_env("agk_bad"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "Invalid or revoked" in result.stderr


def test_mcp_ai_tools_respect_disabled_state(client, admin, workspace, db):
    service = _scanned(db, workspace)
    key = client.post("/api/auth/api-keys", headers=admin["headers"],
                      json={"name": "ai", "workspace_id": workspace["id"]}).json()["api_key"]
    _, results = _call(key, [
        ("get_enrichment_status", {"workspace_id": workspace["id"]}),
        ("request_enrichment", {"workspace_id": workspace["id"],
                                "targets": [{"type": "node", "id": service.id}],
                                "categories": ["SERVICE_OVERVIEW"]}),
    ])
    assert results["get_enrichment_status"]["data"]["ai_enabled"] is False
    assert results["request_enrichment"]["ok"] is False
    assert "AI is disabled" in results["request_enrichment"]["error"]
