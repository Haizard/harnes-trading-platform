"""
Pine Script to Python Converter - AI-powered strategy conversion.
DSH Pattern: EventBus -> DB -> Singleton
"""
import os, time, asyncio, json, re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

PINE_SYSTEM = """You are an expert Pine Script to Python converter.
Convert TradingView Pine Script strategies into backtestable Python code.

RULES:
1. Output ONLY valid Python code.
2. Use pandas for OHLCV data handling.
3. Use backtesting.py pattern: Strategy class with init() and next().
4. Convert ta.rsi, ta.macd, ta.crossover to pandas equivalents.
5. Convert strategy.entry/exit to position tracking dicts.
6. Include main() that loads CSV and runs the strategy.
7. Add type hints and error handling.
"""


class PineConverter:
    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._count = 0

    def _emit(self, name, payload):
        try:
            from src.db_storage import log_event
            log_event(name, payload)
        except: pass
        if self.event_bus:
            try: asyncio.ensure_future(self.event_bus.emit(name, payload))
            except: pass

    async def convert(self, pine_script, strategy_name=None):
        try:
            from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions, is_bedrock_configured
            if not is_bedrock_configured():
                return {"error": "Bedrock not configured. Set AWS_ACCESS_KEY_ID."}
            if not strategy_name:
                for line in pine_script.split(chr(10))[:5]:
                    if "strategy(" in line:
                        m = re.search(r"strategy\(([^)]+)\)", line)
                        if m: strategy_name = m.group(1).strip().strip('"')
                        break
            if not strategy_name: strategy_name = "Converted Strategy"
            prompt = "Convert this Pine Script to backtestable Python:" + chr(10) + chr(10) + pine_script
            response = await bedrock_chat(
                [ChatMessage(role="user", content=prompt)],
                ChatOptions(system_prompt=PINE_SYSTEM, temperature=0.2, max_tokens=8192))
            code = response.text
            if "`{BT}python" in code:
                code = code.split("`{BT}python")[1].split("`{BT}")[0]
            elif "`{BT}" in code:
                code = code.split("`{BT}")[1].split("`{BT}")[0]
            self._count += 1
            self._emit("pine/conversion", {"name": strategy_name, "len": len(pine_script),
                "ts": datetime.now(timezone.utc).isoformat()})
            return {"success": True, "strategy_name": strategy_name,
                    "python_code": code.strip(), "pine_length": len(pine_script),
                    "python_length": len(code.strip()),
                    "model": os.environ.get("AWS_BEDROCK_MODEL_ID", "unknown")}
        except Exception as e:
            return {"error": str(e)}

    async def explain(self, pine_script):
        try:
            from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions
            prompt = "Explain this Pine Script in plain English:" + chr(10) + chr(10) + pine_script
            r = await bedrock_chat([ChatMessage(role="user", content=prompt)],
                ChatOptions(temperature=0.3, max_tokens=4096))
            return {"success": True, "explanation": r.text}
        except Exception as e:
            return {"error": str(e)}


_inst = None

def get_pine_converter(event_bus=None):
    global _inst
    if _inst is None: _inst = PineConverter(event_bus=event_bus)
    return _inst


async def tool_pine_to_python(pine_script, strategy_name=""):
    return await get_pine_converter().convert(pine_script, strategy_name)

async def tool_pine_explain(pine_script):
    return await get_pine_converter().explain(pine_script)
