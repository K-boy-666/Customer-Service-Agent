---
name: minimal-order-mcp
description: >
  Build a minimal order database + REST API + MCP Server that exposes order data as typed
  MCP tools for Codex. Use this skill whenever the user wants to build an order query system,
  create an MCP server for orders, wrap an order API for AI agents, scaffold an order
  management backend, or mentions "order MCP", "订单 MCP", "expose order data to Codex",
  "给 Codex 加订单查询", "order tools for agent", "AI 查订单", or any three-tier
  data→API→MCP setup — even when they don't say "MCP" explicitly but describe building
  an API that an AI agent can query.
---

# Minimal Order MCP — 三层架构搭建指南

Build a complete **data → REST API → MCP Server** stack so an AI agent can query
an order database through typed MCP tools.

---

## Architecture Overview

```
Agent (Codex)  ──stdio──▶  MCP Server (server.py)
                                  │ HTTP
                                  ▼
                            REST API (order_api.py)
                                  │
                                  ▼
                         Order Database (in-memory / SQLite)
```

Three independent layers. Each can be tested in isolation. The MCP server reads
`API_BASE_URL` from the environment, so it doesn't care where the API lives —
local, remote, or containerized.

---

## Phase 1: Scaffold the Project

Create the project structure and install dependencies.

### 1.1 Directory layout

```
my-order-mcp/
├── order_api.py          # REST API (FastAPI)
├── api_client.py         # HTTP client (httpx)
├── server.py             # MCP Server (FastMCP, stdio)
├── pyproject.toml        # Project + dependencies
└── .env                  # API_BASE_URL=http://localhost:8000
```

### 1.2 pyproject.toml

Create `pyproject.toml` with these dependencies:

```toml
[project]
name = "mcp-order-server"
version = "0.1.0"
description = "MCP server for querying an order REST API"
requires-python = ">=3.10"
dependencies = [
    "fastmcp>=3.0.0",
    "httpx>=0.27.0",
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.30.0",
]

[project.scripts]
order-server = "server:main"
```

Then install:

```bash
pip install -e .
# or: uv sync
```

### 1.3 .env

```
API_BASE_URL=http://localhost:8000
```

---

## Phase 2: Build the Data Layer + REST API

Copy the template from `references/order_api_template.py`. Key decisions:

- **In-memory list**: Good for prototyping. Replace with SQLite/SQLAlchemy for persistence.
- **At least 5–8 sample orders**: Include different statuses (`pending`, `shipped`, `delivered`, `cancelled`) so the agent has realistic data to explore.
- **Chinese or English data**: The template uses Chinese customer names — adapt to your domain.

### API Endpoints (all GET, read-only by default)

| Endpoint | Purpose |
|---|---|
| `GET /api/orders/search?q=` | Keyword search across order number, customer, item names |
| `GET /api/orders/{id}` | Fetch single order with line items |
| `GET /api/orders?status=&offset=&limit=` | List with status filter, pagination |
| `GET /api/orders/stats?period=` | Aggregate: count, revenue, by-status breakdown |
| `GET /api/orders/by-customer?customer=` | Filter by customer name/email |

### Start the API

```bash
uvicorn order_api:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/api/orders/stats?period=all
```

---

## Phase 3: Build the HTTP Client

Copy the template from `references/api_client_template.py`. This thin wrapper:

- Reads `API_BASE_URL` and optional `API_KEY` from environment.
- Uses `httpx.AsyncClient` for async HTTP calls.
- Handles 404 gracefully (returns `None` instead of throwing).
- Each function returns dicts ready for JSON serialization.

The MCP server never calls the REST API directly — it always goes through
this client. This keeps the MCP layer clean and makes it trivial to add
auth, retries, or caching later.

---

## Phase 4: Build the MCP Server

Copy the template from `references/mcp_server_template.py`. Design rules:

### Tool granularity

One MCP tool per API endpoint. Don't cram multiple queries into one tool —
the agent can compose calls itself.

### Tool annotations

Every tool MUST carry these annotations:

```python
@mcp.tool(
    annotations={
        "readOnlyHint": True,     # ← prevents the agent from thinking it can write
        "openWorldHint": True,    # ← tells the agent data may be incomplete
        "title": "Search orders", # ← human-readable, shown in tool list
    },
)
```

### Return format

Always return JSON strings. The agent reads them as structured text:

```python
async def search_orders(query: str, limit: int = 20) -> str:
    results = await api_client.search_orders(query, limit)
    if not results:
        return f"No orders matching '{query}'."
    return json.dumps(results, ensure_ascii=False, indent=2)
```

### Friendly empty results

When nothing matches, return a descriptive string (not empty JSON). This helps
the agent decide what to do next.

### Instructions string

Pass an `instructions` parameter to `FastMCP()` — this gets injected into the
agent's system prompt, guiding it on which tools to use first:

```python
mcp = FastMCP(
    name="order-server",
    version="0.1.0",
    instructions=(
        "This server queries a local order database. "
        "Use search_orders to find orders by keyword when you don't have an exact ID. "
        "Use get_order to fetch full details once you have an order ID. "
        "Use list_orders to browse with optional status filtering. "
        "Use get_order_stats first for an overview of what's in the system."
    ),
)
```

### Transport

Use `mcp.run()` with no arguments — this defaults to **stdio transport**,
which is what Codex and Codex Desktop expect.

---

## Phase 5: Configure Codex

### 5.1 Register the MCP server

Add to `.Codex/settings.local.json`:

```json
{
  "mcpServers": {
    "order-server": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "${workspaceFolder}"
    }
  },
  "permissions": {
    "allow": [
      "mcp__order-server__search_orders",
      "mcp__order-server__get_order",
      "mcp__order-server__list_orders",
      "mcp__order-server__get_orders_by_date",
      "mcp__order-server__get_orders_by_customer",
      "mcp__order-server__get_order_stats"
    ]
  }
}
```

### 5.2 Permission strategy

- **Start restrictive**: only allow the tools you've built and tested.
- **Remove `mcp__order-server__` prefix**: that prefix matches any future tool
  from the same server — convenient but risky before you audit all tools.
- **Add write tools separately**: if you later add `create_order` or `update_order`,
  add those permissions individually so the user is prompted before writes.

---

## Phase 6: Start & Verify

### 6.1 Launch sequence

```bash
# Terminal 1: Start the REST API
uvicorn order_api:app --reload --port 8000

# Terminal 2: Codex auto-launches the MCP server via settings.json
Codex
```

### 6.2 Verification checklist

In Codex, ask these questions and confirm sensible answers:

1. **"How many orders are in the system?"** → should use `get_order_stats`
2. **"Find orders for 张三"** → should use `get_orders_by_customer`
3. **"Show me the details of order ORD-20260601-001"** → should use `get_order`
4. **"List all pending orders"** → should use `list_orders` with `status=pending`
5. **"What orders were created this week?"** → should use `get_orders_by_date`

### 6.3 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: fastmcp` | venv not activated | Use `uv run python server.py` or activate venv |
| `Connection refused` on port 8000 | API not started | Run `uvicorn order_api:app --port 8000` first |
| MCP tools don't appear | settings.json not loaded | Restart Codex after editing settings |
| 404 on `/api/orders/xxx` | Wrong order ID format | Use `search_orders` to find valid IDs |

---

## Extending the System

Once the basic stack works, common extensions:

### Add persistence (SQLite)

Replace the in-memory list in `order_api.py`:

```python
import sqlite3
DATABASE = "orders.db"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn
```

### Add write operations

1. Add POST/PUT endpoints to `order_api.py`
2. Add corresponding functions to `api_client.py`
3. Remove `readOnlyHint=True` from the new MCP tools
4. Add explicit permissions in `settings.local.json`

### Ship as .mcpb

Use the `build-mcpb` skill to bundle the server into a standalone
executable — users won't need Python installed.

---

## Reference Files

- `references/order_api_template.py` — Full FastAPI app with sample data and 5 endpoints
- `references/api_client_template.py` — Async HTTP client with error handling
- `references/mcp_server_template.py` — FastMCP server with 6 annotated tools
- `references/settings_template.json` — Codex configuration
