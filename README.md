# MCP Web Search Server

A **Model Context Protocol (MCP) server** that provides web search capabilities, deployable on [Render](https://render.com).

Supports both transport formats:
- **SSE** (Server-Sent Events) — legacy, compatible with Claude Desktop
- **Streamable HTTP** — MCP 2025-03-26 spec, used by modern clients (AIPI, etc.)

## Tools exposed

| Tool | Description |
|------|-------------|
| `web_search` | Search the web. Returns titles, URLs, and descriptions. |
| `fetch_page` | Fetch and extract text content from any URL. |

---

## 🚀 Deploy to Render (step-by-step)

### 1. Push this repo to GitHub

```bash
cd mcp-web-search
git init
git add .
git commit -m "Initial commit"
# create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/mcp-web-search.git
git push -u origin main
```

### 2. Create a new Web Service on Render

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo (`mcp-web-search`)
3. Render will auto-detect `render.yaml` — click **Apply**
4. Set environment variables in the Render dashboard:

| Variable | Required | Description |
|----------|----------|-------------|
| `SERPER_API_KEY` | Recommended | Get free at [serper.dev](https://serper.dev/) — 2,500 queries/month free (Google results) |
| `MCP_API_KEY` | Optional | Set to any secret string to password-protect your server |

5. Click **Create Web Service** — Render will build and deploy automatically.

Your server URL will be: `https://mcp-web-search.onrender.com` (or similar)

---

## 🔌 Connect to AIPI (or any MCP client)

### Streamable HTTP (recommended for AIPI)

```
URL:  https://YOUR-SERVICE.onrender.com/mcp
```

Add to your MCP client config:
```json
{
  "mcpServers": {
    "web-search": {
      "type": "streamable-http",
      "url": "https://YOUR-SERVICE.onrender.com/mcp",
      "headers": {
        "x-api-key": "YOUR_MCP_API_KEY"
      }
    }
  }
}
```

### SSE (legacy / Claude Desktop)

```
URL:  https://YOUR-SERVICE.onrender.com/sse
```

Claude Desktop `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "web-search": {
      "type": "sse",
      "url": "https://YOUR-SERVICE.onrender.com/sse",
      "headers": {
        "x-api-key": "YOUR_MCP_API_KEY"
      }
    }
  }
}
```

> **Tip:** If `MCP_API_KEY` is not set, omit the `headers` field entirely.

---

## 🔍 Search Providers

| Provider | Key required | Quality | Limit |
|----------|-------------|---------|-------|
| **Serper.dev** | Yes (free) | ⭐⭐⭐⭐⭐ | 2,500 queries/month free (Google results) |
| **DuckDuckGo** | No | ⭐⭐⭐ | Unlimited (fallback) |

Set `SERPER_API_KEY` in Render env vars to use Serper. The server falls back to DuckDuckGo automatically if the key is absent.

---

## 🧪 Test locally

```bash
npm install
npm run build
SERPER_API_KEY=your_key npm start
```

Health check:
```bash
curl http://localhost:3000/
```

Test Streamable HTTP:
```bash
curl -X POST http://localhost:3000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": { "name": "test", "version": "1.0" }
    }
  }'
```

---

## ⚠️ Free tier note

Render's **free tier** spins down after 15 minutes of inactivity (cold start ~30s).
Upgrade to the **Starter plan ($7/mo)** to keep it always-on.
