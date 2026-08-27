"""
🌙 Moon Dev's RBI Agent (Research-Backtest-Implement)
Built with love by Moon Dev 🚀

Full Lifecycle:
1. Research -> 2. Backtest -> 3. Package -> 4. Debug -> 5. Execute -> 6. Evaluate -> 7. Deploy/Archive
"""

import os
import time
import re
import subprocess
import shutil
import itertools
import threading
import sys
from datetime import datetime
from pathlib import Path
from termcolor import cprint
import requests
from io import BytesIO
import PyPDF2
from youtube_transcript_api import YouTubeTranscriptApi
import json

# Local imports
from src.config import *
from src.models import model_factory

# Model Configuration
RESEARCH_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}
BACKTEST_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}
DEBUG_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}
PACKAGE_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}
EVALUATE_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}
DEPLOY_CONFIG = {"type": "gemini", "name": "gemini-3-flash-preview"}

# Directory Setup
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

# --- Prompts ---
RESEARCH_PROMPT = """
You are Moon Dev's Research AI 🌙
Create a UNIQUE TWO-WORD NAME for this strategy.
Output format:
STRATEGY_NAME: [UniqueName]
STRATEGY_DETAILS:
[Details]
"""

BACKTEST_PROMPT = """
You are Moon Dev's Backtest AI 🌙
Create a backtesting.py implementation.
Return ONLY a python code block. No explanation.

Use data path: c:/Users/Dell/Desktop/moon-dev-ai-agents-bot/src/data/rbi/BTC-USD-15m.csv
Rules:
1. Data Loading:
   df = pd.read_csv(data_path)
   df.columns = df.columns.str.strip().str.lower()
   mapping = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
   df = df.rename(columns=mapping)
   if 'datetime' in df.columns:
       df['datetime'] = pd.to_datetime(df['datetime'])
       df.set_index('datetime', inplace=True)
   elif 'timestamp' in df.columns:
       df['timestamp'] = pd.to_datetime(df['timestamp'])
       df.set_index('timestamp', inplace=True)
   df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])

2. Indicators: Use self.I() and talib for ALL indicators.
3. Position size: int(round(size)).
4. Print stats and strategy:
   stats = bt.run()
   print(stats)
   print(stats._strategy)
"""

DEBUG_PROMPT = """
You are Moon Dev's Debug AI 🌙
Fix technical issues in the backtest code. Ensure it runs and follows all rules.
Return ONLY the python code block ```python ... ```
"""

PACKAGE_PROMPT = """
You are Moon Dev's Package AI 🌙
Replace all backtesting.lib usage with talib and manual crossover logic.
Return ONLY the python code block ```python ... ```
"""

EVALUATE_PROMPT = """
You are Moon Dev's Performance Analyst 🌙
Decide if this strategy should go live.
Criteria: Positive Return (can be small), Reasonable Drawdown, At least 1 trade.
Return:
DECISION: [GO_LIVE or REJECT]
REASONING: [Brief explanation of stats analysis]
"""

DEPLOY_PROMPT = """
You are Moon Dev's Deployment AI 🌙
Convert this backtest code into a live Strategy Agent class inheriting from BaseStrategy.
Implementation:
- Implement generate_signals(self) method.
- Return a signal dict: {'token', 'signal', 'direction', 'metadata'}.
- Use exact same indicator and crossover logic.
- Use self.data from the live agent context.
Return ONLY the python code block ```python ... ```
"""

# --- Helper Functions ---
def chat_with_model(system_prompt, user_content, config):
    try:
        model = model_factory.get_model(config["type"], config["name"])
        if not model: return None
        response = model.generate_response(system_prompt=system_prompt, user_content=user_content, temperature=AI_TEMPERATURE, max_tokens=AI_MAX_TOKENS)
        return response.content if response else None
    except Exception as e:
        cprint(f"❌ AI Error: {e}", "red")
        return None

def extract_python_code(text):
    if not text: return None
    # Look for python blocks first
    match = re.search(r'```python\n(.*?)\n```', text, re.DOTALL)
    if match: return match.group(1).strip()
    
    # Look for generic code blocks
    match = re.search(r'```\n(.*?)\n```', text, re.DOTALL)
    if match: return match.group(1).strip()
    
    # If no blocks but text looks like code (contains imports/classes), try to use it
    if "import " in text or "class " in text:
        lines = text.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            if "import " in line or "from " in line or "class " in line:
                start_idx = i
                break
        return '\n'.join(lines[start_idx:]).strip()
        
    return None

def run_with_animation(func, agent_name, *args, **kwargs):
    stop_animation = threading.Event()
    def animate():
        spinner = itertools.cycle(['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'])
        while not stop_animation.is_set():
            sys.stdout.write(f'\r{next(spinner)} {agent_name} is thinking...')
            sys.stdout.flush()
            time.sleep(0.5)
        sys.stdout.write('\r' + ' ' * 50 + '\r')
    
    t = threading.Thread(target=animate)
    t.start()
    try: return func(*args, **kwargs)
    finally:
        stop_animation.set()
        t.join()

def get_youtube_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return ' '.join([t['text'] for t in transcript])
    except: return None

def get_idea_content(idea_url):
    try:
        if "youtube.com" in idea_url or "youtu.be" in idea_url:
            video_id = idea_url.split("v=")[1].split("&")[0] if "v=" in idea_url else idea_url.split("/")[-1]
            return f"YouTube Transcript: {get_youtube_transcript(video_id)}"
        return f"Trading Idea: {idea_url}"
    except: return idea_url

# --- Phase Functions ---
def research_strategy(content):
    output = run_with_animation(chat_with_model, "Research Agent", RESEARCH_PROMPT, content, RESEARCH_CONFIG)
    if not output: return None, None
    name = "UnknownStrategy"
    if "STRATEGY_NAME:" in output:
        name = re.sub(r'[^\w]', '', output.split("STRATEGY_NAME:")[1].split("\n")[0].strip())
    return output, name

def create_backtest(strategy, name):
    output = run_with_animation(chat_with_model, "Backtest Agent", BACKTEST_PROMPT, strategy, BACKTEST_CONFIG)
    if not output: return None
    code = extract_python_code(output)
    if not code:
        cprint(f"❌ Could not extract code from Backtest Agent output", "red")
        return None
    path = BACKTEST_DIR / f"{name}_BT.py"
    with open(path, 'w', encoding='utf-8') as f: f.write(code)
    return code

def package_check(code, name):
    output = run_with_animation(chat_with_model, "Package Agent", PACKAGE_PROMPT, code, PACKAGE_CONFIG)
    if not output: return None
    code = extract_python_code(output)
    if not code:
        cprint(f"❌ Could not extract code from Package Agent output", "red")
        return None
    path = PACKAGE_DIR / f"{name}_PKG.py"
    with open(path, 'w', encoding='utf-8') as f: f.write(code)
    return code

def debug_backtest(code, name):
    output = run_with_animation(chat_with_model, "Debug Agent", DEBUG_PROMPT, code, DEBUG_CONFIG)
    if not output: return None
    code = extract_python_code(output)
    if not code:
        cprint(f"❌ Could not extract code from Debug Agent output", "red")
        return None
    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    with open(path, 'w', encoding='utf-8') as f: f.write(code)
    return code

def execute_backtest(name):
    cprint(f"\n🚀 Phase 5: Executing Final Backtest for {name}...", "cyan")
    path = FINAL_BACKTEST_DIR / f"{name}_BTFinal.py"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        res = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, env=env, timeout=120)
        output = res.stdout + "\n" + res.stderr
        cprint("📊 Stats collected successfully!", "green")
        return output
    except Exception as e:
        cprint(f"❌ Execution failed: {e}", "red")
        return str(e)

def evaluate_performance(stats):
    cprint("\n🧠 Phase 5: Evaluating performance via AI...", "cyan")
    output = run_with_animation(chat_with_model, "Evaluation Agent", EVALUATE_PROMPT, f"Stats Output:\n\n{stats}", EVALUATE_CONFIG)
    decision = "REJECT"
    if output and "DECISION: GO_LIVE" in output.upper():
        decision = "GO_LIVE"
    return decision, output

def deploy_to_live(code, name):
    cprint(f"\n🚀 Phase 6: Converting and Deploying {name} to Live!", "green")
    output = run_with_animation(chat_with_model, "Deployment Agent", DEPLOY_PROMPT, code, DEPLOY_CONFIG)
    if output:
        live_code = extract_python_code(output)
        path = LIVE_STRATEGIES_DIR / f"{name.lower()}.py"
        with open(path, 'w', encoding='utf-8') as f: f.write(live_code)
        cprint(f"✨ DEPLOYED SUCCESSFULLY TO: {path}", "green")
        return True
    return False

def archive_strategy(name):
    cprint(f"📁 Phase 6: Damping strategy {name} (Archived)", "yellow")
    for d in [RESEARCH_DIR, BACKTEST_DIR, PACKAGE_DIR, FINAL_BACKTEST_DIR]:
        for f in d.glob(f"{name}*"):
            try: shutil.move(str(f), str(ARCHIVE_DIR / f.name))
            except: pass

def process_trading_idea(idea):
    cprint(f"\n{'='*50}", "magenta")
    cprint(f"🌟 Moon Dev's RBI Agent Processing: {idea[:50]}...", "magenta")
    cprint(f"{'='*50}\n", "magenta")
    
    try:
        content = get_idea_content(idea)
        
        # 🧪 Phases 1-4
        strategy, name = research_strategy(content)
        if not strategy: return
        cprint(f"🏷️ Strategy: {name}", "yellow")
        
        code = create_backtest(strategy, name)
        if not code: return
        
        code = package_check(code, name)
        if not code: return
        
        code = debug_backtest(code, name)
        if not code: return
        
        # 📊 Phase 5: Execute & Evaluate
        stats = execute_backtest(name)
        decision, reasoning = evaluate_performance(stats)
        
        # 🚀 Phase 6: Deploy or Damp
        if decision == "GO_LIVE":
            cprint("✅ Strategy looks spicy! Deploying...", "green")
            deploy_to_live(code, name)
            cprint(f"\n🎉 MISSION SUCCESS: {name} IS LIVE! 🚀🌙", "green")
        else:
            cprint("❌ Strategy did not make the cut.", "yellow")
            archive_strategy(name)
            cprint(f"Reasoning: {reasoning}", "blue")
            
    except Exception as e:
        cprint(f"❌ Fatal Error: {e}", "red")

def main():
    ideas_file = DATA_DIR / "ideas.txt"
    if not ideas_file.exists(): return
    with open(ideas_file, 'r', encoding='utf-8') as f:
        ideas = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    for idea in ideas:
        process_trading_idea(idea)

if __name__ == "__main__":
    main()
