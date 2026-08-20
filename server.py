#!/usr/bin/env python3
"""
MCP Web Search Server - Python implementation
Supports both SSE and Streamable HTTP transports
"""
import os
import re
import httpx
from typing import Any
from mcp.server import Server
from mcp import types
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import Response, JSONResponse
from starlette.requests import Request
import uvicorn

# Configuration
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")
PORT = int(os.getenv("PORT", "3000"))


async def serper_search(query: str, count: int = 5) -> list[dict]:
    """Search using Serper.dev API (Google results)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": count},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "description": r.get("snippet", ""),
            }
            for r in data.get("organic", [])
        ]


async def duckduckgo_search(query: str, count: int = 5) -> list[dict]:
    """Fallback search using DuckDuckGo."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_redirect": "1",
                "no_html": "1",
                "skip_disambig": "1",
            },
            headers={"User-Agent": "mcp-web-search/1.0"},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        results = []
        if data.get("AbstractText") and data.get("AbstractURL"):
            results.append({
                "title": data.get("AbstractSource", query),
                "url": data["AbstractURL"],
                "description": data["AbstractText"],
            })

        for topic in data.get("RelatedTopics", []):
            if len(results) >= count:
                break
            if topic.get("FirstURL") and topic.get("Text"):
                results.append({
                    "title": topic["Text"].split(" - ")[0][:60],
                    "url": topic["FirstURL"],
                    "description": topic["Text"],
                })

        return results[:count]


async def web_search(query: str, count: int = 5) -> list[dict]:
    """Route to appropriate search provider."""
    if SERPER_API_KEY:
        return await serper_search(query, count)
    return await duckduckgo_search(query, count)


# ─── MCP Handler Functions ────────────────────────────────────────────────────

async def handle_list_tools(ctx, params):
    """List available tools."""
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="web_search",
                description="Search the web and return relevant results with titles, URLs, and descriptions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of results to return (1-10, default 5)",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="fetch_page",
                description="Fetch the text content of a web page by URL.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "format": "uri",
                            "description": "The URL to fetch",
                        },
                    },
                    "required": ["url"],
                },
            ),
        ]
    )


async def handle_call_tool(ctx, params: types.CallToolRequestParams):
    """Handle tool execution."""
    name = params.name
    arguments = params.arguments or {}
    
    if name == "web_search":
        query = arguments["query"]
        count = arguments.get("count", 5)
        try:
            results = await web_search(query, count)
            if not results:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f'No results found for: "{query}"')]
                )

            formatted = "\n\n---\n\n".join(
                f"**{i+1}. {r['title']}**\nURL: {r['url']}\n{r['description']}"
                for i, r in enumerate(results)
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f'Search results for: "{query}"\n\n{formatted}')]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Search failed: {str(e)}")]
            )

    elif name == "fetch_page":
        url = arguments["url"]
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; mcp-web-search/1.0)"
                    },
                    timeout=10.0,
                    follow_redirects=True,
                )
                response.raise_for_status()
                ct = response.headers.get("content-type", "")
                if "text" not in ct:
                    raise ValueError(f"Non-text content type: {ct}")

                text = response.text
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s{2,}", " ", text).strip()

                if len(text) > 8000:
                    text = text[:8000] + "\n\n[truncated]"

                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"Content from {url}:\n\n{text}")]
                )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Fetch failed: {str(e)}")]
            )

    raise ValueError(f"Unknown tool: {name}")


# ─── MCP Server Instance ──────────────────────────────────────────────────────

app_server = Server(
    name="mcp-web-search",
    version="1.0.0",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


# ─── Web Server (Starlette) ───────────────────────────────────────────────────

from mcp.server.sse import SseServerTransport
from starlette.routing import Mount

# Create SSE transport
sse = SseServerTransport("/messages/")

async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse({
        "name": "mcp-web-search",
        "version": "1.0.0",
        "transport": ["SSE (/sse)"],
        "searchProvider": "Serper.dev (Google results)" if SERPER_API_KEY else "DuckDuckGo (fallback)",
        "tools": ["web_search", "fetch_page"],
    })


async def handle_sse(request: Request) -> Response:
    """SSE connection endpoint."""
    async with sse.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as streams:
        await app_server.run(
            streams[0],
            streams[1],
            app_server.create_initialization_options()
        )
    return Response()


# Starlette app
starlette_app = Starlette(
    debug=False,
    routes=[
        Route("/", health_check, methods=["GET"]),
        Route("/sse", handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ],
)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Run the web server."""
    print(f"✅ MCP Web Search server starting on port {PORT}")
    print(f"   Health:      GET  http://localhost:{PORT}/")
    print(f"   SSE:         GET  http://localhost:{PORT}/sse")
    print(f"   Search provider: {('Serper.dev (Google results)' if SERPER_API_KEY else 'DuckDuckGo (fallback)')}")
    
    uvicorn.run(
        starlette_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
