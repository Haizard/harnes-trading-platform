"""
📊 Moon Dev's Execution Quality Tracker
DSH Pattern: Session Log — track execution quality for cost analysis.

Measures the gap between expected and actual execution:
- Slippage: difference between quoted and filled price
- Fill rate: percentage of orders that fill
- Latency: time from intent to fill
- True cost: fees + slippage + opportunity cost

Usage:
    tracker = ExecutionTracker()
    await tracker.record_intent('FART', 'buy', 25.0, expected_price=0.0042)
    await tracker.record_fill('FART', 'buy', 25.0, fill_price=0.00425, latency_ms=150)
    report = await tracker.get_quality_report()
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from termcolor import cprint


@dataclass
class ExecutionRecord:
    """A single execution record."""
    symbol: str
    side: str
    amount_usd: float
    expected_price: float = 0.0
    fill_price: float = 0.0
    slippage_bps: float = 0.0
    latency_ms: float = 0.0
    filled: bool = False
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class ExecutionTracker:
    """
    Tracks execution quality across all trades.

    Stores to PostgreSQL when available, falls back to JSONL.
    """

    def __init__(self, history_dir: str = None):
        self.history_dir = history_dir or os.path.join(
            os.path.dirname(__file__), 'data'
        )
        os.makedirs(self.history_dir, exist_ok=True)
        self.executions_path = os.path.join(self.history_dir, 'execution_history.jsonl')
        # Check DB availability
        self._db_available = False
        try:
            from src.db_storage import get_pool
            self._db_available = get_pool() is not None
        except Exception:
            pass

    async def record_intent(self, symbol: str, side: str, amount_usd: float,
                           expected_price: float = 0.0, source: str = ""):
        """Record an order intent (before execution)."""
        timestamp = datetime.utcnow().isoformat()
        record = ExecutionRecord(
            symbol=symbol,
            side=side,
            amount_usd=amount_usd,
            expected_price=expected_price,
            source=source,
        )

        if self._db_available:
            try:
                from src.db_storage import save_execution
                save_execution(
                    symbol=symbol, side=side, amount_usd=amount_usd,
                    expected_price=expected_price, source=source,
                    filled=False, timestamp=timestamp,
                )
            except Exception:
                pass

        self._append_jsonl(self.executions_path, record.__dict__)

    async def record_fill(self, symbol: str, side: str, amount_usd: float,
                         fill_price: float, expected_price: float = 0.0,
                         latency_ms: float = 0.0, source: str = ""):
        """Record a filled order."""
        timestamp = datetime.utcnow().isoformat()
        if expected_price > 0 and fill_price > 0:
            if side == 'buy':
                slippage_bps = ((fill_price - expected_price) / expected_price) * 10000
            else:
                slippage_bps = ((expected_price - fill_price) / expected_price) * 10000
        else:
            slippage_bps = 0.0

        record = ExecutionRecord(
            symbol=symbol,
            side=side,
            amount_usd=amount_usd,
            expected_price=expected_price,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            filled=True,
            source=source,
        )

        if self._db_available:
            try:
                from src.db_storage import save_execution
                save_execution(
                    symbol=symbol, side=side, amount_usd=amount_usd,
                    expected_price=expected_price, fill_price=fill_price,
                    slippage_bps=slippage_bps, latency_ms=latency_ms,
                    filled=True, source=source, timestamp=timestamp,
                )
            except Exception:
                pass

        self._append_jsonl(self.executions_path, record.__dict__)

    async def record_rejection(self, symbol: str, side: str, amount_usd: float,
                              reason: str = "", source: str = ""):
        """Record a rejected order."""
        timestamp = datetime.utcnow().isoformat()
        record = {
            'symbol': symbol,
            'side': side,
            'amount_usd': amount_usd,
            'filled': False,
            'reason': reason,
            'source': source,
            'timestamp': timestamp,
        }

        if self._db_available:
            try:
                from src.db_storage import save_execution
                save_execution(
                    symbol=symbol, side=side, amount_usd=amount_usd,
                    filled=False, reason=reason, source=source,
                    timestamp=timestamp,
                )
            except Exception:
                pass

        self._append_jsonl(self.executions_path, record)

    async def get_quality_report(self, days: int = 30) -> dict:
        """Generate execution quality report."""
        # Prefer DB query
        if self._db_available:
            try:
                return await self._report_from_db(days)
            except Exception:
                pass

        # Fallback to JSONL
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        records = self._read_jsonl(self.executions_path, cutoff)
        return self._build_report(records, days)

    async def _report_from_db(self, days: int) -> dict:
        """Build quality report from PostgreSQL."""
        from src.db_storage import get_executions

        db_records = get_executions(days=days)
        records = []
        for r in db_records:
            records.append({
                'symbol': r.get('symbol', ''),
                'side': r.get('side', ''),
                'amount_usd': r.get('amount_usd', 0),
                'expected_price': r.get('expected_price', 0),
                'fill_price': r.get('fill_price', 0),
                'slippage_bps': r.get('slippage_bps', 0),
                'latency_ms': r.get('latency_ms', 0),
                'filled': r.get('filled', False),
                'reason': r.get('reason', ''),
                'source': r.get('source', ''),
                'timestamp': str(r.get('timestamp', '')),
            })
        return self._build_report(records, days)

    def _build_report(self, records: list, days: int) -> dict:
        """Build quality report from records."""
        if not records:
            return {'total_intents': 0, 'message': 'No execution data'}

        total = len(records)
        filled = [r for r in records if r.get('filled')]
        rejected = [r for r in records if not r.get('filled') and r.get('reason')]

        fill_rate = len(filled) / total if total > 0 else 0

        slippages = [r.get('slippage_bps', 0) for r in filled if r.get('slippage_bps')]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0
        max_slippage = max(slippages) if slippages else 0
        min_slippage = min(slippages) if slippages else 0

        latencies = [r.get('latency_ms', 0) for r in filled if r.get('latency_ms')]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

        by_symbol = {}
        for r in filled:
            sym = r.get('symbol', 'unknown')
            if sym not in by_symbol:
                by_symbol[sym] = {'count': 0, 'total_slippage': 0, 'total_amount': 0}
            by_symbol[sym]['count'] += 1
            by_symbol[sym]['total_slippage'] += r.get('slippage_bps', 0)
            by_symbol[sym]['total_amount'] += r.get('amount_usd', 0)

        for sym, data in by_symbol.items():
            data['avg_slippage_bps'] = data['total_slippage'] / data['count'] if data['count'] > 0 else 0

        buys = [r for r in filled if r.get('side') == 'buy']
        sells = [r for r in filled if r.get('side') == 'sell']

        rejection_reasons = {}
        for r in rejected:
            reason = r.get('reason', 'unknown')
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

        return {
            'period_days': days,
            'total_intents': total,
            'total_filled': len(filled),
            'total_rejected': len(rejected),
            'fill_rate': round(fill_rate, 3),
            'slippage': {
                'avg_bps': round(avg_slippage, 2),
                'max_bps': round(max_slippage, 2),
                'min_bps': round(min_slippage, 2),
            },
            'latency': {
                'avg_ms': round(avg_latency, 1),
                'p95_ms': round(p95_latency, 1),
            },
            'by_symbol': by_symbol,
            'by_side': {
                'buy_count': len(buys),
                'sell_count': len(sells),
            },
            'rejection_reasons': rejection_reasons,
        }

    # ── Internal ──────────────────────────────────────────────

    def _append_jsonl(self, path: str, data: dict):
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, default=str) + '\n')
        except Exception:
            pass

    def _read_jsonl(self, path: str, since: str = None) -> list:
        if not os.path.exists(path):
            return []
        results = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if since and entry.get('timestamp', '') < since:
                        continue
                    results.append(entry)
                except Exception:
                    continue
        return results
