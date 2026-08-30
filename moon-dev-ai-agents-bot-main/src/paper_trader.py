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
            "signals": self.signals,
        }


class PaperTrader:
    """Simulate trades with real market data. No money at risk."""

    def __init__(self, capital=25.0, max_positions=3):
        self.capital = capital
        self.initial_capital = capital
        self.max_positions = max_positions
        self.open_positions = {}
        self.closed_trades = []
        self.data_dir = Path("src/data/paper_trading")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_real_price(self, token_address):
        try:
            resp = requests.get(JUPITER_QUOTE, params={
                "inputMint": SOL_MINT, "outputMint": token_address,
                "amount": "100000000", "slippageBps": 500}, timeout=10)
            if resp.status_code == 200:
                q = resp.json()
                out = int(q.get("outAmount", 0))
                if out > 0:
                    return 15.0 / out
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

    def buy(self, token_address, symbol, amount_usd, score=0.0, signals=None):
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
            score=score, signals=signals or [])
        self.capital -= amount_usd
        self.open_positions[token_address] = trade
        self._log_trade(trade, "entry")
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
        status_map = {"stop_loss": "closed_sl", "take_profit": "closed_tp"}
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
        sign = "+" if pnl_usd >= 0 else ""
        print("[PAPER] SELL " + pos.symbol + " " + sign + "$" + str(round(pnl_usd, 4)) + " (" + sign + str(round(pnl_pct, 1)) + "% - " + reason + ")")
        self._print_capital_status()
        return pos

    def check_exits(self, stop_loss_pct=10.0, take_profit_pct=30.0):
        closed = []
        for addr in list(self.open_positions.keys()):
            pos = self.open_positions[addr]
            cur = self.get_real_price(addr)
            if not cur:
                continue
            pnl = ((cur - pos.entry_price) / pos.entry_price * 100) if pos.entry_price > 0 else 0
            if pnl <= -stop_loss_pct:
                r = self.sell(addr, "stop_loss")
                if r: closed.append(r)
            elif pnl >= take_profit_pct:
                r = self.sell(addr, "take_profit")
                if r: closed.append(r)
        return closed

    def _log_trade(self, trade, action):
        log_path = self.data_dir / "paper_trades.jsonl"
        with open(log_path, "a") as f:
            data = {"action": action, "timestamp": datetime.now(timezone.utc).isoformat()}
            data.update(trade.to_dict())
            f.write(json.dumps(data) + chr(10))

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
