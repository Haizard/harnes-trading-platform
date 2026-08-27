"""
🔍 Moon Dev's Session Query — Search Trade History
DSH Pattern: ctx.sessions — search and replay session events.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from termcolor import cprint


class SessionQuery:
    """Search and analyze trade history from session log."""

    def __init__(self, session_log=None, history_dir: str = None):
        self.log = session_log
        self.history_dir = history_dir or os.path.join(os.path.dirname(__file__), 'data')

    async def search(self, query: str = None, token: str = None,
                    event_type: str = None, days: int = 30,
                    limit: int = 50) -> List[dict]:
        """Search session log with filters."""
        if self.log:
            events = await self.log.get_events(event_type=event_type, token=token, limit=limit)
            return [e.to_dict() for e in events]
        return []

    async def get_trades(self, token: str = None, days: int = 30) -> List[dict]:
        """Get all closed trades, optionally filtered by token."""
        if self.log:
            chains = await self.log.get_trade_chain(token or '', days=days)
            return chains
        return []

    async def get_performance(self, days: int = 30) -> dict:
        """Get performance summary."""
        if self.log:
            return await self.log.get_accuracy_report(days=days)
        return {'total_trades': 0}

    async def get_recent(self, limit: int = 10) -> List[dict]:
        """Get most recent events."""
        if self.log:
            return await self.log.get_recent_activity(limit=limit)
        return []

    async def get_token_history(self, token: str, days: int = 30) -> dict:
        """Get complete history for a specific token."""
        trades = await self.get_trades(token, days)
        events = await self.search(token=token, days=days)

        total_pnl = sum(t.get('pnl_usd', 0) for t in trades)
        wins = sum(1 for t in trades if t.get('pnl_usd', 0) > 0)

        return {
            'token': token,
            'total_trades': len(trades),
            'wins': wins,
            'losses': len(trades) - wins,
            'win_rate': wins / len(trades) if trades else 0,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / len(trades) if trades else 0,
            'trades': trades,
            'events': events[:20],
        }

    async def export_csv(self, filepath: str, days: int = 30) -> str:
        """Export trade history to CSV."""
        trades = await self.get_trades(days=days)
        if not trades:
            return ""

        import csv
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['token', 'pnl_usd', 'holding_time', 'timestamp'])
            writer.writeheader()
            for t in trades:
                writer.writerow({
                    'token': t.get('token', ''),
                    'pnl_usd': t.get('pnl_usd', 0),
                    'holding_time': t.get('holding_time', 0),
                    'timestamp': t.get('timestamp', ''),
                })
        return filepath
