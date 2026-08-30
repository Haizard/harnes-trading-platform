"""
🔍 Moon Dev's Wallet Tracker — Smart Money Intelligence

Monitors Solana wallets for swap transactions via RPC polling.
Detects Jupiter/Raydium/PumpSwap swaps, parses token bought/sold,
and stores activity for wallet scoring and smart money consensus.

Architecture:
  Solana Blockchain
       │
       ▼
  WalletTracker (polls getSignaturesForAddress + getTransaction)
       │
       ├── Detects swap instructions (Jupiter/Raydium/PumpSwap)
       ├── Parses token address, direction, amount, timestamp
       │
       ▼
  wallet_activity.jsonl (persistent storage)
       │
       ▼
  WalletScorer / SmartMoneyDetector

Security: READ-ONLY. Only reads blockchain data. Never executes trades.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from enum import Enum


# ── Constants ─────────────────────────────────────────────────

SOLANA_RPC = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Use path relative to this file, not CWD (works in Docker)
_MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = _MODULE_DIR.parent / "data"
WALLETS_CONFIG = DATA_DIR / "tracked_wallets.json"
WALLET_ACTIVITY_LOG = DATA_DIR / "wallet_tracker" / "wallet_activity.jsonl"

# Known program IDs for swap detection
SWAP_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter_v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcPX7": "jupiter_v4",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "whirlpool",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium_amm",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "raydium_clmm",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "pumpswap",
}

# Minimum seconds between RPC polls for the same wallet
POLL_COOLDOWN = 10


class SwapDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class SwapEvent:
    """A detected swap transaction on a tracked wallet."""
    wallet: str
    token_address: str
    token_symbol: str
    direction: str          # "buy" or "sell"
    amount_tokens: float
    amount_sol: float
    amount_usd: float
    price_usd: float
    tx_signature: str
    block_time: int         # unix timestamp
    timestamp: str          # ISO string
    program: str            # "jupiter_v6", "raydium_amm", etc.
    slot: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class WalletTracker:
    """
    Monitors a list of Solana wallets for swap activity.

    Uses Solana RPC (getSignaturesForAddress + getTransaction) to detect
    swaps on Jupiter, Raydium, and PumpSwap. Stores activity in JSONL
    for downstream scoring and consensus detection.

    Usage:
        tracker = WalletTracker()
        tracker.add_wallet("7xK...abc")
        events = tracker.poll_wallets()  # returns new SwapEvents
    """

    def __init__(self, rpc_url: str = None, data_dir: Path = None, event_bus=None):
        self.rpc_url = rpc_url or SOLANA_RPC
        self.data_dir = data_dir or DATA_DIR
        self.event_bus = event_bus  # Optional EventBus for DSH event emission
        self.wallets: Dict[str, dict] = {}  # address -> config
        self._last_poll: Dict[str, float] = {}  # address -> last poll time
        self._seen_sigs: Set[str] = set()   # already-processed signatures
        self._load_wallets()
        self._load_seen_sigs()

    # ── Wallet Management ──────────────────────────────────────

    def add_wallet(self, address: str, label: str = "", score: float = 0.0, tags: List[str] = None):
        """Add a wallet to track."""
        self.wallets[address] = {
            "address": address,
            "label": label or address[:8],
            "score": score,
            "tags": tags or [],
            "added_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        self._save_wallets()

    def remove_wallet(self, address: str):
        """Remove a wallet from tracking."""
        self.wallets.pop(address, None)
        self._save_wallets()

    def get_tracked_wallets(self) -> List[dict]:
        """List all tracked wallets."""
        return [w for w in self.wallets.values() if w.get("active", True)]

    # ── RPC Polling ────────────────────────────────────────────

    def poll_wallets(self, max_wallets: int = 50) -> List[SwapEvent]:
        """
        Poll all tracked wallets for new swap transactions.

        Returns list of new SwapEvent objects detected since last poll.
        """
        new_events = []
        wallets = self.get_tracked_wallets()[:max_wallets]

        for wallet_cfg in wallets:
            addr = wallet_cfg["address"]
            now = time.time()

            # Respect poll cooldown
            if addr in self._last_poll and (now - self._last_poll[addr]) < POLL_COOLDOWN:
                continue

            try:
                events = self._poll_single_wallet(addr)
                new_events.extend(events)
                self._last_poll[addr] = now
            except Exception as e:
                print(f"[WALLET] Error polling {wallet_cfg.get('label', addr[:8])}: {e}")

        # Persist new events
        if new_events:
            self._append_events(new_events)
            # Emit to EventBus for DSH listeners (Session Log, Telegram, etc.)
            self._emit_swap_events(new_events)

        return new_events

    def _poll_single_wallet(self, wallet_address: str) -> List[SwapEvent]:
        """Poll a single wallet for recent swap transactions."""
        events = []

        # Step 1: Get recent transaction signatures
        sigs = self._get_signatures(wallet_address, limit=20)
        if not sigs:
            return []

        # Step 2: Get full transactions and parse swaps
        for sig_info in sigs:
            sig = sig_info.get("signature", "")
            if sig in self._seen_sigs:
                continue

            block_time = sig_info.get("blockTime", 0)
            slot = sig_info.get("slot", 0)

            # Only look at recent transactions (last 1 hour)
            age_hours = (time.time() - block_time) / 3600 if block_time else 999
            if age_hours > 1:
                self._seen_sigs.add(sig)
                continue

            tx = self._get_transaction(sig)
            if tx:
                swap = self._parse_swap_transaction(wallet_address, tx, sig, block_time, slot)
                if swap:
                    events.append(swap)
                self._seen_sigs.add(sig)

        # Keep seen_sigs bounded
        if len(self._seen_sigs) > 5000:
            self._seen_sigs = set(list(self._seen_sigs)[-2500:])

        return events

    def _get_signatures(self, wallet_address: str, limit: int = 20) -> List[dict]:
        """Get recent transaction signatures for a wallet."""
        try:
            resp = requests.post(self.rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [wallet_address, {"limit": limit}]
            }, timeout=10)

            if resp.status_code == 200:
                result = resp.json().get("result", [])
                return [s for s in result if s.get("err") is None]
        except Exception as e:
            print(f"[WALLET] RPC getSignatures error: {e}")
        return []

    def _get_transaction(self, signature: str) -> Optional[dict]:
        """Get full parsed transaction details."""
        try:
            resp = requests.post(self.rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ]
            }, timeout=15)

            if resp.status_code == 200:
                return resp.json().get("result")
        except Exception as e:
            print(f"[WALLET] RPC getTransaction error: {e}")
        return None

    # ── Swap Parsing ───────────────────────────────────────────

    def _parse_swap_transaction(
        self, wallet_address: str, tx: dict, signature: str, block_time: int, slot: int
    ) -> Optional[SwapEvent]:
        """
        Parse a transaction to detect and extract swap data.

        Looks for:
        - Jupiter v6/v4 swap instructions
        - Raydium AMM swaps
        - PumpSwap swaps

        Returns SwapEvent if a swap is detected, None otherwise.
        """
        meta = tx.get("meta", {})
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])

        # Normalize account_keys (can be strings or dicts)
        if account_keys and isinstance(account_keys[0], dict):
            account_keys = [k.get("pubkey", "") for k in account_keys]

        instructions = message.get("instructions", [])
        inner_instructions = meta.get("innerInstructions", [])

        # Flatten all instructions
        all_instructions = instructions
        for inner in inner_instructions:
            all_instructions.extend(inner.get("instructions", []))

        # Find swap-related instructions
        swap_program = None
        for ix in all_instructions:
            prog_id = ix.get("programId", "") if isinstance(ix, dict) else ""
            if prog_id in SWAP_PROGRAMS:
                swap_program = SWAP_PROGRAMS[prog_id]
                break

        if not swap_program:
            return None

        # Parse token balance changes to detect what was bought/sold
        pre_token = meta.get("preTokenBalances", [])
        post_token = meta.get("postTokenBalances", [])

        wallet_pre = {b.get("mint", ""): float(b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                      for b in pre_token if b.get("owner") == wallet_address}
        wallet_post = {b.get("mint", ""): float(b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                       for b in post_token if b.get("owner") == wallet_address}

        # Check SOL balance changes
        pre_sol = meta.get("preBalances", [])
        post_sol = meta.get("postBalances", [])

        wallet_idx = None
        for i, key in enumerate(account_keys):
            if key == wallet_address:
                wallet_idx = i
                break

        sol_change_lamports = 0
        if wallet_idx is not None and pre_sol and post_sol and wallet_idx < len(pre_sol):
            sol_change_lamports = post_sol[wallet_idx] - pre_sol[wallet_idx]

        # Find token that was bought (balance increased) or sold (balance decreased)
        all_mints = set(list(wallet_pre.keys()) + list(wallet_post.keys()))
        # Exclude SOL/USDC native
        all_mints.discard(SOL_MINT)
        all_mints.discard(USDC_MINT)

        if not all_mints:
            return None

        # Determine direction based on SOL movement and token balance changes
        for mint in all_mints:
            pre_bal = wallet_pre.get(mint, 0)
            post_bal = wallet_post.get(mint, 0)
            change = post_bal - pre_bal

            if abs(change) < 1e-10:
                continue

            if change > 0:
                direction = SwapDirection.BUY
                amount_tokens = change
                # SOL spent (negative change = spent)
                amount_sol = abs(sol_change_lamports) / 1e9
            else:
                direction = SwapDirection.SELL
                amount_tokens = abs(change)
                # SOL received (positive change = received)
                amount_sol = sol_change_lamports / 1e9 if sol_change_lamports > 0 else 0

            # Rough price estimate
            price_usd = 0
            amount_usd = 0
            if amount_tokens > 0 and amount_sol > 0:
                # Assume SOL ≈ $150 for rough estimate (use Jupiter for better)
                sol_usd = 150.0
                amount_usd = amount_sol * sol_usd
                price_usd = amount_usd / amount_tokens if amount_tokens else 0

            timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else ""

            return SwapEvent(
                wallet=wallet_address,
                token_address=mint,
                token_symbol="",  # Will be resolved later if needed
                direction=direction.value,
                amount_tokens=amount_tokens,
                amount_sol=round(amount_sol, 6),
                amount_usd=round(amount_usd, 4),
                price_usd=price_usd,
                tx_signature=signature,
                block_time=block_time,
                timestamp=timestamp,
                program=swap_program,
                slot=slot,
            )

        return None

    # ── Token Symbol Resolution ────────────────────────────────

    def resolve_token_symbol(self, token_address: str) -> str:
        """Resolve a token mint address to its symbol via Jupiter."""
        try:
            resp = requests.get(
                f"https://api.jup.ag/price/v2?ids={token_address}",
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get(token_address, {})
                # Jupiter price API doesn't always return symbol, use DexScreener
                pass
        except Exception:
            pass

        # Fallback: DexScreener
        try:
            resp = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                timeout=5
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    return pairs[0].get("baseToken", {}).get("symbol", token_address[:8])
        except Exception:
            pass

        return token_address[:8]

    # ── Storage ────────────────────────────────────────────────

    def _append_events(self, events: List[SwapEvent]):
        """Append swap events to the activity log."""
        WALLET_ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WALLET_ACTIVITY_LOG, "a") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), default=str) + "\n")

    def _emit_swap_events(self, events: List[SwapEvent]):
        """Emit swap events to EventBus for DSH listeners."""
        if not self.event_bus:
            return
        try:
            import asyncio
            from src.event_bus import Events
            for event in events:
                payload = {
                    "wallet": event.wallet[:12] + "...",
                    "token_address": event.token_address,
                    "direction": event.direction,
                    "amount_sol": event.amount_sol,
                    "amount_usd": event.amount_usd,
                    "price_usd": event.price_usd,
                    "program": event.program,
                    "tx_signature": event.tx_signature,
                    "timestamp": event.timestamp,
                }
                # Fire-and-forget via ensure_future
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(self.event_bus.emit(Events.WALLET_SWAP_DETECTED, payload))
                except RuntimeError:
                    pass  # No running loop — skip emit
        except Exception:
            pass  # Never crash on event emission

    def get_recent_activity(self, hours: int = 24, wallet: str = "", token: str = "") -> List[dict]:
        """Read recent activity from the log."""
        if not WALLET_ACTIVITY_LOG.exists():
            return []

        cutoff = time.time() - (hours * 3600)
        events = []

        try:
            for line in open(WALLET_ACTIVITY_LOG):
                try:
                    e = json.loads(line)
                    if e.get("block_time", 0) < cutoff:
                        continue
                    if wallet and e.get("wallet", "") != wallet:
                        continue
                    if token and e.get("token_address", "") != token:
                        continue
                    events.append(e)
                except json.JSONDecodeError:
                    continue
        except FileNotFoundError:
            pass

        return events

    def get_wallet_activity(self, wallet_address: str, hours: int = 24) -> List[dict]:
        """Get activity for a specific wallet."""
        return self.get_recent_activity(hours=hours, wallet=wallet_address)

    def get_token_activity(self, token_address: str, hours: int = 24) -> List[dict]:
        """Get all tracked wallet activity for a specific token."""
        return self.get_recent_activity(hours=hours, token=token_address)

    # ── Config Persistence ─────────────────────────────────────

    def _load_wallets(self):
        """Load tracked wallets from config."""
        if not WALLETS_CONFIG.exists():
            print(f"[WALLET] Config not found: {WALLETS_CONFIG}")
            return
        try:
            data = json.loads(WALLETS_CONFIG.read_text())
            if isinstance(data, list):
                for w in data:
                    if w.get("address"):
                        self.wallets[w["address"]] = w
            elif isinstance(data, dict):
                self.wallets = data
            print(f"[WALLET] Loaded {len(self.wallets)} wallets from {WALLETS_CONFIG}")
        except Exception as e:
            print(f"[WALLET] Error loading wallets config: {e}")

    def _save_wallets(self):
        """Save tracked wallets to config."""
        WALLETS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        wallet_list = list(self.wallets.values())
        WALLETS_CONFIG.write_text(json.dumps(wallet_list, indent=2, default=str))

    def _load_seen_sigs(self):
        """Load recent seen signatures from activity log (avoid re-processing)."""
        if WALLET_ACTIVITY_LOG.exists():
            try:
                lines = open(WALLET_ACTIVITY_LOG).readlines()[-1000:]
                for line in lines:
                    try:
                        e = json.loads(line)
                        sig = e.get("tx_signature", "")
                        if sig:
                            self._seen_sigs.add(sig)
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return tracker statistics."""
        wallets = self.get_tracked_wallets()
        activity = self.get_recent_activity(hours=24)
        return {
            "tracked_wallets": len(wallets),
            "active_wallets": len([w for w in wallets if w.get("active", True)]),
            "events_24h": len(activity),
            "unique_tokens_24h": len(set(e.get("token_address", "") for e in activity)),
            "buys_24h": len([e for e in activity if e.get("direction") == "buy"]),
            "sells_24h": len([e for e in activity if e.get("direction") == "sell"]),
            "total_sol_volume": sum(e.get("amount_sol", 0) for e in activity),
            "seen_sigs": len(self._seen_sigs),
        }


# ── Factory ───────────────────────────────────────────────────

def create_wallet_tracker() -> WalletTracker:
    """Create a wallet tracker with default settings."""
    tracker = WalletTracker()
    print(f"[WALLET] Wallet Tracker initialized — tracking {len(tracker.get_tracked_wallets())} wallets")
    return tracker
