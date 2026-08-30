"""Micro Sniper for Solana memecoin trading.

Supports two modes:
  - paper: Simulated trades using real Jupiter quotes (default)
  - live: Real Solana wallet signing + Jupiter swap submission

DSH Pattern: Uses DB for all trade storage instead of JSONL files.
"""
import os, json, time, base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List
from pathlib import Path

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    import requests
    HTTPX_AVAILABLE = False

# Jupiter API Endpoints (FREE, no API key needed)
JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP = "https://api.jup.ag/swap/v1/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Solana RPC
SOLANA_RPC = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")

# Defaults
DEFAULT_CAPITAL = 100.0
MAX_POSITION_PCT = 50.0
STOP_LOSS_PCT = 10.0
TAKE_PROFIT_PCT = 30.0
SLIPPAGE_BPS = 500
MAX_RETRIES = 3
RETRY_DELAY = 2


@dataclass
class Position:
    token_address: str
    symbol: str
    side: str
    entry_price: float
    amount_usd: float
    token_amount: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"
    tx_signature: str = ""
    category: str = "unknown"
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    max_hold_hours: float = 12.0
    def to_dict(self):
        return {"token_address": self.token_address, "symbol": self.symbol,
                "side": self.side, "entry_price": self.entry_price,
                "amount_usd": self.amount_usd, "token_amount": self.token_amount,
                "entry_time": self.entry_time.isoformat(),
                "exit_price": self.exit_price,
                "exit_time": self.exit_time.isoformat() if self.exit_time else None,
                "pnl_usd": self.pnl_usd, "pnl_pct": self.pnl_pct,
                "status": self.status, "tx_signature": self.tx_signature,
                "category": self.category}


@dataclass
class TradeSignal:
    token_address: str
    symbol: str
    side: str
    amount_usd: float
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT
    max_hold_hours: float = 12.0
    category: str = "unknown"
    reason: str = ""
    score: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class JupiterExecutor:
    """Execute real Solana swaps via Jupiter API + solders wallet signing."""

    def __init__(self, private_key=None, rpc_url=None, mode="paper"):
        self.mode = mode
        self.rpc_url = rpc_url or SOLANA_RPC
        self._keypair = None
        self._pubkey = None
        pk = private_key or os.getenv("SOLANA_PRIVATE_KEY")
        if pk and mode == "live":
            self._load_wallet(pk)
        elif mode == "live" and not pk:
            print("[SNIPER] WARNING: live mode but no SOLANA_PRIVATE_KEY, falling back to paper")
            self.mode = "paper"

    def _load_wallet(self, private_key):
        try:
            from solders.keypair import Keypair
            import base58 as b58
            secret = b58.b58decode(private_key)
            self._keypair = Keypair.from_bytes(secret)
            self._pubkey = str(self._keypair.pubkey())
            print("[SNIPER] Wallet loaded: " + self._pubkey[:8] + "..." + self._pubkey[-4:])
        except ImportError:
            print("[SNIPER] ERROR: pip install solders base58")
            self.mode = "paper"
        except Exception as e:
            print("[SNIPER] ERROR loading wallet: " + str(e))
            self.mode = "paper"

    def get_quote(self, input_mint, output_mint, amount):
        for attempt in range(MAX_RETRIES):
            try:
                params = {
                    "inputMint": input_mint, "outputMint": output_mint,
                    "amount": str(amount), "slippageBps": SLIPPAGE_BPS
                }
                if HTTPX_AVAILABLE:
                    with httpx.Client(timeout=15) as client:
                        resp = client.get(JUPITER_QUOTE, params=params)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code == 429:
                        time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    resp = requests.get(JUPITER_QUOTE, params=params, timeout=15)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code == 429:
                        time.sleep(RETRY_DELAY * (attempt + 1))
            except Exception as e:
                print("[SNIPER] Quote error: " + str(e))
                break
        return None

    def get_price(self, token_address):
        try:
            if HTTPX_AVAILABLE:
                with httpx.Client(timeout=10) as client:
                    resp = client.get("https://api.jup.ag/price/v2",
                        params={"ids": token_address})
            else:
                resp = requests.get("https://api.jup.ag/price/v2",
                    params={"ids": token_address}, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get(token_address, {})
                price = data.get("price")
                if price:
                    return float(price)
        except Exception:
            pass
        try:
            quote = self.get_quote(SOL_MINT, token_address, int(0.01 * 1e9))
            if quote:
                out = int(quote.get("outAmount", 0))
                if out > 0:
                    return 0.01 / out
        except Exception:
            pass
        return None

    def execute_swap(self, quote):
        if self.mode == "paper":
            return self._simulate_swap(quote)
        return self._real_swap(quote)

    def _simulate_swap(self, quote):
        sig = "paper_" + str(int(time.time()))
        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0))
        print("[SNIPER] PAPER SWAP | out=" + str(out_amount) + " | impact=" + str(round(price_impact, 2)) + "%")
        return sig

    def _real_swap(self, quote):
        for attempt in range(MAX_RETRIES):
            try:
                swap_body = {
                    "quoteResponse": quote,
                    "userPublicKey": self._pubkey,
                    "dynamicComputeUnitLimit": True,
                    "dynamicSlippage": True,
                    "prioritizationFeeLamports": {
                        "priorityLevelWithMaxLamports": {
                            "maxLamports": 1000000,
                            "priorityLevel": "medium"
                        }
                    }
                }
                if HTTPX_AVAILABLE:
                    with httpx.Client(timeout=30) as client:
                        resp = client.post(JUPITER_SWAP, json=swap_body)
                else:
                    resp = requests.post(JUPITER_SWAP, json=swap_body, timeout=30)
                if resp.status_code != 200:
                    print("[SNIPER] Jupiter swap error: " + str(resp.status_code))
                    time.sleep(RETRY_DELAY)
                    continue
                swap_data = resp.json()
                swap_tx_b64 = swap_data.get("swapTransaction")
                if not swap_tx_b64:
                    return None
                fee = swap_data.get("prioritizationFeeLamports", 0)
                print("[SNIPER] Got swap tx | fee=" + str(fee) + " lamports")

                from solders.transaction import VersionedTransaction
                tx_bytes = base64.b64decode(swap_tx_b64)
                tx = VersionedTransaction.from_bytes(tx_bytes)
                signed_tx = VersionedTransaction(tx.message, [self._keypair])
                signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

                submit_body = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "sendTransaction",
                    "params": [signed_b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 2}]
                }
                if HTTPX_AVAILABLE:
                    with httpx.Client(timeout=30) as client:
                        submit_resp = client.post(self.rpc_url, json=submit_body)
                else:
                    submit_resp = requests.post(self.rpc_url, json=submit_body, timeout=30)
                if submit_resp.status_code == 200:
                    result = submit_resp.json()
                    if "result" in result:
                        tx_sig = result["result"]
                        print("[SNIPER] TX SUBMITTED: " + tx_sig[:16] + "...")
                        return tx_sig
                    else:
                        print("[SNIPER] RPC error: " + str(result.get("error", {}).get("message", "")))
                time.sleep(RETRY_DELAY * (attempt + 1))
            except ImportError:
                print("[SNIPER] Missing: pip install solders base58")
                return None
            except Exception as e:
                print("[SNIPER] Swap error: " + str(e))
                time.sleep(RETRY_DELAY * (attempt + 1))
        return None

    def confirm_transaction(self, tx_signature, max_wait=60):
        start = time.time()
        while time.time() - start < max_wait:
            try:
                body = {"jsonrpc": "2.0", "id": 1,
                    "method": "getSignatureStatuses",
                    "params": [[tx_signature], {"searchTransactionHistory": False}]}
                if HTTPX_AVAILABLE:
                    with httpx.Client(timeout=10) as client:
                        resp = client.post(self.rpc_url, json=body)
                else:
                    resp = requests.post(self.rpc_url, json=body, timeout=10)
                if resp.status_code == 200:
                    statuses = resp.json().get("result", {}).get("value", [])
                    if statuses and statuses[0]:
                        s = statuses[0]
                        if s.get("confirmationStatus") in ("confirmed", "finalized"):
                            return True
                        if s.get("err"):
                            return False
            except Exception:
                pass
            time.sleep(3)
        return None


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
        self.max_positions = 8
    def can_trade(self):
        if self.daily_pnl < -self.max_daily_loss: return False, "Daily loss limit"
        if len(self.open_positions) >= self.max_positions: return False, "Max " + str(self.max_positions) + " positions"
        return True, "OK"
    def check_stop_loss(self, position, current_price):
        if position.entry_price == 0: return False
        sl = getattr(position, 'stop_loss_pct', STOP_LOSS_PCT)
        return ((current_price - position.entry_price) / position.entry_price * 100) <= -sl
    def check_take_profit(self, position, current_price):
        if position.entry_price == 0: return False
        tp = getattr(position, 'take_profit_pct', TAKE_PROFIT_PCT)
        return ((current_price - position.entry_price) / position.entry_price * 100) >= tp
    def record_trade(self, pnl_usd):
        self.daily_pnl += pnl_usd


class MicroSniper:
    def __init__(self, capital=DEFAULT_CAPITAL, mode="paper", rpc_url=None):
        self.executor = JupiterExecutor(mode=mode, rpc_url=rpc_url)
        self.sizer = MicroPositionSizer(capital)
        self.risk = MicroRiskManager(capital)
        self.capital = capital
        self.mode = mode
        self.positions = {}
        self.history = []
        self.data_dir = Path("src/data/sniper")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # DB availability
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

    def evaluate_signal(self, token_address, symbol, score, liquidity,
                        category="unknown", stop_loss_pct=10.0,
                        take_profit_pct=30.0, max_hold_hours=12.0):
        can, reason = self.risk.can_trade()
        if not can: return None
        if token_address in self.positions: return None
        if score < 40: return None
        amount = self.sizer.calculate_position(score, liquidity)
        if amount < 1.0: return None
        return TradeSignal(token_address=token_address, symbol=symbol,
            side="buy", amount_usd=amount, score=score,
            category=category, stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct, max_hold_hours=max_hold_hours,
            reason="Score " + str(score) + "/100")

    def execute_buy(self, signal):
        price = self.executor.get_price(signal.token_address)
        if not price or price <= 0: return None
        token_amount = signal.amount_usd / price
        lamports = int(signal.amount_usd * 1e9)
        quote = self.executor.get_quote(SOL_MINT, signal.token_address, lamports)
        if not quote: return None
        price_impact = abs(float(quote.get("priceImpactPct", 0)))
        if price_impact > 10:
            print("[SNIPER] Skipping " + signal.symbol + " - impact " + str(round(price_impact, 1)) + "%")
            return None
        tx_sig = self.executor.execute_swap(quote)
        pos = Position(token_address=signal.token_address, symbol=signal.symbol,
            side="buy", entry_price=price, amount_usd=signal.amount_usd,
            token_amount=token_amount, tx_signature=tx_sig or "",
            category=signal.category, stop_loss_pct=signal.stop_loss_pct,
            take_profit_pct=signal.take_profit_pct, max_hold_hours=signal.max_hold_hours)
        self.positions[signal.token_address] = pos
        self._log_position(pos, "entry")
        self._save_to_db(pos, "entry")
        if self.mode == "live" and tx_sig and not tx_sig.startswith("paper_"):
            print("[SNIPER] Confirming " + tx_sig[:16] + "...")
            if self.executor.confirm_transaction(tx_sig):
                print("[SNIPER] CONFIRMED")
        return pos

    def execute_sell(self, token_address, reason="manual"):
        if token_address not in self.positions: return None
        pos = self.positions[token_address]
        if self.mode == "live":
            try:
                amount_lamports = int(pos.token_amount * 1e6)
                quote = self.executor.get_quote(token_address, SOL_MINT, amount_lamports)
                if quote:
                    tx_sig = self.executor.execute_swap(quote)
                    if tx_sig:
                        pos.tx_signature = tx_sig
            except Exception as e:
                print("[SNIPER] Sell error: " + str(e))
        price = self.executor.get_price(token_address)
        self._close_position(pos, price or pos.entry_price, "closed_" + reason)
        return pos

    def check_exits(self):
        closed = []
        for addr, pos in list(self.positions.items()):
            price = self.executor.get_price(addr)
            if not price: continue

            max_hold = getattr(pos, 'max_hold_hours', 12.0)
            hours_held = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600

            if self.risk.check_stop_loss(pos, price):
                self.execute_sell(addr, "sl"); closed.append(pos)
            elif self.risk.check_take_profit(pos, price):
                self.execute_sell(addr, "tp"); closed.append(pos)
            elif hours_held >= max_hold:
                self.execute_sell(addr, "stale"); closed.append(pos)
        return closed

    def _close_position(self, pos, exit_price, status):
        pos.exit_price = exit_price
        pos.exit_time = datetime.now(timezone.utc)
        pos.status = status
        if pos.entry_price > 0:
            pos.pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            pos.pnl_usd = pos.amount_usd * (pos.pnl_pct / 100)
        self.risk.record_trade(pos.pnl_usd)
        self.history.append(pos)
        if pos.token_address in self.positions:
            del self.positions[pos.token_address]
        self._log_position(pos, "exit")
        self._save_to_db(pos, "exit")

    def _log_position(self, pos, action):
        # Keep JSONL as backup, but DB is primary
        with open(self.data_dir / "positions.jsonl", "a") as f:
            f.write(json.dumps({"action": action, "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": self.mode, **pos.to_dict()}) + chr(10))

    def _save_to_db(self, trade, action):
        """Save trade to PostgreSQL if available."""
        if not self._db_available:
            return
        try:
            from src.db_storage import save_trade, update_trade_exit
            trade_dict = trade.to_dict()
            trade_dict["mode"] = self.mode
            if action == "entry":
                save_trade(trade_dict, mode=self.mode)
            elif action == "exit":
                update_trade_exit(
                    trade.token_address, trade.exit_price,
                    trade.pnl_usd, trade.pnl_pct, trade.status,
                )
        except Exception:
            pass

    def get_stats(self):
        t = len(self.history)
        w = sum(1 for p in self.history if p.pnl_usd > 0)
        return {"total_capital": self.capital, "mode": self.mode,
                "open_positions": len(self.positions),
                "total_trades": t, "wins": w, "losses": t - w,
                "win_rate": (w / t * 100) if t > 0 else 0,
                "total_pnl": round(sum(p.pnl_usd for p in self.history), 2),
                "daily_pnl": round(self.risk.daily_pnl, 2)}
