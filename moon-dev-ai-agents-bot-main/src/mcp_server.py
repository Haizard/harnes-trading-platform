"""
🌐 Moon Dev's Trading MCP Server — HTTP API
Exposes internal trading tools via REST endpoints for the AI agent.

Architecture:
  AI Agent (Bedrock/Qwen3)
       │
       │ HTTP POST /mcp/call
       ▼
  Trading MCP Server (FastAPI)
       │
       ├─ /mcp/call      → Call a tool by name with params
       ├─ /mcp/tools      → List all available tools
       ├─ /mcp/history    → Recent tool call history
       ├─ /mcp/health     → Server health check
       │
       └─ Tools call real data sources:
           Jupiter, Birdeye, Solana RPC, PaperTrader, Scanner, RiskGuard

Start:
    python -m src.mcp_server
    # or
    uvicorn src.mcp_server:app --host 0.0.0.0 --port 8420

Usage from AI agent:
    POST http://localhost:8420/mcp/call
    {"tool": "get_token_price", "params": {"token_address": "..."}}
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.mcp_registry import (
    MCPRegistry, TradingMCPTool, ToolResult,
    create_default_mcp_registry,
)


# ── Pydantic Request/Response Models ──────────────────────────

class ToolCallRequest(BaseModel):
    """Request body for calling an MCP tool."""
    tool: str = Field(..., description="Tool name (e.g. 'get_token_price')")
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ToolCallResponse(BaseModel):
    """Response from an MCP tool call."""
    success: bool
    data: Any = None
    error: str = ""
    tool: str = ""
    source: str = ""
    latency_ms: float = 0.0
    timestamp: str = ""


class ToolInfo(BaseModel):
    """Tool metadata."""
    name: str
    description: str
    parameters: list = []
    source: str = ""


class ToolsListResponse(BaseModel):
    """Response listing all available tools."""
    tools: list
    count: int


class HealthResponse(BaseModel):
    """Server health check."""
    status: str
    tools_count: int
    uptime_seconds: float
    last_call: Optional[str] = None


# ── App Setup ─────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

app = FastAPI(
    title="Moon Dev Trading MCP Server",
    description="Internal trading data tools for AI agent access. READ-ONLY — no execution.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_registry: Optional[MCPRegistry] = None
_start_time = time.time()


def get_registry() -> MCPRegistry:
    """Lazy-init the MCP registry."""
    global _registry
    if _registry is None:
        _registry = create_default_mcp_registry()
    return _registry


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/mcp/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    registry = get_registry()
    return HealthResponse(
        status="ok",
        tools_count=len(registry.list_tool_names()),
        uptime_seconds=round(time.time() - _start_time, 1),
        last_call=(registry.get_call_history(1) or [{}])[-1].get("timestamp"),
    )


@app.get("/mcp/tools", response_model=ToolsListResponse)
async def list_tools():
    """List all available MCP tools with their parameters."""
    registry = get_registry()
    tools = registry.list_tools()
    return ToolsListResponse(tools=tools, count=len(tools))


@app.post("/mcp/call", response_model=ToolCallResponse)
async def call_tool(request: ToolCallRequest):
    """
    Call an MCP tool by name.

    Example:
        POST /mcp/call
        {"tool": "get_token_price", "params": {"token_address": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"}}
    """
    registry = get_registry()
    tool = registry.get_tool(request.tool)

    if not tool:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown tool: '{request.tool}'. Use GET /mcp/tools to list available tools."
        )

    # Validate required parameters
    for param in tool.parameters:
        if param.required and param.name not in request.params:
            if param.default is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Missing required parameter: '{param.name}'. {param.description}"
                )
            request.params[param.name] = param.default

    # Call the tool
    result: ToolResult = await registry.call_tool(request.tool, request.params)

    logger.info(
        f"[MCP] {request.tool} -> success={result.success} "
        f"latency={result.latency_ms:.0f}ms source={result.source}"
    )

    return ToolCallResponse(
        success=result.success,
        data=result.data,
        error=result.error,
        tool=request.tool,
        source=result.source,
        latency_ms=result.latency_ms,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/mcp/history")
async def call_history(limit: int = 20):
    """Get recent tool call history."""
    registry = get_registry()
    return {"history": registry.get_call_history(limit), "count": len(registry.get_call_history())}


# ── Tool Definition Endpoint (for AI agent discovery) ──────────

@app.get("/mcp/schema/{tool_name}")
async def get_tool_schema(tool_name: str):
    """
    Get the JSON schema for a specific tool.
    Useful for AI agents that need to know what parameters a tool accepts.
    """
    registry = get_registry()
    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    params = []
    for p in tool.parameters:
        params.append({
            "name": p.name,
            "type": p.type,
            "required": p.required,
            "default": p.default,
            "description": p.description,
        })

    return {
        "name": tool.name,
        "description": tool.description,
        "source": tool.source,
        "parameters": params,
    }


# ── Bulk Call (for pre-flight data gathering) ──────────────────

class BulkCallRequest(BaseModel):
    """Request multiple tool calls in one request."""
    calls: list  # [{"tool": "...", "params": {...}}, ...]


@app.post("/mcp/bulk")
async def bulk_call(request: BulkCallRequest):
    """
    Execute multiple tool calls in parallel (or sequential for dependencies).
    Useful for pre-flight data gathering before AI analysis.
    """
    registry = get_registry()
    results = []

    for call in request.calls:
        tool_name = call.get("tool", "")
        params = call.get("params", {})

        result = await registry.call_tool(tool_name, params)
        results.append({
            "tool": tool_name,
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "latency_ms": result.latency_ms,
        })

    return {"results": results, "count": len(results)}


# ── Run Server ────────────────────────────────────────────────

def main():
    """Start the MCP server."""
    import uvicorn

    port = int(os.getenv("MCP_PORT", "8420"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    print("")
    print("=" * 60)
    print("  Moon Dev Trading MCP Server")
    print("=" * 60)
    print(f"  Port: {port}")
    print(f"  Host: {host}")
    print(f"  Docs: http://localhost:{port}/docs")
    print(f"  Tools: http://localhost:{port}/mcp/tools")
    print("=" * 60)
    print("")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
