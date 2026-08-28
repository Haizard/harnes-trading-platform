"""
Conftest for RBI integration tests.

Suppresses ModelFactory init noise during collection so pytest's
capture mechanism doesn't break. Both model_factory.py and
rbi_agent.py replace sys.stdout at import time (Windows encoding
fix), which steals the file handle from pytest's capture. We
prevent this by pre-importing with stdout/stderr redirected to
a wrapper that suppresses output and provides a .buffer attr.
"""

import sys
import io
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class _QuietStdout:
    """Drop-in stdout replacement that suppresses writes and has .buffer."""

    def __init__(self):
        # Use an independent BytesIO so that when rbi_agent wraps it
        # in a TextIOWrapper, it never touches the real stdout buffer.
        self.buffer = io.BytesIO()
        self.encoding = "utf-8"

    def write(self, s):
        pass

    def flush(self):
        pass


# Pre-import heavy dependencies with stdout/stderr suppressed.
# After the import, the modules are cached in sys.modules so test
# collection reuses them without re-initializing model_factory.
_orig_stdout = sys.stdout
_orig_stderr = sys.stderr
sys.stdout = _QuietStdout()
sys.stderr = _QuietStdout()
try:
    from src.agents import rbi_agent  # noqa: F401
finally:
    sys.stdout = _orig_stdout
    sys.stderr = _orig_stderr
