# MCP server

ArchGraph exposes the architecture graph to AI coding agents through the Model
Context Protocol. Tools are structured queries, not a generic search endpoint.

## Transports

| Transport | Command | Auth |
| --- | --- | --- |
| stdio | `ARCHGRAPH_API_KEY=<key> python -m app.mcp_server` | env key |
| Streamable HTTP | `python -m app.mcp_server --http --port 8787` | `Authorization: Bearer <key>` or `X-API-Key` |

Both require an ArchGraph API key. Unauthenticated HTTP requests get `401`;
an invalid stdio key exits with code 2 before serving.

## Client configuration

Claude Code:

```bash
claude mcp add archgraph -- docker compose exec -T \
  -e ARCHGRAPH_API_KEY=<key> api python -m app.mcp_server
```

Generic JSON (Codex, Cursor, others):

```json
{
  "mcpServers": {
    "archgraph": {
      "command": "docker",
      "args": ["compose", "exec", "-T", "-e", "ARCHGRAPH_API_KEY=<key>",
               "api", "python", "-m", "app.mcp_server"]
    }
  }
}
```

HTTP clients: `http://localhost:8787/mcp`, header `Authorization: Bearer <key>`.

## Tools (37)

**Workspaces** — `list_workspaces`, `get_workspace`, `list_repositories`,
`get_repository_status`

**Services** — `list_services`, `get_service`, `get_service_dependencies`,
`get_service_dependents`, `get_service_architecture`

**Code** — `search_symbols`, `get_symbol`, `get_source_location`,
`get_method_dependencies`, `get_class_dependencies`

**APIs** — `list_apis`, `get_api_details`, `find_api_implementation`

**Events** — `list_kafka_topics`, `get_topic_producers`, `get_topic_consumers`,
`trace_event_flow`

**Databases** — `list_databases`, `list_tables`, `find_table_accessors`

**Architecture** — `query_graph`, `find_dependency_path`, `analyze_change_impact`,
`trace_business_flow`, `find_unresolved_dependencies`

**Documentation** — `search_documents`, `get_document`, `find_related_requirements`,
`find_implementation_for_requirement`

**AI** — `get_ai_insights`, `get_enrichment_status`, `request_enrichment`,
`review_ai_insight`

Inputs are JSON Schema objects; only `workspace_id` (plus natural keys for lookups)
is required. Every response is `{"ok": true, "data": …}` or
`{"ok": false, "error": "…"}`.

## Isolation and audit

* Every tool calls `ensure_workspace_access`; a key scoped to workspace A cannot
  read workspace B even when its owner is a system administrator.
* Every call writes an `audit_events` row: actor, tool name, arguments (excluding
  the workspace id), error if any.
* AI tools respect the workspace policy: `request_enrichment` fails when AI is
  disabled, no provider is configured, or the category is not allowed.

## Verified

`backend/tests/test_mcp.py` starts the real server with the official MCP client over
stdio and asserts the tool surface, schema validation, workspace isolation, audit
records, invalid-key rejection and AI-disabled behaviour.
