"""ArchGraph MCP server (Model Context Protocol).

Transports
----------
* stdio  — for Claude Code / Codex / Cursor / any local MCP client:
  ``ARCHGRAPH_API_KEY=... python -m app.mcp_server``
* HTTP streamable — ``python -m app.mcp_server --http``; every request must send
  ``Authorization: Bearer <api key>`` (or ``X-API-Key``).

Authentication is an ArchGraph API key. The key's workspace scope is enforced on
every tool call, and each call writes an audit event with actor=api key owner and
actor_type=mcp.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .db import SessionLocal
from .deps import Principal
from .mcp_tools import TOOLS, audit_tool_call
from .models import ApiKey, User
from .security import hash_api_key

SERVER_NAME = "archgraph"
SERVER_VERSION = "1.0.0"

INSTRUCTIONS = (
    "ArchGraph exposes a deterministic architecture knowledge graph of a microservice "
    "estate: services, REST/gRPC endpoints, Kafka topics, databases, tables, jobs and "
    "their provenance. Start with list_workspaces, then list_services or query_graph. "
    "Every fact carries evidence (repository, commit, file, line, detection method). "
    "AI insights are optional, clearly separated from scanner facts and may be pending review."
)


def resolve_principal(api_key: str) -> Principal | None:
    db = SessionLocal()
    try:
        record = db.scalar(
            __import__("sqlalchemy").select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key))
        )
        if not record or record.revoked_at is not None:
            return None
        user = db.get(User, record.user_id)
        if not user or not user.is_active:
            return None
        return Principal(user=user, workspace_id=record.workspace_id, auth_type="api_key",
                         scopes=record.scopes or [])
    finally:
        db.close()


class AuthError(RuntimeError):
    pass


CURRENT_PRINCIPAL: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "archgraph_mcp_principal", default=None
)


def build_server(api_key: str | None) -> Server:
    server = Server(SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS)
    default_principal = resolve_principal(api_key) if api_key else None
    if api_key and default_principal is None:
        raise AuthError("Invalid or revoked ARCHGRAPH_API_KEY")
    if default_principal is not None:
        CURRENT_PRINCIPAL.set(default_principal)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=spec["name"], description=spec["description"],
                 inputSchema=spec["inputSchema"])
            for spec in TOOLS.values()
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        spec = TOOLS.get(name)
        if spec is None:
            raise ValueError(f"Unknown tool: {name}")
        principal = CURRENT_PRINCIPAL.get() or default_principal
        if principal is None:
            raise AuthError("Missing or invalid API key (ARCHGRAPH_API_KEY)")
        db = SessionLocal()
        try:
            try:
                result = spec["handler"](db, principal, arguments or {})
                payload = {"ok": True, "data": result}
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                audit_tool_call(db, principal, name, arguments or {}, str(exc)[:300])
                return [TextContent(type="text", text=json.dumps(payload, default=str))]
            audit_tool_call(db, principal, name, arguments or {})
            return [TextContent(type="text", text=json.dumps(payload, default=str))]
        finally:
            db.close()

    return server


async def run_stdio() -> None:
    server = build_server(os.getenv("ARCHGRAPH_API_KEY"))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def build_http_app(server: Server):
    """Minimal ASGI app: bearer auth then hand off to the MCP session manager."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=True)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            async with manager.run():
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            return
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "")
        if not key or resolve_principal(key) is None:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        token = CURRENT_PRINCIPAL.set(resolve_principal(key))
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            CURRENT_PRINCIPAL.reset(token)

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ArchGraph MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP on MCP_PORT")
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8787")))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args(argv)
    if args.list_tools:
        print(json.dumps([{"name": s["name"], "description": s["description"]}
                          for s in TOOLS.values()], indent=1))
        return
    try:
        if args.http:
            import uvicorn

            server = build_server(None)  # auth per request
            uvicorn.run(build_http_app(server), host=args.host, port=args.port, log_level="info")
        else:
            asyncio.run(run_stdio())
    except AuthError as exc:
        print(f"archgraph-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
