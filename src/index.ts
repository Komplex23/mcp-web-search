import express, { Request, Response } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

// ─── Configuration ───────────────────────────────────────────────────────────
const PORT = parseInt(process.env.PORT || "3000", 10);
const BRAVE_API_KEY = process.env.BRAVE_API_KEY || "";
const API_KEY = process.env.MCP_API_KEY || ""; // Optional: protect your server

// ─── Web Search Functions ─────────────────────────────────────────────────────

interface SearchResult {
  title: string;
  url: string;
  description: string;
}

/** Brave Search API */
async function braveSearch(
  query: string,
  count: number = 5
): Promise<SearchResult[]> {
  const url = `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${count}`;
  const resp = await fetch(url, {
    headers: {
      Accept: "application/json",
      "Accept-Encoding": "gzip",
      "X-Subscription-Token": BRAVE_API_KEY,
    },
  });
  if (!resp.ok) {
    throw new Error(`Brave Search API error: ${resp.status} ${resp.statusText}`);
  }
  const data = (await resp.json()) as {
    web?: { results?: Array<{ title: string; url: string; description: string }> };
  };
  return (data.web?.results || []).map((r) => ({
    title: r.title || "",
    url: r.url || "",
    description: r.description || "",
  }));
}

/** DuckDuckGo HTML fallback (no API key needed) */
async function duckduckgoSearch(
  query: string,
  count: number = 5
): Promise<SearchResult[]> {
  // Use DDG's JSON API endpoint
  const url = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_redirect=1&no_html=1&skip_disambig=1`;
  const resp = await fetch(url, {
    headers: { "User-Agent": "mcp-web-search/1.0" },
  });
  if (!resp.ok) {
    throw new Error(`DuckDuckGo error: ${resp.status}`);
  }
  const data = (await resp.json()) as {
    AbstractText?: string;
    AbstractURL?: string;
    AbstractSource?: string;
    RelatedTopics?: Array<{
      Text?: string;
      FirstURL?: string;
      Topics?: Array<{ Text?: string; FirstURL?: string }>;
    }>;
  };

  const results: SearchResult[] = [];

  // Main abstract result
  if (data.AbstractText && data.AbstractURL) {
    results.push({
      title: data.AbstractSource || query,
      url: data.AbstractURL,
      description: data.AbstractText,
    });
  }

  // Related topics
  for (const topic of data.RelatedTopics || []) {
    if (results.length >= count) break;
    if (topic.FirstURL && topic.Text) {
      results.push({
        title: topic.Text.split(" - ")[0] || topic.Text.slice(0, 60),
        url: topic.FirstURL,
        description: topic.Text,
      });
    }
    // Sub-topics
    for (const sub of topic.Topics || []) {
      if (results.length >= count) break;
      if (sub.FirstURL && sub.Text) {
        results.push({
          title: sub.Text.slice(0, 60),
          url: sub.FirstURL,
          description: sub.Text,
        });
      }
    }
  }

  return results.slice(0, count);
}

/** Route to appropriate search provider */
async function webSearch(query: string, count: number = 5): Promise<SearchResult[]> {
  if (BRAVE_API_KEY) {
    return braveSearch(query, count);
  }
  return duckduckgoSearch(query, count);
}

// ─── MCP Server Factory ───────────────────────────────────────────────────────

function createMcpServer(): McpServer {
  const server = new McpServer({
    name: "mcp-web-search",
    version: "1.0.0",
  });

  // Tool: web_search
  server.tool(
    "web_search",
    "Search the web and return relevant results with titles, URLs, and descriptions.",
    {
      query: z.string().describe("The search query"),
      count: z
        .number()
        .int()
        .min(1)
        .max(10)
        .optional()
        .default(5)
        .describe("Number of results to return (1–10, default 5)"),
    },
    async ({ query, count }) => {
      try {
        const results = await webSearch(query, count);
        if (results.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No results found for: "${query}"`,
              },
            ],
          };
        }
        const formatted = results
          .map(
            (r, i) =>
              `**${i + 1}. ${r.title}**\nURL: ${r.url}\n${r.description}`
          )
          .join("\n\n---\n\n");
        return {
          content: [
            {
              type: "text",
              text: `Search results for: "${query}"\n\n${formatted}`,
            },
          ],
        };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text", text: `Search failed: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  // Tool: fetch_page (bonus — fetch a URL's text content)
  server.tool(
    "fetch_page",
    "Fetch the text content of a web page by URL.",
    {
      url: z.string().url().describe("The URL to fetch"),
    },
    async ({ url }) => {
      try {
        const resp = await fetch(url, {
          headers: {
            "User-Agent":
              "Mozilla/5.0 (compatible; mcp-web-search/1.0; +https://github.com/mcp-web-search)",
          },
          signal: AbortSignal.timeout(10_000),
        });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }
        const ct = resp.headers.get("content-type") || "";
        if (!ct.includes("text")) {
          throw new Error(`Non-text content type: ${ct}`);
        }
        let text = await resp.text();
        // Strip HTML tags (simple)
        text = text.replace(/<[^>]+>/g, " ").replace(/\s{2,}/g, " ").trim();
        // Truncate to 8 000 chars
        if (text.length > 8000) text = text.slice(0, 8000) + "\n\n[truncated]";
        return {
          content: [{ type: "text", text: `Content from ${url}:\n\n${text}` }],
        };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text", text: `Fetch failed: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  return server;
}

// ─── Express App ──────────────────────────────────────────────────────────────

const app = express();
app.use(express.json());

// Optional API key middleware
function authMiddleware(req: Request, res: Response, next: () => void) {
  if (!API_KEY) return next(); // no protection configured
  const provided =
    req.headers["x-api-key"] ||
    req.query["api_key"];
  if (provided !== API_KEY) {
    res.status(401).json({ error: "Unauthorized: invalid or missing API key" });
    return;
  }
  next();
}

// Health check
app.get("/", (_req: Request, res: Response) => {
  res.json({
    name: "mcp-web-search",
    version: "1.0.0",
    transport: ["SSE (/sse)", "Streamable HTTP (/mcp)"],
    searchProvider: BRAVE_API_KEY ? "Brave Search" : "DuckDuckGo (fallback)",
    tools: ["web_search", "fetch_page"],
  });
});

// ── SSE transport (legacy / Claude Desktop compatible) ────────────────────────
// Each SSE connection gets its own server + transport instance
const sseTransports: Record<string, SSEServerTransport> = {};

app.get("/sse", authMiddleware, async (req: Request, res: Response) => {
  const transport = new SSEServerTransport("/messages", res);
  const server = createMcpServer();
  sseTransports[transport.sessionId] = transport;

  res.on("close", () => {
    delete sseTransports[transport.sessionId];
  });

  await server.connect(transport);
});

app.post("/messages", authMiddleware, async (req: Request, res: Response) => {
  const sessionId = req.query.sessionId as string;
  const transport = sseTransports[sessionId];
  if (!transport) {
    res.status(404).json({ error: "SSE session not found" });
    return;
  }
  await transport.handlePostMessage(req, res, req.body);
});

// ── Streamable HTTP transport (MCP 2025-03-26 spec) ──────────────────────────
// STATELESS mode: every POST is self-contained — no mcp-session-id round-trip
// required. This is the most compatible mode for hosted clients (AIPI, n8n,
// serverless, etc.) that don't persist the session header between requests.
app.post("/mcp", authMiddleware, async (req: Request, res: Response) => {
  try {
    const server = createMcpServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined, // stateless — no session id issued
    });

    // Clean up when the response closes
    res.on("close", () => {
      transport.close();
      server.close();
    });

    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    console.error("Error handling /mcp POST:", err);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null,
      });
    }
  }
});

// In stateless mode, GET (server-initiated SSE stream) and DELETE (session
// teardown) are not applicable — respond with 405 so clients fall back cleanly.
app.get("/mcp", authMiddleware, (_req: Request, res: Response) => {
  res.status(405).json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed (stateless server)." },
    id: null,
  });
});

app.delete("/mcp", authMiddleware, (_req: Request, res: Response) => {
  res.status(405).json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed (stateless server)." },
    id: null,
  });
});

// ─── Start ────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`✅ MCP Web Search server running on port ${PORT}`);
  console.log(`   Health:          GET  http://localhost:${PORT}/`);
  console.log(`   SSE (legacy):    GET  http://localhost:${PORT}/sse`);
  console.log(`   Streamable HTTP: POST http://localhost:${PORT}/mcp`);
  console.log(
    `   Search provider: ${BRAVE_API_KEY ? "Brave Search" : "DuckDuckGo (set BRAVE_API_KEY to upgrade)"}`
  );
});
