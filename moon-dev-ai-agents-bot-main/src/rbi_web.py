"""
RBI Agent Web Interface — Full interactive control of the RBI pipeline.

Features:
  - Add trading ideas via web form (no more editing ideas.txt)
  - Trigger pipeline runs from the browser
  - Live terminal log streaming via WebSocket
  - View generated backtest code per strategy
  - Browse strategy results with scores, walk-forward, decisions

Architecture:
  RunManager (singleton) → captures stdout → broadcasts to WebSocket clients
  FastAPI Router → mounted on main dashboard app

Start standalone:
  uvicorn src.rbi_web:app --host 0.0.0.0 --port 8081
"""

import os
import sys
import json
import time
import uuid
import io
import re
import threading
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
from contextlib import contextmanager

from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Paths ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data" / "rbi"
IDEAS_FILE = DATA_DIR / "ideas.txt"
STRATEGY_MEMORY_DIR = DATA_DIR / "strategy_memory"
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


# ── Run Manager ────────────────────────────────────────────

class OutputCapture(io.StringIO):
    """Captures stdout and forwards lines to the RunManager."""

    def __init__(self, run_id: str, run_manager: 'RunManager'):
        super().__init__()
        self.run_id = run_id
        self.run_manager = run_manager
        self._original_stdout = sys.stdout

    def write(self, text: str):
        if text and text.strip():
            self._original_stdout.write(text)
            self.run_manager.append_log(self.run_id, text)
        elif text:
            self._original_stdout.write(text)

    def flush(self):
        self._original_stdout.flush()


class RunManager:
    """Manages RBI pipeline runs — tracks state, captures output, streams via WebSocket."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.runs: Dict[str, dict] = {}
        self.logs: Dict[str, List[str]] = {}
        self.websocket_clients: Dict[str, Set[WebSocket]] = {}
        self._lock = threading.Lock()
        self._load_runs_from_disk()

    def _load_runs_from_disk(self):
        """Load persisted runs from JSONL."""
        runs_file = RUNS_DIR / "runs.jsonl"
        if runs_file.exists():
            try:
                for line in runs_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        run = json.loads(line)
                        rid = run.get("id", "")
                        if rid:
                            self.runs[rid] = run
                            # Load logs if available
                            log_file = RUNS_DIR / f"{rid}.log"
                            if log_file.exists():
                                self.logs[rid] = log_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                pass

    def create_run(self, idea_text: str, auto_mode: bool = False) -> str:
        """Create a new pipeline run. Returns run_id."""
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        run = {
            "id": run_id,
            "idea": idea_text[:2000],
            "auto_mode": auto_mode,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "strategy_name": None,
            "error": None,
            "phases": {},
        }
        with self._lock:
            self.runs[run_id] = run
            self.logs[run_id] = []
        self._persist_run(run)
        return run_id

    def start_run(self, run_id: str):
        """Mark a run as started and launch it in a thread."""
        with self._lock:
            if run_id not in self.runs:
                return
            self.runs[run_id]["status"] = "running"
            self.runs[run_id]["started_at"] = datetime.now(timezone.utc).isoformat()
        self._persist_run(self.runs[run_id])
        self._broadcast_status(run_id)

        thread = threading.Thread(target=self._execute_run, args=(run_id,), daemon=True)
        thread.start()

    def _execute_run(self, run_id: str):
        """Execute the RBI pipeline with stdout capture."""
        run = self.runs.get(run_id)
        if not run:
            return

        # Add to ideas.txt
        idea_text = run["idea"]
        self._append_idea(idea_text)

        # Capture stdout
        capture = OutputCapture(run_id, self)
        old_stdout = sys.stdout
        sys.stdout = capture

        try:
            # Import and run the pipeline
            sys.path.insert(0, str(PROJECT_ROOT / "moon-dev-ai-agents-bot-main"))
            sys.path.insert(0, str(PROJECT_ROOT))
            from src.agents.rbi_agent import process_trading_idea

            # Set auto_mode to True for web runs (skip human gate)
            process_trading_idea(idea_text, auto_mode=True)

            # Mark completed
            with self._lock:
                self.runs[run_id]["status"] = "completed"
                self.runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

            # Try to extract strategy name from logs
            self._extract_result(run_id)

        except Exception as e:
            with self._lock:
                self.runs[run_id]["status"] = "error"
                self.runs[run_id]["error"] = str(e)
                self.runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.append_log(run_id, f"\n[FATAL ERROR] {e}\n")
        finally:
            sys.stdout = old_stdout
            self._persist_run(self.runs[run_id])
            self._broadcast_status(run_id)
            self._broadcast_log(run_id, "__DONE__")

    def _extract_result(self, run_id: str):
        """Extract strategy result from the pipeline logs."""
        run = self.runs.get(run_id)
        if not run:
            return
        log_text = "\n".join(self.logs.get(run_id, []))

        # Try to find strategy name
        m = re.search(r"STRATEGY_NAME:\s*(\S+)", log_text)
        if m:
            run["strategy_name"] = m.group(1)

        # Try to find result
        if "SUCCESS:" in log_text or "is LIVE" in log_text:
            run["result"] = "GO_LIVE"
        elif "REJECTED" in log_text:
            run["result"] = "REJECT"
        elif "Phase 1 failed" in log_text or "Phase 2 failed" in log_text:
            run["result"] = "REJECT"

        # Extract phases from logs
        phases = {}
        for line in self.logs.get(run_id, []):
            if "Phase 1" in line or "RESEARCH" in line.upper():
                phases["research"] = True
            if "Phase 2" in line or "BACKTEST" in line.upper():
                phases["backtest"] = True
            if "Phase 3" in line or "DEBUG" in line.upper():
                phases["debug"] = True
            if "Phase 4" in line or "EXECUTE" in line.upper():
                phases["execute"] = True
            if "Phase 5" in line or "EVALUATE" in line.upper():
                phases["evaluate"] = True
            if "Phase 6" in line or "DEPLOY" in line.upper():
                phases["deploy"] = True
            if "walk-forward" in line.lower():
                phases["walk_forward"] = True
            if "alpha decay" in line.lower():
                phases["alpha_decay"] = True
        run["phases"] = phases

    def append_log(self, run_id: str, text: str):
        """Append a log line and broadcast to WebSocket clients."""
        with self._lock:
            if run_id not in self.logs:
                self.logs[run_id] = []
            self.logs[run_id].append(text)
        self._broadcast_log(run_id, text)

    def get_run(self, run_id: str) -> Optional[dict]:
        return self.runs.get(run_id)

    def get_all_runs(self, limit: int = 50) -> List[dict]:
        runs = sorted(self.runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)
        return runs[:limit]

    def get_logs(self, run_id: str, offset: int = 0) -> List[str]:
        logs = self.logs.get(run_id, [])
        return logs[offset:]

    async def register_ws(self, run_id: str, ws: WebSocket):
        await ws.accept()
        with self._lock:
            if run_id not in self.websocket_clients:
                self.websocket_clients[run_id] = set()
            self.websocket_clients[run_id].add(ws)
        # Send existing logs
        for line in self.logs.get(run_id, []):
            try:
                await ws.send_text(line)
            except Exception:
                break

    def unregister_ws(self, run_id: str, ws: WebSocket):
        with self._lock:
            if run_id in self.websocket_clients:
                self.websocket_clients[run_id].discard(ws)

    def _broadcast_log(self, run_id: str, text: str):
        # Thread-safe: store the line, let the WS handler poll for it
        pass  # Logs are stored in self.logs; WS handler reads them

    def _broadcast_status(self, run_id: str):
        # Thread-safe: status is stored in self.runs; WS handler reads it
        pass

    def get_new_logs(self, run_id: str, offset: int = 0) -> List[str]:
        """Get log lines from offset (for WebSocket polling)."""
        return self.logs.get(run_id, [])[offset:]

    def _persist_run(self, run: dict):
        """Append run to JSONL file."""
        runs_file = RUNS_DIR / "runs.jsonl"
        try:
            with open(runs_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(run, default=str) + "\n")
        except Exception:
            pass

        # Also save logs
        log_file = RUNS_DIR / f"{run['id']}.log"
        try:
            log_text = "\n".join(self.logs.get(run["id"], []))
            log_file.write_text(log_text, encoding="utf-8")
        except Exception:
            pass

    def _append_idea(self, text: str):
        """Append idea to ideas.txt for persistence."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(IDEAS_FILE, "a", encoding="utf-8") as f:
                f.write(text.strip() + "\n\n")
        except Exception:
            pass


# ── FastAPI Router ─────────────────────────────────────────

router = APIRouter(tags=["rbi"])
run_manager = RunManager()


# ── API Routes ─────────────────────────────────────────────

@router.get("/api/rbi/ideas")
async def list_ideas():
    """List ideas from ideas.txt."""
    ideas = []
    if IDEAS_FILE.exists():
        content = IDEAS_FILE.read_text(encoding="utf-8").strip()
        if content:
            # Split by double newline
            parts = re.split(r"\n\s*\n", content)
            for i, part in enumerate(parts):
                text = part.strip()
                if text:
                    ideas.append({
                        "id": i,
                        "text": text[:2000],
                        "length": len(text),
                    })
    return {"ideas": ideas, "count": len(ideas)}


@router.post("/api/rbi/ideas")
async def add_idea(request: Request):
    """Add a new trading idea and optionally start a run."""
    body = await request.json()
    text = body.get("text", "").strip()
    auto_run = body.get("auto_run", False)

    if not text:
        raise HTTPException(400, "Idea text is required")

    # Create a run
    run_id = run_manager.create_run(text)

    if auto_run:
        run_manager.start_run(run_id)

    return {"run_id": run_id, "status": "queued" if not auto_run else "running"}


@router.post("/api/rbi/run/{run_id}")
async def start_run(run_id: str):
    """Start a queued run."""
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "queued":
        raise HTTPException(400, f"Run is already {run['status']}")
    run_manager.start_run(run_id)
    return {"status": "running"}


@router.get("/api/rbi/runs")
async def list_runs(limit: int = 50):
    """List all runs."""
    return {"runs": run_manager.get_all_runs(limit)}


@router.get("/api/rbi/run/{run_id}")
async def get_run(run_id: str):
    """Get a specific run with its logs."""
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "run": run,
        "logs": run_manager.get_logs(run_id),
    }


@router.get("/api/rbi/run/{run_id}/logs")
async def get_run_logs(run_id: str, offset: int = 0):
    """Get logs for a run."""
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"logs": run_manager.get_logs(run_id, offset)}


@router.get("/api/rbi/results")
async def list_results():
    """List strategy results from strategy_memory."""
    history_path = STRATEGY_MEMORY_DIR / "strategy_history.jsonl"
    results = []
    if history_path.exists():
        try:
            for line in history_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    results.append(entry)
        except Exception:
            pass
    # Most recent first
    results.reverse()
    return {"results": results[:100], "count": len(results)}


@router.get("/api/rbi/result/{strategy_name}")
async def get_result(strategy_name: str):
    """Get detailed result for a specific strategy."""
    history_path = STRATEGY_MEMORY_DIR / "strategy_history.jsonl"
    if not history_path.exists():
        raise HTTPException(404, "No history found")
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if entry.get("strategy_name") == strategy_name:
                return {"result": entry}
    raise HTTPException(404, f"Strategy {strategy_name} not found")


@router.get("/api/rbi/code/{strategy_name}")
async def get_strategy_code(strategy_name: str):
    """Get the generated backtest code for a strategy."""
    backtest_dir = DATA_DIR / "backtests_final"
    code_file = backtest_dir / f"{strategy_name}_BTFinal.py"
    if not code_file.exists():
        # Try backtests dir
        backtest_dir = DATA_DIR / "backtests"
        code_file = backtest_dir / f"{strategy_name}_BT.py"
    if not code_file.exists():
        raise HTTPException(404, f"Code for {strategy_name} not found")
    return {"code": code_file.read_text(encoding="utf-8"), "strategy_name": strategy_name}


@router.get("/api/rbi/backtest/{strategy_name}")
async def get_backtest_data(strategy_name: str):
    """Get backtest results (stats + code) for a strategy."""
    # Get stats from strategy memory
    history_path = STRATEGY_MEMORY_DIR / "strategy_history.jsonl"
    stats = None
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("strategy_name") == strategy_name:
                    stats = entry
                    break

    # Get code
    code = None
    for d in ["backtests_final", "backtests"]:
        code_file = DATA_DIR / d / f"{strategy_name}_BTFinal.py"
        if not code_file.exists():
            code_file = DATA_DIR / d / f"{strategy_name}_BT.py"
        if code_file.exists():
            code = code_file.read_text(encoding="utf-8")
            break

    return {"stats": stats, "code": code, "strategy_name": strategy_name}


# ── WebSocket ──────────────────────────────────────────────

@router.websocket("/ws/rbi/{run_id}")
async def websocket_run_logs(websocket: WebSocket, run_id: str):
    """Stream live logs for a run via WebSocket.
    
    Polls for new log lines every 0.3s and sends them to the client.
    This is thread-safe because we only read from shared state.
    """
    await websocket.accept()
    offset = 0
    try:
        while True:
            run = run_manager.get_run(run_id)
            if not run:
                await websocket.send_text("__ERROR__: Run not found")
                break

            # Send new log lines
            new_lines = run_manager.get_new_logs(run_id, offset)
            for line in new_lines:
                await websocket.send_text(line)
                offset += 1

            # Send status update if run finished
            if run["status"] in ("completed", "error"):
                status_msg = json.dumps({"type": "status", "run": run}, default=str)
                await websocket.send_text(status_msg)
                if new_lines:  # Wait for final logs to flush
                    await asyncio.sleep(0.5)
                    new_lines2 = run_manager.get_new_logs(run_id, offset)
                    for line in new_lines2:
                        await websocket.send_text(line)
                        offset += 1
                await websocket.send_text("__DONE__")
                break

            # Wait before polling again
            try:
                # Also check for client pings (non-blocking)
                import asyncio as _aio
                done = False
                async def _check_ping():
                    nonlocal done
                    try:
                        data = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                        if data == "ping":
                            await websocket.send_text("pong")
                    except Exception:
                        pass
                    done = True
                await _check_ping()
            except Exception:
                pass

            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ── HTML Frontend ──────────────────────────────────────────

RBI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🧪 RBI Agent — Strategy Pipeline</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f; color: #e0e0e0;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            padding: 16px 24px; border-bottom: 1px solid #333;
            display: flex; justify-content: space-between; align-items: center;
        }
        .header h1 { color: #00d4ff; font-size: 20px; }
        .header a { color: #888; text-decoration: none; font-size: 13px; }
        .header a:hover { color: #00d4ff; }

        .tabs {
            background: #111; padding: 0 24px;
            display: flex; gap: 0; border-bottom: 1px solid #222;
        }
        .tab {
            padding: 12px 20px; cursor: pointer; font-size: 13px;
            color: #888; border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }
        .tab:hover { color: #ccc; }
        .tab.active { color: #00d4ff; border-bottom-color: #00d4ff; }

        .container { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
        .panel { display: none; }
        .panel.active { display: block; }

        .card {
            background: #111; border: 1px solid #222; border-radius: 8px;
            padding: 16px; margin-bottom: 16px;
        }
        .card h3 { color: #00d4ff; font-size: 13px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }

        /* Idea Input */
        .idea-input { width: 100%; min-height: 120px; background: #0a0a0f; border: 1px solid #333;
            border-radius: 8px; color: #fff; padding: 14px; font-size: 14px; font-family: inherit;
            resize: vertical; transition: border-color 0.2s; }
        .idea-input:focus { outline: none; border-color: #00d4ff; }
        .idea-input::placeholder { color: #555; }

        .btn {
            padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer;
            font-size: 13px; font-weight: 600; transition: opacity 0.2s;
        }
        .btn:hover { opacity: 0.9; }
        .btn-primary { background: linear-gradient(135deg, #00d4ff, #0088cc); color: #fff; }
        .btn-green { background: #4ade80; color: #000; }
        .btn-red { background: #f87171; color: #fff; }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        /* Terminal */
        .terminal {
            background: #000; border: 1px solid #333; border-radius: 8px;
            padding: 12px; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 12px; line-height: 1.5; max-height: 500px; overflow-y: auto;
            white-space: pre-wrap; word-break: break-all;
        }
        .terminal .line { color: #aaa; }
        .terminal .line-green { color: #4ade80; }
        .terminal .line-red { color: #f87171; }
        .terminal .line-yellow { color: #fbbf24; }
        .terminal .line-cyan { color: #22d3ee; }
        .terminal .line-magenta { color: #e879f9; }
        .terminal .line-blue { color: #60a5fa; }
        .terminal .line-dim { color: #555; }

        /* Code Viewer */
        .code-viewer {
            background: #0d1117; border: 1px solid #333; border-radius: 8px;
            padding: 16px; font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 12px; line-height: 1.6; overflow-x: auto;
            max-height: 600px; overflow-y: auto; white-space: pre;
        }
        .code-viewer .kw { color: #ff7b72; }
        .code-viewer .fn { color: #d2a8ff; }
        .code-viewer .str { color: #a5d6ff; }
        .code-viewer .cm { color: #8b949e; }
        .code-viewer .num { color: #79c0ff; }

        /* Results Table */
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 10px; color: #888; border-bottom: 1px solid #222; }
        td { padding: 10px; border-bottom: #1a1a1a; }
        tr { border-bottom: 1px solid #1a1a1a; }
        tr:hover { background: #1a1a2e; }

        .badge {
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 600;
        }
        .badge-green { background: #4ade8022; color: #4ade80; }
        .badge-red { background: #f8717122; color: #f87171; }
        .badge-yellow { background: #fbbf2422; color: #fbbf24; }
        .badge-blue { background: #00d4ff22; color: #00d4ff; }
        .badge-gray { background: #66666622; color: #999; }

        .phase-bar { display: flex; gap: 4px; margin: 8px 0; }
        .phase-dot {
            width: 28px; height: 28px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 11px; font-weight: 600;
            background: #222; color: #555; border: 1px solid #333;
        }
        .phase-dot.done { background: #4ade8033; color: #4ade80; border-color: #4ade8066; }
        .phase-dot.active { background: #00d4ff33; color: #00d4ff; border-color: #00d4ff66; animation: pulse 1.5s infinite; }
        .phase-dot.fail { background: #f8717133; color: #f87171; border-color: #f8717166; }

        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        .empty-state { text-align: center; padding: 40px; color: #555; }
        .empty-state h2 { color: #333; margin-bottom: 8px; }

        .run-card {
            background: #111; border: 1px solid #222; border-radius: 8px;
            padding: 14px; margin-bottom: 10px; cursor: pointer;
            transition: border-color 0.2s;
        }
        .run-card:hover { border-color: #00d4ff44; }
        .run-card .run-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
        .run-card .run-idea { color: #ccc; font-size: 13px; line-height: 1.4; }
        .run-card .run-meta { color: #666; font-size: 11px; margin-top: 6px; }

        .detail-panel {
            background: #0a0a0f; border: 1px solid #222; border-radius: 8px;
            padding: 16px; margin-top: 16px;
        }

        .split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        @media (max-width: 900px) { .split { grid-template-columns: 1fr; } }

        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .status-dot.running { background: #00d4ff; animation: pulse 1.5s infinite; }
        .status-dot.completed { background: #4ade80; }
        .status-dot.error { background: #f87171; }
        .status-dot.queued { background: #fbbf24; }
    </style>
</head>
<body>
    <div class="header">
        <div style="display:flex;align-items:center;gap:16px;">
            <h1>🧪 RBI Agent</h1>
            <a href="/">← Back to Dashboard</a>
        </div>
        <div style="color:#888;font-size:13px;" id="header-status"></div>
    </div>

    <div class="tabs">
        <div class="tab active" onclick="showTab('ideas')">💡 New Idea</div>
        <div class="tab" onclick="showTab('runs')">🚀 Pipeline Runs</div>
        <div class="tab" onclick="showTab('results')">📊 Results</div>
    </div>

    <div class="container">
        <!-- New Idea Panel -->
        <div class="panel active" id="panel-ideas">
            <div class="card">
                <h3>Submit Trading Idea</h3>
                <p style="color:#888;font-size:13px;margin-bottom:12px;">
                    Paste a trading idea, YouTube URL, or channel URL. The RBI pipeline will analyze it,
                    generate backtest code, run simulations, and evaluate the strategy.
                </p>
                <textarea class="idea-input" id="idea-text"
                    placeholder="Paste your trading idea here...&#10;&#10;Examples:&#10;• A text description of a trading strategy&#10;• https://www.youtube.com/watch?v=xxxxx (single video)&#10;• https://www.youtube.com/@channelhandle (scrape channel)"></textarea>
                <div style="display:flex;gap:10px;margin-top:12px;align-items:center;">
                    <button class="btn btn-primary" id="submit-btn" onclick="submitIdea(true)">
                        🚀 Submit & Run
                    </button>
                    <button class="btn" style="background:#333;color:#aaa;" id="queue-btn" onclick="submitIdea(false)">
                        📝 Queue Only
                    </button>
                    <span style="color:#555;font-size:12px;margin-left:auto;" id="submit-status"></span>
                </div>
            </div>

            <div class="card">
                <h3>Queued Ideas</h3>
                <div id="queued-ideas"><div class="empty-state">No queued ideas</div></div>
            </div>
        </div>

        <!-- Pipeline Runs Panel -->
        <div class="panel" id="panel-runs">
            <div id="runs-list"></div>
            <div id="run-detail" style="display:none;"></div>
        </div>

        <!-- Results Panel -->
        <div class="panel" id="panel-results">
            <div id="results-list"></div>
        </div>
    </div>

    <script>
    // ── State ──────────────────────────────────────────
    let currentRunId = null;
    let ws = null;
    let logOffset = 0;

    // ── Tabs ───────────────────────────────────────────
    function showTab(name) {
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.getElementById('panel-' + name).classList.add('active');
        event.target.classList.add('active');

        if (name === 'ideas') loadQueuedIdeas();
        if (name === 'runs') loadRuns();
        if (name === 'results') loadResults();
    }

    async function fetchAPI(url, opts) {
        try {
            const resp = await fetch(url, opts);
            return await resp.json();
        } catch(e) { return {error: e.message}; }
    }

    // ── Ideas ──────────────────────────────────────────
    async function submitIdea(autoRun) {
        const text = document.getElementById('idea-text').value.trim();
        if (!text) return;

        const btn = autoRun ? document.getElementById('submit-btn') : document.getElementById('queue-btn');
        btn.disabled = true;
        document.getElementById('submit-status').textContent = 'Submitting...';

        const data = await fetchAPI('/api/rbi/ideas', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, auto_run: autoRun}),
        });

        btn.disabled = false;
        if (data.error) {
            document.getElementById('submit-status').textContent = 'Error: ' + data.error;
        } else {
            document.getElementById('idea-text').value = '';
            document.getElementById('submit-status').textContent = autoRun
                ? '✅ Run started: ' + data.run_id
                : '✅ Queued: ' + data.run_id;
            if (autoRun) {
                showTab('runs');
                // simulate click on runs tab
                document.querySelectorAll('.tab')[1].click();
                viewRun(data.run_id);
            }
            loadQueuedIdeas();
        }
    }

    async function loadQueuedIdeas() {
        const data = await fetchAPI('/api/rbi/ideas');
        const el = document.getElementById('queued-ideas');
        if (!data.ideas || data.ideas.length === 0) {
            el.innerHTML = '<div class="empty-state">No ideas yet — submit one above!</div>';
            return;
        }
        el.innerHTML = data.ideas.map(idea => `
            <div style="padding:10px;border-bottom:1px solid #1a1a1a;font-size:13px;color:#aaa;">
                <div style="color:#ccc;">${escapeHtml(idea.text.slice(0, 200))}${idea.text.length > 200 ? '...' : ''}</div>
                <div style="color:#555;font-size:11px;margin-top:4px;">${idea.length} chars</div>
            </div>
        `).join('');
    }

    // ── Runs ───────────────────────────────────────────
    async function loadRuns() {
        const data = await fetchAPI('/api/rbi/runs');
        const el = document.getElementById('runs-list');
        document.getElementById('run-detail').style.display = 'none';
        el.style.display = 'block';

        if (!data.runs || data.runs.length === 0) {
            el.innerHTML = '<div class="empty-state"><h2>No runs yet</h2><p>Submit a trading idea to start your first pipeline run</p></div>';
            return;
        }

        el.innerHTML = data.runs.map(run => {
            const statusClass = run.status;
            const resultBadge = run.result
                ? `<span class="badge ${run.result === 'GO_LIVE' ? 'badge-green' : 'badge-red'}">${run.result}</span>`
                : '';
            const ideaPreview = escapeHtml(run.idea.slice(0, 120)) + (run.idea.length > 120 ? '...' : '');
            const time = run.created_at ? new Date(run.created_at).toLocaleTimeString() : '';
            return `
                <div class="run-card" onclick="viewRun('${run.id}')">
                    <div class="run-header">
                        <div><span class="status-dot ${statusClass}"></span><strong>${run.id}</strong></div>
                        <div>${resultBadge} <span class="badge badge-gray">${run.status}</span></div>
                    </div>
                    <div class="run-idea">${ideaPreview}</div>
                    <div class="run-meta">${time} ${run.strategy_name ? '• Strategy: ' + run.strategy_name : ''}</div>
                </div>
            `;
        }).join('');
    }

    async function viewRun(runId) {
        currentRunId = runId;
        document.getElementById('runs-list').style.display = 'none';
        const detail = document.getElementById('run-detail');
        detail.style.display = 'block';

        const data = await fetchAPI('/api/rbi/run/' + runId);
        if (data.error) {
            detail.innerHTML = '<div class="card">Run not found</div>';
            return;
        }

        const run = data.run;
        const logs = data.logs || [];
        logOffset = logs.length;

        detail.innerHTML = `
            <div style="margin-bottom:12px;">
                <button class="btn btn-sm" style="background:#333;color:#aaa;" onclick="loadRuns()">← Back to Runs</button>
                ${run.status === 'running' ? '<span class="status-dot running" style="margin-left:12px;"></span><span style="color:#00d4ff;font-size:13px;">Streaming live...</span>' : ''}
            </div>
            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                    <h3 style="margin:0;">Run: ${run.id}</h3>
                    <div>
                        ${run.result ? `<span class="badge ${run.result === 'GO_LIVE' ? 'badge-green' : 'badge-red'}" style="font-size:13px;padding:4px 12px;">${run.result}</span>` : ''}
                        <span class="badge badge-gray" style="margin-left:6px;">${run.status}</span>
                    </div>
                </div>
                <div style="color:#aaa;font-size:13px;margin-bottom:8px;">${escapeHtml(run.idea)}</div>
                ${run.strategy_name ? `<div style="color:#00d4ff;font-size:13px;">Strategy: <strong>${run.strategy_name}</strong></div>` : ''}
                ${run.error ? `<div style="color:#f87171;font-size:13px;margin-top:8px;">Error: ${escapeHtml(run.error)}</div>` : ''}
            </div>

            <!-- Phase Progress -->
            <div class="card">
                <h3>Pipeline Progress</h3>
                <div class="phase-bar">
                    ${renderPhase('R', 'Research', run.phases?.research)}
                    ${renderPhase('B', 'Backtest', run.phases?.backtest)}
                    ${renderPhase('D', 'Debug', run.phases?.debug)}
                    ${renderPhase('E', 'Execute', run.phases?.execute)}
                    ${renderPhase('W', 'WalkFwd', run.phases?.walk_forward)}
                    ${renderPhase('V', 'Eval', run.phases?.evaluate)}
                    ${renderPhase('A', 'Alpha', run.phases?.alpha_decay)}
                    ${renderPhase('!', 'Deploy', run.phases?.deploy)}
                </div>
            </div>

            <div class="split">
                <!-- Terminal Logs -->
                <div class="card" style="grid-column:1/-1;">
                    <h3>Terminal Output</h3>
                    <div class="terminal" id="terminal-output">${renderLogs(logs)}</div>
                </div>
            </div>

            ${run.strategy_name ? `
            <div class="card" style="margin-top:16px;">
                <h3>Generated Backtest Code</h3>
                <div style="margin-bottom:8px;">
                    <button class="btn btn-sm btn-primary" onclick="loadCode('${run.strategy_name}')">Load Code</button>
                </div>
                <div class="code-viewer" id="code-output" style="display:none;"></div>
            </div>
            ` : ''}
        `;

        // Scroll terminal to bottom
        const term = document.getElementById('terminal-output');
        if (term) term.scrollTop = term.scrollHeight;

        // Connect WebSocket for live streaming
        if (run.status === 'running') {
            connectWebSocket(runId);
        }
    }

    function renderPhase(abbr, label, done) {
        const cls = done ? 'done' : '';
        return `<div class="phase-dot ${cls}" title="${label}">${abbr}</div>`;
    }

    function renderLogs(logs) {
        return logs.map(line => {
            const cls = getLogClass(line);
            return `<div class="${cls}">${escapeHtml(line)}</div>`;
        }).join('');
    }

    function getLogClass(line) {
        if (line.includes('ERROR') || line.includes('FATAL') || line.includes('BLOCKED') || line.includes('❌')) return 'line-red';
        if (line.includes('SUCCESS') || line.includes('✅') || line.includes('PASSED') || line.includes('CONNECTED')) return 'line-green';
        if (line.includes('WARNING') || line.includes('⚠')) return 'line-yellow';
        if (line.includes('[PHASE') || line.includes('RBI PIPELINE')) return 'line-magenta';
        if (line.includes('[ENGINE]') || line.includes('[SETUP]')) return 'line-cyan';
        if (line.includes('Phase') || line.includes('STRATEGY')) return 'line-blue';
        if (line.startsWith('=') || line.startsWith('-') || line.startsWith('─')) return 'line-dim';
        return 'line';
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ── WebSocket ──────────────────────────────────────
    function connectWebSocket(runId) {
        if (ws) { ws.close(); ws = null; }
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/ws/rbi/${runId}`);

        ws.onmessage = (event) => {
            const data = event.data;
            if (data === '__DONE__') {
                // Refresh run data
                setTimeout(() => viewRun(runId), 500);
                return;
            }
            if (data === 'pong') return;

            try {
                const parsed = JSON.parse(data);
                if (parsed.type === 'status') {
                    // Run status update — refresh
                    setTimeout(() => viewRun(runId), 200);
                    return;
                }
            } catch(e) {}

            // Regular log line
            const term = document.getElementById('terminal-output');
            if (term) {
                const div = document.createElement('div');
                div.className = getLogClass(data);
                div.textContent = data;
                term.appendChild(div);
                term.scrollTop = term.scrollHeight;
            }
        };

        ws.onclose = () => { ws = null; };
        ws.onerror = () => { ws = null; };

        // Ping every 30s to keep alive
        setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 30000);
    }

    // ── Code Viewer ────────────────────────────────────
    async function loadCode(strategyName) {
        const el = document.getElementById('code-output');
        el.style.display = 'block';
        el.textContent = 'Loading...';

        const data = await fetchAPI('/api/rbi/code/' + strategyName);
        if (data.error) {
            el.textContent = 'Code not found: ' + data.error;
            return;
        }
        el.innerHTML = highlightPython(data.code);
    }

    function highlightPython(code) {
        let html = escapeHtml(code);
        // Keywords
        html = html.replace(/\\b(import|from|class|def|return|if|else|elif|for|while|try|except|with|as|True|False|None|and|or|not|in|is|lambda|yield|raise|pass|break|continue|global|nonlocal|assert|del|finally)\\b/g,
            '<span class="kw">$1</span>');
        // Strings
        html = html.replace(/(f?"[^"]*"|f?'[^']*')/g, '<span class="str">$1</span>');
        // Comments
        html = html.replace(/(#.*$)/gm, '<span class="cm">$1</span>');
        // Numbers
        html = html.replace(/\\b(\\d+\\.?\\d*)\\b/g, '<span class="num">$1</span>');
        // Function defs
        html = html.replace(/(\\bdef\\s+)(\\w+)/g, '$1<span class="fn">$2</span>');
        return html;
    }

    // ── Results ────────────────────────────────────────
    async function loadResults() {
        const data = await fetchAPI('/api/rbi/results');
        const el = document.getElementById('results-list');

        if (!data.results || data.results.length === 0) {
            el.innerHTML = '<div class="empty-state"><h2>No results yet</h2><p>Run the pipeline to see strategy results here</p></div>';
            return;
        }

        el.innerHTML = `
            <div class="card">
                <h3>Strategy Results (${data.results.length})</h3>
                <table>
                    <tr>
                        <th>Strategy</th>
                        <th>Result</th>
                        <th>Backtest Return</th>
                        <th>Walk-Forward</th>
                        <th>Decay</th>
                        <th>Time</th>
                        <th></th>
                    </tr>
                    ${data.results.map(r => {
                        const name = r.strategy_name || 'Unknown';
                        const result = r.result || 'PENDING';
                        const badge = result === 'GO_LIVE' ? 'badge-green' : result === 'REJECT' ? 'badge-red' : 'badge-yellow';
                        const stats = r.backtest_stats || {};
                        const ret = stats['Return [%]'];
                        const retStr = ret != null ? (ret >= 0 ? '+' : '') + ret.toFixed(1) + '%' : '—';
                        const wf = r.walk_forward || {};
                        const wfStr = wf.out_of_sample != null ? (wf.out_of_sample >= 0 ? '+' : '') + (wf.out_of_sample * 100).toFixed(1) + '%' : '—';
                        const decay = r.decay_status || '—';
                        const elapsed = r.elapsed_seconds ? r.elapsed_seconds.toFixed(0) + 's' : '—';
                        return `<tr>
                            <td><strong>${escapeHtml(name)}</strong></td>
                            <td><span class="badge ${badge}">${result}</span></td>
                            <td style="color:${(ret||0)>=0?'#4ade80':'#f87171'}">${retStr}</td>
                            <td>${wfStr}</td>
                            <td>${escapeHtml(decay)}</td>
                            <td>${elapsed}</td>
                            <td><button class="btn btn-sm" style="background:#222;color:#aaa;" onclick="viewResult('${escapeHtml(name)}')">View</button></td>
                        </tr>`;
                    }).join('')}
                </table>
            </div>
            <div id="result-detail"></div>
        `;
    }

    async function viewResult(name) {
        const data = await fetchAPI('/api/rbi/backtest/' + name);
        const el = document.getElementById('result-detail');
        if (data.error) { el.innerHTML = ''; return; }

        const stats = data.stats || {};
        const code = data.code || '';
        const wf = stats.walk_forward || {};

        el.innerHTML = `
            <div class="card" style="margin-top:16px;">
                <h3>${escapeHtml(name)}</h3>
                <div class="split" style="margin-top:12px;">
                    <div>
                        <h3 style="color:#888;font-size:12px;">Backtest Stats</h3>
                        <table>
                            ${Object.entries(stats.backtest_stats || {}).map(([k,v]) =>
                                `<tr><td style="color:#aaa;">${escapeHtml(k)}</td><td>${typeof v === 'number' ? v.toFixed(2) : v}</td></tr>`
                            ).join('')}
                        </table>
                    </div>
                    <div>
                        <h3 style="color:#888;font-size:12px;">Walk-Forward Validation</h3>
                        <table>
                            ${wf.in_sample != null ? `<tr><td>In-Sample Return</td><td>${(wf.in_sample*100).toFixed(2)}%</td></tr>` : ''}
                            ${wf.out_of_sample != null ? `<tr><td>Out-of-Sample Return</td><td>${(wf.out_of_sample*100).toFixed(2)}%</td></tr>` : ''}
                            ${wf.overfit_score != null ? `<tr><td>Overfit Score</td><td>${wf.overfit_score.toFixed(3)}</td></tr>` : ''}
                            ${stats.decay_status ? `<tr><td>Alpha Decay</td><td>${stats.decay_status}</td></tr>` : ''}
                            <tr><td>Result</td><td><span class="badge ${stats.result === 'GO_LIVE' ? 'badge-green' : 'badge-red'}">${stats.result || 'N/A'}</span></td></tr>
                            ${stats.reasoning ? `<tr><td>Reasoning</td><td style="color:#aaa;">${escapeHtml(stats.reasoning)}</td></tr>` : ''}
                        </table>
                    </div>
                </div>
                ${code ? `
                <div style="margin-top:16px;">
                    <h3 style="color:#888;font-size:12px;margin-bottom:8px;">Generated Backtest Code</h3>
                    <div class="code-viewer">${highlightPython(code)}</div>
                </div>
                ` : ''}
            </div>
        `;
        el.scrollIntoView({behavior: 'smooth'});
    }

    // ── Init ───────────────────────────────────────────
    loadQueuedIdeas();

    // Auto-refresh runs if on that tab
    setInterval(() => {
        const active = document.querySelector('.panel.active');
        if (active && active.id === 'panel-runs' && !document.getElementById('run-detail').style.display !== 'none') {
            // Don't auto-refresh if viewing a specific run
        }
    }, 10000);
    </script>
</body>
</html>"""


# ── Standalone App ─────────────────────────────────────────

app = FastAPI(title="RBI Agent Web", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def rbi_dashboard():
    return RBI_HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
