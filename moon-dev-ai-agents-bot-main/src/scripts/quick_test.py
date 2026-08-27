"""
Quick single-test: run the market structure prompt and save full response.
"""
import os, sys, io, json, time, asyncio
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from bedrock_llm import bedrock_chat_with_retry, ChatMessage, ChatOptions

MARKET_STRUCTURE_PROMPT = """
You are an expert crypto trading strategist specializing in market structure analysis.

Create a comprehensive market structure trading strategy for cryptocurrency markets that includes:

1. **Core Concepts**:
   - Define market structure (higher highs, higher lows, lower highs, lower lows)
   - Explain order blocks, fair value gaps, and liquidity zones
   - Describe how to identify trend changes using structure breaks

2. **Entry Rules**:
   - When to enter long positions (bullish structure)
   - When to enter short positions (bearish structure)
   - Confirmation signals needed before entry
   - Optimal timeframe selection

3. **Exit Rules**:
   - Take profit targets based on structure
   - Stop loss placement using structure invalidation
   - Trailing stop strategies
   - Partial profit-taking rules

4. **Risk Management**:
   - Position sizing based on structure confidence
   - Maximum risk per trade
   - Portfolio allocation rules
   - Drawdown limits

5. **Implementation**:
   - Python code for detecting market structure
   - Signal generation logic
   - Backtesting framework suggestions

6. **Edge Cases**:
   - How to handle ranging/choppy markets
   - What to do during high volatility events
   - News/event-based market structure shifts

Provide the strategy in a structured format with clear rules and Python code examples where appropriate.
"""

async def main():
    print("Running qwen3-coder-next market structure test...")
    start = time.time()
    
    response = await bedrock_chat_with_retry(
        messages=[ChatMessage(role="user", content=MARKET_STRUCTURE_PROMPT)],
        options=ChatOptions(
            system_prompt="You are a professional crypto trading strategist. Be thorough and provide actionable code.",
            temperature=0.3,
            max_tokens=8192,
        ),
        max_retries=3,
    )
    
    elapsed = time.time() - start
    print(f"Done in {elapsed:.1f}s — {len(response.text)} chars")
    
    outfile = "qwen3_market_structure_response.md"
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(response.text)
    
    print(f"Full response saved to: {outfile}")

asyncio.run(main())
