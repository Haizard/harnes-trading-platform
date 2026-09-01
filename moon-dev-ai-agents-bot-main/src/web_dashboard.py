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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
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
    allow_headers=["*"],
)

DATA_DIR = Path("src/data")


# ── API Endpoints ──────────────────────────────────────────

@app.get("/api/portfolio")
async def get_portfolio():
    """Get portfolio overview from DB."""
    try:
        from src.db_storage import get_portfolio, get_trades
        portfolio = get_portfolio()
        trades = get_trades(limit=100)
        
        # Get open positions
        open_trades = [t for t in trades if t.get("status") == "open"]
        closed_trades = [t for t in trades if t.get("status") != "open"]
        
        # Calculate stats
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed_trades)
        wins = sum(1 for t in closed_trades if t.get("pnl_usd", 0) > 0)
        losses = sum(1 for t in closed_trades if t.get("pnl_usd", 0) < 0)
        
        return {
            "portfolio": portfolio or {},
            "open_positions": open_trades,
            "closed_trades": closed_trades[-20:],  # Last 20
            "stats": {
                "total_trades": len(closed_trades),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / max(len(closed_trades), 1) * 100, 1),
                "total_pnl": round(total_pnl, 2),
                "open_count": len(open_trades),
            }
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scanner")
async def get_scanner():
    """Get scanner results and token candidates."""
    try:
        from src.db_storage import get_pool
        pool = get_pool()
        if not pool:
            return {"error": "No DB"}
        
        with pool.connection() as conn:
            # Recent scanner results
            rows = conn.execute("""
                SELECT * FROM scanner_results 
                ORDER BY created_at DESC 
                LIMIT 50
            """).fetchall()
            
            # Token categories
            categories = conn.execute("""
                SELECT category, COUNT(*) as cnt 
                FROM scanner_results 
                GROUP BY category 
                ORDER BY cnt DESC
            """).fetchall()
            
            return {
                "results": [dict(r) for r in rows],
                "categories": [dict(c) for c in categories],
                "total_tokens": sum(c["cnt"] for c in categories),
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/wallets")
async def get_wallets():
    """Get wallet tracker activity."""
    try:
        from src.db_storage import get_pool
        pool = get_pool()
        if not pool:
            return {"error": "No DB"}
        
        with pool.connection() as conn:
            # Recent wallet events
            rows = conn.execute("""
                SELECT * FROM wallet_events 
                ORDER BY created_at DESC 
                LIMIT 50
            """).fetchall()
            
            # Whale alerts
            whale_alerts = []
            try:
                whale_rows = conn.execute("""
                    SELECT * FROM whale_alerts 
                    ORDER BY alert_time DESC 
                    LIMIT 20
                """).fetchall()
                whale_alerts = [dict(w) for w in whale_rows]
            except Exception:
                pass
            
            # Wallet summary
            wallets = conn.execute("""
                SELECT wallet, 
                       COUNT(*) as swaps,
                       SUM(CASE WHEN data->>'direction' = 'buy' THEN 1 ELSE 0 END) as buys,
                       SUM(CASE WHEN data->>'direction' = 'sell' THEN 1 ELSE 0 END) as sells
                FROM wallet_events 
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY wallet
                ORDER BY swaps DESC
            """).fetchall()
            
            return {
                "events": [dict(r) for r in rows],
                "whale_alerts": whale_alerts,
                "wallet_summary": [dict(w) for w in wallets],
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/rbi")
async def get_rbi():
    """Get Risk-Based Intelligence data."""
    try:
        from src.db_storage import get_pool
        pool = get_pool()
        if not pool:
            return {"error": "No DB"}
        
        with pool.connection() as conn:
            # Risk events
            risk_events = conn.execute("""
                SELECT * FROM engine_events 
                WHERE event_type LIKE 'risk/%'
                ORDER BY created_at DESC 
                LIMIT 30
            """).fetchall()
            
            # Circuit breaker status
            circuit_breaker = conn.execute("""
                SELECT * FROM engine_events 
                WHERE event_type = 'risk/circuit_breaker'
                ORDER BY created_at DESC 
                LIMIT 1
            """).fetchone()
            
            # Risk rejections
            risk_rejections = []
            try:
                risk_rejections = conn.execute("""
                    SELECT * FROM risk_rejections 
                    ORDER BY created_at DESC 
                    LIMIT 20
                """).fetchall()
            except Exception:
                pass
            
            # Strategy signals
            strategy_signals = conn.execute("""
                SELECT * FROM engine_events 
                WHERE event_type LIKE 'strategy/%'
                ORDER BY created_at DESC 
                LIMIT 30
            """).fetchall()
            
            return {
                "risk_events": [dict(r) for r in risk_events],
                "circuit_breaker": dict(circuit_breaker) if circuit_breaker else None,
                "risk_rejections": [dict(r) for r in risk_rejections],
                "strategy_signals": [dict(s) for s in strategy_signals],
            }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/mcp")
async def get_mcp():
    """Get MCP (Model Context Protocol) data."""
    try:
        from src.mcp_registry import MCPRegistry, create_default_mcp_registry
        registry = create_default_mcp_registry()
        
        tools = []
        for name, tool in registry.tools.items():
            tools.append({
                "name": name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        
        # Get recent MCP calls from DB
        recent_calls = []
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                with pool.connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM engine_events 
                        WHERE event_type LIKE 'mcp/%'
                        ORDER BY created_at DESC 
                        LIMIT 20
                    """).fetchall()
                    recent_calls = [dict(r) for r in rows]
        except Exception:
            pass
        
        return {
            "tools": tools,
            "tool_count": len(tools),
            "recent_calls": recent_calls,
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
            from src.event_bus import EventBus
            modules["event_bus"] = {"status": "ok"}
        except Exception:
            modules["event_bus"] = {"status": "error"}
        
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


# ── HTML Dashboard ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard HTML."""
    return DASHBOARD_HTML


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
        <div class="status" id="header-status">Loading...</div>
    </div>
    
    <div class="nav">
        <button class="active" onclick="showPanel('portfolio')">📊 Portfolio</button>
        <button onclick="showPanel('trades')">📈 Trades</button>
        <button onclick="showPanel('scanner')">🔍 Scanner</button>
        <button onclick="showPanel('wallets')">🐋 Wallets</button>
        <button onclick="showPanel('rbi')">🛡️ RBI</button>
        <button onclick="showPanel('mcp')">🤖 MCP</button>
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
            
            const events = (data.risk_events || []).slice(0, 20).map(e => 
                `<tr><td>${e.event_type}</td><td>${e.created_at?.slice(0,19)}</td></tr>`
            ).join('');
            document.getElementById('risk-events').innerHTML = `<table><tr><th>Event</th><th>Time</th></tr>${events || '<tr><td colspan="2">No risk events</td></tr>'}</table>`;
            
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
        
        // Load default panel
        loadPortfolio();
        loadHealth();
        
        // Auto-refresh every 30 seconds
        setInterval(() => {
            const active = document.querySelector('.panel.active');
            if (active) {
                const name = active.id.replace('panel-', '');
                if (panels[name]) panels[name]();
            }
        }, 30000);
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
