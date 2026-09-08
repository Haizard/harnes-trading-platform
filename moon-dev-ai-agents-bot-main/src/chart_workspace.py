"""Persistent chart workspace state built on the existing event log."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.db_storage import get_events, log_event


_DEFAULT_AGENTS = {
    "rbi_agent": {"label": "RBI strategies", "enabled": True, "render": True},
    "custom_chart_bot": {"label": "Custom chart bots", "enabled": True, "render": True},
    "bedrock": {"label": "Bedrock analysis", "enabled": True, "render": True},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_chat(session_id: str, role: str, content: str, symbol: str, timeframe: str, snapshot_time: str | None = None) -> dict[str, Any]:
    message = {
        "session_id": session_id,
        "role": role,
        "content": content[:12000],
        "symbol": symbol,
        "timeframe": timeframe,
        "snapshot_time": snapshot_time,
        "created_at": _now(),
    }
    log_event("chart_chat_message", message)
    return message


def get_chat(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    rows = get_events("chart_chat_message", max(1, min(limit, 100)))
    messages = []
    for row in rows:
        data = row.get("data", row)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        if data.get("session_id") == session_id:
            messages.append(data)
    return list(reversed(messages))


def get_agents() -> dict[str, dict[str, Any]]:
    agents = {key: value.copy() for key, value in _DEFAULT_AGENTS.items()}
    rows = get_events("chart_agent_toggle", 100)
    for row in reversed(rows):
        data = row.get("data", row)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        agent_id = data.get("agent_id")
        if agent_id in agents:
            agents[agent_id].update({key: data[key] for key in ("enabled", "render") if key in data})
    return agents


def toggle_agent(agent_id: str, enabled: bool | None = None, render: bool | None = None) -> dict[str, Any] | None:
    agents = get_agents()
    if agent_id not in agents:
        return None
    update = {"agent_id": agent_id, "enabled": enabled if enabled is not None else agents[agent_id]["enabled"], "render": render if render is not None else agents[agent_id]["render"]}
    log_event("chart_agent_toggle", {**update, "changed_at": _now()})
    agents[agent_id].update(update)
    return agents[agent_id]


def new_session_id() -> str:
    return uuid.uuid4().hex
