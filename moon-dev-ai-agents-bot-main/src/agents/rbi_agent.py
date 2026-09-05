"""
Moon Dev's RBI Agent (Research-Backtest-Implement) — Fully Integrated

Lifecycle (6 phases, down from 7 — redundant Package phase removed):
1. Research → validate strategy name, log to session
2. Backtest → generate code, validate syntax + structure + no backtesting.lib
3. Debug → validate + retry with error context (up to 3 attempts)
4. Execute → run backtest with runtime retry loop + error context pass-through
5. Evaluate → AI decides GO_LIVE or REJECT, enhanced with walk-forward + decay data
6. Deploy → convert to BaseStrategy class, register with alpha decay detector

Key integrations (bridging DSH modules into the live pipeline):
  - Session Log: every phase, decision, and error is permanently recorded
  - Runtime retry: execution failures get debug-fix-retry loop (3 attempts)
  - Realistic costs: commission ~3% approximating real Jupiter slippage/fees
  - Human approval gate: interactive confirmation before live deployment
  - Alpha Decay Detector: blocks strategies that have decayed historically
  - Walk-Forward Validation: catches overfitting on training data
  - Strategy Memory: tracks idea→outcome history for self-improvement
  - Post-Deploy Hooks: registers strategy with decay detector + baseline tracker
"""

import os
import sys
import asyncio
import io
import hashlib
import time
import re
import subprocess
import shutil
import itertools
import threading
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from termcolor import cprint

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    # New API (>=0.6) uses instance methods, not class methods
    _ytt_api = YouTubeTranscriptApi()
except ImportError:
    YouTubeTranscriptApi = None
    _ytt_api = None

# Local imports
from src.config import *
from src.models import model_factory
from src.agents.code_validator import CodeValidator

# DSH module imports — bridging standalone modules into the live pipeline
from src.alpha_decay import AlphaDecayDetector, DecayStatus
from src.walk_forward import WalkForwardValidator
from src.feedback_loop import TradeFeedbackLoop

# ── Model Configuration ──────────────────────────────────────
# Coding tasks use qwen.qwen3-coder-next (best for code generation)
# Evaluation uses deepseek.v3.2 (best for reasoning)
# All overridable via env (#11): RBI_MODEL_TYPE, RBI_MODEL_CODER, RBI_MODEL_EVAL
_MODEL_TYPE = os.environ.get("RBI_MODEL_TYPE", "bedrock")
_MODEL_CODER = os.environ.get("RBI_MODEL_CODER", "qwen.qwen3-coder-next")
_MODEL_EVAL = os.environ.get("RBI_MODEL_EVAL", "deepseek.v3.2")
RESEARCH_CONFIG  = {"type": _MODEL_TYPE, "name": _MODEL_CODER}
BACKTEST_CONFIG  = {"type": _MODEL_TYPE, "name": _MODEL_CODER}
DEBUG_CONFIG     = {"type": _MODEL_TYPE, "name": _MODEL_CODER}
EVALUATE_CONFIG  = {"type": _MODEL_TYPE, "name": _MODEL_EVAL}
DEPLOY_CONFIG    = {"type": _MODEL_TYPE, "name": _MODEL_CODER}

# ── Directory Setup ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data/rbi"
RESEARCH_DIR = DATA_DIR / "research"
BACKTEST_DIR = DATA_DIR / "backtests"
FINAL_BACKTEST_DIR = DATA_DIR / "backtests_final"
ARCHIVE_DIR = DATA_DIR / "archive"
LIVE_STRATEGIES_DIR = PROJECT_ROOT / "strategies/custom"
STRATEGY_MEMORY_DIR = DATA_DIR / "strategy_memory"

for d in [DATA_DIR, RESEARCH_DIR, BACKTEST_DIR, FINAL_BACKTEST_DIR,
          ARCHIVE_DIR, LIVE_STRATEGIES_DIR, STRATEGY_MEMORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Data Path ────────────────────────────────────────────────
DATA_PATH = str(DATA_DIR / "BTC-USD-15m.csv")

# ── Retry Limits ─────────────────────────────────────────────
MAX_DEBUG_RETRIES = 3      # Debug phase: AI-fix attempts per validation failure
MAX_EXEC_RETRIES = 3       # Execute phase: runtime retry attempts
EXEC_TIMEOUT = 180         # Default fallback (dynamic timeout used when possible)

# ── Realistic Backtest Costs ─────────────────────────────────
# Real Solana/Jupiter trading: ~1% fee each side + ~0.5% slippage + spread
# Total round-trip ≈ 3%. The old 0.2% commission was dangerously optimistic.
BACKTEST_CASH = 1_000         # Realistic portfolio size (not $1M)
BACKTEST_COMMISSION = 0.015   # ~1.5% per side (includes slippage + fees)

# ── Prompts (optimized for Qwen3-Coder-Next) ────────────────

RESEARCH_PROMPT = """You are Moon Dev's Research AI.

Analyze the following trading idea and create:
1. A UNIQUE TWO-WORD strategy name (PascalCase, no spaces)
2. A detailed strategy specification

Output format:
STRATEGY_NAME: [UniqueTwoWordName]
STRATEGY_DETAILS:
[Detailed strategy description including indicators, timeframes, entry/exit rules]
"""

BACKTEST_PROMPT = """You are Moon Dev's Backtest Code Generator.

Create a COMPLETE, RUNNABLE backtesting.py implementation.

CRITICAL RULES — VIOLATION = AUTOMATIC FAILURE:
1. Import EXACTLY these:
   import pandas as pd
   import numpy as np
   import talib
   from backtesting import Strategy, Backtest

2. Data loading MUST follow this EXACT pattern:
```python
DATA_PATH = "{data_path}"
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip().str.lower()
mapping = {{'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}}
df = df.rename(columns=mapping)
if 'datetime' in df.columns:
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
elif 'timestamp' in df.columns:
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
```

3. ALL indicators MUST use self.I() wrapper:
   - self.I(talib.RSI, self.data.Close, timeperiod=14)
   - self.I(talib.SMA, self.data.Close, timeperiod=20)
   - self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
   - NEVER use backtesting.lib — it is FORBIDDEN

4. Position sizing MUST be:
   size = int(round(size))
   self.buy(size=pos_size, sl=stop_loss, tp=take_profit)

5. Strategy class MUST have:
   class MyStrategy(Strategy):
       def init(self):  # indicators here
       def next(self):  # trading logic here

6. Main block MUST include:
```python
if __name__ == "__main__":
    bt = Backtest(data, MyStrategy, cash={cash}, commission={commission})
    stats = bt.run()
    print(stats)
    print(stats._strategy)
```

7. NEVER use: backtesting.lib, crossignal, crossover from backtesting.lib
8. Use talib for ALL indicators: RSI, SMA, EMA, ATR, MACD, Bollinger Bands, etc.
9. For crossover detection, write manual logic:
   bullish_cross = (fast_ma[-1] > slow_ma[-1]) and (fast_ma[-2] <= slow_ma[-2])

Strategy to implement:
{strategy}

Return ONLY the Python code block. No explanation. No markdown outside the code block.
"""

DEBUG_PROMPT = """You are Moon Dev's Debug Code Fixer.

The following backtest code has errors. Fix ONLY the errors listed below.

ERRORS TO FIX:
{errors}

CURRENT CODE:
```python
{code}
```

RULES:
1. Fix ONLY the listed errors — do not change working code
2. Keep the same strategy logic and indicators
3. Ensure the code still follows backtesting.py conventions
4. Use self.I() for ALL indicators
5. Use talib ONLY — no backtesting.lib
6. Position size must be int(round(size))

Return ONLY the fixed Python code block. No explanation.
"""

EVALUATE_PROMPT = """You are Moon Dev's Performance Analyst.

Analyze these backtest results and decide if the strategy should go live.

BACKTEST STATS:
{stats}

WALK-FORWARD VALIDATION:
{walk_forward}

ALPHA DECAY STATUS:
{decay_status}

Criteria for GO_LIVE:
1. Return must be positive with a good risk/reward profile
2. Max Drawdown < 25% (strict — this is real money)
3. At least 3 trades executed (statistical significance)
4. Win Rate > 45% AND Profit Factor > 1.3
5. Walk-forward out-of-sample return must be positive (no overfitting)
6. Strategy must NOT be in decayed/dead status

Reject if:
- Walk-forward overfit score > 3.0 (strategy only works on training data)
- Strategy is flagged as DECAYED or DEAD by alpha decay detector
- Fewer than 3 trades (not statistically significant)

Respond in this EXACT format:
DECISION: [GO_LIVE or REJECT]
REASONING: [Brief explanation — 2-3 sentences max]
"""

DEPLOY_PROMPT = """You are Moon Dev's Deployment Agent.

Convert this backtest code into a live Strategy Agent class.

REQUIREMENTS:
1. Class must inherit from BaseStrategy
2. Must implement generate_signals(self) method
3. Must return: {{'token': str, 'signal': float, 'direction': str, 'metadata': dict}}
4. Use the SAME indicator and crossover logic from the backtest
5. Use self.data for live market data access

Import at top:
from src.strategies.base_strategy import BaseStrategy
from src.config import MONITORED_TOKENS
import pandas as pd
import talib
from src import nice_funcs as n

Backtest code to convert:
{code}

Return ONLY the Python code block. No explanation.
"""


# ── Session Logger (Synchronous wrapper) ─────────────────────
class RBISessionLogger:
    """
    Synchronous session logger for the RBI pipeline.

    Wraps the async SessionLog with a thin sync layer so the pipeline
    doesn't need asyncio.run() on every call. Events are buffered
    and flushed at pipeline end or on demand.

    Uses the same event types and data format as SessionLog so the
    two are fully interoperable — queries on the CSV work identically.
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or str(DATA_DIR)
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, "rbi_session_log.csv")
        self.session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._events = []

    def log(self, event_type: str, data: dict, signal_id: str = None):
        """Append an event to the session log."""
        event = {
            "id": f"{self.session_id}_{len(self._events):04d}",
            "event_type": event_type,
            "data": json.dumps(data, default=str),
            "timestamp": datetime.utcnow().isoformat(),
            "session_id": self.session_id,
            "signal_id": signal_id or "",
        }
        self._events.append(event)
        self._flush()
        # Best-effort DB persistence (rbi_session_events) — never crashes pipeline
        try:
            from src.db_storage import log_rbi_event
            log_rbi_event(event_type, data, session_id=self.session_id,
                          signal_id=signal_id, event_id=event["id"])
        except Exception:
            pass

    def _flush(self):
        """Write buffered events to CSV."""
        if not self._events:
            return
        try:
            import csv
            file_exists = os.path.exists(self.log_path)
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                if self._events:
                    writer = csv.DictWriter(f, fieldnames=self._events[0].keys())
                    if not file_exists:
                        writer.writeheader()
                    for event in self._events:
                        writer.writerow(event)
            self._events = []
        except Exception:
            pass  # Never crash on logging failure


# ── Strategy Memory (Prompt → Outcome Tracking) ──────────────
class StrategyMemory:
    """
    Tracks the full lifecycle of each strategy from idea to outcome.

    Stores in data/rbi/strategy_memory/strategy_history.jsonl:
    - Idea text → research prompt → strategy name
    - Code hash → backtest result → GO_LIVE/REJECT decision
    - Walk-forward result → alpha decay status
    - If deployed: live performance vs backtest prediction
    """

    def __init__(self, memory_dir: str = None):
        self.memory_dir = memory_dir or str(STRATEGY_MEMORY_DIR)
        os.makedirs(self.memory_dir, exist_ok=True)
        self.history_path = os.path.join(self.memory_dir, "strategy_history.jsonl")

    def record_pipeline_run(self, record: dict):
        """Append a pipeline run record."""
        record["timestamp"] = datetime.utcnow().isoformat()
        try:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass
        # Best-effort DB persistence (rbi_strategies) — never crashes pipeline
        try:
            from src.db_storage import save_rbi_strategy
            save_rbi_strategy(record)
        except Exception:
            pass

    def get_strategy_history(self, strategy_name: str = None, limit: int = 50) -> list:
        """Read strategy history, optionally filtered by name."""
        if not os.path.exists(self.history_path):
            return []
        results = []
        with open(self.history_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if strategy_name and entry.get("strategy_name") != strategy_name:
                        continue
                    results.append(entry)
                except Exception:
                    continue
        return results[-limit:]


# ── Helper Functions ─────────────────────────────────────────

def chat_with_model(system_prompt: str, user_content: str, config: dict) -> str:
    """Call the AI model and return response text"""
    try:
        model = model_factory.get_model(config["type"], config["name"])
        if not model:
            cprint("[RBI] Model not available", "red")
            return None
        response = model.generate_response(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=AI_TEMPERATURE,
            max_tokens=AI_MAX_TOKENS,
        )
        return response.content if response else None
    except Exception as e:
        cprint(f"[RBI] AI Error: {e}", "red")
        return None


def extract_python_code(text: str) -> str:
    """Extract Python code from AI response"""
    if not text:
        return None

    # Try ```python ... ``` blocks
    match = re.search(r'```python\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try generic ``` ... ``` blocks
    match = re.search(r'```\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # If text looks like code, try to use it
    if "import " in text or "class " in text:
        lines = text.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            if any(kw in line for kw in ["import ", "from ", "class "]):
                start_idx = i
                break
        return '\n'.join(lines[start_idx:]).strip()

    return None


def run_with_animation(func, agent_name: str, *args, **kwargs):
    """Run function with spinning animation"""
    stop_animation = threading.Event()

    def animate():
        spinner = itertools.cycle(['-', '\\', '|', '/'])
        while not stop_animation.is_set():
            sys.stdout.write(f'\r{next(spinner)} {agent_name} is working...')
            sys.stdout.flush()
            time.sleep(0.3)
        sys.stdout.write('\r' + ' ' * 50 + '\r')

    t = threading.Thread(target=animate)
    t.start()
    try:
        return func(*args, **kwargs)
    finally:
        stop_animation.set()
        t.join()


def get_idea_content(idea_url: str) -> str:
    """Extract content from a trading idea URL.

    Handles:
    - Single YouTube video URLs
    - YouTube channel URLs (discovers recent videos, extracts all transcripts)
    - Plain text ideas
    """
    try:
        if "youtube.com" in idea_url or "youtu.be" in idea_url:
            # Check if this is a channel URL
            channel_id = _extract_channel_id(idea_url)
            if channel_id:
                return _extract_channel_content(channel_id, idea_url)

            # Single video — use the helper that handles both API versions
            video_id = _extract_video_id(idea_url)
            if video_id:
                transcript = _extract_video_transcript(video_id)
                if transcript:
                    return f"YouTube Transcript: {transcript}"
        return f"Trading Idea: {idea_url}"
    except Exception as e:
        cprint(f"[YOUTUBE] Content extraction failed: {e}", "yellow")
        return idea_url


# ── YouTube Channel Scraper ─────────────────────────────────
# No API key needed — uses YouTube's public RSS feed + transcript API

def _extract_video_id(url: str) -> str:
    """Extract video ID from any YouTube URL format."""
    # youtube.com/watch?v=VIDEO_ID
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    # youtu.be/VIDEO_ID
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]
    # youtube.com/embed/VIDEO_ID
    if "/embed/" in url:
        return url.split("/embed/")[1].split("?")[0]
    # youtube.com/shorts/VIDEO_ID
    if "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    return None


def _extract_channel_id(url: str) -> str:
    """Extract channel ID from various YouTube channel URL formats.

    Supports:
    - youtube.com/channel/UCxxxxxx
    - youtube.com/@handle
    - youtube.com/c/CustomName
    - youtube.com/user/UserName

    Returns channel ID (UC...) or None if not a channel URL.
    """
    url = url.strip().rstrip('/')

    # Direct channel ID: youtube.com/channel/UCxxxxxx
    if '/channel/' in url:
        return url.split('/channel/')[-1].split('/')[0]

    # Handle: youtube.com/@handle
    if '/@' in url:
        handle = url.split('/@')[-1].split('/')[0]
        return _resolve_handle_to_channel_id(handle)

    # Custom URL: youtube.com/c/CustomName
    if '/c/' in url:
        custom_name = url.split('/c/')[-1].split('/')[0]
        return _resolve_handle_to_channel_id(custom_name)

    # Legacy user URL: youtube.com/user/UserName
    if '/user/' in url:
        user_name = url.split('/user/')[-1].split('/')[0]
        return _resolve_handle_to_channel_id(user_name)

    return None


def _resolve_handle_to_channel_id(handle: str) -> str:
    """Resolve a YouTube handle/custom name to a channel ID.

    Method: Fetch the channel page and parse the canonical URL
    which always contains /channel/UCxxxxx.
    """
    try:
        url = f"https://www.youtube.com/@{handle}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Look for canonical URL containing channel ID
        match = re.search(r'href="https://www\.youtube\.com/channel/(UC[\w-]+)"', html)
        if match:
            return match.group(1)

        # Alternative: look for externalId in page data
        match = re.search(r'"externalId":"(UC[\w-]+)"', html)
        if match:
            return match.group(1)

        # Another pattern: channelId in meta tags
        match = re.search(r'"channelId":"(UC[\w-]+)"', html)
        if match:
            return match.group(1)

        cprint(f"[YOUTUBE] Could not resolve channel ID for @{handle}", "yellow")
        return None
    except Exception as e:
        cprint(f"[YOUTUBE] Failed to resolve handle @{handle}: {e}", "yellow")
        return None


def _get_channel_video_ids(channel_id: str, max_videos: int = 15) -> list:
    """Fetch recent video IDs from a channel using YouTube's RSS feed.

    No API key needed. Returns up to max_videos video IDs.
    RSS feed typically contains the last 15 videos.
    """
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    cprint(f"[YOUTUBE] Fetching RSS feed for channel {channel_id}...", "cyan")

    try:
        req = urllib.request.Request(rss_url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RBI-Bot/1.0)'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode('utf-8')

        # Parse XML — YouTube uses Atom namespace
        root = ET.fromstring(xml_data)

        # Namespace handling
        ns = {'atom': 'http://www.w3.org/2005/Atom',
              'media': 'http://search.yahoo.com/mrss/',
              'yt': 'http://www.youtube.com/xml/schemas/2015'}

        videos = []
        for entry in root.findall('atom:entry', ns):
            video_id_el = entry.find('yt:videoId', ns)
            title_el = entry.find('atom:title', ns)
            if video_id_el is not None and video_id_el.text:
                videos.append({
                    'video_id': video_id_el.text,
                    'title': title_el.text if title_el is not None else 'Unknown',
                    'url': f'https://www.youtube.com/watch?v={video_id_el.text}'
                })
            if len(videos) >= max_videos:
                break

        cprint(f"[YOUTUBE] Found {len(videos)} videos on channel", "green")
        return videos
    except Exception as e:
        cprint(f"[YOUTUBE] RSS feed failed: {e}", "yellow")
        return []


def _extract_video_transcript(video_id: str) -> str:
    """Extract transcript from a single YouTube video.

    Supports both old API (class method) and new API (instance method).
    """
    if not _ytt_api:
        return None
    try:
        # New API (>=0.6): instance.fetch() returns FetchedTranscript
        result = _ytt_api.fetch(video_id)
        if hasattr(result, 'snippets'):
            return ' '.join([s.text for s in result.snippets])
        # Fallback: old API returns list of dicts
        elif isinstance(result, list):
            return ' '.join([t['text'] for t in result])
        return str(result)
    except AttributeError:
        # Very old API fallback
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            return ' '.join([t['text'] for t in transcript])
        except Exception:
            return None
    except Exception:
        return None


def _extract_channel_content(channel_id: str, original_url: str) -> str:
    """Discover videos from a YouTube channel and extract transcripts.

    Returns a combined string of all video transcripts with titles,
    formatted for the Research AI to analyze.
    """
    cprint(f"\n[YOUTUBE] 🎬 Channel detected — discovering videos...", "magenta")

    videos = _get_channel_video_ids(channel_id)
    if not videos:
        cprint("[YOUTUBE] No videos found — falling back to raw URL", "yellow")
        return f"Trading Idea: {original_url}"

    # Filter for likely trading-related videos by title keywords
    trading_keywords = [
        'trading', 'strategy', 'backtest', 'indicator', 'signal',
        'buy', 'sell', 'entry', 'exit', 'stop loss', 'take profit',
        'rsi', 'ema', 'sma', 'macd', 'bollinger', 'atr',
        'scalp', 'swing', 'day trad', 'crypto', 'bitcoin', 'btc',
        'solana', 'sol', 'forex', 'chart', 'technical', 'price action',
        'candlestick', 'momentum', 'reversal', 'breakout', 'trend',
        'algorithm', 'algo', 'bot', 'automat', 'quant', 'edge',
        'profit', 'risk management', 'portfolio', 'alpha',
    ]

    def is_trading_related(title: str) -> bool:
        title_lower = title.lower()
        return any(kw in title_lower for kw in trading_keywords)

    # Sort: trading-related first, then by recency
    videos.sort(key=lambda v: (not is_trading_related(v['title'])))

    cprint(f"[YOUTUBE] Extracting transcripts from {len(videos)} videos...", "cyan")

    combined_parts = []
    successful = 0
    failed = 0

    for i, video in enumerate(videos):
        title = video['title']
        video_id = video['video_id']
        trading_tag = "📊" if is_trading_related(title) else "  "

        cprint(f"  {trading_tag} [{i+1}/{len(videos)}] {title[:60]}...", "cyan")

        transcript = _extract_video_transcript(video_id)
        if transcript and len(transcript) > 50:  # Skip very short transcripts
            combined_parts.append(
                f"\n{'='*60}\n"
                f"VIDEO: {title}\n"
                f"URL: https://www.youtube.com/watch?v={video_id}\n"
                f"{'='*60}\n"
                f"{transcript}\n"
            )
            successful += 1
        else:
            failed += 1

    if not combined_parts:
        cprint("[YOUTUBE] No transcripts extracted — falling back to raw URL", "yellow")
        return f"Trading Idea: {original_url}"

    result = (
        f"YouTube Channel Content — {successful} transcripts extracted "
        f"({failed} unavailable)\n"
        f"Analyze the following videos and identify the best trading strategy to backtest.\n"
        f"Focus on the videos marked with 📊 as they are most likely trading-related.\n"
        f"\n{'#'*60}\n"
        f"{'#'*60}\n"
        + '\n'.join(combined_parts)
    )

    cprint(f"[YOUTUBE] ✅ Extracted {successful} transcripts ({failed} failed)", "green")
    cprint(f"[YOUTUBE] Total content length: {len(result):,} characters", "cyan")

    return result


def _parse_backtest_stats(output: str) -> dict:
    """Parse backtest stats from subprocess output into a dict."""
    stats = {}
    if not output:
        return stats

    # Try to parse key stats from output
    # backtesting.py formats like:  Return [%]   15.3241
    # Some outputs use colons:     Return [%]:  15.3241
    # Use flexible whitespace matching for both
    patterns = {
        "Return [%]": r"Return \[%\]\s*:?\s+([-\d.]+)",
        "Max. Drawdown [%]": r"Max\. Drawdown \[%\]\s*:?\s+([-\d.]+)",
        "Win Rate [%]": r"Win Rate \[%\]\s*:?\s+([-\d.]+)",
        "Sharpe Ratio": r"Sharpe Ratio\s*:?\s+([-\d.]+)",
        "Profit Factor": r"Profit Factor\s*:?\s+([-\d.]+)",
        "# Trades": r"# Trades\s*:?\s+(\d+)",
        "Avg. Trade [%]": r"Avg\. Trade \[%\]\s*:?\s+([-\d.]+)",
        "Max. Consecutive Losses": r"Max\. Consecutive Losses\s*:?\s+(\d+)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            try:
                stats[key] = float(match.group(1))
            except ValueError:
                stats[key] = match.group(1)

    return stats


# ── Global Instances ─────────────────────────────────────────
validator = CodeValidator()
alpha_detector = AlphaDecayDetector({
    "min_trades": 5,
    "decay_win_rate": 0.35,
    "dead_win_rate": 0.25,
    "max_drawdown": 0.15,
})
walk_forward_validator = WalkForwardValidator(train_days=60, test_days=7)
feedback_loop = TradeFeedbackLoop(history_dir=str(DATA_DIR))
strategy_memory = StrategyMemory()



# ── Security: Prompt Injection Sanitization ──────────────────
_INJECTION_PATTERNS = [
    r'(?i)ignore\s+(all\s+)?previous\s+instructions',
    r'(?i)you\s+are\s+now\s+(a|an)\s+',
    r'(?i)disregard\s+(all\s+)?prior',
    r'(?i)override\s+(your\s+)?instructions',
    r'(?i)new\s+instructions?:',
    r'(?i)system\s*:\s*',
    r'(?i)<\s*script',
]

MAX_IDEA_LENGTH = 15000


def sanitize_user_input(text: str) -> str:
    if not text:
        return text
    original_length = len(text)
    text = re.sub(r'[--]', '', text)
    injection_found = []
    for pattern in _INJECTION_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            injection_found.extend(matches)
    if injection_found:
        cprint(f"[SECURITY] Detected {len(injection_found)} potential injection(s)", "red")
        for pattern in _INJECTION_PATTERNS:
            text = re.sub(pattern, '[FILTERED]', text)
    if len(text) > MAX_IDEA_LENGTH:
        text = text[:MAX_IDEA_LENGTH] + '[TRUNCATED]'
        cprint(f"[SECURITY] Input truncated from {original_length} to {MAX_IDEA_LENGTH} chars", "yellow")
    return text


# ── Multi-Asset Data Resolver ────────────────────────────────
ASSET_DATA = {
    "BTC": str(DATA_DIR / "BTC-USD-15m.csv"),
    "ETH": str(DATA_DIR / "ETH-USD-15m.csv"),
    "SOL": str(DATA_DIR / "SOL-USD-15m.csv"),
}

ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc", "satoshi"],
    "ETH": ["ethereum", "eth", "vitalik", "gas", "defi"],
    "SOL": ["solana", "sol", "jupiter", "raydium", "orca", "pump.fun"],
}


def get_strategy_asset_target(strategy_text: str) -> str:
    text_lower = strategy_text.lower()
    for asset, keywords in ASSET_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return asset
    return "BTC"


def download_asset_data(asset: str) -> str:
    target_path = ASSET_DATA.get(asset)
    if not target_path:
        return None
    if os.path.exists(target_path):
        return target_path
    yf_tickers = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}
    ticker = yf_tickers.get(asset)
    if not ticker:
        return None
    try:
        import yfinance as yf
        cprint(f"[DATA] Downloading {asset} data from Yahoo Finance...", "cyan")
        data = yf.download(ticker, period="2y", interval="15m", progress=False)
        if data.empty:
            cprint(f"[DATA] No data returned for {ticker}", "yellow")
            return None
        if hasattr(data.columns, 'levels'):
            data.columns = data.columns.get_level_values(0)
        data.to_csv(target_path)
        cprint(f"[DATA] Saved {len(data)} rows to {target_path}", "green")
        return target_path
    except ImportError:
        cprint("[DATA] yfinance not installed", "yellow")
        return None
    except Exception as e:
        cprint(f"[DATA] Download failed for {asset}: {e}", "yellow")
        return None


def resolve_data_path(strategy_text: str) -> str:
    """Resolve backtest data for a strategy.

    Priority (#5):
      1. Fresh OHLCV from our own ohlcv_candles DB table (live-collected,
         asset-appropriate: SOL-USDC for Solana strategies) written to a
         temp CSV — avoids validating every idea against the same stale
         BTC file.
      2. Static per-asset CSV (ASSET_DATA) if it exists.
      3. Downloaded data.
      4. Fallback: BTC-USD-15m.csv.
    """
    asset = get_strategy_asset_target(strategy_text)
    cprint(f"[DATA] Strategy targets: {asset}", "cyan")

    # Prefer fresh DB-collected candles (#5)
    fresh = _fresh_data_from_db(asset)
    if fresh:
        return fresh

    # TradingView feed candles (gap 1): same data source family the live
    # chart/pine systems use — better regime match than the stale BTC file
    tv = _tv_data_for_asset(asset)
    if tv:
        return tv

    path = ASSET_DATA.get(asset, DATA_PATH)
    if os.path.exists(path):
        return path
    downloaded = download_asset_data(asset)
    if downloaded:
        return downloaded
    cprint("[DATA] Falling back to BTC data", "yellow")
    return DATA_PATH


def _tv_data_for_asset(asset: str, min_bars: int = 300) -> str:
    """Fetch recent candles from the TradingView feed to a temp CSV (gap 1).

    Same data source family as the live chart/pine systems, so backtest
    validation happens on data with the same regime as live trading.
    """
    tv_map = {"BTC": "BTCUSDT", "SOL": "SOLUSDT", "ETH": "ETHUSDT"}
    base = asset.split("/")[0].strip().upper() if "/" in asset else asset.upper()
    tv_sym = tv_map.get(base)
    if not tv_sym:
        return None
    try:
        from src.tradingview_feed import get_tradingview_feed
        ohlcv = get_tradingview_feed().get_ohlcv_candles(
            symbol=tv_sym, interval="15m", limit=1000)
        candles = ohlcv.get("candles") if isinstance(ohlcv, dict) else None
        if not candles or len(candles) < min_bars:
            return None
        import tempfile
        df = pd.DataFrame([{
            "datetime": pd.to_datetime(c["time"], unit="s", utc=True),
            "Open": float(c["open"]), "High": float(c["high"]),
            "Low": float(c["low"]), "Close": float(c["close"]),
            "Volume": float(c.get("volume") or 0),
        } for c in candles])
        path = os.path.join(tempfile.gettempdir(), f"rbi_{base}_tv.csv")
        df.to_csv(path, index=False)
        cprint(f"[DATA] Using {len(df)} TradingView bars for {base} -> {path}", "green")
        return path
    except Exception as e:
        cprint(f"[DATA] TradingView data unavailable ({e})", "yellow")
        return None


def _fresh_data_from_db(asset: str, min_bars: int = 300) -> str:
    """Export recent candles from ohlcv_candles to a temp CSV (#5).

    Solana strategies use SOL-USDC (the ecosystem's base pair); BTC uses
    BTC-USDC. Returns None when the DB lacks enough recent bars, so the
    caller falls back to static/downloaded data.
    """
    symbol_map = {"SOL": "SOL", "BTC": "BTC"}
    base = symbol_map.get(asset.split("/")[0].strip().upper()
                          if "/" in asset else asset.upper())
    if not base:
        return None
    token_address = f"{base}-USDC"  # collector's symbol-keyed addresses
    try:
        from src.db_storage import get_ohlcv_candles
        rows = get_ohlcv_candles(token_address, hours=24 * 14, limit=1500)
        if not rows or len(rows) < min_bars:
            cprint(f"[DATA] DB has {len(rows) if rows else 0} fresh {base} bars "
                   f"(<{min_bars}) — using static data", "yellow")
            return None
        import tempfile
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"])
        path = os.path.join(tempfile.gettempdir(), f"rbi_{base}_fresh.csv")
        df[["datetime", "Open", "High", "Low", "Close", "Volume"]].to_csv(path, index=False)
        cprint(f"[DATA] Using {len(df)} fresh DB bars for {base} -> {path}", "green")
        return path
    except Exception as e:
        cprint(f"[DATA] Fresh DB data unavailable ({e}) — using static data", "yellow")
        return None


# ── Dynamic Timeout Calculator ───────────────────────────────
BASE_TIMEOUT = 60
TIMEOUT_PER_10K_ROWS = 15
MAX_TIMEOUT = 600


def calculate_timeout(data_path: str) -> int:
    try:
        with open(data_path, "r") as f:
            row_count = sum(1 for _ in f) - 1
        timeout = BASE_TIMEOUT + (row_count // 10000) * TIMEOUT_PER_10K_ROWS
        timeout = min(timeout, MAX_TIMEOUT)
        timeout = max(timeout, BASE_TIMEOUT)
        cprint(f"[TIMEOUT] {row_count:,} rows -> {timeout}s timeout", "cyan")
        return timeout
    except Exception:
        return 180


# ── Pre-Execution Import Validation ──────────────────────────
REQUIRED_BACKTEST_IMPORTS = ["pandas", "numpy", "talib", "backtesting"]


def validate_backtest_imports(code: str) -> tuple:
    errors = []
    needed = set()

    # Find ALL import statements in the code
    import_pattern = r'(?:^|\s)import\s+(\w+)'
    from_pattern = r'(?:^|\s)from\s+(\w+)\s+import'
    for match in re.finditer(import_pattern, code):
        needed.add(match.group(1))
    for match in re.finditer(from_pattern, code):
        needed.add(match.group(1))

    # Try to import each module
    for module in needed:
        try:
            __import__(module)
        except ImportError as e:
            errors.append(f"Missing required module '{module}': {e}")
        except Exception as e:
            errors.append(f"Error importing '{module}': {e}")
    return (len(errors) == 0, errors)


# ── Phase Functions ──────────────────────────────────────────

def research_strategy(content: str, session_log: RBISessionLogger) -> tuple:
    """Phase 1: Research the strategy idea"""
    cprint("\n[PHASE 1] Researching strategy...", "cyan")
    session_log.log("signal/generated", {"phase": "research", "idea_preview": content[:200]})

    output = run_with_animation(chat_with_model, "Research Agent", RESEARCH_PROMPT, content, RESEARCH_CONFIG)
    if not output:
        session_log.log("agent/error", {"phase": "research", "error": "No output from research AI"})
        return None, None

    name = "UnknownStrategy"
    if "STRATEGY_NAME:" in output:
        raw_name = output.split("STRATEGY_NAME:")[1].split("\n")[0].strip()
        name = re.sub(r'[^\w]', '', raw_name)
        if not name:
            name = "UnknownStrategy"

    session_log.log("signal/validated", {
        "phase": "research", "strategy_name": name, "output_length": len(output)
    })
    cprint(f"[PHASE 1] Strategy name: {name}", "green")
    return output, name


def create_backtest(strategy: str, name: str, session_log: RBISessionLogger, data_path: str = None) -> str:
    """Phase 2: Generate backtest code with validation + backtesting.lib check (merged from old Phase 3)"""
    cprint("\n[PHASE 2] Generating backtest code...", "cyan")

    # Use resolved data path if provided, otherwise default
    actual_data_path = data_path or DATA_PATH

    prompt = BACKTEST_PROMPT.format(
        strategy=strategy,
        data_path=actual_data_path,
        cash=BACKTEST_CASH,
        commission=BACKTEST_COMMISSION,
    )

    output = run_with_animation(chat_with_model, "Backtest Agent", prompt, strategy, BACKTEST_CONFIG)
    if not output:
        session_log.log("agent/error", {"phase": "backtest", "error": "No output from backtest AI"})
        return None

    code = extract_python_code(output)
    if not code:
        cprint("[PHASE 2] Could not extract code from response", "red")
        session_log.log("agent/error", {"phase": "backtest", "error": "Could not extract code from AI response"})
        return None

    # Validate (includes syntax + structure + no backtesting.lib)
    result = validator.validate_all(code, phase="backtest")
    if not result.passed:
        cprint(f"[PHASE 2] Validation failed, attempting fix...", "yellow")
        session_log.log("signal/validated", {
            "phase": "backtest", "passed": False, "errors": result.errors[:3]
        })
        code = _try_fix_code(code, result.errors, DEBUG_CONFIG, "backtest", session_log)
        if not code:
            return None
    else:
        session_log.log("signal/validated", {"phase": "backtest", "passed": True})

    # Save
    path = BACKTEST_DIR / f"{name}_BT.py"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    cprint(f"[PHASE 2] Saved to {path}", "green")

    return code


def debug_backtest(code: str, name: str, session_log: RBISessionLogger) -> str:
    """Phase 3: Debug with programmatic retry loop"""
    cprint("\n[PHASE 3] Debugging with validation...", "cyan")

    current_code = code
    for attempt in range(MAX_DEBUG_RETRIES):
        cprint(f"[PHASE 3] Debug attempt {attempt + 1}/{MAX_DEBUG_RETRIES}", "cyan")

        # Validate current code
        result = validator.validate_all(current_code, phase="debug")
        if result.passed:
            cprint("[PHASE 3] Code passed validation", "green")
            session_log.log("signal/validated", {"phase": "debug", "passed": True, "attempt": attempt + 1})
            break

        session_log.log("signal/validated", {
            "phase": "debug", "passed": False, "attempt": attempt + 1, "errors": result.errors[:3]
        })

        # Try to fix
        fixed_code = _try_fix_code(current_code, result.errors, DEBUG_CONFIG, "debug", session_log)
        if fixed_code and fixed_code != current_code:
            current_code = fixed_code
        else:
            cprint(f"[PHASE 3] Could not fix code on attempt {attempt + 1}", "yellow")
            break

    # Final validation
    result = validator.validate_all(current_code, phase="final")
    if not result.passed:
        cprint("[PHASE 3] Final validation still has issues", "yellow")
        session_log.log("signal/validated", {
            "phase": "debug_final", "passed": False, "errors": result.errors[:3]
        })

    # Save
    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(current_code)
    cprint(f"[PHASE 3] Saved to {path}", "green")

    return current_code


def _try_fix_code(code: str, errors: list, config: dict, phase: str,
                   session_log: RBISessionLogger = None) -> str:
    """Attempt to fix code using AI with error context"""
    context = validator.generate_fix_context(code, errors)
    output = run_with_animation(chat_with_model, f"Debug Fix ({phase})", context, code, config)
    if not output:
        if session_log:
            session_log.log("agent/error", {"phase": f"debug_fix_{phase}", "error": "No fix output from AI"})
        return None

    fixed_code = extract_python_code(output)
    if not fixed_code:
        return None

    # Validate the fix
    result = validator.validate_all(fixed_code, phase=phase)
    if not result.passed:
        cprint(f"[DEBUG] Fix attempt still has errors: {result.errors[:2]}", "yellow")

    return fixed_code


def execute_backtest(name: str, session_log: RBISessionLogger) -> tuple:
    """
    Phase 4: Execute the backtest with runtime retry loop.

    Returns (stats_output, error_context):
      - stats_output: raw subprocess output if successful
      - error_context: dict with error details if all retries failed

    If execution fails, error_context is passed back to debug phase
    for AI-assisted fix (feedback loop).
    """
    cprint("\n[PHASE 4] Executing backtest...", "cyan")

    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    if not path.exists():
        cprint(f"[PHASE 4] Backtest file not found: {path}", "red")
        session_log.log("agent/error", {"phase": "execute", "error": f"File not found: {path}"})
        return None, {"error": "file_not_found", "path": str(path)}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    # Pre-execution import validation — catch missing modules before subprocess
    code_text = path.read_text(encoding='utf-8')
    imports_ok, import_errors = validate_backtest_imports(code_text)
    if not imports_ok:
        cprint(f"[PHASE 4] Import validation failed: {import_errors}", "yellow")
        session_log.log("agent/error", {
            "phase": "execute", "error": "import_validation_failed",
            "details": import_errors
        })
        # Don't fail immediately — the subprocess might have different env
        # Just log the warning and proceed

    # Dynamic timeout based on data file size
    data_path_for_timeout = DATA_PATH  # Could be made smarter
    timeout = calculate_timeout(data_path_for_timeout)

    last_error_context = None

    for attempt in range(MAX_EXEC_RETRIES):
        cprint(f"[PHASE 4] Execution attempt {attempt + 1}/{MAX_EXEC_RETRIES}", "cyan")

        try:
            res = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, env=env, timeout=timeout,
            )
            output = res.stdout + "\n" + res.stderr

            if res.returncode == 0:
                cprint("[PHASE 4] Backtest execution complete", "green")
                session_log.log("signal/validated", {
                    "phase": "execute", "passed": True, "attempt": attempt + 1
                })
                return output, None

            # Non-zero exit — parse error for debug feedback
            error_context = {
                "error": "runtime_error",
                "returncode": res.returncode,
                "stderr": res.stderr[:1000],
                "stdout": res.stdout[:500],
                "attempt": attempt + 1,
            }
            last_error_context = error_context
            cprint(f"[PHASE 4] Backtest exited with code {res.returncode} (attempt {attempt + 1})", "yellow")
            cprint(f"[PHASE 4] Error: {res.stderr[:300]}", "yellow")

            session_log.log("agent/error", {
                "phase": "execute", "attempt": attempt + 1,
                "returncode": res.returncode, "error_preview": res.stderr[:200]
            })

            # If we have error context, try to AI-fix the code and re-execute
            if attempt < MAX_EXEC_RETRIES - 1:
                cprint(f"[PHASE 4] Attempting AI-assisted fix...", "cyan")
                fixed_code = _try_fix_code_from_runtime(
                    path.read_text(encoding='utf-8'), res.stderr, name, session_log
                )
                if fixed_code:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(fixed_code)
                    cprint(f"[PHASE 4] Code fixed, retrying execution...", "cyan")

        except subprocess.TimeoutExpired:
            error_context = {
                "error": "timeout",
                "timeout_seconds": EXEC_TIMEOUT,
                "attempt": attempt + 1,
            }
            last_error_context = error_context
            cprint(f"[PHASE 4] Backtest timed out ({EXEC_TIMEOUT}s, attempt {attempt + 1})", "yellow")
            session_log.log("agent/error", {
                "phase": "execute", "attempt": attempt + 1, "error": "timeout"
            })

        except Exception as e:
            error_context = {
                "error": "exception",
                "message": str(e),
                "attempt": attempt + 1,
            }
            last_error_context = error_context
            cprint(f"[PHASE 4] Execution failed: {e}", "red")
            session_log.log("agent/error", {
                "phase": "execute", "attempt": attempt + 1, "error": str(e)
            })

    # All retries exhausted
    cprint(f"[PHASE 4] All {MAX_EXEC_RETRIES} execution attempts failed", "red")
    return None, last_error_context


def _try_fix_code_from_runtime(code: str, stderr: str, name: str,
                                session_log: RBISessionLogger) -> str:
    """
    AI-assisted fix for runtime errors (not just syntax).
    Passes the actual runtime error to the debug AI for context-aware fixing.
    """
    # Extract meaningful error lines from stderr
    error_lines = []
    for line in stderr.split('\n'):
        if any(kw in line.lower() for kw in ['error', 'traceback', 'exception', 'import', 'module']):
            error_lines.append(line.strip())

    if not error_lines:
        return None

    errors_text = '\n'.join(error_lines[:10])  # Top 10 error lines

    context = f"""The following backtest code has RUNTIME ERRORS (not syntax).
Fix the errors so the code runs successfully.

RUNTIME ERRORS:
{errors_text}

CURRENT CODE:
```python
{code}
```

RULES:
1. Fix the specific runtime errors listed above
2. Keep the same strategy logic and indicators
3. Use self.I() for ALL indicators
4. Use talib ONLY — no backtesting.lib
5. Position size must be int(round(size))

Return ONLY the fixed Python code block. No explanation.
"""
    output = run_with_animation(chat_with_model, "Runtime Debug Fix", context, code, DEBUG_CONFIG)
    if not output:
        return None

    fixed_code = extract_python_code(output)
    if not fixed_code:
        return None

    # Quick validation
    result = validator.validate_all(fixed_code, phase="runtime_fix")
    if not result.passed:
        cprint(f"[RUNTIME DEBUG] Fix still has validation errors: {result.errors[:2]}", "yellow")
        return None

    session_log.log("signal/validated", {
        "phase": "runtime_fix", "strategy_name": name, "fixed": True
    })
    return fixed_code


def evaluate_performance(stats: str, walk_forward_result=None,
                         decay_status=None, session_log: RBISessionLogger = None) -> tuple:
    """
    Phase 5: AI evaluates the backtest results.

    Enhanced with:
    - Walk-forward validation data (catches overfitting)
    - Alpha decay status (catches degraded strategies)
    - Stricter criteria for GO_LIVE
    """
    cprint("\n[PHASE 5] Evaluating performance...", "cyan")

    if not stats:
        session_log.log("signal/validated", {"phase": "evaluate", "decision": "REJECT", "reason": "No stats"})
        return "REJECT", "No stats output to evaluate"

    # Format walk-forward data for evaluator
    wf_text = "No walk-forward data available"
    if walk_forward_result:
        wf_text = (
            f"In-sample return: {walk_forward_result.in_sample_return:.2%}\n"
            f"Out-of-sample return: {walk_forward_result.out_of_sample_return:.2%}\n"
            f"Overfit score: {walk_forward_result.overfit_score:.2f} (lower is better, <3.0 is good)\n"
            f"Periods tested: {walk_forward_result.periods_tested}\n"
            f"Deployable: {walk_forward_result.deployable}"
        )

    # Format decay data for evaluator
    decay_text = "No decay data available"
    if decay_status:
        decay_text = (
            f"Status: {decay_status.status.value}\n"
            f"Win rate: {decay_status.win_rate:.1%}\n"
            f"Recent win rate: {decay_status.recent_win_rate:.1%}\n"
            f"Sharpe ratio: {decay_status.sharpe_ratio:.2f}\n"
            f"Max drawdown: {decay_status.max_drawdown:.1%}\n"
            f"Total trades: {decay_status.total_trades}\n"
            f"Recommendation: {decay_status.recommendation}"
        )

    output = run_with_animation(
        chat_with_model, "Evaluation Agent",
        EVALUATE_PROMPT.format(
            stats=stats,
            walk_forward=wf_text,
            decay_status=decay_text,
        ),
        stats, EVALUATE_CONFIG,
    )

    # Robust decision parsing (#7): substring matching produced false
    # positives on reasoning like "do NOT GO_LIVE". Require the decision
    # marker on its own (word boundary), not preceded by NOT/never.
    decision = "REJECT"
    if output:
        upper = output.upper()
        for m in re.finditer(r"GO[_\s]?LIVE", upper):
            prefix = upper[max(0, m.start() - 30):m.start()]
            if not re.search(r"\b(NOT|NEVER|NO|REJECT\w*|DENY\w*|REFUSE\w*)\s*$", prefix):
                decision = "GO_LIVE"
                break

    # Hard overrides — AI can't override these safety checks
    if walk_forward_result and not walk_forward_result.deployable:
        if decision == "GO_LIVE":
            cprint("[PHASE 5] Walk-forward override: REJECT (overfitting detected)", "yellow")
            decision = "REJECT"
            output = (output or "") + "\n[OVERRIDE] Walk-forward validation failed — strategy is overfitted."

    if decay_status and decay_status.status in (DecayStatus.DECAYED, DecayStatus.DEAD):
        if decision == "GO_LIVE":
            cprint(f"[PHASE 5] Decay override: REJECT ({decay_status.status.value})", "yellow")
            decision = "REJECT"
            output = (output or "") + f"\n[OVERRIDE] Alpha decay detected — strategy is {decay_status.status.value}."

    session_log.log("signal/validated", {
        "phase": "evaluate", "decision": decision,
        "walk_forward_deployable": walk_forward_result.deployable if walk_forward_result else None,
        "decay_status": decay_status.status.value if decay_status else None,
    })
    cprint(f"[PHASE 5] Decision: {decision}", "green" if decision == "GO_LIVE" else "yellow")
    return decision, output


def human_approval_gate(name: str, stats: dict, reasoning: str,
                        auto_mode: bool = False,
                        approval_callback=None) -> bool:
    """
    Human approval gate before live deployment.

    Shows backtest stats and asks for confirmation.
    Priority (#3):
      1. approval_callback (web UI) — real human review, no skipping
      2. auto_mode — explicit opt-in for unattended batch runs
      3. CLI interactive input()
    """
    cprint("\n" + "=" * 60, "magenta")
    cprint("🛑 HUMAN APPROVAL REQUIRED", "white", "on_red")
    cprint("=" * 60, "magenta")
    cprint(f"\n📊 Strategy: {name}", "cyan")
    cprint(f"   Return: {stats.get('Return [%]', 'N/A')}%", "cyan")
    cprint(f"   Max Drawdown: {stats.get('Max. Drawdown [%]', 'N/A')}%", "cyan")
    cprint(f"   Win Rate: {stats.get('Win Rate [%]', 'N/A')}%", "cyan")
    cprint(f"   Sharpe Ratio: {stats.get('Sharpe Ratio', 'N/A')}", "cyan")
    cprint(f"   # Trades: {stats.get('# Trades', 'N/A')}", "cyan")

    if reasoning:
        # Show first 200 chars of reasoning
        cprint(f"\n   AI Assessment: {reasoning[:200]}", "blue")

    if approval_callback is not None:
        cprint("\n   [WEB] Waiting for approval via dashboard...", "cyan")
        try:
            return bool(approval_callback(name, stats, reasoning))
        except Exception as e:
            cprint(f"\n   [GATE ERROR] {e} — rejecting", "yellow")
            return False

    if auto_mode:
        cprint("\n   [AUTO MODE] Skipping human approval", "yellow")
        return True

    cprint("\n   Deploy this strategy to live? (y/n)", "yellow", end=" ")
    try:
        response = input().strip().lower()
        return response == 'y'
    except (EOFError, KeyboardInterrupt):
        cprint("\n   [CANCELLED] No input received — rejecting", "yellow")
        return False


def deploy_to_live(code: str, name: str, session_log: RBISessionLogger) -> bool:
    """Phase 6: Deploy to live strategies + register with alpha decay detector"""
    cprint("\n[PHASE 6] Deploying to live...", "cyan")
    session_log.log("order/intent", {"phase": "deploy", "strategy_name": name, "action": "deploy_to_live"})

    prompt = DEPLOY_PROMPT.format(code=code)
    output = run_with_animation(chat_with_model, "Deployment Agent", prompt, code, DEPLOY_CONFIG)

    if output:
        live_code = extract_python_code(output)
        if live_code:
            path = LIVE_STRATEGIES_DIR / f"{name.lower()}.py"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(live_code)

            # Register with alpha decay detector for future monitoring
            alpha_detector.record_trade(name, pnl_pct=0.0)  # Baseline entry

            session_log.log("order/submitted", {
                "phase": "deploy", "strategy_name": name,
                "path": str(path), "action": "deployed"
            })
            cprint(f"[PHASE 6] DEPLOYED TO: {path}", "green")
            return True

    session_log.log("agent/error", {"phase": "deploy", "error": "Deployment AI failed"})
    cprint("[PHASE 6] Deployment failed", "red")
    return False


def archive_strategy(name: str, session_log: RBISessionLogger):
    """Archive a rejected strategy"""
    cprint(f"[ARCHIVE] Archiving {name}...", "yellow")
    session_log.log("position/closed", {"phase": "archive", "strategy_name": name, "action": "archived"})
    for d in [RESEARCH_DIR, BACKTEST_DIR, FINAL_BACKTEST_DIR]:
        for f in d.glob(f"{name}*"):
            try:
                shutil.move(str(f), str(ARCHIVE_DIR / f.name))
            except Exception:
                pass


# ── Main Pipeline ────────────────────────────────────────────

def process_trading_idea(idea: str, auto_mode: bool = False,
                         approval_callback=None, cancel_check=None):
    """
    Process a single trading idea through the full integrated RBI pipeline.

    Pipeline: Research → Backtest → Debug → Execute → Evaluate → Deploy
    (Package phase removed — validation merged into Phase 2)

    Integrations:
    - Session Log records every phase/decision/error
    - Runtime retry loop in Execute phase
    - Walk-Forward validation before Evaluate
    - Alpha Decay check before Deploy
    - Human approval gate before Deploy (or approval_callback from web UI)
    - Strategy Memory tracks full lifecycle + code-hash dedupe (#8)
    - cancel_check() polled between phases for cooperative cancel (#9)
    """
    cprint(f"\n{'='*60}", "magenta")
    cprint(f"RBI PIPELINE (INTEGRATED): {idea[:50]}...", "magenta")
    cprint(f"{'='*60}\n", "magenta")

    start_time = time.time()
    signal_id = datetime.utcnow().strftime("%H%M%S")
    session_log = RBISessionLogger()
    memory_record = {"idea": idea[:500], "signal_id": signal_id,
                     "session_id": session_log.session_id}

    def _cancelled() -> bool:
        try:
            return bool(cancel_check()) if cancel_check else False
        except Exception:
            return False

    def _bail(reason: str):
        memory_record["result"] = "CANCELLED"
        memory_record["reason"] = reason
        strategy_memory.record_pipeline_run(memory_record)
        cprint(f"[RBI] Pipeline cancelled: {reason}", "yellow")

    try:
        content = get_idea_content(idea)

        # Security: sanitize user input before passing to AI
        content = sanitize_user_input(content)

        session_log.log("model/call", {"phase": "init", "idea_preview": content[:200]}, signal_id)

        if _cancelled():
            return _bail("before research")

        # Phase 1: Research
        strategy, name = research_strategy(content, session_log)
        if not strategy:
            cprint("[RBI] Phase 1 failed — no strategy generated", "red")
            memory_record["result"] = "REJECT"
            memory_record["reason"] = "Phase 1 failed — no strategy"
            strategy_memory.record_pipeline_run(memory_record)
            return
        memory_record["strategy_name"] = name

        if _cancelled():
            return _bail("after research")

        # Phase 2: Backtest (with validation + backtesting.lib check)
        # Resolve appropriate data file based on strategy content
        data_path = resolve_data_path(strategy)
        code = create_backtest(strategy, name, session_log, data_path=data_path)
        if not code:
            cprint("[RBI] Phase 2 failed — no valid backtest code", "red")
            memory_record["result"] = "REJECT"
            memory_record["reason"] = "Phase 2 failed — no valid code"
            strategy_memory.record_pipeline_run(memory_record)
            return

        if _cancelled():
            return _bail("after backtest codegen")

        # Phase 3: Debug (with retry loop)
        code = debug_backtest(code, name, session_log)

        # Code-hash dedupe (#8): identical generated code already evaluated
        # → reuse the prior decision instead of burning another full run
        code_hash = hashlib.md5(code.encode("utf-8")).hexdigest()
        memory_record["code_hash"] = code_hash
        try:
            prior = [h for h in strategy_memory.get_strategy_history(limit=200)
                     if h.get("code_hash") == code_hash and h.get("result")]
            if prior:
                last = prior[-1]
                cprint(f"[RBI] Duplicate code detected (hash {code_hash[:8]}) — "
                       f"prior decision: {last.get('result')} ({last.get('reasoning', '')[:80]})",
                       "yellow")
                memory_record["result"] = last.get("result")
                memory_record["decision"] = last.get("result")
                memory_record["reason"] = f"Duplicate of prior run {last.get('session_id', '?')}"
                memory_record["deduped"] = True
                strategy_memory.record_pipeline_run(memory_record)
                return
        except Exception:
            pass  # dedupe is best-effort; never block the pipeline

        if _cancelled():
            return _bail("after debug")

        # Phase 4: Execute (with runtime retry + error context)
        stats_output, error_context = execute_backtest(name, session_log)

        # Parse stats for downstream use
        parsed_stats = _parse_backtest_stats(stats_output) if stats_output else {}
        memory_record["backtest_stats"] = parsed_stats

        if not stats_output:
            cprint("[RBI] Phase 4 failed — no backtest output after all retries", "red")
            memory_record["result"] = "REJECT"
            memory_record["reason"] = "Phase 4 failed — execution error"
            memory_record["error_context"] = error_context
            strategy_memory.record_pipeline_run(memory_record)
            archive_strategy(name, session_log)
            return

        # Phase 4.5: Walk-Forward Validation (catch overfitting)
        cprint("\n[PHASE 4.5] Walk-forward validation...", "cyan")
        wf_result = None
        try:
            # Load the backtest data for walk-forward — use the SAME fresh,
            # asset-resolved file the backtest ran on (#5), not stale BTC CSV
            import pandas as pd
            wf_df = pd.read_csv(data_path)
            wf_df.columns = wf_df.columns.str.strip().str.lower()
            mapping = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
            wf_df = wf_df.rename(columns=mapping)
            if 'datetime' in wf_df.columns:
                wf_df['datetime'] = pd.to_datetime(wf_df['datetime'])
                wf_df.set_index('datetime', inplace=True)
            elif 'timestamp' in wf_df.columns:
                wf_df['timestamp'] = pd.to_datetime(wf_df['timestamp'])
                wf_df.set_index('timestamp', inplace=True)
            wf_df = wf_df.dropna(subset=['Open', 'High', 'Low', 'Close'])

            # Run walk-forward using actual OHLCV DataFrame slices
            from src.strategy_runner import create_strategy_return_fn
            backtest_path = str(FINAL_BACKTEST_DIR / f"{name}_BTFinal.py")
            if os.path.exists(backtest_path):
                strategy_return_fn = create_strategy_return_fn(backtest_path, str(data_path))
                # Use DataFrame-based walk-forward with 15-min data (96 bars/day)
                wf_result = asyncio.run(walk_forward_validator.validate_with_dataframes(
                    strategy_return_fn, name, wf_df, bars_per_day=96
                ))
            else:
                # Fallback: use price-only walk-forward with buy-and-hold
                prices = wf_df['Close'].tolist()
                def strategy_return_fn(prices_window):
                    if len(prices_window) < 2:
                        return 0.0
                    return (prices_window[-1] - prices_window[0]) / prices_window[0]
                wf_result = asyncio.run(walk_forward_validator.validate(strategy_return_fn, name, prices))
            cprint(f"[PHASE 4.5] Walk-forward: IS={wf_result.in_sample_return:.2%}, "
                   f"OOS={wf_result.out_of_sample_return:.2%}, "
                   f"Overfit={wf_result.overfit_score:.2f}", "cyan")
            session_log.log("signal/validated", {
                "phase": "walk_forward",
                "in_sample": wf_result.in_sample_return,
                "out_of_sample": wf_result.out_of_sample_return,
                "overfit_score": wf_result.overfit_score,
                "deployable": wf_result.deployable,
            }, signal_id)
        except Exception as e:
            cprint(f"[PHASE 4.5] Walk-forward skipped: {e}", "yellow")

        # Phase 4.6: Alpha Decay Check
        cprint("\n[PHASE 4.6] Alpha decay check...", "cyan")
        decay_status = None
        try:
            decay_status = alpha_detector.check_strategy(name)
            cprint(f"[PHASE 4.6] Decay status: {decay_status.status.value} "
                   f"(win_rate={decay_status.win_rate:.1%}, sharpe={decay_status.sharpe_ratio:.2f})", "cyan")
            session_log.log("signal/validated", {
                "phase": "alpha_decay",
                "status": decay_status.status.value,
                "win_rate": decay_status.win_rate,
                "sharpe": decay_status.sharpe_ratio,
            }, signal_id)
        except Exception as e:
            cprint(f"[PHASE 4.6] Decay check skipped: {e}", "yellow")

        # Phase 5: Evaluate (enhanced with walk-forward + decay data)
        decision, reasoning = evaluate_performance(
            stats_output, wf_result, decay_status, session_log
        )

        # Update strategy memory
        memory_record["result"] = decision
        memory_record["decision"] = decision
        memory_record["backtest_stats"] = parsed_stats
        memory_record["reasoning"] = reasoning[:500] if reasoning else None
        memory_record["walk_forward"] = {
            "in_sample": wf_result.in_sample_return,
            "out_of_sample": wf_result.out_of_sample_return,
            "overfit_score": wf_result.overfit_score,
        } if wf_result else None
        memory_record["decay_status"] = decay_status.status.value if decay_status else None

        # Phase 6: Deploy or Archive
        if decision == "GO_LIVE":
            # Human approval gate (#3) — approval_callback (web UI) takes
            # priority over auto_mode so web runs are never auto-deployed
            approved = human_approval_gate(name, parsed_stats, reasoning or "",
                                           auto_mode=auto_mode,
                                           approval_callback=approval_callback)

            if approved:
                cprint("\n[RBI] Strategy APPROVED — deploying...", "green")
                deployed = deploy_to_live(code, name, session_log)
                if deployed:
                    elapsed = time.time() - start_time
                    cprint(f"\n[RBI] SUCCESS: {name} is LIVE! ({elapsed:.0f}s)", "green")
                    memory_record["deployed"] = True
                    memory_record["deploy_time"] = elapsed
                    memory_record["code_path"] = str(LIVE_STRATEGIES_DIR / f"{name.lower()}.py")

                    # Post-deploy: register for monitoring
                    cprint(f"[RBI] Registered '{name}' with alpha decay detector for monitoring", "cyan")
                else:
                    memory_record["deployed"] = False
                    memory_record["deploy_error"] = "Deployment AI failed to produce valid live code (#10)"
                    memory_record["reason"] = "Deployment failed"
            else:
                cprint(f"\n[RBI] Strategy REJECTED by human gate", "yellow")
                memory_record["result"] = "REJECT"
                memory_record["reason"] = "Human gate rejected"
                archive_strategy(name, session_log)
        else:
            cprint(f"\n[RBI] Strategy REJECTED", "yellow")
            archive_strategy(name, session_log)
            if reasoning:
                cprint(f"[RBI] Reason: {reasoning[:200]}", "blue")

        # Record to feedback loop — signal AND outcome (#4): previously only
        # the prediction was recorded, so the loop never had pairs to learn from
        try:
            asyncio.run(feedback_loop.record_signal(
                symbol=name,
                signal="GO_LIVE" if decision == "GO_LIVE" else "REJECT",
                confidence=parsed_stats.get("Win Rate [%]", 50) / 100,
                factors={"return": parsed_stats.get("Return [%]", 0),
                         "max_dd": parsed_stats.get("Max. Drawdown [%]", 0),
                         "sharpe": parsed_stats.get("Sharpe Ratio", 0)},
                regime="backtest",
                signal_id=signal_id,
            ))
            # Close the loop: the backtest result IS the outcome for this signal
            asyncio.run(feedback_loop.record_outcome(
                symbol=name,
                pnl_usd=parsed_stats.get("Return [%]", 0),
                pnl_pct=parsed_stats.get("Return [%]", 0),
                holding_minutes=0.0,
                signal_id=signal_id,
            ))
        except Exception:
            pass

        # Record to strategy memory
        memory_record["elapsed_seconds"] = time.time() - start_time
        strategy_memory.record_pipeline_run(memory_record)

    except Exception as e:
        cprint(f"\n[RBI] Fatal error: {e}", "red")
        import traceback
        cprint(traceback.format_exc(), "red")
        session_log.log("agent/error", {"phase": "fatal", "error": str(e)}, signal_id)
        memory_record["result"] = "ERROR"
        memory_record["error"] = str(e)
        strategy_memory.record_pipeline_run(memory_record)


def process_channel(channel_url: str, auto_mode: bool = False, max_videos: int = 15):
    """Process a YouTube channel — discover videos, extract transcripts, run RBI on each.

    Args:
        channel_url: YouTube channel URL (any format)
        auto_mode: Skip human approval gates
        max_videos: Max videos to process from channel
    """
    cprint(f"\n{'='*60}", "magenta")
    cprint(f"RBI CHANNEL MODE: {channel_url[:60]}", "magenta")
    cprint(f"{'='*60}\n", "magenta")

    # Step 1: Resolve channel ID
    channel_id = _extract_channel_id(channel_url)
    if not channel_id:
        cprint("[RBI] Could not extract channel ID from URL", "red")
        cprint("[RBI] Supported formats:", "yellow")
        cprint("  - https://www.youtube.com/@handle", "yellow")
        cprint("  - https://www.youtube.com/channel/UCxxxxx", "yellow")
        cprint("  - https://www.youtube.com/c/CustomName", "yellow")
        return

    cprint(f"[RBI] Channel ID: {channel_id}", "cyan")

    # Step 2: Discover recent videos
    videos = _get_channel_video_ids(channel_id, max_videos=max_videos)
    if not videos:
        cprint("[RBI] No videos found on channel", "red")
        return

    # Step 3: Filter for trading-related videos
    trading_keywords = [
        'trading', 'strategy', 'backtest', 'indicator', 'signal',
        'buy', 'sell', 'entry', 'exit', 'stop loss', 'take profit',
        'rsi', 'ema', 'sma', 'macd', 'bollinger', 'atr',
        'scalp', 'swing', 'day trad', 'crypto', 'bitcoin', 'btc',
        'solana', 'sol', 'forex', 'chart', 'technical', 'price action',
        'candlestick', 'momentum', 'reversal', 'breakout', 'trend',
        'algorithm', 'algo', 'bot', 'automat', 'quant', 'edge',
        'profit', 'risk management', 'portfolio', 'alpha',
    ]

    def is_trading_related(title: str) -> bool:
        title_lower = title.lower()
        return any(kw in title_lower for kw in trading_keywords)

    # Separate trading videos from others
    trading_videos = [v for v in videos if is_trading_related(v['title'])]
    other_videos = [v for v in videos if not is_trading_related(v['title'])]

    cprint(f"\n[RBI] Found {len(trading_videos)} trading-related videos, "
           f"{len(other_videos)} other videos", "cyan")

    # Show video list
    cprint("\n[RBI] Trading videos:", "green")
    for i, v in enumerate(trading_videos, 1):
        cprint(f"  {i}. {v['title'][:70]}", "green")

    if other_videos:
        cprint("\n[RBI] Other videos (will still be analyzed):", "yellow")
        for i, v in enumerate(other_videos, 1):
            cprint(f"  {i}. {v['title'][:70]}", "yellow")

    # Step 4: Process each video through RBI pipeline
    # Combine all videos — trading ones first
    all_videos = trading_videos + other_videos

    cprint(f"\n[RBI] Processing {len(all_videos)} videos through RBI pipeline...", "magenta")
    cprint(f"[RBI] Each video becomes a separate strategy idea\n", "cyan")

    for i, video in enumerate(all_videos, 1):
        cprint(f"\n{'─'*60}", "magenta")
        cprint(f"[VIDEO {i}/{len(all_videos)}] {video['title'][:60]}", "magenta")
        cprint(f"{'─'*60}", "magenta")

        # Extract transcript for this video
        transcript = _extract_video_transcript(video['video_id'])
        if not transcript or len(transcript) < 50:
            cprint(f"[RBI] Skipping — no transcript available", "yellow")
            continue

        # Build the idea content with full context
        idea_content = (
            f"YouTube Video: {video['title']}\n"
            f"URL: {video['url']}\n"
            f"Channel: {channel_url}\n\n"
            f"VIDEO TRANSCRIPT:\n{transcript}"
        )

        # Sanitize and process through RBI pipeline
        idea_content = sanitize_user_input(idea_content)
        process_trading_idea(idea_content, auto_mode=auto_mode)

    # Print session summary
    cprint(f"\n{'='*60}", "magenta")
    cprint("RBI CHANNEL PIPELINE COMPLETE — Summary", "magenta")
    cprint(f"{'='*60}", "magenta")
    try:
        history = strategy_memory.get_strategy_history(limit=10)
        for record in history:
            name = record.get("strategy_name", "Unknown")
            result = record.get("result", "UNKNOWN")
            elapsed = record.get("elapsed_seconds", 0)
            status = "✅" if result == "GO_LIVE" else "❌"
            cprint(f"  {status} {name}: {result} ({elapsed:.0f}s)", "green" if result == "GO_LIVE" else "yellow")
    except Exception:
        pass


def main():
    """Process ideas from ideas.txt, or a YouTube channel.

    Usage:
      python rbi_agent.py                          # Process ideas.txt
      python rbi_agent.py --channel @handle        # Scrape channel videos
      python rbi_agent.py --channel https://...    # Scrape channel videos
      python rbi_agent.py --auto                   # Skip human approval
    """
    import argparse
    parser = argparse.ArgumentParser(description="Moon Dev RBI Agent")
    parser.add_argument("--auto", action="store_true", help="Auto-mode: skip human approval gates")
    parser.add_argument("--channel", type=str, help="YouTube channel URL to scrape for strategy ideas")
    parser.add_argument("--max-videos", type=int, default=15, help="Max videos to process from channel (default: 15)")
    args, _ = parser.parse_known_args()

    # Channel mode: scrape YouTube channel
    if args.channel:
        process_channel(args.channel, auto_mode=args.auto, max_videos=args.max_videos)
        return

    # Default mode: process ideas.txt
    ideas_file = DATA_DIR / "ideas.txt"
    if not ideas_file.exists():
        cprint("[RBI] No ideas.txt found", "yellow")
        cprint("[RBI] Usage:", "cyan")
        cprint("  python rbi_agent.py --channel @moondevonyt", "cyan")
        cprint("  python rbi_agent.py --channel https://www.youtube.com/@handle", "cyan")
        cprint("  echo 'https://www.youtube.com/watch?v=xxx' > data/rbi/ideas.txt", "cyan")
        cprint("  python rbi_agent.py", "cyan")
        return

    with open(ideas_file, 'r', encoding='utf-8') as f:
        ideas = [l.strip() for l in f if l.strip() and not l.startswith('#')]

    cprint(f"[RBI] Found {len(ideas)} ideas to process", "cyan")
    if args.auto:
        cprint("[RBI] AUTO MODE — human approval gates will be skipped", "yellow")

    for i, idea in enumerate(ideas, 1):
        cprint(f"\n[RBI] Processing idea {i}/{len(ideas)}", "cyan")
        process_trading_idea(idea, auto_mode=args.auto)

    # Print session summary
    cprint(f"\n{'='*60}", "magenta")
    cprint("RBI PIPELINE COMPLETE — Summary", "magenta")
    cprint(f"{'='*60}", "magenta")
    try:
        history = strategy_memory.get_strategy_history(limit=10)
        for record in history:
            name = record.get("strategy_name", "Unknown")
            result = record.get("result", "UNKNOWN")
            elapsed = record.get("elapsed_seconds", 0)
            status = "✅" if result == "GO_LIVE" else "❌"
            cprint(f"  {status} {name}: {result} ({elapsed:.0f}s)", "green" if result == "GO_LIVE" else "yellow")
    except Exception:
        pass


if __name__ == "__main__":
    main()
