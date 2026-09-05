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
        # Strict capital check: need amount + 10% buffer for safety
        if amount_usd >= self.capital * 0.90:
            # Scale down to available capital minus buffer
            amount_usd = max(1.0, self.capital * 0.80)
        if amount_usd > self.capital:
            return None
        # Safety floor: never let capital go below $5
        if self.capital - amount_usd < 5.0:
            amount_usd = max(1.0, self.capital - 5.0)
            if amount_usd < 1.0:
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
        # Safety: ensure capital never goes negative
        returned = pos.amount_usd + pnl_usd
        if returned < 0:
            returned = 0  # Worst case: lose entire investment
        self.capital = max(0.0, self.capital + returned)  # Hard floor at $0
        self.closed_trades.append(pos)
        del self.open_positions[token_address]
        self._log_trade(pos, "exit")
        self._save_to_db(pos, "exit")
        self._save_portfolio()
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
            f.write(json.dumps(data, default=str) + chr(10))

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

    def restore_open_trades(self):
        """Restore open positions from DB after a deploy restart.
        Deducts capital for each restored position so cash balance is accurate."""
        try:
            from src.db_storage import get_trades
            open_trades = get_trades(status="open", limit=self.max_positions)
            if not open_trades:
                return
            restored = 0
            total_deducted = 0.0
            for t in open_trades:
                addr = t.get("token_address", "")
                if not addr or addr in self.open_positions:
                    continue
                trade = PaperTrade(
                    token_address=addr,
                    symbol=t.get("symbol", ""),
                    side=t.get("side", "buy"),
                    amount_usd=t.get("amount_usd", 0),
                    entry_price=t.get("entry_price", 0),
                    token_amount=t.get("token_amount", 0),
                    slippage_pct=t.get("slippage_pct", 0),
                    price_impact_pct=t.get("price_impact_pct", 0),
                    entry_time=t.get("entry_time", ""),
                    score=t.get("score", 0),
                    signals=t.get("signals", []) if isinstance(t.get("signals"), list) else [],
                    category=t.get("category", "unknown") if "category" in t else "unknown",
                    stop_loss_pct=t.get("stop_loss_pct", 10.0) if "stop_loss_pct" in t else 10.0,
                    take_profit_pct=t.get("take_profit_pct", 30.0) if "take_profit_pct" in t else 30.0,
                    max_hold_hours=t.get("max_hold_hours", 12.0) if "max_hold_hours" in t else 12.0,
                )
                self.open_positions[addr] = trade
                self.capital -= trade.amount_usd
                total_deducted += trade.amount_usd
                restored += 1
            if restored > 0:
                print("[PAPER] Restored " + str(restored) + " open trade(s) from DB — deducted $" + str(round(total_deducted, 2)) + " from capital")
                self._print_capital_status()
        except Exception as e:
            print("[PAPER] Restore error: " + str(e))

    def reset_capital(self, new_capital: float = None, reason: str = "manual") -> dict:
        """Reset capital back to the initial amount (or a given amount).

        Used by the auto-reset mechanism after a circuit-breaker trip.
        Any open positions are force-closed with status 'capital_reset'
        so the portfolio returns to a clean, fully-cash state.

        Returns {"capital_before", "capital_after", "positions_cleared"}.
        """
        reset_to = new_capital if new_capital is not None else self.initial_capital
        cleared = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for addr, pos in list(self.open_positions.items()):
            try:
                pos.status = "capital_reset"
                pos.exit_time = now_iso
                self.closed_trades.append(pos)
            except Exception:
                pass
            del self.open_positions[addr]
            cleared += 1
        capital_before = self.capital
        self.capital = reset_to
        self._save_portfolio()
        print("[PAPER] Capital reset $" + str(round(capital_before, 2)) + " -> $" +
              str(round(reset_to, 2)) + " (" + reason + "); cleared " + str(cleared) + " open position(s)")
        return {
            "capital_before": round(capital_before, 4),
            "capital_after": round(reset_to, 4),
            "positions_cleared": cleared,
        }

    def get_stats(self):
        total = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.pnl_usd > 0)
        total_pnl = sum(t.pnl_usd for t in self.closed_trades)
        win_pnl = sum(t.pnl_usd for t in self.closed_trades if t.pnl_usd > 0)
        loss_pnl = sum(t.pnl_usd for t in self.closed_trades if t.pnl_usd < 0)
        # Total portfolio value = cash + open positions value
        open_positions_value = sum(pos.amount_usd for pos in self.open_positions.values())
        total_portfolio_value = self.capital + open_positions_value
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.capital, 4),
            "open_positions_value": round(open_positions_value, 4),
            "total_portfolio_value": round(total_portfolio_value, 4),
            "open_positions": len(self.open_positions),
            "total_trades": total,
            "wins": wins, "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(total_pnl, 4),
            "profit_factor": round(win_pnl / abs(loss_pnl), 2) if loss_pnl != 0 else 0,
        }

    def _save_portfolio(self):
        """Save portfolio state to DB after every trade."""
        try:
            from src.db_storage import save_portfolio
            s = self.get_stats()
            save_portfolio(
                initial_capital=s["initial_capital"],
                current_capital=s["current_capital"],
                total_pnl=s["total_pnl"],
                total_trades=s["total_trades"],
                wins=s["wins"],
                losses=s["losses"],
            )
        except Exception:
            pass

    def get_positions_for_risk(self) -> dict:
        """Get all open positions formatted for risk management systems."""
        positions = {}
        for addr, pos in self.open_positions.items():
            cur = self.get_real_price(addr)
            pnl_pct = 0.0
            pnl_usd = 0.0
            if cur and pos.entry_price > 0:
                pnl_pct = ((cur - pos.entry_price) / pos.entry_price * 100)
                pnl_usd = pos.amount_usd * (pnl_pct / 100)
            
            try:
                from datetime import datetime, timezone
                entry_dt = datetime.fromisoformat(pos.entry_time.replace("+00:00", "+00:00"))
                hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
            except Exception:
                hours_held = 0
            
            positions[addr] = {
                "symbol": pos.symbol,
                "amount_usd": pos.amount_usd,
                "entry_price": pos.entry_price,
                "current_price": cur or pos.entry_price,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(pnl_usd, 4),
                "hours_held": round(hours_held, 1),
                "stop_loss_pct": pos.stop_loss_pct,
                "take_profit_pct": pos.take_profit_pct,
                "category": pos.category,
            }
        return positions

    def _print_capital_status(self):
        """Print capital status after every trade."""
        s = self.get_stats()
        pnl_sign = "+" if s["total_pnl"] >= 0 else ""
        emoji = "UP" if s["total_pnl"] >= 0 else "DOWN"
        print("")
        print("  CAPITAL STATUS: Cash=$" + str(round(s["current_capital"], 2)) +
              " | Open=$" + str(round(s["open_positions_value"], 2)) +
              " | Total=$" + str(round(s["total_portfolio_value"], 2)) +
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
