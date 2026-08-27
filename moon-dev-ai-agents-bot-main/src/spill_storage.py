"""
💾 Moon Dev's Spill & Smart Data Storage
DSH Pattern: Spill — persist oversized tool output, show bounded preview.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SpillResult:
    preview: str
    file_path: Optional[str] = None
    total_size: int = 0
    truncated: bool = False

    def to_dict(self):
        return {'preview': self.preview[:200], 'file_path': self.file_path,
                'total_size': self.total_size, 'truncated': self.truncated}


class SpillStorage:
    """Persist large data to disk, return bounded preview for LLM context."""

    def __init__(self, spill_dir: str = None, max_preview_chars: int = 500):
        self.spill_dir = spill_dir or os.path.join(os.path.dirname(__file__), 'data', 'spill')
        os.makedirs(self.spill_dir, exist_ok=True)
        self.max_preview_chars = max_preview_chars

    def spill(self, data: Any, name: str = None) -> SpillResult:
        text = json.dumps(data, default=str, indent=2) if not isinstance(data, str) else data
        total_size = len(text)

        if total_size <= self.max_preview_chars:
            return SpillResult(preview=text, total_size=total_size, truncated=False)

        preview = text[:self.max_preview_chars] + f"\n... ({total_size} chars total, truncated)"
        file_name = f"{name or 'spill'}_{hash(text) % 10000}.json"
        file_path = os.path.join(self.spill_dir, file_name)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(text)

        return SpillResult(preview=preview, file_path=file_path,
                          total_size=total_size, truncated=True)

    def retrieve(self, file_path: str) -> Optional[str]:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        return None

    def compact_for_llm(self, data: Any, name: str = None) -> str:
        result = self.spill(data, name)
        return result.preview
