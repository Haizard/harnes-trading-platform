"""
MCP Web Management Panel — Full interactive control of the Model Context Protocol server.

Features:
  - Server status: health check, uptime, tool count, auto-connect
  - Config management: API keys, server URL, enable/disable individual tools
  - Tool tester: call any MCP tool from the browser, see live results
  - Call history: recent tool calls with latency, success/failure
  - Auto-connect: MCP server starts with platform and auto-registers tools

Architecture:
  APIRouter → mounted on main dashboard app
  Config stored in JSON file (src/data/mcp/config.json)
  Server runs as part of the dashboard process (in-process, not separate port)
"""

import os
import sys
import json
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ── Paths ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data" / "mcp"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"

# ── Config Management ──────────────────────────────────────

DEFAULT_CONFIG = {
    "auto_connect": True,
    "server_host": "0.0.0.0",
    "server_port": 8420,
    "api_keys": {
        "birdeye": "",
        "twitter_bearer": "",
    },
    "enabled_tools": [],  # Empty = all enabled
    "disabled_tools": [],
    "last_health_check": None,
    "server_status": "unknown",
}


def load_config() -> dict:
    """Load MCP config from JSON file."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            # Merge with defaults for any missing keys
            config = {**DEFAULT_CONFIG, **saved}
            return config
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save MCP config to JSON file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ── MCP Server Integration ─────────────────────────────────
# We import the MCP registry and expose it through the dashboard

_mcp_registry = None
_mcp_start_time = None


def get_mcp_registry():
    """Lazy-init the MCP registry."""
    global _mcp_registry, _mcp_start_time
    if _mcp_registry is None:
        try:
            from src.mcp_registry import create_default_mcp_registry
            _mcp_registry = create_default_mcp_registry()
            _mcp_start_time = time.time()
            print("[MCP] Registry initialized via web panel", flush=True)
        except Exception as e:
            print(f"[MCP] Failed to init registry: {e}", flush=True)
    return _mcp_registry


# ── Router ──────────────────────────────────────────────────

router = APIRouter(prefix="/mcp", tags=["MCP Web"])


# ── HTML Frontend ──────────────────────────────────────────

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "mcp"


def _load_mcp_html() -> str:
    """Load MCP HTML from template file."""
    html_file = _TEMPLATE_DIR / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>MCP Agent template not found</h1>"


MCP_HTML_CONTENT = None


def get_mcp_html() -> str:
    global MCP_HTML_CONTENT
    if MCP_HTML_CONTENT is None:
        MCP_HTML_CONTENT = _load_mcp_html()
    return MCP_HTML_CONTENT


# ── API Endpoints ──────────────────────────────────────────

@router.get("/status")
async def mcp_status():
    """Get MCP server status including health, uptime, tool count."""
    config = load_config()
    registry = get_mcp_registry()

    uptime = 0
    health = {"status": "unknown"}
    if registry and _mcp_start_time:
        uptime = round(time.time() - _mcp_start_time, 1)

    # Try health check if MCP server is running separately
    try:
        import requests as req
        port = config.get("server_port", 8420)
        r = req.get(f"http://127.0.0.1:{port}/mcp/health", timeout=2)
        if r.status_code == 200:
            health = r.json()
    except Exception:
        # In-process mode — health from registry
        tool_count = len(registry.list_tool_names()) if registry else 0
        health = {
            "status": "running_in_dashboard",
            "tools_count": tool_count,
            "uptime_seconds": uptime,
        }

    return {
        "auto_connect": config.get("auto_connect", True),
        "server_status": health.get("status", "unknown"),
        "tools_count": health.get("tools_count", 0),
        "uptime_seconds": health.get("uptime_seconds", 0),
        "last_call": health.get("last_call"),
        "server_port": config.get("server_port", 8420),
    }


@router.get("/tools")
async def mcp_tools():
    """List all MCP tools with metadata."""
    registry = get_mcp_registry()
    if not registry:
        return {"tools": [], "count": 0, "error": "Registry not initialized"}

    config = load_config()
    disabled = set(config.get("disabled_tools", []))
    tools = []
    for tool_dict in registry.list_tools():
        tools.append({
            **tool_dict,
            "enabled": tool_dict["name"] not in disabled,
        })

    return {"tools": tools, "count": len(tools)}


@router.post("/call")
async def mcp_call(request: Request):
    """Call an MCP tool by name with parameters."""
    body = await request.json()
    tool_name = body.get("tool", "")
    params = body.get("params", {})

    if not tool_name:
        raise HTTPException(400, "Tool name is required")

    registry = get_mcp_registry()
    if not registry:
        raise HTTPException(503, "MCP registry not initialized")

    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(404, f"Unknown tool: {tool_name}")

    # Validate required params
    for param in tool.parameters:
        if param.required and param.name not in params:
            if param.default is not None:
                params[param.name] = param.default
            else:
                raise HTTPException(422, f"Missing required parameter: {param.name}")

    result = await registry.call_tool(tool_name, params)

    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "tool": tool_name,
        "source": result.source,
        "latency_ms": round(result.latency_ms, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/history")
async def mcp_history(limit: int = 50):
    """Get recent MCP tool call history."""
    registry = get_mcp_registry()
    if not registry:
        return {"history": [], "count": 0}

    history = registry.get_call_history(limit)
    return {"history": history, "count": len(history)}


@router.post("/config")
async def mcp_update_config(request: Request):
    """Update MCP configuration."""
    body = await request.json()
    config = load_config()

    # Update allowed fields
    if "auto_connect" in body:
        config["auto_connect"] = bool(body["auto_connect"])
    if "api_keys" in body:
        config["api_keys"] = {**config.get("api_keys", {}), **body["api_keys"]}
    if "enabled_tools" in body:
        config["enabled_tools"] = body["enabled_tools"]
    if "disabled_tools" in body:
        config["disabled_tools"] = body["disabled_tools"]
    if "server_port" in body:
        config["server_port"] = int(body["server_port"])

    save_config(config)

    # Apply API keys to environment
    for key, value in config.get("api_keys", {}).items():
        if value:
            env_key = key.upper()
            if not env_key.endswith("_API_KEY"):
                env_key += "_API_KEY"
            os.environ[env_key] = value

    return {"status": "ok", "config": config}


@router.get("/config")
async def mcp_get_config():
    """Get current MCP configuration (masks API keys)."""
    config = load_config()
    # Mask API keys for security
    safe_config = config.copy()
    safe_config["api_keys"] = {
        k: ("***" + v[-4:] if len(v) > 4 else ("set" if v else ""))
        for k, v in config.get("api_keys", {}).items()
    }
    return safe_config


@router.get("/schema/{tool_name}")
async def mcp_tool_schema(tool_name: str):
    """Get detailed schema for a specific tool."""
    registry = get_mcp_registry()
    if not registry:
        raise HTTPException(503, "Registry not initialized")

    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(404, f"Unknown tool: {tool_name}")

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


# ── Standalone App ─────────────────────────────────────────

app = None  # Will be set if run standalone

def create_standalone_app():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    standalone = FastAPI(title="MCP Web Panel")
    standalone.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    standalone.include_router(router)

    @standalone.get("/", response_class=HTMLResponse)
    async def index():
        return get_mcp_html()

    return standalone


if __name__ == "__main__":
    import uvicorn
    standalone_app = create_standalone_app()
    uvicorn.run(standalone_app, host="0.0.0.0", port=8421, log_level="info")
