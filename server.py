#!/usr/bin/env python3
"""
MCP Web Search Server
Exposes two spec-compliant remote transports so it works with AIPI, Claude, Cursor, etc.:
  - Streamable HTTP (recommended)  ->  POST/GET/DELETE  /mcp
  - SSE (legacy/deprecated)        ->  GET /sse  +  POST /messages/
Tools: web_search, fetch_page
"""
import os
import re
import contextlib
from collections.abc import AsyncIterator

import httpx
import uvicorn
from mcp.server import Server
from mcp import types
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response, JSONResponse
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Scope, Receive, Send

# ─── Configuration ────────────────────────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
MCP_API_KEY    = os.getenv("MCP_API_KEY", "")
PORT           = int(os.getenv("PORT", "3000"))

# ─── Search Functions ─────────────────────────────────────────────────────────
async def serper_search(query: str, count: int = 5) -> list[dict]:
    """Search using Serper.dev (Google results, 2500 free/month)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": count},
        )
        r.raise_for_status()
        data = r.json()
        return [
            {"title": x.get("title", ""), "url": x.get("link", ""), "description": x.get("snippet", "")}
            for x in data.get("organic", [])
        ]

async def duckduckgo_search(query: str, count: int = 5) -> list[dict]:
    """Fallback DuckDuckGo search (no key needed)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1", "skip_disambig": "1"},
            headers={"User-Agent": "mcp-web-search/1.0"},
        )
        r.raise_for_status()
        data = r.json()
    results = []
    if data.get("AbstractText") and data.get("AbstractURL"):
        results.append({"title": data.get("AbstractSource", query), "url": data["AbstractURL"], "description": data["AbstractText"]})
    for t in data.get("RelatedTopics", []):
        if len(results) >= count:
            break
        if t.get("FirstURL") and t.get("Text"):
            results.append({"title": t["Text"][:60], "url": t["FirstURL"], "description": t["Text"]})
    return results[:count]

async def do_web_search(query: str, count: int = 5) -> list[dict]:
    if SERPER_API_KEY:
        return await serper_search(query, count)
    return await duckduckgo_search(query, count)

# ─── MCP Tool Handlers ────────────────────────────────────────────────────────
async def handle_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(
            name="web_search",
            description="Search the web and return relevant results with titles, URLs, and descriptions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "count": {"type": "integer", "description": "Number of results (1-10, default 5)", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="fetch_page",
            description="Fetch the text content of a web page by URL.",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri", "description": "URL to fetch"}},
                "required": ["url"],
            },
        ),
    ])

async def handle_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    name      = params.name
    arguments = params.arguments or {}

    if name == "web_search":
        query = arguments.get("query", "")
        count = int(arguments.get("count", 5))
        try:
            results = await do_web_search(query, count)
            if not results:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f'No results for: "{query}"')])
            body = "\n\n---\n\n".join(
                f"**{i+1}. {r['title']}**\nURL: {r['url']}\n{r['description']}" for i, r in enumerate(results)
            )
            return types.CallToolResult(content=[types.TextContent(type="text", text=f'Search results for: "{query}"\n\n{body}')])
        except Exception as e:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Search error: {e}")], isError=True)

    if name == "fetch_page":
        url = arguments.get("url", "")
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; mcp-web-search/1.0)"})
                r.raise_for_status()
                if "text" not in r.headers.get("content-type", ""):
                    raise ValueError("Non-text content type")
                text = re.sub(r"<[^>]+>", " ", r.text)
                text = re.sub(r"\s{2,}", " ", text).strip()
                if len(text) > 8000:
                    text = text[:8000] + "\n\n[truncated]"
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"Content from {url}:\n\n{text}")])
        except Exception as e:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Fetch error: {e}")], isError=True)

    return types.CallToolResult(content=[types.TextContent(type="text", text=f"Unknown tool: {name}")], isError=True)

# ─── MCP Server instance ──────────────────────────────────────────────────────
mcp = Server(name="mcp-web-search", version="1.0.0",
             on_list_tools=handle_list_tools, on_call_tool=handle_call_tool)

# ─── Streamable HTTP transport (official SDK, stateless) ──────────────────────
session_manager = StreamableHTTPSessionManager(
    app=mcp,
    json_response=True,   # return plain JSON responses (simpler for HTTP clients)
    stateless=True,       # no session id round-trips required — ideal for AIPI
)

async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
    await session_manager.handle_request(scope, receive, send)

# ─── SSE transport (legacy) ───────────────────────────────────────────────────
sse = SseServerTransport("/messages/")

async def handle_sse(request: Request) -> Response:
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())
    return Response()

# ─── Health Check ─────────────────────────────────────────────────────────────
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "name": "mcp-web-search",
        "version": "1.0.0",
        "transport": ["Streamable HTTP (/mcp)", "SSE (/sse)"],
        "searchProvider": "Serper.dev (Google)" if SERPER_API_KEY else "DuckDuckGo (fallback)",
        "tools": ["web_search", "fetch_page"],
    })

# ─── Optional API Key Authentication ──────────────────────────────────────────
class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Only enforced when MCP_API_KEY env var is set. Accepts the key via:
      - Authorization: Bearer <key>
      - X-API-Key: <key>
      - ?api_key=<key> query param (for clients that can't send headers)
    Health check (GET /) and CORS preflight (OPTIONS) are always allowed.
    """
    async def dispatch(self, request: Request, call_next):
        if not MCP_API_KEY:
            return await call_next(request)
        if request.url.path == "/" or request.method == "OPTIONS":
            return await call_next(request)

        provided = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = request.headers.get("x-api-key", "").strip() or None
        if not provided:
            provided = request.query_params.get("api_key")

        if provided != MCP_API_KEY:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32001, "message": "Unauthorized: invalid or missing API key"}},
                status_code=401,
            )
        return await call_next(request)

# ─── Lifespan: run the streamable session manager ─────────────────────────────
@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with session_manager.run():
        print("✅ Streamable HTTP session manager started")
        yield
        print("⏹  Streamable HTTP session manager stopped")

# ─── Starlette App ────────────────────────────────────────────────────────────
app = Starlette(
    debug=False,
    routes=[
        Route("/",          health,     methods=["GET"]),
        Route("/sse",       handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        # Streamable HTTP handles POST + GET + DELETE on both /mcp and /mcp/
        Mount("/mcp",       app=handle_streamable_http),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id", "Mcp-Session-Id", "mcp-protocol-version"],
        ),
        Middleware(ApiKeyMiddleware),
    ],
    lifespan=lifespan,
)

# ─── Path normalizer ──────────────────────────────────────────────────────────
# Starlette's Mount issues a 307 redirect for "/mcp" -> "/mcp/". Many MCP clients
# (including AIPI) POST to "/mcp" without a trailing slash and do NOT follow the
# redirect, so the connection silently fails. This ASGI wrapper rewrites the path
# to "/mcp/" transparently so the request reaches the transport with no redirect.
class PathNormalizer:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") in ("/mcp", "/messages"):
            scope = dict(scope)
            new_path = scope["path"] + "/"
            scope["path"] = new_path
            if scope.get("raw_path"):
                scope["raw_path"] = new_path.encode()
        await self.application(scope, receive, send)

asgi_app = PathNormalizer(app)

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ MCP Web Search server on port {PORT}")
    print(f"   Health:      GET  http://0.0.0.0:{PORT}/")
    print(f"   Streamable:  POST http://0.0.0.0:{PORT}/mcp")
    print(f"   SSE:         GET  http://0.0.0.0:{PORT}/sse")
    print(f"   Provider:    {'Serper.dev' if SERPER_API_KEY else 'DuckDuckGo (no key set)'}")
    print(f"   Auth:        {'ENABLED (MCP_API_KEY set)' if MCP_API_KEY else 'disabled'}")
    uvicorn.run(
        asgi_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        http="h11",  # force HTTP/1.1 — prevents Cloudflare/Render buffering SSE via HTTP/2
    )
