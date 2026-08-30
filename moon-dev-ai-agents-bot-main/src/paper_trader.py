"""Paper Trading Mode for micro-cap trading engine."""
import json, time, requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class PaperTrade:
    token_address: str
    symbol: str
    side: str
    amount_usd: float
    entry_price: float = 0.0
    exit_price: float = 0.0
    token_amount: float = 0.0
    slippage_pct: float = 0.0
    price_impact_pct: float = 0.0
    entry_time: str = ""
    exit_time: str = ""
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"
    score: float = 0.0
    signals: list = field(default_factory=list)
    category: str = "unknown"
    stop_loss_pct: float = 10.0
    take_profit_pct: float = 30.0
    max_hold_hours: float = 12.0

    def to_dict(self):
        return {
            "token_address": self.token_address, "symbol": self.symbol,
            "side": self.side, "amount_usd": self.amount_usd,
            "entry_price": self.entry_price, "exit_price": self.exit_price,
            "token_amount": self.token_amount,
            "slippage_pct": self.slippage_pct,
            "price_impact_pct": self.price_impact_pct,
            "entry_time": self.entry_time, "exit_time": self.exit_time,
            "pnl_usd": self.pnl_usd, "pnl_pct": self.pnl_pct,
            "status": self.status, "score": self.score,
            "signals": self.signals, "category": self.category,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_hold_hours": self.max_hold_hours,
        }


class PaperTrader:
    """Simulate trades with real market data. No money at risk."""

    def __init__(self, capital=100.0, max_positions=8):
        self.capital = capital
        self.initial_capital = capital
        self.max_positions = max_positions
        self.open_positions = {}
        self.closed_trades = []
        self.data_dir = Path("src/data/paper_trading")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_real_price(self, token_address, amount_sol=None):
        """Get current token price from Jupiter.
        Uses the same SOL amount as buy() by default for consistent pricing.
        """
        if amount_sol is None:
            amount_sol = 1.0  # Standard reference amount for price quotes
        try:
            resp = requests.get(JUPITER_QUOTE, params={
                "inputMint": SOL_MINT, "outputMint": token_address,
                "amount": str(int(amount_sol * 1e9)), "slippageBps": 500}, timeout=10)
            if resp.status_code == 200:
                q = resp.json()
                out = int(q.get("outAmount", 0))
                if out > 0:
                    return (amount_sol * 15.0) / out
        except Exception:
            pass
        return None

    def get_quote(self, token_address, amount_sol=0.01):
        try:
            resp = requests.get(JUPITER_QUOTE, params={
                "inputMint": SOL_MINT, "outputMint": token_address,
                "amount": str(int(amount_sol * 1e9)), "slippageBps": 500}, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def buy(self, token_address, symbol, amount_usd, score=0.0, signals=None, category="unknown", stop_loss_pct=10.0, take_profit_pct=30.0, max_hold_hours=12.0):
        if len(self.open_positions) >= self.max_positions:
            return None
        if token_address in self.open_positions:
            return None
        if amount_usd > self.capital:
            return None
        amount_sol = amount_usd / 15.0
        quote = self.get_quote(token_address, amount_sol)
        if not quote:
            return None
        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0))
        if out_amount <= 0:
            return None
        slippage = abs(price_impact) + 0.002
        entry_price = amount_usd / out_amount if out_amount > 0 else 0
        trade = PaperTrade(
            token_address=token_address, symbol=symbol, side="buy",
            amount_usd=amount_usd, entry_price=entry_price,
            token_amount=float(out_amount),
            slippage_pct=round(slippage * 100, 3),
            price_impact_pct=round(price_impact, 3),
            entry_time=datetime.now(timezone.utc).isoformat(),
            score=score, signals=signals or [],
            category=category, stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct, max_hold_hours=max_hold_hours)
        self.capital -= amount_usd
        self.open_positions[token_address] = trade
        self._log_trade(trade, "entry")
        self._save_to_db(trade, "entry")
        print("[PAPER] BUY " + symbol + " $" + str(round(amount_usd, 2)) + " impact=" + str(round(price_impact, 2)) + "%")
        self._print_capital_status()
        return trade

    def sell(self, token_address, reason="manual"):
        if token_address not in self.open_positions:
            return None
        pos = self.open_positions[token_address]
        cur = self.get_real_price(token_address)
        if not cur:
            cur = pos.entry_price
        pnl_pct = ((cur - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0
        pnl_usd = pos.amount_usd * (pnl_pct / 100)
        status_map = {"stop_loss": "closed_sl", "take_profit": "closed_tp", "stale_exit": "closed_stale"}
        status = status_map.get(reason, "closed_manual")
        pos.exit_price = cur
        pos.exit_time = datetime.now(timezone.utc).isoformat()
        pos.pnl_usd = round(pnl_usd, 4)
        pos.pnl_pct = round(pnl_pct, 2)
        pos.status = status
        self.capital += pos.amount_usd + pnl_usd
        self.closed_trades.append(pos)
        del self.open_positions[token_address]
        self._log_trade(pos, "exit")
        self._save_to_db(pos, "exit")
        sign = "+" if pnl_usd >= 0 else ""
        print("[PAPER] SELL " + pos.symbol + " " + sign + "$" + str(round(pnl_usd, 4)) + " (" + sign + str(round(pnl_pct, 1)) + "% - " + reason + ")")
        self._print_capital_status()
        return pos

    def check_exits(self):
        """Check all open positions for exit conditions.
        
        Uses per-category exit parameters stored on each trade.
        Exits triggered by:
        1. Stop loss (pnl <= -stop_loss_pct%)
        2. Take profit (pnl >= take_profit_pct%)
        3. Stale position (held longer than max_hold_hours)
        """
        closed = []
        for addr in list(self.open_positions.keys()):
            pos = self.open_positions[addr]
            cur = self.get_real_price(addr)
            if not cur:
                continue

            # Use per-category params from the trade
            sl = pos.stop_loss_pct
            tp = pos.take_profit_pct
            max_hold = pos.max_hold_hours

            # Force-close stale positions held too long
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time.replace("+00:00", "+00:00"))
                hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
            except Exception:
                hours_held = 0

            pnl = ((cur - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0

            if pnl <= -sl:
                r = self.sell(addr, "stop_loss")
                if r: closed.append(r)
            elif pnl >= tp:
                r = self.sell(addr, "take_profit")
                if r: closed.append(r)
            elif hours_held >= max_hold:
                r = self.sell(addr, "stale_exit")
                if r: closed.append(r)
        return closed

    def _log_trade(self, trade, action):
        log_path = self.data_dir / "paper_trades.jsonl"
        with open(log_path, "a") as f:
            data = {"action": action, "timestamp": datetime.now(timezone.utc).isoformat()}
            data.update(trade.to_dict())
            f.write(json.dumps(data) + chr(10))

    def _save_to_db(self, trade, action):
        """Save trade to PostgreSQL if available."""
        try:
            from src.db_storage import save_trade, update_trade_exit
            trade_dict = trade.to_dict()
            trade_dict["mode"] = "paper"
            if action == "entry":
                save_trade(trade_dict, mode="paper")
            elif action == "exit":
                update_trade_exit(
                    trade.token_address, trade.exit_price,
                    trade.pnl_usd, trade.pnl_pct, trade.status,
                )
        except Exception:
            pass

    def get_stats(self):
        total = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.pnl_usd > 0)
        total_pnl = sum(t.pnl_usd for t in self.closed_trades)
        win_pnl = sum(t.pnl_usd for t in self.closed_trades if t.pnl_usd > 0)
        loss_pnl = sum(t.pnl_usd for t in self.closed_trades if t.pnl_usd < 0)
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.capital, 4),
            "open_positions": len(self.open_positions),
            "total_trades": total,
            "wins": wins, "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(total_pnl, 4),
            "profit_factor": round(win_pnl / abs(loss_pnl), 2) if loss_pnl != 0 else 0,
        }

    def _print_capital_status(self):
        """Print capital status after every trade."""
        s = self.get_stats()
        pnl_sign = "+" if s["total_pnl"] >= 0 else ""
        emoji = "UP" if s["total_pnl"] >= 0 else "DOWN"
        print("")
        print("  CAPITAL STATUS: $" + str(round(s["current_capital"], 2)) +
              " | P&L: " + pnl_sign + "$" + str(round(s["total_pnl"], 2)) +
              " | Trades: " + str(s["total_trades"]) +
              " | Win Rate: " + str(s["win_rate"]) + "% | " + emoji)
        if self.open_positions:
            for addr, pos in self.open_positions.items():
                print("    OPEN: " + pos.symbol + " $" + str(round(pos.amount_usd, 2)))
        print("")

    def print_summary(self):
        s = self.get_stats()
        print("")
        print("=" * 50)
        print("PAPER TRADING SUMMARY")
        print("=" * 50)
        for k, v in s.items():
            print("  " + k + ": " + str(v))
        print("=" * 50)
