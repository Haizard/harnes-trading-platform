"""
🌙 Moon Dev's Model Benchmark Test
Tests qwen3-coder-next with a market structure trading strategy prompt.
No fallback - pure model testing for performance comparison.

Usage:
    python src/scripts/test_qwen3_market_structure.py
"""

import os
import sys
import json
import time
import asyncio
import io
from datetime import datetime

# Fix Windows encoding for emoji output
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file
from dotenv import load_dotenv
load_dotenv()

from bedrock_llm import (
    bedrock_chat,
    ChatMessage,
    ChatOptions,
    is_bedrock_configured,
    get_bedrock_config,
)


# ── Market Structure Trading Strategy Prompt ──────────────────
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


# ── Performance Metrics ────────────────────────────────────────
class PerformanceMetrics:
    """Track model performance metrics."""
    
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.first_token_time = None
        self.tokens_generated = 0
        self.response_text = ""
        self.error = None
        
    def start(self):
        self.start_time = time.time()
        
    def record_first_token(self):
        if self.first_token_time is None:
            self.first_token_time = time.time()
            
    def finish(self, text: str):
        self.end_time = time.time()
        self.response_text = text
        self.tokens_generated = len(text.split())  # Rough word count
        
    def fail(self, error: Exception):
        self.end_time = time.time()
        self.error = str(error)
        
    def get_stats(self) -> dict:
        total_time = (self.end_time or time.time()) - self.start_time
        first_token_latency = None
        if self.first_token_time:
            first_token_latency = self.first_token_time - self.start_time
            
        return {
            "total_time_seconds": round(total_time, 2),
            "first_token_latency_seconds": round(first_token_latency, 2) if first_token_latency else None,
            "tokens_generated": self.tokens_generated,
            "tokens_per_second": round(self.tokens_generated / total_time, 2) if total_time > 0 else 0,
            "error": self.error,
            "success": self.error is None,
        }


# ── Test Functions ─────────────────────────────────────────────
async def test_direct_bedrock_call() -> dict:
    """
    Test qwen3-coder-next directly via Bedrock - NO FALLBACK.
    This bypasses bedrock_chat_with_fallback to test the model纯粹.
    """
    metrics = PerformanceMetrics()
    metrics.start()
    
    try:
        # Direct call - no fallback mechanism
        response = await bedrock_chat(
            messages=[ChatMessage(role="user", content=MARKET_STRUCTURE_PROMPT)],
            options=ChatOptions(
                system_prompt="You are a professional crypto trading strategist. Be thorough and provide actionable code.",
                temperature=0.3,
                max_tokens=8192,
                keep_json=False,
            ),
        )
        
        metrics.finish(response.text)
        
        return {
            "test_name": "Direct Bedrock Call (No Fallback)",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "response_preview": response.text[:500] + "..." if len(response.text) > 500 else response.text,
            "response_full": response.text,
            "response_length": len(response.text),
        }
        
    except Exception as e:
        metrics.fail(e)
        return {
            "test_name": "Direct Bedrock Call (No Fallback)",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "error": str(e),
        }


async def test_with_retry() -> dict:
    """
    Test with retry mechanism (still no fallback to other models).
    """
    from bedrock_llm import bedrock_chat_with_retry
    
    metrics = PerformanceMetrics()
    metrics.start()
    
    try:
        response = await bedrock_chat_with_retry(
            messages=[ChatMessage(role="user", content=MARKET_STRUCTURE_PROMPT)],
            options=ChatOptions(
                system_prompt="You are a professional crypto trading strategist. Be thorough and provide actionable code.",
                temperature=0.3,
                max_tokens=8192,
                keep_json=False,
            ),
            max_retries=3,
        )
        
        metrics.finish(response.text)
        
        return {
            "test_name": "With Retry (No Fallback)",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "response_preview": response.text[:500] + "..." if len(response.text) > 500 else response.text,
            "response_full": response.text,
            "response_length": len(response.text),
        }
        
    except Exception as e:
        metrics.fail(e)
        return {
            "test_name": "With Retry (No Fallback)",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "error": str(e),
        }


async def test_json_output() -> dict:
    """
    Test JSON output format for structured strategy.
    """
    json_prompt = MARKET_STRUCTURE_PROMPT + """

IMPORTANT: Return your strategy as a JSON object with the following structure:
{
    "strategy_name": "Market Structure Trading Strategy",
    "core_concepts": [...],
    "entry_rules": {...},
    "exit_rules": {...},
    "risk_management": {...},
    "python_code": "...",
    "edge_cases": {...}
}
"""
    
    metrics = PerformanceMetrics()
    metrics.start()
    
    try:
        response = await bedrock_chat(
            messages=[ChatMessage(role="user", content=json_prompt)],
            options=ChatOptions(
                system_prompt="You are a professional crypto trading strategist. Return valid JSON only.",
                temperature=0.1,  # Lower for structured output
                max_tokens=8192,
                keep_json=True,
            ),
        )
        
        metrics.finish(response.text)
        
        return {
            "test_name": "JSON Output Test",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "has_json": response.json_data is not None,
            "json_keys": list(response.json_data.keys()) if response.json_data else [],
            "response_preview": response.text[:500] + "..." if len(response.text) > 500 else response.text,
            "response_full": response.text,
        }
        
    except Exception as e:
        metrics.fail(e)
        return {
            "test_name": "JSON Output Test",
            "model": "qwen.qwen3-coder-next",
            "metrics": metrics.get_stats(),
            "error": str(e),
        }


# ── Main Test Runner ───────────────────────────────────────────
async def run_benchmark():
    """Run all benchmark tests."""
    print("\n" + "=" * 70)
    print("🌙 MOON DEV MODEL BENCHMARK TEST")
    print("Testing: qwen.qwen3-coder-next")
    print("Task: Market Structure Trading Strategy")
    print("=" * 70 + "\n")
    
    # Check configuration
    config = get_bedrock_config()
    print(f"📋 Configuration:")
    print(f"   Region: {config['region']}")
    print(f"   Model: {config['model_id']}")
    print(f"   Configured: {config['configured']}")
    print()
    
    if not config['configured']:
        print("❌ ERROR: AWS Bedrock not configured!")
        print("   Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env")
        return
    
    # Run tests
    results = []
    
    print("🧪 Test 1/3: Direct Bedrock Call (No Fallback)")
    print("-" * 50)
    result1 = await test_direct_bedrock_call()
    results.append(result1)
    _print_result(result1)
    
    print("\n🧪 Test 2/3: With Retry Mechanism")
    print("-" * 50)
    result2 = await test_with_retry()
    results.append(result2)
    _print_result(result2)
    
    print("\n🧪 Test 3/3: JSON Output Format")
    print("-" * 50)
    result3 = await test_json_output()
    results.append(result3)
    _print_result(result3)
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 BENCHMARK SUMMARY")
    print("=" * 70)
    
    successful = [r for r in results if r.get("metrics", {}).get("success")]
    failed = [r for r in results if not r.get("metrics", {}).get("success")]
    
    print(f"\n✅ Successful: {len(successful)}/{len(results)}")
    print(f"❌ Failed: {len(failed)}/{len(results)}")
    
    if successful:
        avg_time = sum(r["metrics"]["total_time_seconds"] for r in successful) / len(successful)
        avg_tokens = sum(r["metrics"]["tokens_generated"] for r in successful) / len(successful)
        print(f"\n📈 Average Performance:")
        print(f"   Total Time: {avg_time:.2f}s")
        print(f"   Tokens Generated: {avg_tokens:.0f}")
        print(f"   Tokens/Second: {sum(r['metrics']['tokens_per_second'] for r in successful) / len(successful):.2f}")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"benchmark_results_{timestamp}.json"
    
    with open(results_file, "w") as f:
        json.dump({
            "timestamp": timestamp,
            "model": "qwen.qwen3-coder-next",
            "task": "Market Structure Trading Strategy",
            "results": results,
        }, f, indent=2)
    
    # Also save full responses to separate files for inspection
    for i, r in enumerate(results, 1):
        if r.get("metrics", {}).get("success") and r.get("response_full"):
            resp_file = f"benchmark_response_{timestamp}_test{i}.md"
            with open(resp_file, "w", encoding="utf-8") as f:
                f.write(f"# Test {i}: {r['test_name']}\n\n")
                f.write(f"Model: {r['model']}\n")
                f.write(f"Time: {r['metrics']['total_time_seconds']}s\n")
                f.write(f"Speed: {r['metrics']['tokens_per_second']} tokens/s\n\n")
                f.write("---\n\n")
                f.write(r["response_full"])
            print(f"\n📄 Response {i} saved to: {resp_file}")
    
    print(f"\n💾 Results saved to: {results_file}")
    print("=" * 70 + "\n")


def _print_result(result: dict):
    """Print a single test result."""
    metrics = result.get("metrics", {})
    
    if metrics.get("success"):
        print(f"   ✅ Success!")
        print(f"   ⏱️  Total Time: {metrics['total_time_seconds']}s")
        if metrics.get("first_token_latency_seconds"):
            print(f"   ⚡ First Token: {metrics['first_token_latency_seconds']}s")
        print(f"   📝 Tokens: {metrics['tokens_generated']}")
        print(f"   🚀 Speed: {metrics['tokens_per_second']} tokens/s")
        print(f"   📏 Response Length: {result.get('response_length', 'N/A')} chars")
        if result.get("has_json") is not None:
            print(f"   📦 JSON Parsed: {result['has_json']}")
            if result.get("json_keys"):
                print(f"   🔑 JSON Keys: {', '.join(result['json_keys'][:5])}")
        print(f"\n   📄 Response Preview:")
        preview = result.get("response_preview", "")
        for line in preview.split("\n")[:10]:
            print(f"      {line}")
    else:
        print(f"   ❌ Failed: {metrics.get('error', 'Unknown error')}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
