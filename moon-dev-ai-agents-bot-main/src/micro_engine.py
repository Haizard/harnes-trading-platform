"""
Moon Dev Micro-Cap Trading Engine
DSH Pattern: Event-driven engine that coordinates scanner -> sniper -> tracker.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

from src.token_scanner import TokenScanner, TokenCandidate
from src.micro_sniper import MicroSniper, TradeSignal

DEFAULT_CAPITAL = 25.0
SCAN_INTERVAL = 30
EXIT_CHECK_INTERVAL = 10


class MicroEngine:
    """DSH-compliant micro-cap trading engine."""

    def __init__(self, capital=DEFAULT_CAPITAL):
        self.capital = capital
        self.scanner = TokenScanner(callback=self._on_candidate)
        self.sniper = MicroSniper(capital=capital)
        self._running = False
        self._scan_count = 0
        self._signals_generated = 0
        self._trades_executed = 0
        self.data_dir = Path("src/data/micro_engine")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._events = []

    def _on_candidate(self, candidate):
        """Called when scanner finds a candidate."""
        self._signals_generated += 1

        # Log event
        self._events.append({
            "type": "token/candidate",
            "data": candidate.to_dict(),
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Try to create trade signal
        signal = self.sniper.evaluate_signal(
            candidate.address,
            candidate.symbol,
            candidate.score,
            candidate.liquidity_usd,
        )

        if signal:
            self._execute_signal(signal, candidate)

    def _execute_signal(self, signal, candidate):
        """Execute a trade signal."""
        print("")
        print("=" * 60)
        print("EXECUTING: " + signal.symbol)
        print("   Score: " + str(int(signal.score)) + "/100")
        print("   Amount: $" + "{:.2f}".format(signal.amount_usd))
        print("   Reason: " + signal.reason)
        print("=" * 60)

        # Log intent
        self._events.append({
            "type": "order/intent",
            "data": {
                "token": signal.token_address,
                "symbol": signal.symbol,
                "side": signal.side,
                "amount_usd": signal.amount_usd,
                "score": signal.score,
            },
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Execute
        position = self.sniper.execute_buy(signal)

        if position:
            self._trades_executed += 1
            self._events.append({
                "type": "order/submitted",
                "data": {
                    "token": signal.token_address,
                    "symbol": signal.symbol,
                    "amount_usd": signal.amount_usd,
                    "entry_price": position.entry_price,
                },
                "timestamp": datetime.utcnow().isoformat(),
            })

    def _check_exits(self):
        """Check all positions for exits."""
        closed = self.sniper.check_exits()
        for position in closed:
            self._events.append({
                "type": "position/closed",
                "data": position.to_dict(),
                "timestamp": datetime.utcnow().isoformat(),
            })

    def _log_events(self):
        """Persist events to JSONL file."""
        log_path = self.data_dir / "engine_events.jsonl"
        with open(log_path, "a") as f:
            for event in self._events:
                f.write(json.dumps(event, default=str) + chr(10))
        self._events = []

    async def run(self):
        """Main engine loop."""
        self._running = True

        print("")
        print("=" * 60)
        print("MICRO ENGINE STARTED")
        print("   Capital: $" + "{:.2f}".format(self.capital))
        print("   Scan interval: " + str(SCAN_INTERVAL) + "s")
        print("   Exit check: " + str(EXIT_CHECK_INTERVAL) + "s")
        print("=" * 60)
        print("")

        last_exit_check = time.time()

        while self._running:
            try:
                candidates = self.scanner.scan_once()
                self._scan_count += 1

                if candidates:
                    print("[ENGINE] Scan #" + str(self._scan_count) + ": Found " + str(len(candidates)) + " candidates")

                if time.time() - last_exit_check >= EXIT_CHECK_INTERVAL:
                    self._check_exits()
                    last_exit_check = time.time()

                # Persist events every cycle
                if self._events:
                    self._log_events()

                if self._scan_count % 10 == 0:
                    self._print_stats()

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                print("")
                print("[ENGINE] Stopping...")
                self._running = False
            except Exception as e:
                print("[ENGINE] Error: " + str(e))
                await asyncio.sleep(5)

        # Final persist
        self._log_events()
        self._print_stats()
        print("[ENGINE] Stopped")

    def _print_stats(self):
        scanner_stats = self.scanner.get_scan_stats()
        sniper_stats = self.sniper.get_stats()
        print("")
        print("--- Engine Stats ---")
        print("  Scans: " + str(self._scan_count))
        print("  Unique tokens: " + str(scanner_stats.get("unique_tokens_seen", 0)))
        print("  Signals: " + str(self._signals_generated))
        print("  Trades: " + str(self._trades_executed))
        print("  Capital: $" + "{:.2f}".format(self.capital))
        print("  Open positions: " + str(sniper_stats.get("open_positions", 0)))
        print("  Total PnL: $" + str(sniper_stats.get("total_pnl", 0)))
        print("  Win rate: " + str(sniper_stats.get("win_rate", 0)) + "%")
        print("--- End Stats ---")


def main():
    import sys
    capital = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CAPITAL
    engine = MicroEngine(capital=capital)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
