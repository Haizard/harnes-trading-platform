"""
Moon Dev Telegram Reporter — Sends trade notifications and portfolio reports.

Setup:
  1. Talk to @BotFather on Telegram → /newbot → get your BOT_TOKEN
  2. Send a message to your bot, then visit:
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     to find your CHAT_ID
  3. Add to .env:
     TELEGRAM_BOT_TOKEN=your_bot_token
     TELEGRAM_CHAT_ID=your_chat_id
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class TelegramReporter:
    """Sends trade notifications and portfolio reports via Telegram."""

    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.enabled else ""
        
        # Portfolio tracking
        self.portfolio_path = Path("src/data/telegram_portfolio.json")
        self.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
        self.portfolio = self._load_portfolio()
        
        if self.enabled:
            print("[TG] Telegram Reporter enabled")
        else:
            print("[TG] Telegram Reporter disabled (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    def _load_portfolio(self) -> dict:
        """Load portfolio state from disk."""
        if self.portfolio_path.exists():
            try:
                with open(self.portfolio_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "total_invested": 0.0,
            "total_returned": 0.0,
            "total_pnl": 0.0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "open_positions": [],
            "history": [],
        }

    def _save_portfolio(self):
        """Save portfolio state to disk."""
        try:
            with open(self.portfolio_path, "w") as f:
                json.dump(self.portfolio, f, indent=2, default=str)
        except Exception:
            pass

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message via Telegram."""
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[TG] Send failed: {e}")
            return False

    # ── Trade Notifications ──────────────────────────────────

    def notify_entry(self, symbol: str, amount_usd: float, price: float = 0,
                     score: int = 0, ai_confidence: float = 0, mode: str = "paper"):
        """Notify when a trade is opened."""
        mode_emoji = "📝" if mode == "paper" else "💰"
        text = (
            f"{mode_emoji} <b>ENTRY — {symbol}</b>\n\n"
            f"💵 Amount: <code>${amount_usd:.2f}</code>\n"
        )
        if price > 0:
            text += f"💲 Price: <code>${price:.6f}</code>\n"
        if score > 0:
            text += f"📊 Score: {score}/100\n"
        if ai_confidence > 0:
            text += f"🧠 AI Confidence: {ai_confidence:.0%}\n"
        text += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        
        self.send(text)
        
        # Track in portfolio
        self.portfolio["open_positions"].append({
            "symbol": symbol,
            "amount_usd": amount_usd,
            "entry_price": price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "score": score,
            "ai_confidence": ai_confidence,
            "mode": mode,
        })
        self.portfolio["total_invested"] += amount_usd
        self.portfolio["trades"] += 1
        self._save_portfolio()

    def notify_exit(self, symbol: str, amount_usd: float, pnl_usd: float,
                    pnl_pct: float, reason: str = "take_profit", mode: str = "paper"):
        """Notify when a trade is closed."""
        profit_emoji = "🟢" if pnl_usd >= 0 else "🔴"
        pnl_sign = "+" if pnl_usd >= 0 else ""
        
        text = (
            f"{profit_emoji} <b>EXIT — {symbol}</b>\n\n"
            f"💵 Returned: <code>${amount_usd:.2f}</code>\n"
            f"📈 P&L: <code>{pnl_sign}${pnl_usd:.2f} ({pnl_sign}{pnl_pct:.1f}%)</code>\n"
            f"📋 Reason: {reason}\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )
        
        self.send(text)
        
        # Update portfolio
        self.portfolio["total_returned"] += amount_usd + pnl_usd
        self.portfolio["total_pnl"] += pnl_usd
        if pnl_usd >= 0:
            self.portfolio["wins"] += 1
        else:
            self.portfolio["losses"] += 1
        
        # Remove from open positions
        self.portfolio["open_positions"] = [
            p for p in self.portfolio["open_positions"] if p["symbol"] != symbol
        ]
        
        # Add to history
        self.portfolio["history"].append({
            "symbol": symbol,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
        })
        
        # Keep only last 100 history entries
        if len(self.portfolio["history"]) > 100:
            self.portfolio["history"] = self.portfolio["history"][-100:]
        
        self._save_portfolio()

    def notify_scan_summary(self, scan_num: int, candidates: int, 
                            ai_approved: int, ai_rejected: int):
        """Send a brief scan summary (only if there's activity)."""
        if ai_approved == 0 and candidates == 0:
            return  # Don't spam empty scans
        
        text = (
            f"📡 <b>Scan #{scan_num}</b>\n"
            f"🔍 Candidates: {candidates}\n"
            f"✅ AI Approved: {ai_approved}\n"
            f"❌ AI Rejected: {ai_rejected}"
        )
        self.send(text)

    def notify_error(self, error: str, context: str = ""):
        """Notify on critical errors."""
        text = (
            f"🚨 <b>ERROR</b>\n\n"
            f"❌ {error[:500]}"
        )
        if context:
            text += f"\n\n📋 Context: {context}"
        self.send(text)

    # ── Portfolio Reports ────────────────────────────────────

    def report_portfolio(self) -> str:
        """Generate and send a full portfolio report."""
        p = self.portfolio
        win_rate = (p["wins"] / max(p["wins"] + p["losses"], 1)) * 100
        pnl_sign = "+" if p["total_pnl"] >= 0 else ""
        emoji = "🟢" if p["total_pnl"] >= 0 else "🔴"
        
        text = (
            f"📊 <b>PORTFOLIO REPORT</b>\n"
            f"{'═' * 28}\n\n"
            f"{emoji} <b>P&L: {pnl_sign}${p['total_pnl']:.2f}</b>\n\n"
            f"💰 Total Invested: <code>${p['total_invested']:.2f}</code>\n"
            f"💵 Total Returned: <code>${p['total_returned']:.2f}</code>\n"
            f"📈 Win Rate: <code>{win_rate:.1f}%</code> ({p['wins']}W / {p['losses']}L)\n"
            f"📋 Total Trades: {p['trades']}\n"
        )
        
        # Open positions
        if p["open_positions"]:
            text += f"\n📌 <b>Open Positions ({len(p['open_positions'])})</b>\n"
            for pos in p["open_positions"]:
                text += f"  • {pos['symbol']}: ${pos['amount_usd']:.2f}\n"
        else:
            text += "\n📌 <b>No open positions</b>\n"
        
        # Recent history
        recent = p["history"][-5:]
        if recent:
            text += f"\n📜 <b>Recent Trades</b>\n"
            for trade in reversed(recent):
                pnl = trade["pnl_usd"]
                sign = "+" if pnl >= 0 else ""
                e = "🟢" if pnl >= 0 else "🔴"
                text += f"  {e} {trade['symbol']}: {sign}${pnl:.2f}\n"
        
        text += f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        
        self.send(text)
        return text

    def report_daily_summary(self):
        """Send end-of-day summary."""
        p = self.portfolio
        
        # Calculate today's trades
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_trades = [t for t in p["history"] if t.get("closed_at", "").startswith(today)]
        today_pnl = sum(t["pnl_usd"] for t in today_trades)
        today_wins = sum(1 for t in today_trades if t["pnl_usd"] >= 0)
        
        emoji = "🟢" if today_pnl >= 0 else "🔴"
        pnl_sign = "+" if today_pnl >= 0 else ""
        
        text = (
            f"📅 <b>DAILY SUMMARY — {today}</b>\n"
            f"{'═' * 28}\n\n"
            f"{emoji} <b>Today's P&L: {pnl_sign}${today_pnl:.2f}</b>\n"
            f"📊 Trades Today: {len(today_trades)}\n"
            f"✅ Wins: {today_wins}\n"
            f"❌ Losses: {len(today_trades) - today_wins}\n\n"
            f"💰 <b>Overall</b>\n"
            f"  Total P&L: {('+' if p['total_pnl'] >= 0 else '')}${p['total_pnl']:.2f}\n"
            f"  Win Rate: {p['wins']}/{p['wins'] + p['losses']}\n"
        )
        
        if p["open_positions"]:
            text += f"\n📌 Still holding: {', '.join(pos['symbol'] for pos in p['open_positions'])}\n"
        
        self.send(text)

    def send_startup_message(self, capital: float, mode: str):
        """Notify that the engine has started."""
        text = (
            f"🚀 <b>ENGINE STARTED</b>\n\n"
            f"💰 Capital: <code>${capital:.2f}</code>\n"
            f"📋 Mode: <b>{mode.upper()}</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Type /status for portfolio report."
        )
        self.send(text)


def get_telegram_reporter() -> TelegramReporter:
    """Get or create the singleton reporter."""
    if not hasattr(get_telegram_reporter, "_instance"):
        get_telegram_reporter._instance = TelegramReporter()
    return get_telegram_reporter._instance
