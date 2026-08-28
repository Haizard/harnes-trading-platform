"""Micro Sniper for Solana memecoin trading."""
import os, json, time, requests
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from pathlib import Path

SOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP = "https://api.jup.ag/swap/v1/swap"
DEFAULT_CAPITAL = 25.0
MAX_POSITION_PCT = 50.0
STOP_LOSS_PCT = 10.0
TAKE_PROFIT_PCT = 30.0
SLIPPAGE_BPS = 500

@dataclass
class Position:
    token_address: str
    symbol: str
    side: str
    entry_price: float
    amount_usd: float
    token_amount: float = 0.0
    entry_time: datetime = field(default_factory=datetime.utcnow)
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"
    tx_signature: str = ""
    def to_dict(self):
        return {"token_address": self.token_address, "symbol": self.symbol,
                "side": self.side, "entry_price": self.entry_price,
                "amount_usd": self.amount_usd, "token_amount": self.token_amount,
                "entry_time": self.entry_time.isoformat(),
                "exit_price": self.exit_price,
                "exit_time": self.exit_time.isoformat() if self.exit_time else None,
                "pnl_usd": self.pnl_usd, "pnl_pct": self.pnl_pct,
                "status": self.status, "tx_signature": self.tx_signature}

@dataclass
class TradeSignal:
    token_address: str
    symbol: str
    side: str
    amount_usd: float
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    reason: str = ""
    score: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

class JupiterExecutor:
    def __init__(self, private_key=None):
        self.private_key = private_key or os.getenv("SOLANA_PRIVATE_KEY")
    def get_quote(self, input_mint, output_mint, amount):
        try:
            resp = requests.get(JUPITER_QUOTE, params={
                "inputMint": input_mint, "outputMint": output_mint,
                "amount": str(amount), "slippageBps": SLIPPAGE_BPS}, timeout=10)
            if resp.status_code == 200: return resp.json()
        except: pass
        return None
    def get_price(self, token_address):
        try:
            resp = requests.get("https://api.jup.ag/price/v2",
                params={"ids": token_address}, timeout=10)
            if resp.status_code == 200:
                return float(resp.json().get("data", {}).get(token_address, {}).get("price", 0))
        except: pass
        return None
    def execute_swap(self, quote):
        print("[SNIPER] Simulating swap execution")
        return f"simulated_{int(time.time())}"

class MicroPositionSizer:
    def __init__(self, total_capital=DEFAULT_CAPITAL):
        self.total_capital = total_capital
        self.max_position = total_capital * (MAX_POSITION_PCT / 100)
    def calculate_position(self, score, liquidity):
        if score >= 80: base_pct = 0.40
        elif score >= 60: base_pct = 0.25
        elif score >= 40: base_pct = 0.15
        else: base_pct = 0.10
        pos = self.total_capital * base_pct
        pos = min(pos, liquidity * 0.10)
        pos = min(pos, self.max_position)
        return round(max(pos, 1.0), 2)

class MicroRiskManager:
    def __init__(self, total_capital=DEFAULT_CAPITAL):
        self.total_capital = total_capital
        self.max_loss_per_trade = total_capital * 0.10
        self.max_daily_loss = total_capital * 0.30
        self.daily_pnl = 0.0
        self.open_positions = []
    def can_trade(self):
        if self.daily_pnl < -self.max_daily_loss: return False, "Daily loss limit reached"
        if len(self.open_positions) >= 3: return False, "Max 3 open positions"
        return True, "OK"
    def check_stop_loss(self, position, current_price):
        if position.entry_price == 0: return False
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        return pnl_pct <= -STOP_LOSS_PCT
    def check_take_profit(self, position, current_price):
        if position.entry_price == 0: return False
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        return pnl_pct >= TAKE_PROFIT_PCT
    def record_trade(self, pnl_usd):
        self.daily_pnl += pnl_usd

class MicroSniper:
    def __init__(self, capital=DEFAULT_CAPITAL):
        self.executor = JupiterExecutor()
        self.sizer = MicroPositionSizer(capital)
        self.risk = MicroRiskManager(capital)
        self.capital = capital
        self.positions = {}
        self.history = []
        self.data_dir = Path("src/data/sniper")
        self.data_dir.mkdir(parents=True, exist_ok=True)
    def evaluate_signal(self, token_address, symbol, score, liquidity):
        can, reason = self.risk.can_trade()
        if not can: return None
        if token_address in self.positions: return None
        if score < 40: return None
        amount = self.sizer.calculate_position(score, liquidity)
        if amount < 1.0: return None
        return TradeSignal(token_address=token_address, symbol=symbol,
            side="buy", amount_usd=amount, score=score,
            reason=f"Score {score}/100, Liquidity ")
    def execute_buy(self, signal):
        price = self.executor.get_price(signal.token_address)
        if not price or price <= 0: return None
        token_amount = signal.amount_usd / price
        lamports = int(signal.amount_usd * 1e9)
        quote = self.executor.get_quote(SOL_MINT, signal.token_address, lamports)
        if not quote: return None
        tx_sig = self.executor.execute_swap(quote)
        pos = Position(token_address=signal.token_address, symbol=signal.symbol,
            side="buy", entry_price=price, amount_usd=signal.amount_usd,
            token_amount=token_amount, tx_signature=tx_sig or "")
        self.positions[signal.token_address] = pos
        self._log_position(pos, "entry")
        return pos
    def check_exits(self):
        closed = []
        for addr, pos in list(self.positions.items()):
            price = self.executor.get_price(addr)
            if not price: continue
            if self.risk.check_stop_loss(pos, price):
                self._close_position(pos, price, "closed_sl"); closed.append(pos)
            elif self.risk.check_take_profit(pos, price):
                self._close_position(pos, price, "closed_tp"); closed.append(pos)
        return closed
    def _close_position(self, pos, exit_price, status):
        pos.exit_price = exit_price; pos.exit_time = datetime.utcnow(); pos.status = status
        if pos.entry_price > 0:
            pos.pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            pos.pnl_usd = pos.amount_usd * (pos.pnl_pct / 100)
        self.risk.record_trade(pos.pnl_usd)
        self.history.append(pos)
        if pos.token_address in self.positions: del self.positions[pos.token_address]
        self._log_position(pos, "exit")
    def _log_position(self, pos, action):
        with open(self.data_dir / "positions.jsonl", "a") as f:
            f.write(json.dumps({"action": action, "timestamp": datetime.utcnow().isoformat(), **pos.to_dict()}) + chr(10))
    def get_stats(self):
        t = len(self.history)
        w = sum(1 for p in self.history if p.pnl_usd > 0)
        return {"total_capital": self.capital, "open_positions": len(self.positions),
                "total_trades": t, "wins": w, "losses": t - w,
                "win_rate": (w / t * 100) if t > 0 else 0,
                "total_pnl": round(sum(p.pnl_usd for p in self.history), 2),
                "daily_pnl": round(self.risk.daily_pnl, 2)}
