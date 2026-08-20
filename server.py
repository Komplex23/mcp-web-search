#!/usr/bin/env python3
"""
MCP Web Search Server
Supports SSE and Streamable HTTP transports, compatible with AIPI and Claude Desktop.
"""
import os
import re
import json
import asyncio
import httpx
from typing import Any
from mcp.server import Server
from mcp import types
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response, JSONResponse
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn

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
        if len(results) >= count: break
        if t.get("FirstURL") and t.get("Text"):
            results.append({"title": t["Text"][:60], "url": t["FirstURL"], "description": t["Text"]})
    return results[:count]

async def web_search(query: str, count: int = 5) -> list[dict]:
    if SERPER_API_KEY:
        return await serper_search(query, count)
    return await duckduckgo_search(query, count)

# ─── MCP Tool Handlers ────────────────────────────────────────────────────────

async def handle_list_tools(ctx, params):
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

async def handle_call_tool(ctx, params: types.CallToolRequestParams):
    name      = params.name
    arguments = params.arguments or {}

    if name == "web_search":
        query = arguments.get("query", "")
        count = int(arguments.get("count", 5))
        try:
            results = await web_search(query, count)
            if not results:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f'No results for: "{query}"')])
            body = "\n\n---\n\n".join(
                f"**{i+1}. {r['title']}**\nURL: {r['url']}\n{r['description']}" for i, r in enumerate(results)
            )
            return types.CallToolResult(content=[types.TextContent(type="text", text=f'Search results for: "{query}"\n\n{body}')])
        except Exception as e:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Search error: {e}")])

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
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Fetch error: {e}")])

    raise ValueError(f"Unknown tool: {name}")

# ─── MCP Server ───────────────────────────────────────────────────────────────
mcp = Server(name="mcp-web-search", version="1.0.0",
             on_list_tools=handle_list_tools, on_call_tool=handle_call_tool)

# ─── SSE Transport ────────────────────────────────────────────────────────────
sse = SseServerTransport("/messages/")

async def handle_sse(request: Request) -> Response:
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())
    return Response()

# ─── Streamable HTTP Transport (stateless) ───────────────────────────────────
async def handle_mcp_post(request: Request) -> Response:
    """Stateless Streamable HTTP — each request is self-contained."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}, status_code=400)

    method = body.get("method", "")
    req_id = body.get("id")

    # Handle initialize
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-web-search", "version": "1.0.0"},
            }
        })

    # Handle tools/list
    if method == "tools/list":
        tools_result = await handle_list_tools(None, None)
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in tools_result.tools
        ]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})

    # Handle tools/call
    if method == "tools/call":
        params = body.get("params", {})
        tool_params = types.CallToolRequestParams(name=params.get("name", ""), arguments=params.get("arguments", {}))
        result = await handle_call_tool(None, tool_params)
        content = [{"type": c.type, "text": c.text} for c in result.content if hasattr(c, "text")]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": content}})

    # Unknown method
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }, status_code=404)

# ─── Health Check ─────────────────────────────────────────────────────────────
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "name": "mcp-web-search",
        "version": "1.0.0",
        "transport": ["SSE (/sse)", "Streamable HTTP (/mcp)"],
        "searchProvider": "Serper.dev (Google)" if SERPER_API_KEY else "DuckDuckGo (fallback)",
        "tools": ["web_search", "fetch_page"],
    })

# ─── Starlette App ────────────────────────────────────────────────────────────
app = Starlette(
    debug=False,
    routes=[
        Route("/",         health,          methods=["GET"]),
        Route("/sse",      handle_sse,      methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/mcp",      handle_mcp_post, methods=["POST"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id", "Mcp-Session-Id"],
        )
    ],
)

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"✅ MCP Web Search server on port {PORT}")
    print(f"   Health:      GET  http://0.0.0.0:{PORT}/")
    print(f"   SSE:         GET  http://0.0.0.0:{PORT}/sse")
    print(f"   Streamable:  POST http://0.0.0.0:{PORT}/mcp")
    print(f"   Provider:    {'Serper.dev' if SERPER_API_KEY else 'DuckDuckGo (no key set)'}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        # Force HTTP/1.1 — prevents Cloudflare/Render from buffering SSE via HTTP/2
        http="h11",
    )
