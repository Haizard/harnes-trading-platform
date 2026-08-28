"""
🚀 Moon Dev's Micro-Cap Trading Engine
DSH Pattern: Event-driven engine that coordinates scanner → sniper → tracker.

This is the main entry point for the micro-cap trading system.
It wires together:
- TokenScanner (detect opportunities)
- MicroSniper (execute trades)
- SessionLog (track everything)
- EventBus (decouple components)
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

from src.token_scanner import TokenScanner, TokenCandidate
from src.micro_sniper import MicroSniper, TradeSignal
from src.event_bus import EventBus, Event
from src.session_log import SessionLog


# ── Configuration ────────────────────────────────────────────

DEFAULT_CAPITAL = 25.0
SCAN_INTERVAL = 30  # seconds between scans
EXIT_CHECK_INTERVAL = 10  # seconds between exit checks


# ── Micro Trading Engine ─────────────────────────────────────

class MicroEngine:
    """
    DSH-compliant micro-cap trading engine.
    
    Data flow:
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │   Scanner   │───▶│   Sniper    │───▶│   Tracker   │
    │ (detect)    │    │ (execute)   │    │ (log/P&L)   │
    └─────────────┘    └─────────────┘    └─────────────┘
           │                  │                  │
           └──────────────────┴──────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   EventBus +      │
                    │   SessionLog      │
                    └───────────────────┘
    """
    
    def __init__(self, capital: float = DEFAULT_CAPITAL):
        self.capital = capital
        
        # DSH components
        self.event_bus = EventBus()
        self.session_log = SessionLog()
        
        # Trading components
        self.scanner = TokenScanner(callback=self._on_candidate)
        self.sniper = MicroSniper(capital=capital)
        
        # State
        self._running = False
        self._scan_count = 0
        self._signals_generated = 0
        self._trades_executed = 0
        
        # Data directory
        self.data_dir = Path("src/data/micro_engine")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Register event handlers
        self._setup_events()
    
    def _setup_events(self):
        """Set up event handlers for DSH compliance."""
        # Scanner events
        self.event_bus.on("token/candidate", self._handle_candidate_event)
        self.event_bus.on("token/signal", self._handle_signal_event)
        
        # Trade events
        self.event_bus.on("trade/executed", self._handle_trade_event)
        self.event_bus.on("trade/closed", self._handle_close_event)
    
    def _on_candidate(self, candidate: TokenCandidate):
        """Called when scanner finds a candidate."""
        self._signals_generated += 1
        
        # Emit event
        event = Event(
            type="token/candidate",
            data=candidate.to_dict(),
            source="scanner"
        )
        asyncio.create_task(self.event_bus.emit("token/candidate", event))
        
        # Log to session
        self.session_log.log("signal/generated", {
            "token": candidate.address,
            "symbol": candidate.symbol,
            "score": candidate.score,
            "volume_1h": candidate.volume_1h,
            "liquidity": candidate.liquidity_usd,
            "signals": candidate.signals,
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
    
    def _execute_signal(self, signal: TradeSignal, candidate: TokenCandidate):
        """Execute a trade signal."""
        print(f"\n{'='*60}")
        print(f"🎯 EXECUTING: {signal.symbol}")
        print(f"   Score: {signal.score}/100")
        print(f"   Amount: ${signal.amount_usd:.2f}")
        print(f"   Reason: {signal.reason}")
        print(f"{'='*60}")
        
        # Log intent
        self.session_log.log("order/intent", {
            "token": signal.token_address,
            "symbol": signal.symbol,
            "side": signal.side,
            "amount_usd": signal.amount_usd,
            "score": signal.score,
        })
        
        # Execute
        position = self.sniper.execute_buy(signal)
        
        if position:
            self._trades_executed += 1
            
            # Log execution
            self.session_log.log("order/submitted", {
                "token": signal.token_address,
                "symbol": signal.symbol,
                "amount_usd": signal.amount_usd,
                "entry_price": position.entry_price,
                "tx_signature": position.tx_signature,
            })
            
            # Emit event
            event = Event(
                type="trade/executed",
                data=position.to_dict(),
                source="sniper"
            )
            asyncio.create_task(self.event_bus.emit("trade/executed", event))
    
    async def _handle_candidate_event(self, event: Event):
        """Handle candidate event."""
        pass  # Already handled in _on_candidate
    
    async def _handle_signal_event(self, event: Event):
        """Handle signal event."""
        pass  # Already handled in _execute_signal
    
    async def _handle_trade_event(self, event: Event):
        """Handle trade execution event."""
        pass  # Already logged in _execute_signal
    
    async def _handle_close_event(self, event: Event):
        """Handle trade close event."""
        data = event.data
        self.session_log.log("position/closed", {
            "token": data.get("token_address"),
            "symbol": data.get("symbol"),
            "entry_price": data.get("entry_price"),
            "exit_price": data.get("exit_price"),
            "pnl_usd": data.get("pnl_usd"),
            "pnl_pct": data.get("pnl_pct"),
            "status": data.get("status"),
        })
    
    def _check_exits(self):
        """Check all positions for exits."""
        closed = self.sniper.check_exits()
        
        for position in closed:
            event = Event(
                type="trade/closed",
                data=position.to_dict(),
                source="sniper"
            )
            asyncio.create_task(self.event_bus.emit("trade/closed", event))
    
    async def run(self):
        """Main engine loop."""
        self._running = True
        
        print(f"\n{'='*60}")
        print(f"🚀 MICRO ENGINE STARTED")
        print(f"   Capital: ${self.capital:.2f}")
        print(f"   Scan interval: {SCAN_INTERVAL}s")
        print(f"   Exit check: {EXIT_CHECK_INTERVAL}s")
        print(f"{'='*60}\n")
        
        last_exit_check = time.time()
        
        while self._running:
            try:
                # Run scanner
                candidates = self.scanner.scan_once()
                self._scan_count += 1
                
                if candidates:
                    print(f"\n[ENGINE] Scan #{self._scan_count}: Found {len(candidates)} candidates")
                
                # Check exits periodically
                if time.time() - last_exit_check >= EXIT_CHECK_INTERVAL:
                    self._check_exits()
                    last_exit_check = time.time()
                
                # Print stats periodically
                if self._scan_count % 10 == 0:
                    self._print_stats()
                
                # Wait for next scan
                await asyncio.sleep(SCAN_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n[ENGINE] Stopping...")
                self._running = False
            except Exception as e:
                print(f"[ENGINE] Error: {e}")
                await asyncio.sleep(5)
        
        self._print_stats()
        print("[ENGINE] Stopped")
    
    def _print_stats(self):
        ""
