"""
Moon Dev Telegram Reporter — Trade notifications, portfolio reports, and bot commands.

Commands:
  /status   - Current portfolio, capital, P&L
  /trades   - Recent trade history
  /open     - Open positions
  /summary  - Daily summary
  /help     - List commands

Setup:
  1. Talk to @BotFather on Telegram -> /newbot -> get your BOT_TOKEN
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
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class TelegramReporter:
    """Sends trade notifications and responds to commands via Telegram."""

    def __init__(self):
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.enabled else ""

        # Portfolio tracking
        self.portfolio_path = Path("src/data/telegram_portfolio.json")
        self.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
        self.portfolio = self._load_portfolio()

        # Capital tracking (set by engine)
        self.initial_capital = 0.0
        self.current_capital = 0.0

        # Command polling
        self._last_update_id = 0
        self._polling_thread = None
        self._paper_trader = None  # Set by engine

        if self.enabled:
            print("[TG] Telegram Reporter enabled — waiting for commands")
            self._start_command_polling()
        else:
            print("[TG] Telegram Reporter disabled (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    def set_paper_trader(self, paper_trader):
        """Link to the paper trader for real-time stats."""
        self._paper_trader = paper_trader

    def set_capital(self, initial: float, current: float):
        """Update capital info from engine."""
        self.initial_capital = initial
        self.current_capital = current

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

    # ── Command Polling ──────────────────────────────────────

    def _start_command_polling(self):
        """Start background thread to listen for Telegram commands."""
        def poll():
            while True:
                try:
                    self._poll_commands()
                except Exception as e:
                    print(f"[TG] Poll error: {e}")
                time.sleep(2)

        self._polling_thread = threading.Thread(target=poll, daemon=True)
        self._polling_thread.start()

    def _poll_commands(self):
        """Check for new Telegram messages and handle commands."""
        if not self.enabled:
            return
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self._last_update_id + 1, "timeout": 1},
                timeout=5,
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            for update in data.get("result", []):
                self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                # Only respond to our chat
                if chat_id != self.chat_id:
                    continue

                if text == "/status":
                    self._cmd_status()
                elif text == "/trades":
                    self._cmd_trades()
                elif text == "/open":
                    self._cmd_open()
                elif text == "/summary":
                    self._cmd_summary()
                elif text == "/capital":
                    self._cmd_capital()
                elif text == "/help":
                    self._cmd_help()
                elif text.startswith("/"):
                    self.send("Unknown command. Type /help for options.")
        except Exception:
            pass

    def _cmd_status(self):
        """Handle /status command."""
        if self._paper_trader:
            stats = self._paper_trader.get_stats()
        else:
            stats = self.portfolio

        pnl = stats.get("total_pnl", 0)
        current = stats.get("current_capital", self.current_capital)
        initial = stats.get("initial_capital", self.initial_capital)
        pnl_sign = "+" if pnl >= 0 else ""
        emoji = "GREEN" if pnl >= 0 else "RED"

        text = (
            f"PORTFOLIO STATUS\n"
            f"================\n\n"
            f"P&L: {pnl_sign}${pnl:.2f} ({emoji})\n"
            f"Capital: ${current:.2f} (started ${initial:.2f})\n"
            f"Trades: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0)}%\n"
            f"Wins/Losses: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
            f"Open Positions: {stats.get('open_positions', 0)}\n\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        self.send(text)

    def _cmd_trades(self):
        """Handle /trades command."""
        history = self.portfolio.get("history", [])
        recent = history[-10:]

        if not recent:
            self.send("No trades yet. The engine is scanning for opportunities.")
            return

        text = "RECENT TRADES\n=============\n\n"
        for t in reversed(recent):
            pnl = t.get("pnl_usd", 0)
            sign = "+" if pnl >= 0 else ""
            e = "+" if pnl >= 0 else "-"
            text += f"{t['symbol']}: {sign}${pnl:.2f} ({t.get('reason', 'exit')})\n"

        text += f"\nTotal: {len(history)} trades"
        self.send(text)

    def _cmd_open(self):
        """Handle /open command."""
        if self._paper_trader and self._paper_trader.open_positions:
            text = "OPEN POSITIONS\n==============\n\n"
            for addr, pos in self._paper_trader.open_positions.items():
                text += f"{pos.symbol}: ${pos.amount_usd:.2f}\n"
                text += f"  Entry: {pos.entry_time[:16]}\n"
                text += f"  Score: {pos.score}/100\n\n"
            self.send(text)
        else:
            self.send("No open positions.")

    def _cmd_summary(self):
        """Handle /summary command."""
        self.report_daily_summary()

    def _cmd_capital(self):
        """Handle /capital command."""
        if self._paper_trader:
            stats = self._paper_trader.get_stats()
            current = stats.get("current_capital", self.current_capital)
            initial = stats.get("initial_capital", self.initial_capital)
            change = current - initial
            sign = "+" if change >= 0 else ""
            text = (
                f"CAPITAL STATUS\n==============\n\n"
                f"Starting: ${initial:.2f}\n"
                f"Current: ${current:.2f}\n"
                f"Change: {sign}${change:.2f}\n"
            )
            if self._paper_trader.open_positions:
                invested = sum(p.amount_usd for p in self._paper_trader.open_positions.values())
                text += f"Invested: ${invested:.2f}\n"
            self.send(text)
        else:
            self.send(f"Capital: ${self.current_capital:.2f}")

    def _cmd_help(self):
        """Handle /help command."""
        text = (
            "COMMANDS\n========\n\n"
            "/status - Portfolio overview\n"
            "/trades - Recent trade history\n"
            "/open - Open positions\n"
            "/capital - Capital details\n"
            "/summary - Daily summary\n"
            "/help - This message\n\n"
            "The bot also sends automatic alerts for:\n"
            "- Trade entries and exits\n"
            "- System errors\n"
            "- Heartbeat every 30 minutes"
        )
        self.send(text)

    # ── Trade Notifications ──────────────────────────────────

    def notify_entry(self, symbol: str, amount_usd: float, price: float = 0,
                     score: int = 0, ai_confidence: float = 0, mode: str = "paper"):
        """Notify when a trade is opened."""
        mode_emoji = "PAPER" if mode == "paper" else "LIVE"
        text = (
            f"ENTRY [{mode_emoji}] {symbol}\n\n"
            f"Amount: ${amount_usd:.2f}\n"
        )
        if price > 0:
            text += f"Price: ${price:.6f}\n"
        if score > 0:
            text += f"Score: {score}/100\n"
        if ai_confidence > 0:
            text += f"AI Confidence: {ai_confidence:.0%}\n"
        text += f"Capital: ${self.current_capital:.2f}\n"
        text += f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"

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
        profit_emoji = "WIN" if pnl_usd >= 0 else "LOSS"
        pnl_sign = "+" if pnl_usd >= 0 else ""

        text = (
            f"EXIT [{profit_emoji}] {symbol}\n\n"
            f"Returned: ${amount_usd:.2f}\n"
            f"P&L: {pnl_sign}${pnl_usd:.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
            f"Reason: {reason}\n"
            f"Capital: ${self.current_capital:.2f}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
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
            return

        text = (
            f"Scan #{scan_num}\n"
            f"Candidates: {candidates}\n"
            f"AI Approved: {ai_approved}\n"
            f"AI Rejected: {ai_rejected}"
        )
        self.send(text)

    def notify_error(self, error: str, context: str = ""):
        """Notify on critical errors."""
        text = f"ERROR: {error[:500]}"
        if context:
            text += f"\nContext: {context}"
        self.send(text)

    def notify_heartbeat(self):
        """Send periodic heartbeat with capital status."""
        if self._paper_trader:
            stats = self._paper_trader.get_stats()
            current = stats.get("current_capital", self.current_capital)
            pnl = stats.get("total_pnl", 0)
        else:
            current = self.current_capital
            pnl = 0

        pnl_sign = "+" if pnl >= 0 else ""
        open_count = len(self._paper_trader.open_positions) if self._paper_trader else 0

        text = (
            f"HEARTBEAT\n"
            f"Capital: ${current:.2f}\n"
            f"P&L: {pnl_sign}${pnl:.2f}\n"
            f"Open: {open_count} positions\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        self.send(text)

    # ── Portfolio Reports ────────────────────────────────────

    def report_portfolio(self) -> str:
        """Generate and send a full portfolio report."""
        if self._paper_trader:
            stats = self._paper_trader.get_stats()
        else:
            stats = self.portfolio

        p = self.portfolio
        win_rate = stats.get("win_rate", 0)
        pnl = stats.get("total_pnl", 0)
        current = stats.get("current_capital", self.current_capital)
        initial = stats.get("initial_capital", self.initial_capital)
        pnl_sign = "+" if pnl >= 0 else ""
        emoji = "GREEN" if pnl >= 0 else "RED"

        text = (
            f"PORTFOLIO REPORT\n"
            f"================\n\n"
            f"P&L: {pnl_sign}${pnl:.2f} ({emoji})\n"
            f"Capital: ${current:.2f} (started ${initial:.2f})\n"
            f"Win Rate: {win_rate}% ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)\n"
            f"Total Trades: {stats.get('total_trades', 0)}\n"
        )

        # Open positions
        if self._paper_trader and self._paper_trader.open_positions:
            text += f"\nOpen Positions ({len(self._paper_trader.open_positions)})\n"
            for addr, pos in self._paper_trader.open_positions.items():
                text += f"  {pos.symbol}: ${pos.amount_usd:.2f}\n"
        else:
            text += "\nNo open positions\n"

        # Recent history
        recent = p["history"][-5:]
        if recent:
            text += "\nRecent Trades\n"
            for trade in reversed(recent):
                pnl_t = trade["pnl_usd"]
                sign = "+" if pnl_t >= 0 else ""
                e = "+" if pnl_t >= 0 else "-"
                text += f"  {trade['symbol']}: {sign}${pnl_t:.2f}\n"

        text += f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

        self.send(text)
        return text

    def report_daily_summary(self):
        """Send end-of-day summary."""
        p = self.portfolio

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_trades = [t for t in p["history"] if t.get("closed_at", "").startswith(today)]
        today_pnl = sum(t["pnl_usd"] for t in today_trades)
        today_wins = sum(1 for t in today_trades if t["pnl_usd"] >= 0)

        pnl_sign = "+" if today_pnl >= 0 else ""
        emoji = "GREEN" if today_pnl >= 0 else "RED"

        text = (
            f"DAILY SUMMARY - {today}\n"
            f"========================\n\n"
            f"Today P&L: {pnl_sign}${today_pnl:.2f} ({emoji})\n"
            f"Trades Today: {len(today_trades)}\n"
            f"Wins: {today_wins}\n"
            f"Losses: {len(today_trades) - today_wins}\n\n"
            f"Overall\n"
            f"  Total P&L: {('+' if p['total_pnl'] >= 0 else '')}${p['total_pnl']:.2f}\n"
            f"  Win Rate: {p['wins']}/{p['wins'] + p['losses']}\n"
        )

        if self._paper_trader and self._paper_trader.open_positions:
            text += f"\nStill holding: {', '.join(pos.symbol for pos in self._paper_trader.open_positions.values())}\n"

        self.send(text)

    def send_startup_message(self, capital: float, mode: str):
        """Notify that the engine has started with DSH architecture health check."""
        self.initial_capital = capital
        self.current_capital = capital

        # ── DSH Architecture Health Check ──
        checks = []

        # 1. Database
        db_ok = False
        db_tables = 0
        try:
            from src.db_storage import get_pool
            pool = get_pool()
            if pool:
                db_ok = True
                with pool.connection() as conn:
                    sql = "SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema = 'public'"
                    row = conn.execute(sql).fetchone()
                    db_tables = row["cnt"] if row else 0
                checks.append(f"PostgreSQL: CONNECTED ({db_tables} tables)")
            else:
                checks.append("PostgreSQL: OFFLINE (JSON fallback)")
        except Exception:
            checks.append("PostgreSQL: ERROR")

        # 2. SessionLog
        try:
            from src.session_log import SessionLog, PGStorage
            pg = PGStorage()
            if pg._get_pool():
                checks.append("SessionLog: PostgreSQL backend")
            else:
                checks.append("SessionLog: CSV fallback")
        except Exception:
            checks.append("SessionLog: ERROR")

        # 3. EventBus
        try:
            from src.event_bus import EventBus, Events
            bus = EventBus()
            checks.append(f"EventBus: OK ({len(Events.__dict__)-2} event types)")
        except Exception:
            checks.append("EventBus: ERROR")

        # 4. AsyncScheduler
        try:
            from src.async_scheduler import AsyncScheduler
            checks.append("AsyncScheduler: OK")
        except Exception:
            checks.append("AsyncScheduler: ERROR")

        # 5. FeedbackLoop
        try:
            from src.feedback_loop import TradeFeedbackLoop
            fl = TradeFeedbackLoop()
            db_status = "DB+JSONL" if fl._db_available else "JSONL only"
            checks.append(f"FeedbackLoop: {db_status}")
        except Exception:
            checks.append("FeedbackLoop: ERROR")

        # 6. ExecutionTracker
        try:
            from src.execution_tracker import ExecutionTracker
            et = ExecutionTracker()
            db_status = "DB+JSONL" if et._db_available else "JSONL only"
            checks.append(f"ExecutionTracker: {db_status}")
        except Exception:
            checks.append("ExecutionTracker: ERROR")

        # 7. MCP Registry
        try:
            from src.mcp_registry import MCPRegistry
            checks.append("MCP Registry: OK")
        except Exception:
            checks.append("MCP Registry: not available")

        # Build message
        checks_text = "\n".join(f"  {c}" for c in checks)
        db_icon = "OK" if db_ok else "FALLBACK"

        text = (
            f"ENGINE STARTED - DSH Architecture Check\n"
            f"========================================\n\n"
            f"Capital: ${capital:.2f}\n"
            f"Mode: {mode.upper()}\n"
            f"Database: {db_icon}\n\n"
            f"DSH Components:\n{checks_text}\n\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Commands: /status /trades /open /capital /help"
        )
        self.send(text)


def get_telegram_reporter() -> TelegramReporter:
    """Get or create the singleton reporter."""
    if not hasattr(get_telegram_reporter, "_instance"):
        get_telegram_reporter._instance = TelegramReporter()
    return get_telegram_reporter._instance
