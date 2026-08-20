# MCP Web Search Server

A **Model Context Protocol (MCP) server** that provides web search capabilities, deployable on [Render](https://render.com).

Built with **Python** using the official MCP SDK, supporting **SSE (Server-Sent Events)** and **Streamable HTTP** transports for AIPI.

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

## 🔌 Connect to AIPI

In AIPI's **Add Custom MCP** screen, use this **flat** configuration (no wrapper object):

**SSE:**
```json
{
  "url": "https://mcp-web-search-j73g.onrender.com/sse",
  "name": "web-search",
  "type": "sse"
}
```

**Streamable HTTP:**
```json
{
  "url": "https://mcp-web-search-j73g.onrender.com/mcp",
  "name": "web-search",
  "type": "streamable"
}
```

Replace the host with your actual Render service URL if different. AIPI uses a flat
`url` / `name` / `type` object — it does **not** use a `mcpServers` wrapper.

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
pip install -r requirements.txt
SERPER_API_KEY=your_key python server.py
```

Health check:
```bash
curl http://localhost:3000/
```

The server runs on port 3000 by default. Set `PORT` environment variable to change it.

---

## ⚠️ Free tier note

Render's **free tier** spins down after 15 minutes of inactivity (cold start ~30s).
Upgrade to the **Starter plan ($7/mo)** to keep it always-on.
