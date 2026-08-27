"""
Moon Dev's RBI Agent (Research-Backtest-Implement) — Enhanced

Full Lifecycle with Validation:
1. Research -> validate strategy name
2. Backtest -> validate syntax + structure
3. Package -> validate no backtesting.lib
4. Debug -> validate + retry with error context (up to 3 attempts)
5. Execute -> run backtest with timeout
6. Evaluate -> AI decides GO_LIVE or REJECT
7. Deploy -> convert to BaseStrategy class

Key improvements:
  - Code validation between every phase
  - Programmatic retry loop (replaces GUI-based debug)
  - Stricter prompts for Qwen3-Coder-Next
  - Error context passed to debug agent
  - Data path made configurable (not hardcoded)
"""

import os
import sys
import io
import time
import re
import subprocess
import shutil
import itertools
import threading
import json
from datetime import datetime
from pathlib import Path
from termcolor import cprint

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

# Local imports
from src.config import *
from src.models import model_factory
from src.agents.code_validator import CodeValidator

# ── Model Configuration ──────────────────────────────────────
# Coding tasks use qwen.qwen3-coder-next (best for code generation)
# Evaluation uses deepseek.v3.2 (best for reasoning)
RESEARCH_CONFIG  = {"type": "bedrock", "name": "qwen.qwen3-coder-next"}
BACKTEST_CONFIG  = {"type": "bedrock", "name": "qwen.qwen3-coder-next"}
PACKAGE_CONFIG   = {"type": "bedrock", "name": "qwen.qwen3-coder-next"}
DEBUG_CONFIG     = {"type": "bedrock", "name": "qwen.qwen3-coder-next"}
EVALUATE_CONFIG  = {"type": "bedrock", "name": "deepseek.v3.2"}
DEPLOY_CONFIG    = {"type": "bedrock", "name": "qwen.qwen3-coder-next"}

# ── Directory Setup ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data/rbi"
RESEARCH_DIR = DATA_DIR / "research"
BACKTEST_DIR = DATA_DIR / "backtests"
PACKAGE_DIR = DATA_DIR / "backtests_package"
FINAL_BACKTEST_DIR = DATA_DIR / "backtests_final"
ARCHIVE_DIR = DATA_DIR / "archive"
LIVE_STRATEGIES_DIR = PROJECT_ROOT / "strategies/custom"

for d in [DATA_DIR, RESEARCH_DIR, BACKTEST_DIR, PACKAGE_DIR, FINAL_BACKTEST_DIR, ARCHIVE_DIR, LIVE_STRATEGIES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Data Path (use relative path that works cross-platform) ─
DATA_PATH = str(DATA_DIR / "BTC-USD-15m.csv")

# ── Max retry attempts for debug loop ──
MAX_DEBUG_RETRIES = 3

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
    bt = Backtest(data, MyStrategy, cash=1_000_000, commission=.002)
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

PACKAGE_PROMPT = """You are Moon Dev's Code Package Agent.

Your job: Ensure the backtest code uses ONLY talib and manual logic. NO backtesting.lib.

RULES:
1. Replace ANY backtesting.lib imports with talib equivalents
2. Replace crossignal() with manual crossover logic:
   bullish = (fast[-1] > slow[-1]) and (fast[-2] <= slow[-2])
   bearish = (fast[-1] < slow[-1]) and (fast[-2] >= slow[-2])
3. Replace crossover() similarly
4. Ensure ALL indicators use self.I(talib.XXX, ...)
5. Keep all other code structure identical

Input code:
{code}

Return ONLY the fixed Python code block. No explanation.
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

Criteria for GO_LIVE:
1. Return can be positive (even small) OR have a good risk/reward profile
2. Max Drawdown < 30%
3. At least 1 trade executed
4. Win Rate > 40% OR Profit Factor > 1.2

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
    """Extract content from a trading idea URL"""
    try:
        if "youtube.com" in idea_url or "youtu.be" in idea_url:
            if YouTubeTranscriptApi:
                video_id = idea_url.split("v=")[1].split("&")[0] if "v=" in idea_url else idea_url.split("/")[-1]
                transcript = YouTubeTranscriptApi.get_transcript(video_id)
                return f"YouTube Transcript: {' '.join([t['text'] for t in transcript])}"
        return f"Trading Idea: {idea_url}"
    except Exception:
        return idea_url


# ── Phase Functions (with validation) ────────────────────────

validator = CodeValidator()


def research_strategy(content: str) -> tuple:
    """Phase 1: Research the strategy idea"""
    cprint("\n[PHASE 1] Researching strategy...", "cyan")
    output = run_with_animation(chat_with_model, "Research Agent", RESEARCH_PROMPT, content, RESEARCH_CONFIG)
    if not output:
        return None, None

    name = "UnknownStrategy"
    if "STRATEGY_NAME:" in output:
        raw_name = output.split("STRATEGY_NAME:")[1].split("\n")[0].strip()
        name = re.sub(r'[^\w]', '', raw_name)
        if not name:
            name = "UnknownStrategy"

    cprint(f"[PHASE 1] Strategy name: {name}", "green")
    return output, name


def create_backtest(strategy: str, name: str) -> str:
    """Phase 2: Generate backtest code with validation"""
    cprint("\n[PHASE 2] Generating backtest code...", "cyan")

    prompt = BACKTEST_PROMPT.format(
        strategy=strategy,
        data_path=DATA_PATH,
    )

    output = run_with_animation(chat_with_model, "Backtest Agent", prompt, strategy, BACKTEST_CONFIG)
    if not output:
        return None

    code = extract_python_code(output)
    if not code:
        cprint("[PHASE 2] Could not extract code from response", "red")
        return None

    # Validate
    result = validator.validate_all(code, phase="backtest")
    if not result.passed:
        cprint(f"[PHASE 2] Validation failed, attempting fix...", "yellow")
        # Try one fix attempt
        code = _try_fix_code(code, result.errors, DEBUG_CONFIG, "backtest")
        if not code:
            return None

    # Save
    path = BACKTEST_DIR / f"{name}_BT.py"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    cprint(f"[PHASE 2] Saved to {path}", "green")

    return code


def package_check(code: str, name: str) -> str:
    """Phase 3: Remove backtesting.lib, use talib only"""
    cprint("\n[PHASE 3] Packaging (removing backtesting.lib)...", "cyan")

    prompt = PACKAGE_PROMPT.format(code=code)
    output = run_with_animation(chat_with_model, "Package Agent", prompt, code, PACKAGE_CONFIG)
    if not output:
        return code  # Keep original if packaging fails

    new_code = extract_python_code(output)
    if not new_code:
        cprint("[PHASE 3] Could not extract packaged code", "yellow")
        return code  # Keep original

    # Validate no backtesting.lib remains
    result = validator.validate_all(new_code, phase="package")
    if not result.passed:
        cprint(f"[PHASE 3] Validation failed, keeping original code", "yellow")
        return code

    # Save
    path = PACKAGE_DIR / f"{name}_PKG.py"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_code)
    cprint(f"[PHASE 3] Saved to {path}", "green")

    return new_code


def debug_backtest(code: str, name: str) -> str:
    """Phase 4: Debug with programmatic retry loop"""
    cprint("\n[PHASE 4] Debugging with validation...", "cyan")

    current_code = code
    for attempt in range(MAX_DEBUG_RETRIES):
        cprint(f"[PHASE 4] Debug attempt {attempt + 1}/{MAX_DEBUG_RETRIES}", "cyan")

        # Validate current code
        result = validator.validate_all(current_code, phase="debug")
        if result.passed:
            cprint("[PHASE 4] Code passed validation", "green")
            break

        # Try to fix
        fixed_code = _try_fix_code(current_code, result.errors, DEBUG_CONFIG, "debug")
        if fixed_code and fixed_code != current_code:
            current_code = fixed_code
        else:
            cprint(f"[PHASE 4] Could not fix code on attempt {attempt + 1}", "yellow")
            # Try execution as last resort
            break

    # Final validation
    result = validator.validate_all(current_code, phase="final")
    if not result.passed:
        cprint("[PHASE 4] Final validation still has issues", "yellow")

    # Save
    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(current_code)
    cprint(f"[PHASE 4] Saved to {path}", "green")

    return current_code


def _try_fix_code(code: str, errors: list, config: dict, phase: str) -> str:
    """Attempt to fix code using AI with error context"""
    context = validator.generate_fix_context(code, errors)
    output = run_with_animation(chat_with_model, f"Debug Fix ({phase})", context, code, config)
    if not output:
        return None

    fixed_code = extract_python_code(output)
    if not fixed_code:
        return None

    # Validate the fix
    result = validator.validate_all(fixed_code, phase=phase)
    if not result.passed:
        cprint(f"[DEBUG] Fix attempt still has errors: {result.errors[:2]}", "yellow")

    return fixed_code


def execute_backtest(name: str) -> str:
    """Phase 5: Execute the backtest"""
    cprint("\n[PHASE 5] Executing backtest...", "cyan")

    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    if not path.exists():
        cprint(f"[PHASE 5] Backtest file not found: {path}", "red")
        return None

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        res = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        output = res.stdout + "\n" + res.stderr

        if res.returncode != 0:
            cprint(f"[PHASE 5] Backtest exited with code {res.returncode}", "yellow")
            cprint(f"[PHASE 5] Error output: {res.stderr[:500]}", "yellow")

        cprint("[PHASE 5] Backtest execution complete", "green")
        return output

    except subprocess.TimeoutExpired:
        cprint("[PHASE 5] Backtest timed out (120s)", "red")
        return None
    except Exception as e:
        cprint(f"[PHASE 5] Execution failed: {e}", "red")
        return str(e)


def evaluate_performance(stats: str) -> tuple:
    """Phase 6: AI evaluates the backtest results"""
    cprint("\n[PHASE 6] Evaluating performance...", "cyan")

    if not stats:
        return "REJECT", "No stats output to evaluate"

    output = run_with_animation(
        chat_with_model, "Evaluation Agent",
        EVALUATE_PROMPT.format(stats=stats),
        stats, EVALUATE_CONFIG,
    )

    decision = "REJECT"
    if output and "GO_LIVE" in output.upper():
        decision = "GO_LIVE"

    cprint(f"[PHASE 6] Decision: {decision}", "green" if decision == "GO_LIVE" else "yellow")
    return decision, output


def deploy_to_live(code: str, name: str) -> bool:
    """Phase 7: Deploy to live strategies"""
    cprint("\n[PHASE 7] Deploying to live...", "cyan")

    prompt = DEPLOY_PROMPT.format(code=code)
    output = run_with_animation(chat_with_model, "Deployment Agent", prompt, code, DEPLOY_CONFIG)

    if output:
        live_code = extract_python_code(output)
        if live_code:
            path = LIVE_STRATEGIES_DIR / f"{name.lower()}.py"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(live_code)
            cprint(f"[PHASE 7] DEPLOYED TO: {path}", "green")
            return True

    cprint("[PHASE 7] Deployment failed", "red")
    return False


def archive_strategy(name: str):
    """Archive a rejected strategy"""
    cprint(f"[ARCHIVE] Archiving {name}...", "yellow")
    for d in [RESEARCH_DIR, BACKTEST_DIR, PACKAGE_DIR, FINAL_BACKTEST_DIR]:
        for f in d.glob(f"{name}*"):
            try:
                shutil.move(str(f), str(ARCHIVE_DIR / f.name))
            except Exception:
                pass


# ── Main Pipeline ────────────────────────────────────────────

def process_trading_idea(idea: str):
    """Process a single trading idea through the full RBI pipeline"""
    cprint(f"\n{'='*60}", "magenta")
    cprint(f"RBI PIPELINE: {idea[:60]}...", "magenta")
    cprint(f"{'='*60}\n", "magenta")

    start_time = time.time()

    try:
        content = get_idea_content(idea)

        # Phase 1: Research
        strategy, name = research_strategy(content)
        if not strategy:
            cprint("[RBI] Phase 1 failed — no strategy generated", "red")
            return

        # Phase 2: Backtest (with validation)
        code = create_backtest(strategy, name)
        if not code:
            cprint("[RBI] Phase 2 failed — no valid backtest code", "red")
            return

        # Phase 3: Package (with validation)
        code = package_check(code, name)

        # Phase 4: Debug (with retry loop)
        code = debug_backtest(code, name)

        # Phase 5: Execute
        stats = execute_backtest(name)

        # Phase 6: Evaluate
        decision, reasoning = evaluate_performance(stats)

        # Phase 7: Deploy or Archive
        if decision == "GO_LIVE":
            cprint("\n[RBI] Strategy APPROVED — deploying...", "green")
            deploy_to_live(code, name)
            elapsed = time.time() - start_time
            cprint(f"\n[RBI] SUCCESS: {name} is LIVE! ({elapsed:.0f}s)", "green")
        else:
            cprint(f"\n[RBI] Strategy REJECTED", "yellow")
            archive_strategy(name)
            if reasoning:
                cprint(f"[RBI] Reason: {reasoning[:200]}", "blue")

    except Exception as e:
        cprint(f"\n[RBI] Fatal error: {e}", "red")
        import traceback
        cprint(traceback.format_exc(), "red")


def main():
    """Process all ideas from ideas.txt"""
    ideas_file = DATA_DIR / "ideas.txt"
    if not ideas_file.exists():
        cprint("[RBI] No ideas.txt found", "yellow")
        return

    with open(ideas_file, 'r', encoding='utf-8') as f:
        ideas = [l.strip() for l in f if l.strip() and not l.startswith('#')]

    cprint(f"[RBI] Found {len(ideas)} ideas to process", "cyan")

    for i, idea in enumerate(ideas, 1):
        cprint(f"\n[RBI] Processing idea {i}/{len(ideas)}", "cyan")
        process_trading_idea(idea)


if __name__ == "__main__":
    main()
