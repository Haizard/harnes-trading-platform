"""
🌙 Moon Dev Web Dashboard — Full System Monitoring Panel

Panels:
  - Portfolio Overview: Capital, P&L, open positions
  - Trade History: Recent entries/exits with performance
  - Scanner Results: Token candidates and scores
  - Wallet Tracker: Whale wallet activity and swaps
  - RBI Panel: Risk-Based Intelligence signals
  - MCP Panel: Model Context Protocol tools and calls
  - Storage Monitor: DB usage, backups, compression
  - System Health: All engines and their status

Start:
    python -m src.web_dashboard
    # or
    uvicorn src.web_dashboard:app --host 0.0.0.0 --port 8080

DSH Pattern: EventBus → DB → Singleton
"""

import os
import sys
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# FastAPI
from fastapi import FastAPI, HTTPException, Request, Response, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from termcolor import cprint

app = FastAPI(title="Moon Dev Trading Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    expose_headers=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path("src/data")

# Mount RBI Agent Web Interface
try:
    from src.rbi_web import router as rbi_router
    app.include_router(rbi_router)
    print("[DASHBOARD] RBI Agent web routes loaded", flush=True)
except Exception as e:
    print(f"[DASHBOARD] RBI web routes failed: {e}", flush=True)

# Mount MCP Web Panel
try:
    from src.mcp_web import router as mcp_router
    app.include_router(mcp_router)
    print("[DASHBOARD] MCP web panel loaded", flush=True)
except Exception as e:
    print(f"[DASHBOARD] MCP web panel failed: {e}", flush=True)

# Auth helpers

def get_current_user(request: Request) -> Optional[dict]:
    """Extract user from cookie token."""
    token = request.cookies.get("auth_token")
    if not token:
        return None
    try:
        from src.auth import get_auth_manager
        auth = get_auth_manager()
        return auth.verify_session(token)
    except Exception:
        return None


# ── JSON Fallback Helpers ──────────────────────────────────

def _load_trades_from_jsonl():
    """Load trades from paper_trades.jsonl as fallback."""
    trades = []
    jsonl_path = Path("src/data/paper_trading/paper_trades.jsonl")
    if jsonl_path.exists():
        try:
            entries = []
            for line in jsonl_path.read_text().splitlines():
                if line.strip():
                    entries.append(json.loads(line))
            # Group by token_address: last entry wins
            by_token = {}
            for e in entries:
                addr = e.get("token_address", "")
                by_token[addr] = e  # last action per token
            # Reconstruct trade lifecycle
            all_entries = {}
            for e in entries:
                addr = e.get("token_address", "")
                action = e.get("action", "entry")
                key = addr
                if action == "entry":
                    all_entries[key] = {"entry": e, "exit": None}
                elif action == "exit":
                    if key in all_entries:
                        all_entries[key]["exit"] = e
                    else:
                        all_entries[key] = {"entry": None, "exit": e}
            # Build trade list
            for addr, pair in all_entries.items():
                entry = pair.get("entry") or {}
                exit_ = pair.get("exit") or {}
                trade = {
                    "token_address": addr,
                    "symbol": entry.get("symbol") or exit_.get("symbol", "?"),
                    "amount_usd": entry.get("amount_usd", 0),
                    "entry_price": entry.get("entry_price", 0),
                    "exit_price": exit_.get("exit_price", 0),
                    "pnl_usd": exit_.get("pnl_usd", 0),
                    "pnl_pct": exit_.get("pnl_pct", 0),
                    "status": exit_.get("status") or entry.get("status", "open"),
                    "score": entry.get("score", 0),
                    "entry_time": entry.get("entry_time", entry.get("timestamp", "")),
                    "exit_time": exit_.get("exit_time", exit_.get("timestamp", "")),
                    "signals": entry.get("signals", []),
                    "exit_reason": exit_.get("status", ""),
                }
                trades.append(trade)
        except Exception as e:
            print(f"[DASHBOARD] JSONL load error: {e}")
    return trades


def _load_scanner_from_events():
    """Load scanner data from engine_events.jsonl as fallback."""
    results = []
    jsonl_path = Path("src/data/micro_engine/engine_events.jsonl")
    if jsonl_path.exists():
        try:
            for line in jsonl_path.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                etype = entry.get("type") or entry.get("event_type", "")
                if etype == "token/candidate":
                    data = entry.get("data", {})
                    results.append({
                        "token_address": data.get("address", ""),
                        "symbol": data.get("symbol", ""),
                        "score": data.get("score", 0),
                        "liquidity_usd": data.get("liquidity_usd", 0),
                        "volume_24h": data.get("volume_24h", 0),
                        "price_usd": data.get("price_usd", 0),
                        "category": data.get("category", "unknown"),
                        "source": data.get("source", ""),
                        "market_cap": data.get("market_cap", 0),
                        "price_change_1h": data.get("price_change_1h", 0),
                        "txns_1h_buys": data.get("txns_1h_buys", 0),
                        "txns_1h_sells": data.get("txns_1h_sells", 0),
                        "dex": data.get("dex", ""),
                        "signals": data.get("signals", []),
                        "created_at": entry.get("timestamp", ""),
                    })
            # Sort by score desc, take last 100
            results.sort(key=lambda x: x.get("score", 0), reverse=True)
            results = results[:100]
        except Exception as e:
            print(f"[DASHBOARD] Scanner events load error: {e}")
    return results


def _load_events_from_jsonl(event_type_filter=None, limit=50):
    """Load engine events from JSONL as fallback."""
    events = []
    jsonl_path = Path("src/data/micro_engine/engine_events.jsonl")
    if jsonl_path.exists():
        try:
            for line in jsonl_path.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                # JSONL uses 'type', DB uses 'event_type' — normalize
                etype = entry.get("type") or entry.get("event_type", "")
                entry["event_type"] = etype
                if event_type_filter:
                    if isinstance(event_type_filter, str):
                        if not etype.startswith(event_type_filter):
                            continue
                    elif callable(event_type_filter):
                        if not event_type_filter(etype):
                            continue
                events.append(entry)
            events = events[-limit:]
        except Exception as e:
            print(f"[DASHBOARD] Events JSONL load error: {e}")
    return events


def _load_wallets_from_jsonl():
    """Load wallet events from JSONL as fallback."""
    events = []
    wallet_dir = Path("src/data/wallet_tracker")
    if wallet_dir.exists():
        for f in wallet_dir.glob("*.jsonl"):
            try:
                for line in f.read_text().splitlines():
                    if line.strip():
                        events.append(json.loads(line))
            except Exception:
                pass
    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:50]


# ── Capital Reset Stats Helper ─────────────────────────────

def _load_capital_resets() -> dict:
    """Load capital auto-reset stats persisted by PortfolioRiskManager."""
    path = Path("src/data/risk/capital_resets.json")
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            history = state.get("history", [])
            return {
                "reset_count": state.get("reset_count", 0),
                "last_reset": state.get("last_reset"),
                "auto_reset_enabled": state.get("auto_reset_enabled", True),
                "cooldown_hours": state.get("cooldown_hours", 24),
                "recent": history[-5:],
            }
        except Exception:
            pass
    return {
        "reset_count": 0,
        "last_reset": None,
        "auto_reset_enabled": True,
        "cooldown_hours": 24,
        "recent": [],
    }


# ── API Endpoints ──────────────────────────────────────────

@app.get("/api/portfolio")
async def get_portfolio():
    """Get portfolio overview — DB first, JSONL fallback."""
    try:
        # Try DB first
        portfolio = None
        trades = []
        db_used = False
        try:
            from src.db_storage import get_portfolio as db_get_portfolio, get_trades as db_get_trades
            portfolio = db_get_portfolio()
            trades = db_get_trades(limit=200)
            db_used = bool(trades)
        except Exception:
            pass

        # Fallback to JSONL
        if not trades:
            trades = _load_trades_from_jsonl()

        open_trades = [t for t in trades if t.get("status") == "open"]
        closed_trades = [t for t in trades if t.get("status") not in ("open", None)]

        total_pnl = sum(t.get("pnl_usd", 0) for t in closed_trades)
        wins = sum(1 for t in closed_trades if (t.get("pnl_usd") or 0) > 0)
        losses = sum(1 for t in closed_trades if (t.get("pnl_usd") or 0) < 0)

        # If no portfolio from DB, build from capital state
        if not portfolio:
            capital = 100.0
            total_invested = sum(t.get("amount_usd", 0) for t in open_trades)
            portfolio = {
                "initial_capital": 100.0,
                "current_capital": round(capital - total_invested + total_pnl, 2),
                "total_pnl": round(total_pnl, 2),
            }

        return {
            "portfolio": portfolio or {},
            "open_positions": open_trades,
            "closed_trades": closed_trades[-30:],
            "stats": {
                "total_trades": len(closed_trades),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / max(len(closed_trades), 1) * 100, 1),
                "total_pnl": round(total_pnl, 2),
                "open_count": len(open_trades),
            },
            "db_used": db_used,
            "capital_resets": _load_capital_resets(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scanner")
async def get_scanner():
    """Get scanner results — DB first, JSONL fallback."""
    try:
        results = []
        db_used = False

        # Try DB first
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                with pool.connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM scanner_results
                        ORDER BY created_at DESC LIMIT 100
                    """).fetchall()
                    for r in rows:
                        d = dict(r)
                        # Extract category from data JSONB
                        data_field = d.get("data", {})
                        if isinstance(data_field, str):
                            try:
                                data_field = json.loads(data_field)
                            except Exception:
                                data_field = {}
                        d["category"] = data_field.get("category", "unknown")
                        d["source"] = data_field.get("source", "")
                        d["market_cap"] = data_field.get("market_cap", 0)
                        d["price_change_1h"] = data_field.get("price_change_1h", 0)
                        d["txns_1h_buys"] = data_field.get("txns_1h_buys", 0)
                        d["txns_1h_sells"] = data_field.get("txns_1h_sells", 0)
                        d["dex"] = data_field.get("dex", "")
                        d["signals"] = data_field.get("signals", [])
                        results.append(d)
                    db_used = True
        except Exception:
            pass

        # Fallback to JSONL
        if not results:
            results = _load_scanner_from_events()

        # Build categories
        cats = {}
        for r in results:
            cat = r.get("category", "unknown")
            cats[cat] = cats.get(cat, 0) + 1
        categories = [{"category": k, "cnt": v} for k, v in sorted(cats.items(), key=lambda x: -x[1])]

        return {
            "results": results[:50],
            "categories": categories,
            "total_tokens": len(results),
            "db_used": db_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/wallets")
async def get_wallets():
    """Get wallet tracker activity — DB first, JSONL fallback."""
    try:
        events = []
        whale_alerts = []
        db_used = False

        # Try DB first
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                with pool.connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM wallet_events
                        ORDER BY created_at DESC LIMIT 50
                    """).fetchall()
                    events = [dict(r) for r in rows]
                    db_used = True
                    try:
                        whale_rows = conn.execute("""
                            SELECT * FROM whale_alerts
                            ORDER BY alert_time DESC LIMIT 20
                        """).fetchall()
                        whale_alerts = [dict(w) for w in whale_rows]
                    except Exception:
                        pass
        except Exception:
            pass

        # Fallback to JSONL
        if not events:
            events = _load_wallets_from_jsonl()

        return {
            "events": events,
            "whale_alerts": whale_alerts,
            "wallet_summary": [],
            "db_used": db_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/rbi")
async def get_rbi():
    """Get Risk-Based Intelligence data — DB first, JSONL fallback."""
    try:
        risk_events = []
        strategy_signals = []
        circuit_breaker = None
        db_used = False

        # Try DB first
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                with pool.connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM engine_events
                        WHERE event_type LIKE 'risk/%'
                        ORDER BY created_at DESC LIMIT 50
                    """).fetchall()
                    risk_events = [dict(r) for r in rows]
                    cb_row = conn.execute("""
                        SELECT * FROM engine_events
                        WHERE event_type LIKE 'risk/circuit%'
                        ORDER BY created_at DESC LIMIT 1
                    """).fetchone()
                    circuit_breaker = dict(cb_row) if cb_row else None
                    sig_rows = conn.execute("""
                        SELECT * FROM engine_events
                        WHERE event_type LIKE 'strategy/%'
                        ORDER BY created_at DESC LIMIT 50
                    """).fetchall()
                    strategy_signals = [dict(s) for s in sig_rows]
                    db_used = True
        except Exception:
            pass

        # Fallback to JSONL
        if not risk_events:
            all_events = _load_events_from_jsonl(limit=200)
            risk_events = [e for e in all_events if (e.get("event_type") or e.get("type", "")).startswith("risk/")]
            strategy_signals = [e for e in all_events if (e.get("event_type") or e.get("type", "")).startswith("strategy/")]
            if risk_events:
                circuit_breaker = risk_events[0]

        # RBI pipeline data — DB (rbi_runs / rbi_strategies) first, file fallback
        rbi_runs = []
        rbi_strategies = []
        try:
            from src.db_storage import get_rbi_runs, get_rbi_strategies
            rbi_runs = get_rbi_runs(20)
            rbi_strategies = get_rbi_strategies(20)
        except Exception:
            pass
        if not rbi_runs:
            runs_file = Path(__file__).parent / "data" / "rbi" / "runs" / "runs.jsonl"
            if runs_file.exists():
                try:
                    lines = [json.loads(l) for l in runs_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                    rbi_runs = sorted(lines, key=lambda r: r.get("created_at", ""), reverse=True)[:20]
                except Exception:
                    pass
        if not rbi_strategies:
            hist_file = Path(__file__).parent / "data" / "rbi" / "strategy_memory" / "strategy_history.jsonl"
            if hist_file.exists():
                try:
                    lines = [json.loads(l) for l in hist_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                    rbi_strategies = list(reversed(lines))[:20]
                except Exception:
                    pass

        return {
            "risk_events": risk_events[-30:],
            "circuit_breaker": circuit_breaker,
            "capital_resets": _load_capital_resets(),
            "risk_rejections": [],
            "strategy_signals": strategy_signals[-30:],
            "rbi_runs": rbi_runs,
            "rbi_strategies": rbi_strategies,
            "db_used": db_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/mcp")
async def get_mcp():
    """Get MCP (Model Context Protocol) data — DB first, JSONL fallback."""
    try:
        from src.mcp_registry import create_default_mcp_registry
        registry = create_default_mcp_registry()
        
        tools = []
        for name, tool in registry.tools.items():
            tools.append({
                "name": name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        
        recent_calls = []
        db_used = False
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                with pool.connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM engine_events
                        WHERE event_type LIKE 'mcp/%'
                        ORDER BY created_at DESC LIMIT 20
                    """).fetchall()
                    recent_calls = [dict(r) for r in rows]
                    db_used = True
        except Exception:
            pass

        if not recent_calls:
            recent_calls = _load_events_from_jsonl(
                lambda et: et.startswith("mcp/"), limit=20)

        return {
            "tools": tools,
            "tool_count": len(tools),
            "recent_calls": recent_calls,
            "db_used": db_used,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/storage")
async def get_storage():
    """Get storage monitor data."""
    try:
        from src.db_storage import get_pool
        pool = get_pool()
        if not pool:
            return {"error": "No DB"}
        
        with pool.connection() as conn:
            # DB size
            row = conn.execute(
                "SELECT pg_database_size(current_database()) as size_bytes"
            ).fetchone()
            total_mb = row["size_bytes"] / (1024 * 1024) if row else 0
            
            # Table sizes
            table_sizes = conn.execute("""
                SELECT relname, pg_total_relation_size(relid) as size_bytes
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
            """).fetchall()
            
            # Row counts
            tables = {}
            for name in ["ohlcv_candles", "trades", "scanner_results", 
                         "wallet_events", "engine_events", "orderbook_snapshots"]:
                try:
                    r = conn.execute(f"SELECT COUNT(*) as cnt FROM {name}").fetchone()
                    tables[name] = r["cnt"] if r else 0
                except Exception:
                    tables[name] = 0
            
            # Backup status
            backup_info = None
            try:
                backup_row = conn.execute("""
                    SELECT * FROM backup_history 
                    ORDER BY id DESC LIMIT 1
                """).fetchone()
                backup_info = dict(backup_row) if backup_row else None
            except Exception:
                pass
            
            return {
                "total_size_mb": round(total_mb, 1),
                "limit_mb": 10240,
                "usage_pct": round(total_mb / 10240 * 100, 1),
                "table_sizes": {r["relname"]: round(r["size_bytes"] / 1024, 1) for r in table_sizes},
                "row_counts": tables,
                "backup_info": backup_info,
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/health")
async def get_health():
    """Get system health status."""
    try:
        from src.db_storage import get_pool
        pool = get_pool()
        db_ok = pool is not None
        
        # Check each module
        modules = {}
        
        # DB
        modules["database"] = {"status": "ok" if db_ok else "error"}
        
        # EventBus
        try:
            from src.event_bus import EventBus, _pending_tasks, _fire_and_forget
            modules["event_bus"] = {
                "status": "ok",
                "pending_tasks": len(_pending_tasks),
                "note": "fire_and_forget active"
            }
        except Exception:
            modules["event_bus"] = {"status": "error"}

        # MCP Registry
        try:
            from src.mcp_registry import get_mcp_registry
            reg = get_mcp_registry()
            tools = reg.get_tools() if hasattr(reg, 'get_tools') else []
            modules["mcp_registry"] = {
                "status": "ok",
                "tools_loaded": len(tools),
                "tool_names": [t["name"] for t in tools[:5]] if tools else [],
            }
        except Exception as e:
            modules["mcp_registry"] = {"status": "error", "error": str(e)}
        
        # Scanner
        try:
            from src.token_scanner import TokenScanner
            modules["scanner"] = {"status": "ok"}
        except Exception:
            modules["scanner"] = {"status": "error"}
        
        # OHLCV Collector
        try:
            from src.ohlcv_collector import get_ohlcv_collector
            collector = get_ohlcv_collector()
            stats = collector.get_stats()
            modules["ohlcv_collector"] = {
                "status": "ok",
                "tracked_tokens": stats.get("tracked_tokens", 0),
                "total_polls": stats.get("total_polls", 0),
            }
        except Exception:
            modules["ohlcv_collector"] = {"status": "error"}
        
        # Strategy Bridge
        try:
            from src.strategy_bridge import get_strategy_bridge
            modules["strategy_bridge"] = {"status": "ok"}
        except Exception:
            modules["strategy_bridge"] = {"status": "error"}
        
        # Risk Guard
        try:
            from src.risk_guard import RiskGuard
            modules["risk_guard"] = {"status": "ok"}
        except Exception:
            modules["risk_guard"] = {"status": "error"}
        
        # Portfolio Risk Manager
        try:
            from src.portfolio_risk_manager import get_portfolio_risk_manager
            modules["portfolio_risk"] = {"status": "ok"}
        except Exception:
            modules["portfolio_risk"] = {"status": "error"}
        
        # PredictionEngine v2
        try:
            from src.prediction_engine_v2 import get_prediction_engine
            modules["prediction_engine"] = {"status": "ok"}
        except Exception:
            modules["prediction_engine"] = {"status": "error"}
        
        # LLM Exit Decider
        try:
            from src.llm_exit_decider import get_llm_exit_decider
            modules["llm_exit_decider"] = {"status": "ok"}
        except Exception:
            modules["llm_exit_decider"] = {"status": "error"}
        
        # AI Override Engine
        try:
            from src.ai_override_engine import get_ai_override_engine
            modules["ai_override"] = {"status": "ok"}
        except Exception:
            modules["ai_override"] = {"status": "error"}
        
        # Full Sentiment Agent
        try:
            from src.full_sentiment_agent import get_full_sentiment_agent
            modules["sentiment"] = {"status": "ok"}
        except Exception:
            modules["sentiment"] = {"status": "error"}
        
        # Chart Analysis
        try:
            from src.chart_analysis_agent import get_chart_analysis_agent
            modules["chart_analysis"] = {"status": "ok"}
        except Exception:
            modules["chart_analysis"] = {"status": "error"}
        
        # CoinGecko
        try:
            from src.coingecko_agent import get_coingecko_agent
            modules["coingecko"] = {"status": "ok"}
        except Exception:
            modules["coingecko"] = {"status": "error"}
        
        # ICT Analysis
        try:
            from src.ict_analysis_agent import get_ict_analysis_agent
            modules["ict_analysis"] = {"status": "ok"}
        except Exception:
            modules["ict_analysis"] = {"status": "error"}
        
        # Order Book
        try:
            from src.orderbook_collector import get_orderbook_collector
            modules["orderbook"] = {"status": "ok"}
        except Exception:
            modules["orderbook"] = {"status": "error"}
        
        # Storage Tier Manager
        try:
            from src.storage_tier_manager import get_storage_tier_manager
            modules["storage_tier"] = {"status": "ok"}
        except Exception:
            modules["storage_tier"] = {"status": "error"}
        
        # Backup Manager
        try:
            from src.backup_manager import get_backup_manager
            modules["backup"] = {"status": "ok"}
        except Exception:
            modules["backup"] = {"status": "error"}
        
        # Data Compressor
        try:
            from src.data_compressor import get_data_compressor
            modules["compressor"] = {"status": "ok"}
        except Exception:
            modules["compressor"] = {"status": "error"}
        
        # Storage Alerts
        try:
            from src.storage_alerts import get_storage_alerts
            modules["storage_alerts"] = {"status": "ok"}
        except Exception:
            modules["storage_alerts"] = {"status": "error"}
        
        # Telegram
        try:
            from src.telegram_reporter import get_telegram_reporter
            tg = get_telegram_reporter()
            modules["telegram"] = {"status": "ok" if tg.enabled else "disabled"}
        except Exception:
            modules["telegram"] = {"status": "error"}
        
        # Count statuses
        ok_count = sum(1 for m in modules.values() if m["status"] == "ok")
        total = len(modules)
        
        return {
            "overall": "healthy" if ok_count == total else "degraded",
            "modules": modules,
            "ok_count": ok_count,
            "total_count": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}



# ── SMC Chart API ─────────────────────────────────────────

@app.get("/api/smc")
async def api_smc(symbol: str = "SOLUSDT", interval: str = "1h", limit: int = 100):
    """SMC pattern detection + OHLCV + indicators for chart rendering."""
    try:
        from src.smc_patterns import get_smc_detector
        from src.tradingview_feed import get_tradingview_feed
        feed = get_tradingview_feed()
        ohlcv = feed.get_ohlcv_candles(symbol=symbol, interval=interval, limit=limit)
        if ohlcv.get("error") or not ohlcv.get("candles"):
            return {"error": ohlcv.get("error", "No OHLCV data"), "symbol": symbol}
        candles = ohlcv["candles"]
        detector = get_smc_detector()
        smc = detector.detect_all(candles)
        closes = [c["close"] for c in candles]
        times = [c["time"] for c in candles]
        ema9 = _compute_ema(closes, 9)
        ema21 = _compute_ema(closes, 21)
        bb_up, bb_lo = _compute_bollinger(closes, 20, 2.0)
        indicators = {
            "ema9": [{"time": times[i], "value": ema9[i]} for i in range(len(times)) if ema9[i] is not None],
            "ema21": [{"time": times[i], "value": ema21[i]} for i in range(len(times)) if ema21[i] is not None],
            "bb_upper": [{"time": times[i], "value": bb_up[i]} for i in range(len(times)) if bb_up[i] is not None],
            "bb_lower": [{"time": times[i], "value": bb_lo[i]} for i in range(len(times)) if bb_lo[i] is not None],
        }
        from src.event_bus import _fire_and_forget
        try:
            from src.db_storage import log_event
            log_event("smc/chart_request", {"symbol": symbol, "interval": interval, "candles": len(candles)})
        except: pass

        result_dict = {"symbol": symbol, "interval": interval, "candles": candles,
                "indicators": indicators, **smc.to_dict()}
        # RBI deployed-strategy pattern markers (#4): replay hot-loaded
        # custom strategies over the candles so their signals render on
        # the TradingView chart
        try:
            from src.strategy_bridge import get_custom_strategy_chart_markers
            rbi = get_custom_strategy_chart_markers(candles)
            result_dict["rbi_markers"] = rbi.get("markers", [])
            result_dict["rbi_strategies"] = rbi.get("strategies", [])
        except Exception:
            result_dict["rbi_markers"] = []
            result_dict["rbi_strategies"] = []
        # Run custom chart bots
        try:
            from src.custom_chart_bots import run_custom_bots
            custom = run_custom_bots(candles)
            result_dict["markers"] = result_dict.get("markers", []) + custom.markers
            result_dict["custom_price_lines"] = custom.price_lines
            result_dict["custom_panels"] = custom.panels
        except Exception as ce:
            result_dict["custom_error"] = str(ce)
        return result_dict

    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _compute_ema(data, period):
    if not data: return []
    ema = [None] * (period - 1)
    sma = sum(data[:period]) / period
    ema.append(sma)
    mult = 2.0 / (period + 1)
    for i in range(period, len(data)):
        ema.append(data[i] * mult + ema[-1] * (1 - mult))
    return ema


def _compute_bollinger(data, period=20, mult=2.0):
    if len(data) < period: return [None]*len(data), [None]*len(data)
    upper, lower = [None]*(period-1), [None]*(period-1)
    for i in range(period - 1, len(data)):
        window = data[i-period+1:i+1]
        sma = sum(window) / period
        std = (sum((x - sma) ** 2 for x in window) / period) ** 0.5
        upper.append(sma + mult * std)
        lower.append(sma - mult * std)
    return upper, lower


@app.get("/charts/smc/", response_class=HTMLResponse)
async def smc_charts_page():
    """SMC Charts with TradingView embed + Python bot overlays."""
    try:
        from pathlib import Path
        html_file = Path(__file__).parent / "templates" / "charts" / "smc_chart.html"
        if html_file.exists():
            return HTMLResponse(html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>SMC Charts module not available</h1>", status_code=503)
    except Exception as e:
        return HTMLResponse(f"<h1>SMC Charts error: {e}</h1>", status_code=500)


# ── Auth API Endpoints ─────────────────────────────────────

@app.post("/api/auth/signup")
async def auth_signup(request: Request):
    """Create a new user account."""
    try:
        body = await request.json()
        username = body.get("username", "").strip()
        email = body.get("email", "").strip()
        password = body.get("password", "")
        display_name = body.get("display_name", "").strip()

        if not username or not email or not password:
            return {"error": "Username, email, and password are required"}

        from src.auth import get_auth_manager
        auth = get_auth_manager()
        result = auth.signup(username, email, password, display_name)

        if result.get("error"):
            return result

        # Set auth cookie
        response = JSONResponse(result)
        response.set_cookie(
            key="auth_token",
            value=result["token"],
            httponly=True,
            max_age=86400,  # 24 hours
            samesite="lax",
        )
        return response

    except Exception as e:
        return {"error": str(e)}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Authenticate user and return token."""
    try:
        body = await request.json()
        username = body.get("username", "").strip()
        password = body.get("password", "")

        if not username or not password:
            return {"error": "Username and password are required"}

        from src.auth import get_auth_manager
        auth = get_auth_manager()
        result = auth.login(username, password)

        if result.get("error"):
            return result

        # Set auth cookie
        response = JSONResponse(result)
        response.set_cookie(
            key="auth_token",
            value=result["token"],
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        return response

    except Exception as e:
        return {"error": str(e)}


@app.get("/api/auth/credentials")
async def auth_credentials():
    """Get login credentials (for setup only)."""
    return {
        "username": "admin",
        "password": "moondev2026",
        "note": "These are the default credentials. Change the password after first login."
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Log out and clear session."""
    token = request.cookies.get("auth_token")
    if token:
        from src.auth import get_auth_manager
        auth = get_auth_manager()
        auth.logout(token)

    response = JSONResponse({"success": True})
    response.delete_cookie("auth_token")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Get current user info."""
    token = request.cookies.get("auth_token")
    if not token:
        return {"authenticated": False}

    from src.auth import get_auth_manager
    auth = get_auth_manager()
    user = auth.verify_session(token)
    if not user:
        return {"authenticated": False}

    return {"authenticated": True, "user": user}


# ── HTML Dashboard ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard HTML (requires auth)."""
    token = request.cookies.get("auth_token")
    if token:
        from src.auth import get_auth_manager
        auth = get_auth_manager()
        user = auth.verify_session(token)
        if user:
            return DASHBOARD_HTML

    # Not authenticated — show login page
    return LOGIN_HTML


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login page."""
    return LOGIN_HTML


@app.get("/rbi/", response_class=HTMLResponse)
async def rbi_agent_page():
    """RBI Agent web interface."""
    try:
        from src.rbi_web import get_rbi_html
        return get_rbi_html()
    except ImportError:
        return HTMLResponse("<h1>RBI Agent module not available</h1>", status_code=503)


@app.get("/mcp/", response_class=HTMLResponse)
async def mcp_agent_page():
    """MCP Agent web interface."""
    try:
        from src.mcp_web import get_mcp_html
        return get_mcp_html()
    except ImportError:
        return HTMLResponse("<h1>MCP Agent module not available</h1>", status_code=503)


@app.get("/charts/", response_class=HTMLResponse)
async def charts_page():
    """TradingView Lightweight Charts — professional candlestick visualization."""
    try:
        from pathlib import Path
        html_file = Path(__file__).parent / "templates" / "charts" / "index.html"
        if html_file.exists():
            return HTMLResponse(html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Charts module not available</h1>", status_code=503)
    except Exception as e:
        return HTMLResponse(f"<h1>Charts error: {e}</h1>", status_code=500)



DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🌙 Moon Dev Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f; color: #e0e0e0; 
        }
        .header { 
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            padding: 20px; border-bottom: 1px solid #333;
            display: flex; justify-content: space-between; align-items: center;
        }
        .header h1 { color: #00d4ff; font-size: 24px; }
        .header .status { color: #4ade80; font-size: 14px; }
        
        .nav { 
            background: #111; padding: 10px 20px; 
            display: flex; gap: 10px; border-bottom: 1px solid #222;
            overflow-x: auto;
        }
        .nav button {
            background: #1a1a2e; border: 1px solid #333; color: #aaa;
            padding: 8px 16px; border-radius: 6px; cursor: pointer;
            font-size: 13px; white-space: nowrap;
        }
        .nav button:hover { background: #2a2a4e; color: #fff; }
        .nav button.active { background: #00d4ff22; color: #00d4ff; border-color: #00d4ff; }
        
        .container { padding: 20px; max-width: 1400px; margin: 0 auto; }
        
        .panel { display: none; }
        .panel.active { display: block; }
        
        .grid { display: grid; gap: 16px; }
        .grid-2 { grid-template-columns: repeat(2, 1fr); }
        .grid-3 { grid-template-columns: repeat(3, 1fr); }
        .grid-4 { grid-template-columns: repeat(4, 1fr); }
        
        .card {
            background: #111; border: 1px solid #222; border-radius: 8px;
            padding: 16px; transition: border-color 0.2s;
        }
        .card:hover { border-color: #00d4ff44; }
        .card h3 { color: #00d4ff; font-size: 14px; margin-bottom: 12px; text-transform: uppercase; }
        
        .stat-value { font-size: 28px; font-weight: bold; color: #fff; }
        .stat-label { font-size: 12px; color: #888; margin-top: 4px; }
        .stat-change { font-size: 12px; margin-top: 4px; }
        .stat-change.positive { color: #4ade80; }
        .stat-change.negative { color: #f87171; }
        
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 10px; color: #888; border-bottom: 1px solid #222; }
        td { padding: 10px; border-bottom: 1px solid #1a1a1a; }
        tr:hover { background: #1a1a2e; }
        
        .badge {
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 600;
        }
        .badge-green { background: #4ade8022; color: #4ade80; }
        .badge-red { background: #f8717122; color: #f87171; }
        .badge-yellow { background: #fbbf2422; color: #fbbf24; }
        .badge-blue { background: #00d4ff22; color: #00d4ff; }
        
        .progress-bar {
            height: 6px; background: #222; border-radius: 3px; overflow: hidden;
        }
        .progress-fill {
            height: 100%; border-radius: 3px; transition: width 0.3s;
        }
        .progress-fill.green { background: #4ade80; }
        .progress-fill.yellow { background: #fbbf24; }
        .progress-fill.red { background: #f87171; }
        
        .loading { text-align: center; padding: 40px; color: #666; }
        .error { color: #f87171; padding: 20px; }
        
        @media (max-width: 768px) {
            .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌙 Moon Dev Trading Dashboard</h1>
        <div style="display:flex;align-items:center;gap:16px;">
            <div class="status" id="header-status">Loading...</div>
            <div id="user-info" style="color:#aaa;font-size:13px;"></div>
            <button onclick="logout()" style="background:#f8717133;border:1px solid #f8717155;color:#f87171;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:12px;">Logout</button>
        </div>
    </div>
    
    <div class="nav">
        <button class="active" onclick="showPanel('portfolio')">📊 Portfolio</button>
        <button onclick="showPanel('trades')">📈 Trades</button>
        <button onclick="showPanel('scanner')">🔍 Scanner</button>
        <button onclick="showPanel('wallets')">🐋 Wallets</button>
        <button onclick="showPanel('rbi')">🛡️ RBI</button>
        <button onclick="window.open('/rbi/','_blank')" style="color:#e879f9;">🧪 RBI Agent →</button>
        <button onclick="window.open('/mcp/','_blank')" style="color:#22d3ee;">🔌 MCP Agent →</button>
        <button onclick="window.open('/charts/','_blank')" style="color:#4ade80;">📈 Charts →</button>
        <button onclick="showPanel('storage')">💾 Storage</button>
        <button onclick="showPanel('health')">💓 Health</button>
    </div>
    
    <div class="container">
        <!-- Portfolio Panel -->
        <div class="panel active" id="panel-portfolio">
            <div class="grid grid-4" id="portfolio-stats"></div>
            <div class="grid grid-2" style="margin-top: 16px;">
                <div class="card"><h3>Open Positions</h3><div id="open-positions"></div></div>
                <div class="card"><h3>Recent Trades</h3><div id="recent-trades"></div></div>
            </div>
        </div>
        
        <!-- Trades Panel -->
        <div class="panel" id="panel-trades">
            <div class="card"><h3>Trade History</h3><div id="trade-history"></div></div>
        </div>
        
        <!-- Scanner Panel -->
        <div class="panel" id="panel-scanner">
            <div class="grid grid-3" id="scanner-stats"></div>
            <div class="card" style="margin-top: 16px;"><h3>Recent Tokens</h3><div id="scanner-results"></div></div>
        </div>
        
        <!-- Wallets Panel -->
        <div class="panel" id="panel-wallets">
            <div class="grid grid-2">
                <div class="card"><h3>Wallet Activity</h3><div id="wallet-activity"></div></div>
                <div class="card"><h3>Whale Alerts</h3><div id="whale-alerts"></div></div>
            </div>
        </div>
        
        <!-- RBI Panel -->
        <div class="panel" id="panel-rbi">
            <div class="grid grid-2">
                <div class="card"><h3>Risk Events</h3><div id="risk-events"></div></div>
                <div class="card"><h3>Strategy Signals</h3><div id="strategy-signals"></div></div>
            </div>
        </div>
        
        <!-- MCP Panel -->
        <div class="panel" id="panel-mcp">
            <div class="card"><h3>MCP Tools</h3><div id="mcp-tools"></div></div>
        </div>
        
        <!-- Storage Panel -->
        <div class="panel" id="panel-storage">
            <div class="grid grid-3" id="storage-stats"></div>
            <div class="card" style="margin-top: 16px;"><h3>Table Sizes</h3><div id="storage-tables"></div></div>
        </div>
        
        <!-- Health Panel -->
        <div class="panel" id="panel-health">
            <div class="grid grid-3" id="health-modules"></div>
        </div>
    </div>
    
    <script>
        const panels = {
            portfolio: loadPortfolio,
            trades: loadTrades,
            scanner: loadScanner,
            wallets: loadWallets,
            rbi: loadRBI,
            mcp: loadMCP,
            storage: loadStorage,
            health: loadHealth,
        };
        
        function showPanel(name) {
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            document.getElementById('panel-' + name).classList.add('active');
            event.target.classList.add('active');
            panels[name]();
        }
        
        async function fetchAPI(url) {
            try {
                const resp = await fetch(url);
                return await resp.json();
            } catch(e) { return {error: e.message}; }
        }
        
        function formatUSD(n) { return '$' + (n || 0).toFixed(2); }
        function formatPct(n) { return (n || 0).toFixed(1) + '%'; }
        
        async function loadPortfolio() {
            const data = await fetchAPI('/api/portfolio');
            if (data.error) return;
            const p = data.portfolio || {};
            const s = data.stats || {};
            
            document.getElementById('portfolio-stats').innerHTML = `
                <div class="card"><h3>Capital</h3><div class="stat-value">${formatUSD(p.current_capital)}</div><div class="stat-label">Started: ${formatUSD(p.initial_capital)}</div></div>
                <div class="card"><h3>P&L</h3><div class="stat-value">${formatUSD(p.total_pnl)}</div><div class="stat-change ${(p.total_pnl||0)>=0?'positive':'negative'}">${(p.total_pnl||0)>=0?'+':''}${formatPct(((p.total_pnl||0)/(p.initial_capital||100))*100)}</div></div>
                <div class="card"><h3>Win Rate</h3><div class="stat-value">${s.win_rate || 0}%</div><div class="stat-label">${s.wins || 0}W / ${s.losses || 0}L</div></div>
                <div class="card"><h3>Open Positions</h3><div class="stat-value">${s.open_count || 0}</div><div class="stat-label">Active trades</div></div>
                <div class="card"><h3>Capital Resets</h3><div class="stat-value">${(data.capital_resets||{}).reset_count || 0}</div><div class="stat-label">${(data.capital_resets||{}).last_reset ? 'Last: ' + ((data.capital_resets.last_reset||'').slice(0,16)).replace('T',' ') : 'Never reset'}</div></div>
            `;
            
            const positions = (data.open_positions || []).map(t => 
                `<tr><td>${t.symbol || '?'}</td><td>${formatUSD(t.amount_usd)}</td><td>${t.score || 0}/100</td><td><span class="badge badge-blue">${t.status || 'open'}</span></td></tr>`
            ).join('');
            document.getElementById('open-positions').innerHTML = `<table><tr><th>Token</th><th>Amount</th><th>Score</th><th>Status</th></tr>${positions || '<tr><td colspan="4">No open positions</td></tr>'}</table>`;
            
            const trades = (data.closed_trades || []).slice(-10).reverse().map(t => {
                const pnl = t.pnl_usd || 0;
                const cls = pnl >= 0 ? 'badge-green' : 'badge-red';
                return `<tr><td>${t.symbol || '?'}</td><td>${formatUSD(pnl)}</td><td><span class="badge ${cls}">${pnl>=0?'+':''}${formatPct(t.pnl_pct)}</span></td><td>${t.exit_reason || t.status || ''}</td></tr>`;
            }).join('');
            document.getElementById('recent-trades').innerHTML = `<table><tr><th>Token</th><th>P&L</th><th>%</th><th>Reason</th></tr>${trades || '<tr><td colspan="4">No trades yet</td></tr>'}</table>`;
        }
        
        async function loadTrades() { await loadPortfolio(); }
        
        async function loadScanner() {
            const data = await fetchAPI('/api/scanner');
            if (data.error) return;
            
            document.getElementById('scanner-stats').innerHTML = `
                <div class="card"><h3>Total Tokens</h3><div class="stat-value">${data.total_tokens || 0}</div></div>
                <div class="card"><h3>Categories</h3><div class="stat-value">${(data.categories||[]).length}</div></div>
                <div class="card"><h3>Recent Results</h3><div class="stat-value">${(data.results||[]).length}</div></div>
            `;
            
            const results = (data.results || []).slice(0, 20).map(r => {
                const score = r.score || 0;
                const cls = score >= 70 ? 'badge-green' : score >= 40 ? 'badge-yellow' : 'badge-red';
                return `<tr><td>${r.symbol || r.token_address?.slice(0,8)}</td><td>${r.category || '?'}</td><td><span class="badge ${cls}">${score}/100</span></td><td>${formatUSD(r.liquidity_usd)}</td><td>${formatUSD(r.volume_24h)}</td></tr>`;
            }).join('');
            document.getElementById('scanner-results').innerHTML = `<table><tr><th>Token</th><th>Category</th><th>Score</th><th>Liquidity</th><th>Volume</th></tr>${results || '<tr><td colspan="5">No results</td></tr>'}</table>`;
        }
        
        async function loadWallets() {
            const data = await fetchAPI('/api/wallets');
            if (data.error) return;
            
            const activity = (data.events || []).slice(0, 20).map(e => {
                const dir = e.direction || '?';
                const cls = dir === 'buy' ? 'badge-green' : 'badge-red';
                return `<tr><td>${e.wallet?.slice(0,8) || '?'}</td><td><span class="badge ${cls}">${dir.toUpperCase()}</span></td><td>${e.token_address?.slice(0,8) || '?'}</td><td>${(e.amount_sol||0).toFixed(4)} SOL</td></tr>`;
            }).join('');
            document.getElementById('wallet-activity').innerHTML = `<table><tr><th>Wallet</th><th>Direction</th><th>Token</th><th>Amount</th></tr>${activity || '<tr><td colspan="4">No activity</td></tr>'}</table>`;
            
            const whales = (data.whale_alerts || []).map(w => 
                `<tr><td>${w.token_address?.slice(0,8)}</td><td>${w.alert_type}</td><td>${formatUSD(w.size_usd)}</td></tr>`
            ).join('');
            document.getElementById('whale-alerts').innerHTML = `<table><tr><th>Token</th><th>Type</th><th>Size</th></tr>${whales || '<tr><td colspan="3">No whale alerts</td></tr>'}</table>`;
        }
        
        async function loadRBI() {
            const data = await fetchAPI('/api/rbi');
            if (data.error) return;
            
            const resets = data.capital_resets || {};
            const resetBadge = `<div style="margin-bottom:10px">` +
                `<span class="badge badge-blue">Capital resets: ${resets.reset_count || 0}</span> ` +
                (resets.last_reset ? `<span class="badge badge-blue">Last: ${(resets.last_reset||'').slice(0,19).replace('T',' ')}</span> ` : `<span class="badge badge-blue">Never reset</span> `) +
                `<span class="badge ${resets.auto_reset_enabled === false ? 'badge-red' : 'badge-green'}">Auto-reset: ${resets.auto_reset_enabled === false ? 'OFF' : 'ON'}${resets.cooldown_hours ? ' (' + resets.cooldown_hours + 'h cooldown)' : ''}</span>` +
                `</div>`;
            const resetRows = (resets.recent || []).slice().reverse().map(r =>
                `<tr><td>#${r.reset_number}</td><td>${formatUSD(r.capital_before)} → ${formatUSD(r.capital_after)}</td><td>${r.positions_cleared || 0}</td><td>${(r.timestamp||'').slice(0,19).replace('T',' ')}</td></tr>`
            ).join('');
            const resetsTable = resetRows ? `<h3>Recent Capital Resets</h3><table><tr><th>#</th><th>Capital</th><th>Positions Cleared</th><th>Time</th></tr>${resetRows}</table><br/>` : '';
            const events = (data.risk_events || []).slice(0, 20).map(e => 
                `<tr><td>${e.event_type}</td><td>${e.created_at?.slice(0,19)}</td></tr>`
            ).join('');
            document.getElementById('risk-events').innerHTML = resetBadge + resetsTable + `<table><tr><th>Event</th><th>Time</th></tr>${events || '<tr><td colspan="2">No risk events</td></tr>'}</table>`;
            
            const signals = (data.strategy_signals || []).slice(0, 20).map(s => 
                `<tr><td>${s.event_type}</td><td>${s.created_at?.slice(0,19)}</td></tr>`
            ).join('');
            document.getElementById('strategy-signals').innerHTML = `<table><tr><th>Signal</th><th>Time</th></tr>${signals || '<tr><td colspan="2">No signals</td></tr>'}</table>`;
        }
        
        async function loadMCP() {
            const data = await fetchAPI('/api/mcp');
            if (data.error) return;
            
            const tools = (data.tools || []).map(t => 
                `<tr><td>${t.name}</td><td>${t.description?.slice(0,80)}</td></tr>`
            ).join('');
            document.getElementById('mcp-tools').innerHTML = `<table><tr><th>Tool</th><th>Description</th></tr>${tools || '<tr><td colspan="2">No tools</td></tr>'}</table>`;
        }
        
        async function loadStorage() {
            const data = await fetchAPI('/api/storage');
            if (data.error) return;
            
            const pct = data.usage_pct || 0;
            const pctClass = pct > 80 ? 'red' : pct > 50 ? 'yellow' : 'green';
            
            document.getElementById('storage-stats').innerHTML = `
                <div class="card"><h3>DB Size</h3><div class="stat-value">${data.total_size_mb || 0} MB</div><div class="stat-label">of ${data.limit_mb || 10240} MB</div><div class="progress-bar" style="margin-top:8px"><div class="progress-fill ${pctClass}" style="width:${pct}%"></div></div></div>
                <div class="card"><h3>Usage</h3><div class="stat-value">${pct}%</div><div class="stat-change ${pctClass==='red'?'negative':''}">${pctClass==='red'?'WARNING: High usage':'OK'}</div></div>
                <div class="card"><h3>Last Backup</h3><div class="stat-value">${data.backup_info?.backup_time?.slice(0,10) || 'None'}</div></div>
            `;
            
            const tables = Object.entries(data.row_counts || {}).map(([name, count]) => 
                `<tr><td>${name}</td><td>${count.toLocaleString()}</td></tr>`
            ).join('');
            document.getElementById('storage-tables').innerHTML = `<table><tr><th>Table</th><th>Rows</th></tr>${tables}</table>`;
        }
        
        async function loadHealth() {
            const data = await fetchAPI('/api/health');
            if (data.error) return;
            
            document.getElementById('header-status').innerHTML = 
                `<span style="color:${data.overall==='healthy'?'#4ade80':'#fbbf24'}">${data.overall?.toUpperCase()} (${data.ok_count}/${data.total_count})</span>`;
            
            const modules = Object.entries(data.modules || {}).map(([name, info]) => {
                const cls = info.status === 'ok' ? 'badge-green' : info.status === 'disabled' ? 'badge-yellow' : 'badge-red';
                const extra = info.tracked_tokens ? ` • ${info.tracked_tokens} tokens` : '';
                return `<div class="card"><h3>${name.replace(/_/g, ' ')}</h3><span class="badge ${cls}">${info.status.toUpperCase()}</span><div class="stat-label">${extra}</div></div>`;
            }).join('');
            document.getElementById('health-modules').innerHTML = modules;
        }
        
        // Load user info
        async function loadUserInfo() {
            try {
                const resp = await fetch('/api/auth/me');
                const data = await resp.json();
                if (data.authenticated && data.user) {
                    document.getElementById('user-info').innerHTML = 
                        `<span style="color:#4ade80">●</span> ${data.user.display_name || data.user.username}`;
                }
            } catch(e) {}
        }
        loadUserInfo();
        
        async function logout() {
            await fetch('/api/auth/logout', {method: 'POST'});
            window.location.href = '/login';
        }
        
        // Load default panel
        loadPortfolio();
        loadHealth();
        
        // Auto-refresh every 10 seconds
        setInterval(() => {
            const active = document.querySelector('.panel.active');
            if (active) {
                const name = active.id.replace('panel-', '');
                if (panels[name]) panels[name]();
            }
        }, 10000);
    </script>
</body>
</html>
"""


# ── Auth Pages ─────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🌙 Moon Dev — Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f; color: #e0e0e0; 
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh;
        }
        .auth-container { width: 100%; max-width: 420px; padding: 20px; }
        .auth-card { background: #111; border: 1px solid #333; border-radius: 12px; padding: 40px 32px; }
        .auth-card h1 { color: #00d4ff; font-size: 28px; text-align: center; margin-bottom: 8px; }
        .auth-card .subtitle { color: #888; text-align: center; margin-bottom: 32px; font-size: 14px; }
        .auth-card label { display: block; color: #aaa; font-size: 13px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
        .auth-card input { width: 100%; padding: 12px 14px; background: #0a0a0f; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 14px; margin-bottom: 16px; transition: border-color 0.2s; }
        .auth-card input:focus { outline: none; border-color: #00d4ff; }
        .auth-card input::placeholder { color: #555; }
        .auth-btn { width: 100%; padding: 14px; background: linear-gradient(135deg, #00d4ff, #0088cc); border: none; border-radius: 8px; color: #fff; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 8px; transition: opacity 0.2s; }
        .auth-btn:hover { opacity: 0.9; }
        .auth-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .auth-link { text-align: center; margin-top: 20px; font-size: 14px; color: #888; }
        .auth-link a { color: #00d4ff; text-decoration: none; }
        .auth-link a:hover { text-decoration: underline; }
        .auth-error { background: #f8717122; border: 1px solid #f8717144; border-radius: 8px; padding: 12px; margin-bottom: 16px; color: #f87171; font-size: 13px; display: none; }
    </style>
</head>
<body>
    <div class="auth-container">
        <div class="auth-card">
            <h1>🌙 Moon Dev</h1>
            <p class="subtitle">Sign in to your trading dashboard</p>
            
            <div class="auth-error" id="error-msg"></div>
            
            <form id="login-form">
                <label>Username</label>
                <input type="text" id="username" placeholder="Enter username" required autocomplete="username">
                
                <label>Password</label>
                <input type="password" id="password" placeholder="Enter password" required autocomplete="current-password">
                
                <button type="submit" class="auth-btn" id="submit-btn">Sign In</button>
            </form>
            
            <p class="auth-link" style="background:#00d4ff11;border:1px solid #00d4ff33;border-radius:8px;padding:12px;margin-top:20px;">
                <span style="color:#00d4ff;">🔑 Default credentials</span><br>
                <span style="color:#888;">Username:</span> <span style="color:#fff;">admin</span> &nbsp;|&nbsp; <span style="color:#888;">Password:</span> <span style="color:#fff;">moondev2026</span>
            </p>
        </div>
    </div>
    
    <script>
        document.getElementById('login-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submit-btn');
            const errDiv = document.getElementById('error-msg');
            
            btn.disabled = true;
            btn.textContent = 'Signing in...';
            errDiv.style.display = 'none';
            
            try {
                const resp = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value,
                    }),
                });
                const data = await resp.json();
                
                if (data.error) {
                    errDiv.textContent = data.error;
                    errDiv.style.display = 'block';
                } else {
                    window.location.href = '/';
                }
            } catch(err) {
                errDiv.textContent = 'Connection failed: ' + err.message;
                errDiv.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sign In';
            }
        });
    </script>
</body>
</html>
"""




# ── Singleton ──────────────────────────────────────────────

_dashboard_instance = None

def get_web_dashboard():
    """Get or create the web dashboard."""
    global _dashboard_instance
    if _dashboard_instance is None:
        _dashboard_instance = app
        cprint("[DASHBOARD] Web Dashboard initialized on port 8080", "white", "on_green")
    return _dashboard_instance


if __name__ == "__main__":
    import uvicorn
    cprint("[DASHBOARD] Starting Moon Dev Trading Dashboard on port 8080...", "cyan")
    uvicorn.run(app, host="0.0.0.0", port=8080)
