"""
RBI Agent Web Interface — Full interactive control of the RBI pipeline.

DSH Pattern: EventBus → DB → Singleton

Features:
  - Add trading ideas via web form (no more editing ideas.txt)
  - Trigger pipeline runs from the browser
  - Live terminal log streaming via WebSocket
  - View generated backtest code per strategy
  - Browse strategy results with scores, walk-forward, decisions

Architecture:
  RunManager (DSH singleton) → EventBus → DB persistence → WebSocket broadcast
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
from src.event_bus import _fire_and_forget

# ── Paths ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data" / "rbi"
IDEAS_FILE = DATA_DIR / "ideas.txt"
STRATEGY_MEMORY_DIR = DATA_DIR / "strategy_memory"
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Memory bounds — everything stays persisted on disk/DB, RAM only keeps recent
MAX_MEMORY_RUNS = 200          # max runs kept in RAM (older ones pruned from memory)
MAX_FINISHED_LOG_LINES = 1000  # trim in-RAM logs of finished runs (full log on disk)
MAX_WS_LIFETIME_SECONDS = 3600 # WebSocket stream hard cap (defensive)
MAX_CONCURRENT_RUNS = 2        # pipeline concurrency limit (#6) — extra runs queue


# ── Run Manager ────────────────────────────────────────────

class OutputCapture:
    """Per-run log sink. Writes are routed here by StdoutRouter for the
    pipeline's thread only — no global stdout swap, so concurrent runs
    (or unrelated threads) can never interleave into each other's logs."""

    def __init__(self, run_id: str, run_manager: 'RunManager'):
        self.run_id = run_id
        self.run_manager = run_manager

    def write(self, text: str):
        if text and text.strip():
            self.run_manager.append_log(self.run_id, text)

    def flush(self):
        pass


class StdoutRouter(io.TextIOBase):
    """Process-wide stdout router (installed once, at import).

    Routes writes from a registered pipeline thread to that run's
    OutputCapture (mirroring to the real console); all other threads
    write straight through to the real stdout. Fixes the bug where
    swapping sys.stdout globally made concurrent runs interleave logs.
    """

    def __init__(self):
        self._real = sys.stdout
        self._local = threading.local()

    def register(self, capture: OutputCapture):
        self._local.capture = capture

    def unregister(self):
        self._local.capture = None

    def write(self, text: str):
        capture = getattr(self._local, "capture", None)
        try:
            if capture is not None:
                capture.write(text)
            self._real.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass


STDOUT_ROUTER = StdoutRouter()
if not isinstance(sys.stdout, StdoutRouter):
    sys.stdout = STDOUT_ROUTER


class RunManager:
    """Manages RBI pipeline runs — tracks state, captures output, streams via WebSocket.

    DSH compliance: accepts event_bus, emits events for run lifecycle,
    persists via DB/JSONL, singleton via get_run_manager() factory.
    """

    _instance = None

    def __new__(cls, event_bus=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, event_bus=None):
        if self._initialized:
            return
        self._initialized = True
        self.event_bus = event_bus
        self.runs: Dict[str, dict] = {}
        self.logs: Dict[str, List[str]] = {}
        self.websocket_clients: Dict[str, Set[WebSocket]] = {}
        self._lock = threading.Lock()
        self._pipeline_slots = threading.Semaphore(MAX_CONCURRENT_RUNS)
        self._approval_gates: Dict[str, tuple] = {}  # run_id -> (Event, result-dict)
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

    def _emit_event(self, event_name: str, payload: dict):
        """Emit event via EventBus (DSH pattern)."""
        # Persist to DB
        try:
            from src.db_storage import log_event
            log_event(event_name, payload)
        except Exception:
            pass

        # Emit to EventBus
        if self.event_bus:
            try:
                _fire_and_forget(self.event_bus.emit(event_name, payload))
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
        self._prune_memory()

        # DSH: emit event
        self._emit_event("rbi/run_created", {
            "run_id": run_id,
            "idea": idea_text[:200],
            "auto_mode": auto_mode,
            "timestamp": run["created_at"],
        })
        self._emit_event("rbi/idea_added", {
            "run_id": run_id,
            "idea": idea_text[:500],
            "timestamp": run["created_at"],
        })

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

        # DSH: emit event
        self._emit_event("rbi/run_started", {
            "run_id": run_id,
            "idea": self.runs[run_id]["idea"][:200],
            "timestamp": self.runs[run_id]["started_at"],
        })

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

        # Route this thread's stdout to the run's log capture (thread-safe)
        capture = OutputCapture(run_id, self)
        STDOUT_ROUTER.register(capture)

        # Concurrency limit (#6): pipeline threads queue on this semaphore
        # so N rapid submissions can't hammer the model API / CPU at once
        self._pipeline_slots.acquire()
        try:
            self.append_log(run_id, f"[WEB] Pipeline slot acquired "
                                    f"({MAX_CONCURRENT_RUNS} max concurrent)\n")

            # Real human approval (#3): web runs block here for dashboard
            # approval instead of silently auto-deploying (auto_mode=True bug)
            approval_event = threading.Event()
            approval_result = {"approved": False}
            # Register the gate so the approve/reject endpoints can signal it
            with self._lock:
                self._approval_gates[run_id] = (approval_event, approval_result)

            def approval_callback(name, stats, reasoning):
                with self._lock:
                    self.runs[run_id]["status"] = "awaiting_approval"
                    self.runs[run_id]["approval"] = {
                        "strategy_name": name,
                        "reasoning": (reasoning or "")[:300],
                        "stats": stats or {},
                    }
                self._persist_run(self.runs[run_id])
                self._broadcast_status(run_id)
                self.append_log(run_id, f"[WEB] ⏸ Awaiting approval for "
                                        f"'{name}' via dashboard (1h timeout)...\n")
                approved = approval_event.wait(timeout=3600)
                with self._lock:
                    self.runs[run_id]["status"] = "running"
                    self.runs[run_id].pop("approval", None)
                self._persist_run(self.runs[run_id])
                if not approved:
                    self.append_log(run_id, "[WEB] Approval timed out — rejected\n")
                return approved and approval_result["approved"]

            def cancel_check():
                return bool(self.runs.get(run_id, {}).get("cancel_requested"))

            try:
                # Import and run the pipeline
                sys.path.insert(0, str(PROJECT_ROOT / "moon-dev-ai-agents-bot-main"))
                sys.path.insert(0, str(PROJECT_ROOT))
                from src.agents.rbi_agent import process_trading_idea

                process_trading_idea(idea_text, auto_mode=False,
                                     approval_callback=approval_callback,
                                     cancel_check=cancel_check)

                # Mark completed (or cancelled if cancel was requested)
                with self._lock:
                    if self.runs[run_id].get("cancel_requested"):
                        self.runs[run_id]["status"] = "cancelled"
                    else:
                        self.runs[run_id]["status"] = "completed"
                    self.runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

                # Try to extract strategy name from logs
                self._extract_result(run_id)

                # DSH: emit event
                self._emit_event("rbi/run_completed", {
                    "run_id": run_id,
                    "idea": idea_text[:200],
                    "strategy_name": self.runs[run_id].get("strategy_name"),
                    "result": self.runs[run_id].get("result"),
                    "phases": self.runs[run_id].get("phases", {}),
                    "timestamp": self.runs[run_id]["finished_at"],
                })

            except Exception as e:
                with self._lock:
                    self.runs[run_id]["status"] = "error"
                    self.runs[run_id]["error"] = str(e)
                    self.runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
                self.append_log(run_id, f"\n[FATAL ERROR] {e}\n")

                # DSH: emit error event
                self._emit_event("rbi/run_error", {
                    "run_id": run_id,
                    "idea": idea_text[:200],
                    "error": str(e),
                    "timestamp": self.runs[run_id]["finished_at"],
                })
        finally:
            self._pipeline_slots.release()
            STDOUT_ROUTER.unregister()
            with self._lock:
                self._approval_gates.pop(run_id, None)
                # Unblock a pending approval wait if the run ended otherwise
                gate = self._approval_gates.get(run_id)
            self._persist_run(self.runs[run_id])
            self._broadcast_status(run_id)
            self._broadcast_log(run_id, "__DONE__")
            # Bound in-RAM logs for finished runs (full log stays on disk)
            with self._lock:
                logs = self.logs.get(run_id)
                if logs and len(logs) > MAX_FINISHED_LOG_LINES:
                    self.logs[run_id] = logs[-MAX_FINISHED_LOG_LINES:]

    # ── Approval / Cancel control (#3, #9) ────────────────────

    def resolve_approval(self, run_id: str, approved: bool) -> tuple:
        """Approve or reject a run awaiting the human gate.
        Returns (ok, message)."""
        with self._lock:
            gate = self._approval_gates.get(run_id)
            run = self.runs.get(run_id)
            if not gate or not run or run.get("status") != "awaiting_approval":
                return False, "Run is not awaiting approval"
            gate[1]["approved"] = approved
            gate[0].set()
            self.append_log(run_id, f"[WEB] {'✅ APPROVED' if approved else '❌ REJECTED'} "
                                    f"by dashboard operator\n")
        self._persist_run(self.runs[run_id])
        return True, f"Run {'approved' if approved else 'rejected'}"

    def cancel_run(self, run_id: str) -> tuple:
        """Request cancellation of a running/queued run (#9).
        Returns (ok, message)."""
        with self._lock:
            run = self.runs.get(run_id)
            if not run:
                return False, "Run not found"
            if run.get("status") in ("completed", "error", "cancelled"):
                return False, f"Run already {run['status']}"
            run["cancel_requested"] = True
            # If waiting on the approval gate, wake it up (rejected) so it exits
            gate = self._approval_gates.get(run_id)
            if gate and run.get("status") == "awaiting_approval":
                gate[1]["approved"] = False
                gate[0].set()
        self.append_log(run_id, "[WEB] 🛑 Cancel requested by operator\n")
        self._persist_run(self.runs[run_id])
        return True, "Cancel requested"

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

        # DB persistence (best-effort — upserts into rbi_runs)
        try:
            from src.db_storage import save_rbi_run
            save_rbi_run(run)
        except Exception:
            pass

        # Also save logs
        log_file = RUNS_DIR / f"{run['id']}.log"
        try:
            log_text = "\n".join(self.logs.get(run["id"], []))
            log_file.write_text(log_text, encoding="utf-8")
        except Exception:
            pass

    def _prune_memory(self):
        """Keep RAM bounded: drop oldest finished runs beyond MAX_MEMORY_RUNS
        (they remain persisted on disk/DB) — running/queued runs are never dropped."""
        with self._lock:
            if len(self.runs) <= MAX_MEMORY_RUNS:
                return
            finished = sorted(
                [r for r in self.runs.values()
                 if r.get("status") in ("completed", "error")],
                key=lambda r: r.get("created_at", ""))
            excess = len(self.runs) - MAX_MEMORY_RUNS
            for r in finished[:excess]:
                self.runs.pop(r["id"], None)
                self.logs.pop(r["id"], None)

    def _append_idea(self, text: str):
        """Append idea to ideas.txt for persistence."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(IDEAS_FILE, "a", encoding="utf-8") as f:
                f.write(text.strip() + "\n\n")
        except Exception:
            pass


# ── Singleton Factory (DSH pattern) ──────────────────────

_run_manager_instance = None

def get_run_manager(event_bus=None) -> RunManager:
    """Get or create the singleton RunManager instance (DSH pattern)."""
    global _run_manager_instance
    if _run_manager_instance is None:
        _run_manager_instance = RunManager(event_bus=event_bus)
    return _run_manager_instance


# ── FastAPI Router ─────────────────────────────────────────

router = APIRouter(tags=["rbi"])
run_manager = RunManager()  # Initial instance; get_run_manager() preferred


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
    """List runs — DB (rbi_runs) merged with fresher in-memory state."""
    # DB is the durable source; in-memory overrides with latest statuses
    runs: Dict[str, dict] = {}
    try:
        from src.db_storage import get_rbi_runs
        for r in get_rbi_runs(limit):
            runs[r.get("id")] = r
    except Exception:
        pass
    for r in run_manager.get_all_runs(limit):
        runs[r["id"]] = r
    merged = sorted(runs.values(),
                    key=lambda r: r.get("created_at") or "", reverse=True)
    return {"runs": merged[:limit]}


@router.get("/api/rbi/events")
async def list_rbi_events(limit: int = 100, event_type: str = None, session_id: str = None):
    """RBI pipeline session events — DB (rbi_session_events) first, CSV fallback."""
    events = []
    try:
        from src.db_storage import get_rbi_events
        events = get_rbi_events(limit=limit, event_type=event_type, session_id=session_id)
    except Exception:
        pass

    if not events:
        # CSV fallback: tail of rbi_session_log.csv
        csv_path = DATA_DIR / "rbi_session_log.csv"
        if csv_path.exists():
            try:
                import csv as _csv
                from collections import deque
                with open(csv_path, "r", encoding="utf-8", newline="") as f:
                    tail = deque(_csv.DictReader(f), maxlen=max(limit, 500))
                events = list(tail)[::-1]
                if event_type:
                    events = [e for e in events if e.get("event_type") == event_type]
                if session_id:
                    events = [e for e in events if e.get("session_id") == session_id]
                events = events[:limit]
            except Exception:
                pass

    return {"events": events, "count": len(events), "db_used": bool(events)}


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


@router.post("/api/rbi/run/{run_id}/approve")
async def approve_run(run_id: str):
    """Approve a run awaiting the human deployment gate (#3)."""
    ok, msg = run_manager.resolve_approval(run_id, approved=True)
    if not ok:
        raise HTTPException(400, msg)
    return {"status": msg}


@router.post("/api/rbi/run/{run_id}/reject")
async def reject_run(run_id: str):
    """Reject a run awaiting the human deployment gate (#3)."""
    ok, msg = run_manager.resolve_approval(run_id, approved=False)
    if not ok:
        raise HTTPException(400, msg)
    return {"status": msg}


@router.post("/api/rbi/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a queued/running run (#9)."""
    ok, msg = run_manager.cancel_run(run_id)
    if not ok:
        raise HTTPException(400, msg)
    return {"status": msg}


@router.get("/api/rbi/results")
async def list_results():
    """List strategy results — DB (rbi_strategies) first, JSONL fallback."""
    results = []
    try:
        from src.db_storage import get_rbi_strategies
        results = get_rbi_strategies(100)
    except Exception:
        pass

    if not results:
        history_path = STRATEGY_MEMORY_DIR / "strategy_history.jsonl"
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
    return {"results": results[:100], "count": len(results), "db_used": bool(results)}


@router.get("/api/rbi/result/{strategy_name}")
async def get_result(strategy_name: str):
    """Get detailed result for a specific strategy — DB first, JSONL fallback."""
    try:
        from src.db_storage import get_rbi_strategy
        record = get_rbi_strategy(strategy_name)
        if record:
            return {"result": record, "db_used": True}
    except Exception:
        pass

    history_path = STRATEGY_MEMORY_DIR / "strategy_history.jsonl"
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("strategy_name") == strategy_name:
                    return {"result": entry, "db_used": False}
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
    Thread-safe: only reads from shared state.
    """
    await websocket.accept()
    offset = 0
    connected = True
    ws_started = time.time()

    try:
        while connected:
            # Hard lifetime cap — defensive against zombie streams
            if time.time() - ws_started > MAX_WS_LIFETIME_SECONDS:
                break

            run = run_manager.get_run(run_id)
            if not run:
                await websocket.send_text("__ERROR__: Run not found")
                break

            # Send new log lines
            new_lines = run_manager.get_new_logs(run_id, offset)
            for line in new_lines:
                try:
                    await websocket.send_text(line)
                    offset += 1
                except Exception:
                    connected = False
                    break

            if not connected:
                break

            # Send status update if run finished
            if run["status"] in ("completed", "error"):
                try:
                    status_msg = json.dumps({"type": "status", "run": run}, default=str)
                    await websocket.send_text(status_msg)
                    # Flush any final logs
                    await asyncio.sleep(0.3)
                    final_lines = run_manager.get_new_logs(run_id, offset)
                    for line in final_lines:
                        await websocket.send_text(line)
                        offset += 1
                    await websocket.send_text("__DONE__")
                except Exception:
                    pass
                break

            # Poll interval
            await asyncio.sleep(0.3)

    except WebSocketDisconnect:
        pass  # Client disconnected — normal
    except Exception as e:
        # Log unexpected errors but don't crash
        try:
            await websocket.send_text(f"__ERROR__: {e}")
        except Exception:
            pass


# ── HTML Frontend ──────────────────────────────────────────

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "rbi"


def _load_rbi_html() -> str:
    """Load RBI HTML from template file."""
    html_file = _TEMPLATE_DIR / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>RBI Agent template not found</h1>"


RBI_HTML_CONTENT = None  # Lazy-loaded


def get_rbi_html() -> str:
    global RBI_HTML_CONTENT
    if RBI_HTML_CONTENT is None:
        RBI_HTML_CONTENT = _load_rbi_html()
    return RBI_HTML_CONTENT





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
    return get_rbi_html()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
