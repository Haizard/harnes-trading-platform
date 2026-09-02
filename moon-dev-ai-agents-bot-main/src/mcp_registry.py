"""
🔌 Moon Dev's Trading MCP Server — Internal Data Tools
DSH Pattern: mcp-client — register external tools via Model Context Protocol.

This is the INTERNAL trading MCP server that exposes the platform's own data
as MCP tools. The AI agent (Bedrock/Qwen3) calls these tools to query:
  - Token prices, liquidity, profiles (from DexScreener/Jupiter/Birdeye)
  - Portfolio state, open positions (from PaperTrader)
  - Risk state, daily PnL (from RiskGuard)
  - Scanner results, scores (from TokenScanner)
  - Whale data (from Solana RPC)
  - Trade history (from paper_trades.jsonl)

Architecture:
  AI Agent → HTTP → TradingMCPTool.call() → Existing data sources

Security: READ-ONLY. No execution permissions through MCP.
Trade execution stays behind the deterministic Python risk/execution layer.
"""

import os
import asyncio
import json
import time
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from enum import Enum


# ── Constants ─────────────────────────────────────────────────

JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
JUPITER_PRICE = "https://api.jup.ag/price/v2"
DEXSCREENER_PAIR = "https://api.dexscreener.com/latest/dex/pairs/solana"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
BIRDEYE_API = "https://public-api.birdeye.so"
SOLANA_RPC = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
SOL_MINT = "So11111111111111111111111111111111111111112"

DATA_DIR = Path("src/data")


# ── Tool Definitions ──────────────────────────────────────────

@dataclass
class ToolParameter:
    """A parameter that a tool accepts."""
    name: str
    type: str          # 'string', 'number', 'integer', 'boolean'
    required: bool = True
    default: Any = None
    description: str = ""

    def to_dict(self):
        return {
            'name': self.name, 'type': self.type,
            'required': self.required, 'default': self.default,
            'description': self.description,
        }


@dataclass
class ToolResult:
    """Standardized result from a tool call."""
    success: bool
    data: Any = None
    error: str = ""
    source: str = ""
    latency_ms: float = 0.0

    def to_dict(self):
        return {
            'success': self.success, 'data': self.data,
            'error': self.error, 'source': self.source,
            'latency_ms': round(self.latency_ms, 1),
        }


@dataclass
class TradingMCPTool:
    """A callable trading tool exposed via MCP."""
    name: str
    description: str
    parameters: List[ToolParameter]
    execute_fn: Callable
    source: str = ""  # Which data source backs this tool

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'parameters': [p.to_dict() for p in self.parameters],
            'source': self.source,
        }

    async def call(self, params: dict = None) -> ToolResult:
        """Call the tool with given parameters."""
        start = time.monotonic()
        try:
            if params is None:
                params = {}
            data = await self.execute_fn(**params)
            latency = (time.monotonic() - start) * 1000
            return ToolResult(success=True, data=data, source=self.source, latency_ms=latency)
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return ToolResult(success=False, error=str(e), source=self.source, latency_ms=latency)


# ── MCP Registry ──────────────────────────────────────────────

class MCPRegistry:
    """
    Registry for internal trading MCP tools.

    Unlike the previous stub implementation, each tool has a REAL
    execute_fn that calls actual data sources (DexScreener, Jupiter,
    Birdeye, Solana RPC, PaperTrader, scanner results, etc.).

    Security: All tools are READ-ONLY. No order execution through MCP.
    """

    def __init__(self):
        self._tools: Dict[str, TradingMCPTool] = {}
        self._call_history: List[dict] = []

    def register_tool(self, tool: TradingMCPTool):
        """Register a trading tool."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[TradingMCPTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[dict]:
        return [t.to_dict() for t in self._tools.values()]

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    async def call_tool(self, name: str, params: dict = None) -> ToolResult:
        """Call a tool by name (async). Returns standardized ToolResult."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        result = await tool.call(params)

        # Record in call history
        self._call_history.append({
            'tool': name, 'params': params,
            'success': result.success, 'latency_ms': result.latency_ms,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 100 calls
        if len(self._call_history) > 100:
            self._call_history = self._call_history[-100:]

        return result

    def call_tool_sync(self, name: str, params: dict = None) -> ToolResult:
        """Call a tool by name (sync). For use outside async contexts.
        Useful when _build_market_state() is called from sync code
        within an already-running event loop.
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        import time as _time
        start = _time.monotonic()
        if params is None:
            params = {}

        # Tool execute_fn is async def but uses sync requests internally.
        # The coroutine must be awaited in an isolated event loop.
        # We use a dedicated thread + event loop to avoid conflicts
        # with the container's running event loop.
        import threading
        _result = [None]
        _error = [None]
        _done = threading.Event()

        def _worker():
            try:
                # Create a fresh event loop for this thread, explicitly
                # detached from any existing loop in the main thread.
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    _result[0] = loop.run_until_complete(tool.execute_fn(**params))
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            except Exception as e:
                _error[0] = e
            finally:
                _done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=15)

        latency = (_time.monotonic() - start) * 1000
        if _error[0]:
            result = ToolResult(success=False, error=str(_error[0]), source=tool.source, latency_ms=latency)
        elif _result[0] is not None:
            result = ToolResult(success=True, data=_result[0], source=tool.source, latency_ms=latency)
        else:
            result = ToolResult(success=False, error="Tool call timed out (15s)", source=tool.source, latency_ms=latency)

        # Record in call history
        self._call_history.append({
            'tool': name, 'params': params,
            'success': result.success, 'latency_ms': result.latency_ms,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
        if len(self._call_history) > 100:
            self._call_history = self._call_history[-100:]

        return result

    def get_call_history(self, limit: int = 20) -> List[dict]:
        return self._call_history[-limit:]


# ── Tool Implementations (Real Data Sources) ──────────────────
# These functions are async and call the actual APIs.

async def tool_get_token_price(token_address: str) -> dict:
    """Get current token price from Jupiter Price API (FREE, no key)."""
    try:
        resp = requests.get(JUPITER_PRICE, params={"ids": token_address}, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get(token_address, {})
            return {
                "token_address": token_address,
                "price_usd": float(data.get("price", 0)),
                "confidence": float(data.get("confidence", 0)),
                "source": "jupiter_price",
            }
    except Exception:
        pass

    # Fallback: calculate from Jupiter quote
    try:
        resp = requests.get(JUPITER_QUOTE, params={
            "inputMint": SOL_MINT, "outputMint": token_address,
            "amount": "100000000", "slippageBps": 500}, timeout=10)
        if resp.status_code == 200:
            q = resp.json()
            out = int(q.get("outAmount", 0))
            if out > 0:
                price = 15.0 / out  # Rough SOL price conversion
                return {
                    "token_address": token_address,
                    "price_usd": round(price, 10),
                    "source": "jupiter_quote_fallback",
                }
    except Exception:
        pass

    return {"token_address": token_address, "price_usd": 0, "error": "Could not fetch price"}


async def tool_get_token_profile(token_address: str) -> dict:
    """Get token profile from Birdeye (requires BIRDEYE_API_KEY)."""
    birdeye_key = os.getenv("BIRDEYE_API_KEY")
    if not birdeye_key:
        return {"error": "BIRDEYE_API_KEY not configured. Set it in MCP Panel > Config tab.", "token_address": token_address, "setup_url": "/mcp/"}

    try:
        r = requests.get(BIRDEYE_API + "/defi/v3/token/profile",
            headers={"X-API-KEY": birdeye_key},
            params={"address": token_address}, timeout=10)
        if r.status_code == 200:
            d = r.json().get("data", {})
            return {
                "token_address": token_address,
                "name": d.get("name", ""),
                "symbol": d.get("symbol", ""),
                "description": (d.get("description") or "")[:200],
                "has_website": bool(d.get("website")),
                "has_twitter": bool(d.get("twitter")),
                "has_telegram": bool(d.get("telegram")),
                "source": "birdeye",
            }
    except Exception as e:
        return {"error": str(e), "token_address": token_address}

    return {"token_address": token_address, "error": "Birdeye API returned an error. Check your BIRDEYE_API_KEY in MCP Panel > Config.", "setup_url": "/mcp/"}


async def tool_get_token_security(token_address: str) -> dict:
    """Get token security metrics from Birdeye (holders, top holder %, creator %)."""
    birdeye_key = os.getenv("BIRDEYE_API_KEY")
    if not birdeye_key:
        return {"error": "BIRDEYE_API_KEY not configured. Set it in MCP Panel > Config tab.", "token_address": token_address, "setup_url": "/mcp/"}

    try:
        r = requests.get(BIRDEYE_API + "/defi/v3/token/security",
            headers={"X-API-KEY": birdeye_key},
            params={"address": token_address}, timeout=10)
        if r.status_code == 200:
            d = r.json().get("data", {})
            top10 = d.get("top10HolderPercent", 0)
            holders = d.get("holderCount", 0)
            creator = d.get("creatorPercent", 0)
            risk = "high" if top10 and top10 > 50 else "medium" if top10 and top10 > 30 else "low"
            return {
                "token_address": token_address,
                "holder_count": holders,
                "top_10_holder_pct": top10,
                "creator_pct": creator,
                "risk_level": risk,
                "source": "birdeye",
            }
    except Exception as e:
        return {"error": str(e), "token_address": token_address}

    return {"token_address": token_address, "error": "Birdeye API returned an error. Check your BIRDEYE_API_KEY in MCP Panel > Config.", "setup_url": "/mcp/"}


async def tool_get_whale_data(token_address: str) -> dict:
    """Get whale/large holder data from Solana RPC (FREE, no key)."""
    try:
        r = requests.post(SOLANA_RPC, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address]
        }, timeout=10)
        if r.status_code == 200:
            accts = r.json().get("result", {}).get("value", [])
            if accts:
                holders = len(accts)
                risk = "high" if holders < 10 else "medium" if holders < 20 else "low"
                return {
                    "token_address": token_address,
                    "large_holders": holders,
                    "risk_level": risk,
                    "top_accounts": [
                        {"address": a.get("address", "")[:12] + "...",
                         "amount": float(a.get("amount", 0)),
                         "decimals": a.get("decimals", 0)}
                        for a in accts[:5]
                    ],
                    "source": "solana_rpc",
                }
    except Exception as e:
        return {"error": str(e), "token_address": token_address}

    return {"token_address": token_address, "error": "Solana RPC error"}


async def tool_get_liquidity_check(token_address: str, amount_sol: float = 0.01) -> dict:
    """Check liquidity depth via Jupiter quote (FREE, no key)."""
    try:
        resp = requests.get(JUPITER_QUOTE, params={
            "inputMint": SOL_MINT, "outputMint": token_address,
            "amount": str(int(amount_sol * 1e9)),
            "slippageBps": 500,
        }, timeout=10)
        if resp.status_code == 200:
            q = resp.json()
            pi = float(q.get("priceImpactPct", 0))
            out = int(q.get("outAmount", 0))
            return {
                "token_address": token_address,
                "amount_sol": amount_sol,
                "output_tokens": out,
                "price_impact_pct": round(pi, 4),
                "tradeable": out > 0,
                "good_entry": abs(pi) < 5.0,
                "source": "jupiter",
            }
    except Exception as e:
        return {"error": str(e), "token_address": token_address}

    return {"token_address": token_address, "error": "Jupiter API error"}


async def tool_get_portfolio_state() -> dict:
    """Get current portfolio state from PaperTrader."""
    try:
        from src.paper_trader import PaperTrader
        # Read paper trades log directly (no need to instantiate full trader)
        trades_path = DATA_DIR / "paper_trading" / "paper_trades.jsonl"
        if not trades_path.exists():
            return {"error": "No paper trading data found"}

        total = wins = losses = 0
        total_pnl = 0.0
        open_positions = []
        closed_trades = []

        for line in open(trades_path):
            try:
                t = json.loads(line)
                if t.get("action") == "entry" and t.get("status") == "open":
                    open_positions.append({
                        "symbol": t.get("symbol", ""),
                        "amount_usd": t.get("amount_usd", 0),
                        "entry_price": t.get("entry_price", 0),
                        "entry_time": t.get("entry_time", ""),
                        "score": t.get("score", 0),
                    })
                elif t.get("action") == "exit":
                    pnl = t.get("pnl_usd", 0)
                    total += 1
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    closed_trades.append({
                        "symbol": t.get("symbol", ""),
                        "pnl_usd": round(pnl, 4),
                        "pnl_pct": t.get("pnl_pct", 0),
                        "status": t.get("status", ""),
                    })
            except json.JSONDecodeError:
                continue

        # Recent closed trades (last 10)
        recent = closed_trades[-10:] if closed_trades else []

        return {
            "open_positions": open_positions,
            "open_count": len(open_positions),
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(total_pnl, 4),
            "recent_trades": recent,
            "source": "paper_trader",
        }
    except Exception as e:
        return {"error": str(e)}


async def tool_get_recent_trades(symbol: str = "", limit: int = 10) -> dict:
    """Get recent trade history from paper_trades.jsonl."""
    trades_path = DATA_DIR / "paper_trading" / "paper_trades.jsonl"
    if not trades_path.exists():
        return {"trades": [], "count": 0}

    trades = []
    try:
        for line in open(trades_path):
            try:
                t = json.loads(line)
                if symbol and t.get("symbol", "").upper() != symbol.upper():
                    continue
                trades.append({
                    "action": t.get("action", ""),
                    "symbol": t.get("symbol", ""),
                    "amount_usd": t.get("amount_usd", 0),
                    "pnl_usd": t.get("pnl_usd"),
                    "pnl_pct": t.get("pnl_pct"),
                    "status": t.get("status", ""),
                    "timestamp": t.get("timestamp", ""),
                    "score": t.get("score", 0),
                })
            except json.JSONDecodeError:
                continue
    except Exception as e:
        return {"error": str(e)}

    return {
        "trades": trades[-limit:],
        "count": len(trades),
        "symbol_filter": symbol,
        "source": "paper_trades",
    }


async def tool_get_scanner_results(limit: int = 20) -> dict:
    """Get latest scanner results and scores."""
    results_path = DATA_DIR / "scanner" / "scanner_results.jsonl"
    if not results_path.exists():
        return {"results": [], "count": 0}

    results = []
    try:
        for line in open(results_path):
            try:
                r = json.loads(line)
                results.append({
                    "symbol": r.get("symbol", ""),
                    "address": r.get("address", ""),
                    "score": r.get("score", 0),
                    "price_usd": r.get("price_usd", 0),
                    "volume_24h": r.get("volume_24h", 0),
                    "liquidity_usd": r.get("liquidity_usd", 0),
                    "market_cap": r.get("market_cap", 0),
                    "price_change_1h": r.get("price_change_1h", 0),
                    "txns_1h_buys": r.get("txns_1h_buys", 0),
                    "txns_1h_sells": r.get("txns_1h_sells", 0),
                    "signals": r.get("signals", []),
                    "timestamp": r.get("timestamp", ""),
                })
            except json.JSONDecodeError:
                continue
    except Exception as e:
        return {"error": str(e)}

    # Sort by score descending, return top N
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {
        "results": results[-limit:],
        "count": len(results),
        "source": "token_scanner",
    }


async def tool_get_risk_state() -> dict:
    """Get current risk state from RiskGuard rejection log."""
    rejections_path = DATA_DIR / "risk_rejections.csv"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rejections_today = 0
    total_rejections = 0
    rejection_reasons = {}

    if rejections_path.exists():
        try:
            import pandas as pd
            df = pd.read_csv(rejections_path)
            if not df.empty and "timestamp" in df.columns:
                total_rejections = len(df)
                # Filter today's rejections
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                today_rej = df[df["timestamp"].dt.strftime("%Y-%m-%d") == today]
                rejections_today = len(today_rej)
                # Count by stage
                if "stage" in df.columns:
                    reasons = df["stage"].value_counts().to_dict()
                    rejection_reasons = {k: int(v) for k, v in reasons.items()}
        except Exception:
            pass

    return {
        "rejections_today": rejections_today,
        "total_rejections": total_rejections,
        "rejection_by_stage": rejection_reasons,
        "date": today,
        "source": "risk_guard",
    }


async def tool_get_token_sentiment(symbol: str) -> dict:
    """Get per-token sentiment from lightweight analyzer."""
    try:
        from src.lightweight_sentiment import get_lightweight_sentiment
        sent = get_lightweight_sentiment()
        data = sent.get_token_sentiment(symbol)
        if data:
            return {
                "symbol": symbol,
                "score": data.get("score", 0),
                "label": data.get("label", "neutral"),
                "tweet_count": data.get("tweet_count", 0),
                "positive_pct": data.get("positive_pct", 0),
                "negative_pct": data.get("negative_pct", 0),
                "source": "lightweight_sentiment",
            }
    except Exception:
        pass
    return {"symbol": symbol, "error": "Sentiment data not available"}


async def tool_get_market_context() -> dict:
    """Get broader market context — BTC/SOL/ETH prices and market sentiment."""
    context = {}

    # Get BTC/SOL/ETH prices from Jupiter
    try:
        btc_addr = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"
        sol_addr = SOL_MINT
        eth_addr = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"

        # Jupiter price API
        ids = f"{btc_addr},{eth_addr}"
        resp = requests.get(JUPITER_PRICE, params={"ids": ids}, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            if btc_addr in data:
                context["btc_price"] = float(data[btc_addr].get("price", 0))
            if eth_addr in data:
                context["eth_price"] = float(data[eth_addr].get("price", 0))
    except Exception:
        pass

    # SOL price (native)
    try:
        resp = requests.get(JUPITER_PRICE, params={"ids": sol_addr}, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get(sol_addr, {})
            if data:
                context["sol_price"] = float(data.get("price", 0))
    except Exception:
        pass

    # Overall market sentiment
    try:
        sent_path = DATA_DIR / "sentiment_history.csv"
        if sent_path.exists():
            import pandas as pd
            df = pd.read_csv(sent_path)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                recent = df[df["timestamp"] > cutoff]
                if not recent.empty:
                    avg = float(recent["sentiment_score"].mean())
                    context["market_sentiment_24h"] = round(avg, 3)
                    context["sentiment_label"] = "positive" if avg > 0.1 else "negative" if avg < -0.1 else "neutral"
    except Exception:
        pass

    context["source"] = "jupiter+sentiment"
    return context


async def tool_get_strategy_signals() -> dict:
    """Get latest orchestrator decisions / strategy signals."""
    events_path = DATA_DIR / "orchestrator" / "orchestrator_events.jsonl"
    if not events_path.exists():
        return {"signals": [], "count": 0}

    signals = []
    try:
        for line in open(events_path):
            try:
                e = json.loads(line)
                if e.get("type") == "orchestrator/decision":
                    d = e.get("data", {})
                    signals.append({
                        "symbol": d.get("symbol", ""),
                        "action": d.get("action", ""),
                        "confidence": d.get("confidence", 0),
                        "reason": d.get("reason", "")[:150],
                        "source": d.get("source", "algorithmic"),
                        "ai_confidence": d.get("ai_analysis", {}).get("confidence"),
                        "entry_quality": d.get("ai_analysis", {}).get("entry_quality"),
                        "risk_quality": d.get("ai_analysis", {}).get("risk_quality"),
                        "timestamp": d.get("timestamp", ""),
                    })
            except json.JSONDecodeError:
                continue
    except Exception as e:
        return {"error": str(e)}

    return {
        "signals": signals[-20:],
        "count": len(signals),
        "source": "agent_orchestrator",
    }


# ── Wallet Intelligence Tool Implementations ─────────────────

async def tool_get_wallet_activity(wallet_address: str, hours: int = 24) -> dict:
    """Get recent swap activity for a tracked wallet."""
    try:
        from src.wallet_tracker import WalletTracker
        tracker = WalletTracker()
        activity = tracker.get_wallet_activity(wallet_address, hours=hours)
        
        # Enrich with basic stats
        buys = [a for a in activity if a.get('direction') == 'buy']
        sells = [a for a in activity if a.get('direction') == 'sell']
        
        return {
            'wallet': wallet_address[:12] + '...',
            'activity_count': len(activity),
            'buys': len(buys),
            'sells': len(sells),
            'total_buy_sol': round(sum(a.get('amount_sol', 0) for a in buys), 4),
            'total_sell_sol': round(sum(a.get('amount_sol', 0) for a in sells), 4),
            'tokens_traded': list(set(a.get('token_address', '') for a in activity)),
            'recent_activity': activity[-10:],
            'source': 'wallet_tracker',
        }
    except Exception as e:
        return {'wallet': wallet_address, 'error': str(e)}


async def tool_get_wallet_score(wallet_address: str) -> dict:
    """Get quality score for a wallet."""
    try:
        from src.wallet_scorer import WalletScorer
        scorer = WalletScorer()
        score = scorer.get_score(wallet_address)
        
        if score:
            return {
                'wallet': wallet_address[:12] + '...',
                'score': score.score,
                'grade': score.grade,
                'win_rate': score.win_rate,
                'avg_roi_pct': score.avg_roi_pct,
                'max_drawdown_pct': score.max_drawdown_pct,
                'trade_count': score.trade_count,
                'profit_factor': score.profit_factor,
                'consistency': score.consistency_score,
                'confidence': score.confidence,
                'source': 'wallet_scorer',
            }
        else:
            return {
                'wallet': wallet_address[:12] + '...',
                'score': None,
                'error': 'Insufficient data to score wallet (need 5+ trades)',
                'source': 'wallet_scorer',
            }
    except Exception as e:
        return {'wallet': wallet_address, 'error': str(e)}


async def tool_get_smart_money_flow(token_address: str) -> dict:
    """Get smart money consensus signals for a token."""
    try:
        from src.smart_money_detector import SmartMoneyDetector
        detector = SmartMoneyDetector()
        signals = detector.get_token_smart_money(token_address)
        recent = detector.get_recent_signals(hours=1)
        
        # Filter for this token
        token_signals = [s for s in recent if s.get('token_address') == token_address]
        
        if token_signals:
            latest = token_signals[-1]
            return {
                'token': token_address[:8] + '...',
                'smart_money_buying': latest.get('wallets_buying', 0),
                'smart_money_selling': latest.get('wallets_selling', 0),
                'aggregate_buy_sol': latest.get('aggregate_buy_sol', 0),
                'aggregate_sell_sol': latest.get('aggregate_sell_sol', 0),
                'avg_wallet_score': latest.get('avg_wallet_score', 0),
                'confidence': latest.get('confidence', 0),
                'signal': 'BUY' if latest.get('wallets_buying', 0) > latest.get('wallets_selling', 0) else 'SELL',
                'source': 'smart_money_detector',
            }
        else:
            return {
                'token': token_address[:8] + '...',
                'smart_money_buying': 0,
                'smart_money_selling': 0,
                'signal': 'NONE',
                'message': 'No recent smart money activity for this token',
                'source': 'smart_money_detector',
            }
    except Exception as e:
        return {'token': token_address, 'error': str(e)}


async def tool_get_wallet_stats() -> dict:
    """Get aggregate wallet tracker statistics."""
    try:
        from src.wallet_tracker import WalletTracker
        from src.wallet_scorer import WalletScorer
        from src.smart_money_detector import SmartMoneyDetector
        
        tracker = WalletTracker()
        scorer = WalletScorer()
        detector = SmartMoneyDetector()
        
        tracker_stats = tracker.get_stats()
        scorer_stats = scorer.get_stats()
        detector_stats = detector.get_stats()
        
        return {
            'tracker': tracker_stats,
            'scorer': scorer_stats,
            'detector': detector_stats,
            'source': 'wallet_intelligence',
        }
    except Exception as e:
        return {'error': str(e)}


# ── Registry Factory ──────────────────────────────────────────

def create_default_mcp_registry() -> MCPRegistry:
    """
    Create MCP registry with real tool implementations.

    Each tool calls actual data sources — no simulated responses.
    All tools are READ-ONLY (no execution through MCP).
    """
    registry = MCPRegistry()

    # ── Token Market Data (DexScreener/Jupiter) ──────────────
    registry.register_tool(TradingMCPTool(
        name="get_token_price",
        description="Get current token price in USD from Jupiter Price API. Returns price and confidence score.",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
        ],
        execute_fn=tool_get_token_price,
        source="jupiter",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_token_profile",
        description="Get token name, description, and social links from Birdeye.",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
        ],
        execute_fn=tool_get_token_profile,
        source="birdeye",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_token_security",
        description="Get token security metrics: holder count, top holder concentration, creator allocation.",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
        ],
        execute_fn=tool_get_token_security,
        source="birdeye",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_whale_data",
        description="Get whale/large holder data from Solana blockchain RPC.",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
        ],
        execute_fn=tool_get_whale_data,
        source="solana_rpc",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_liquidity_check",
        description="Check liquidity depth and price impact for a trade via Jupiter.",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
            ToolParameter("amount_sol", "number", False, 0.01, "Trade size in SOL"),
        ],
        execute_fn=tool_get_liquidity_check,
        source="jupiter",
    ))

    # ── Portfolio & Trade State ───────────────────────────────
    registry.register_tool(TradingMCPTool(
        name="get_portfolio_state",
        description="Get current portfolio: open positions, total PnL, win rate, recent trades.",
        parameters=[],
        execute_fn=tool_get_portfolio_state,
        source="paper_trader",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_recent_trades",
        description="Get recent trade history. Optionally filter by token symbol.",
        parameters=[
            ToolParameter("symbol", "string", False, "", "Filter by token symbol (empty = all)"),
            ToolParameter("limit", "integer", False, 10, "Max trades to return"),
        ],
        execute_fn=tool_get_recent_trades,
        source="paper_trades",
    ))

    # ── Scanner & Signals ─────────────────────────────────────
    registry.register_tool(TradingMCPTool(
        name="get_scanner_results",
        description="Get latest token scanner results with scores, volumes, and liquidity.",
        parameters=[
            ToolParameter("limit", "integer", False, 20, "Max results to return"),
        ],
        execute_fn=tool_get_scanner_results,
        source="token_scanner",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_strategy_signals",
        description="Get latest AI orchestrator decisions and strategy signals.",
        parameters=[],
        execute_fn=tool_get_strategy_signals,
        source="agent_orchestrator",
    ))

    # ── Risk & Safety ─────────────────────────────────────────
    registry.register_tool(TradingMCPTool(
        name="get_risk_state",
        description="Get current risk state: rejections today, rejection patterns by guard stage.",
        parameters=[],
        execute_fn=tool_get_risk_state,
        source="risk_guard",
    ))

    # ── Sentiment & Context ───────────────────────────────────
    registry.register_tool(TradingMCPTool(
        name="get_token_sentiment",
        description="Get Twitter/X sentiment score for a specific token.",
        parameters=[
            ToolParameter("symbol", "string", True, description="Token symbol (e.g. FART, AI16Z)"),
        ],
        execute_fn=tool_get_token_sentiment,
        source="lightweight_sentiment",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_market_context",
        description="Get broader market context: BTC/SOL/ETH prices and 24h sentiment.",
        parameters=[],
        execute_fn=tool_get_market_context,
        source="jupiter+sentiment",
    ))

    # ── Wallet Intelligence (Smart Money) ─────────────────────
    registry.register_tool(TradingMCPTool(
        name="get_wallet_activity",
        description="Get recent swap activity for a tracked wallet (buys/sells).",
        parameters=[
            ToolParameter("wallet_address", "string", True, description="Solana wallet address to query"),
            ToolParameter("hours", "integer", False, 24, "Lookback hours"),
        ],
        execute_fn=tool_get_wallet_activity,
        source="wallet_tracker",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_wallet_score",
        description="Get the quality score for a wallet (PnL, win rate, drawdown, consistency).",
        parameters=[
            ToolParameter("wallet_address", "string", True, description="Solana wallet address"),
        ],
        execute_fn=tool_get_wallet_score,
        source="wallet_scorer",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_smart_money_flow",
        description="Get smart money consensus signals for a token (are profitable wallets buying?).",
        parameters=[
            ToolParameter("token_address", "string", True, description="Solana token mint address"),
        ],
        execute_fn=tool_get_smart_money_flow,
        source="smart_money_detector",
    ))

    registry.register_tool(TradingMCPTool(
        name="get_wallet_stats",
        description="Get aggregate wallet tracker stats: total tracked, events 24h, buy/sell ratio.",
        parameters=[],
        execute_fn=tool_get_wallet_stats,
        source="wallet_tracker",
    ))

    # ── TradingView Data Feed Tools ─────────────────────────
    from src.tradingview_feed import (
        tool_tv_get_analysis, tool_tv_multi_analysis,
        tool_tv_scan_market, tool_tv_search_symbol, tool_tv_market_overview,
        tool_tv_get_ohlcv,

    )

    registry.register_tool(TradingMCPTool(
        name="tv_get_analysis",
        description="Get TradingView technical analysis for any asset: RSI, MACD, Bollinger, EMAs, recommendation. Free, no auth.",
        parameters=[
            ToolParameter("symbol", "string", True, description="Ticker symbol (e.g. BTCUSDT, SOLUSDT, AAPL)"),
            ToolParameter("exchange", "string", False, "BINANCE", "Exchange (BINANCE, NASDAQ, etc.)"),
            ToolParameter("screener", "string", False, "crypto", "Screener: crypto, america, forex"),
            ToolParameter("interval", "string", False, "1h", "Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
        ],
        execute_fn=tool_tv_get_analysis,
        source="tradingview",
    ))

    registry.register_tool(TradingMCPTool(
        name="tv_multi_analysis",
        description="Get TradingView analysis for multiple symbols at once.",
        parameters=[
            ToolParameter("symbols", "string", True, description="Comma-separated symbols (e.g. BINANCE:BTCUSDT,BINANCE:ETHUSDT)"),
            ToolParameter("screener", "string", False, "crypto", "Screener: crypto, america, forex"),
            ToolParameter("interval", "string", False, "1h", "Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
        ],
        execute_fn=tool_tv_multi_analysis,
        source="tradingview",
    ))

    registry.register_tool(TradingMCPTool(
        name="tv_scan_market",
        description="Scan global markets using TradingView screener: top crypto/stocks/forex by volume, with RSI, MACD, recommendations.",
        parameters=[
            ToolParameter("market", "string", False, "crypto", "Market: crypto, america, forex"),
            ToolParameter("limit", "integer", False, 20, "Max results"),
            ToolParameter("min_volume", "number", False, 0, "Minimum volume filter"),
        ],
        execute_fn=tool_tv_scan_market,
        source="tradingview",
    ))

    registry.register_tool(TradingMCPTool(
        name="tv_search_symbol",
        description="Search TradingView for any asset symbol.",
        parameters=[
            ToolParameter("query", "string", True, description="Search query (e.g. tesla, bitcoin, gold)"),
        ],
        execute_fn=tool_tv_search_symbol,
        source="tradingview",
    ))

    registry.register_tool(TradingMCPTool(
        name="tv_get_ohlcv",
        description="Get OHLCV candle data for any crypto pair (from Binance, free). Use for charts and backtesting.",
        parameters=[
            ToolParameter("symbol", "string", False, "SOLUSDT", "Trading pair (e.g. BTCUSDT, ETHUSDT, SOLUSDT)"),
            ToolParameter("interval", "string", False, "1h", "Candle interval: 1m, 5m, 15m, 1h, 4h, 1d"),
            ToolParameter("limit", "integer", False, 100, "Number of candles (max 1000)"),
        ],
        execute_fn=tool_tv_get_ohlcv,
        source="binance",
    ))

    registry.register_tool(TradingMCPTool(
        name="tv_market_overview",
        description="Quick overview of BTC, ETH, SOL: price, RSI, MACD, TradingView recommendation.",
        parameters=[],
        execute_fn=tool_tv_market_overview,
        source="tradingview",
    ))

    from src.pine_converter import tool_pine_to_python, tool_pine_explain

    # Pine Script to Python Converter
    registry.register_tool(TradingMCPTool(
        name="pine_to_python",
        description="Convert TradingView Pine Script strategy to backtestable Python code using AI.",
        parameters=[
            ToolParameter("pine_script", "string", True, description="Pine Script strategy code"),
            ToolParameter("strategy_name", "string", False, "", "Strategy name (auto-detected if empty)"),
        ],
        execute_fn=tool_pine_to_python,
        source="bedrock_llm",
    ))

    registry.register_tool(TradingMCPTool(
        name="pine_explain",
        description="Explain a Pine Script strategy in plain English (indicators, entry/exit rules, risk management).",
        parameters=[
            ToolParameter("pine_script", "string", True, description="Pine Script strategy code"),
        ],
        execute_fn=tool_pine_explain,
        source="bedrock_llm",
    ))

    print("[MCP] Trading MCP Registry initialized with " + str(len(registry.list_tool_names())) + " tools")
    print("[MCP] Tools: " + ", ".join(registry.list_tool_names()))

    return registry
