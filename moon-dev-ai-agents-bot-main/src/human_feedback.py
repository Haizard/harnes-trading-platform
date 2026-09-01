"""
👤 Moon Dev's Human Feedback System
Learn from trader judgment — record feedback, don't require it.
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FeedbackRecord:
    trade_id: str
    rating: int  # 1-5
    category: str  # entry, exit, sizing, timing
    comment: str = ""
    trader_confidence: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


class HumanFeedbackSystem:
    """Records trader feedback for offline learning — not required for operation."""

    def __init__(self, history_dir: str = None):
        self.history_dir = history_dir or os.path.join(os.path.dirname(__file__), 'data')
        os.makedirs(self.history_dir, exist_ok=True)
        self.feedback_path = os.path.join(self.history_dir, 'human_feedback.jsonl')

    def record_feedback(self, trade_id: str, rating: int, category: str = "general",
                       comment: str = "", confidence: float = 0.0):
        rating = max(1, min(5, rating))
        record = FeedbackRecord(trade_id=trade_id, rating=rating, category=category,
                               comment=comment, trader_confidence=confidence)
        with open(self.feedback_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record.to_dict(), default=str) + '\n')

    def get_feedback(self, category: str = None, days: int = 30) -> List[dict]:
        if not os.path.exists(self.feedback_path):
            return []
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        results = []
        with open(self.feedback_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get('timestamp', '') < cutoff: continue
                    if category and entry.get('category') != category: continue
                    results.append(entry)
                except: continue
        return results

    def get_summary(self, days: int = 30) -> dict:
        feedback = self.get_feedback(days=days)
        if not feedback: return {'total': 0, 'avg_rating': 0}

        ratings = [f['rating'] for f in feedback]
        by_category = {}
        for f in feedback:
            cat = f.get('category', 'general')
            by_category.setdefault(cat, []).append(f['rating'])

        return {
            'total': len(feedback),
            'avg_rating': sum(ratings) / len(ratings),
            'by_category': {k: sum(v)/len(v) for k, v in by_category.items()},
        }
