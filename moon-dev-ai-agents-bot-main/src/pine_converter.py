"""
Pine Script to Python Converter - AI-powered strategy conversion.
DSH Pattern: EventBus -> DB -> Singleton
"""
import os, time, asyncio, json, re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from src.event_bus import _fire_and_forget

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

    def _emit_event(self, name, payload):
        try:
            from src.db_storage import log_event
            log_event(name, payload)
        except: pass
        if self.event_bus:
            try: _fire_and_forget(self.event_bus.emit(name, payload))
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
            self._emit_event("pine/conversion", {"name": strategy_name, "len": len(pine_script),
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
    """Get or create the singleton PineConverter instance (DSH pattern)."""
    global _inst
    if _inst is None:
        _inst = PineConverter(event_bus=event_bus)
    return _inst


async def tool_pine_to_python(pine_script, strategy_name=""):
    return await get_pine_converter().convert(pine_script, strategy_name)

async def tool_pine_explain(pine_script):
    return await get_pine_converter().explain(pine_script)



class TypeScriptConverter:
    """Convert TypeScript trading bots to Python for chart rendering."""

    TS_SYSTEM = """You are an expert TypeScript-to-Python converter.
Convert TypeScript trading strategy bots into Python equivalents suitable for chart rendering and backtesting.

RULES:
1. Output ONLY valid Python code.
2. Use pandas for OHLCV data handling.
3. Convert TypeScript interfaces/classes to Python dataclasses or dicts.
4. Convert async/await to sync or asyncio equivalents.
5. Convert TradingView/Pine-style indicator logic to numpy/pandas.
6. Include a detect_patterns(candles) function returning dict with overlay data.
7. Include a run_backtest(candles) function returning entry/exit signals.
8. Add type hints and error handling.
9. Return format: {markers: [...], overlays: {...}, signals: [...]}
"""

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._count = 0

    def _emit_event(self, name, payload):
        try:
            from src.db_storage import log_event
            log_event(name, payload)
        except: pass
        if self.event_bus:
            try:
                from src.event_bus import _fire_and_forget
                _fire_and_forget(self.event_bus.emit(name, payload))
            except: pass

    async def convert(self, typescript_code, strategy_name=None):
        try:
            from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions, is_bedrock_configured
            if not is_bedrock_configured():
                return {"error": "Bedrock not configured. Set AWS_ACCESS_KEY_ID."}
            if not strategy_name:
                for line in typescript_code.split(chr(10))[:10]:
                    if "class " in line or "function " in line:
                        m = __import__("re").search(r"(?:class|function)\s+(\w+)", line)
                        if m: strategy_name = m.group(1)
                        break
            if not strategy_name: strategy_name = "Converted TS Strategy"
            prompt = "Convert this TypeScript trading bot to Python with detect_patterns and run_backtest functions:" + chr(10) + chr(10) + typescript_code
            response = await bedrock_chat(
                [ChatMessage(role="user", content=prompt)],
                ChatOptions(system_prompt=self.TS_SYSTEM, temperature=0.2, max_tokens=8192))
            py_code = response.text
            py_code = response.text
            bt = chr(96) * 3
            if bt + "python" in py_code:
                py_code = py_code.split(bt + "python")[1].split(bt)[0]
            elif bt in py_code:
                py_code = py_code.split(bt)[1].split(bt)[0]
            self._count += 1
            self._emit_event("ts_to_python/conversion", {
                "name": strategy_name, "ts_len": len(typescript_code),
                "py_len": len(py_code.strip()),
                "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            })
            return {"success": True, "strategy_name": strategy_name,
                    "python_code": py_code.strip(),
                    "typescript_length": len(typescript_code),
                    "python_length": len(py_code.strip()),
                    "model": __import__("os").environ.get("AWS_BEDROCK_MODEL_ID", "unknown")}
        except Exception as e:
            return {"error": str(e)}

    async def explain_typescript(self, typescript_code):
        try:
            from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions
            prompt = "Explain this TypeScript trading bot in plain English, covering its strategy logic:" + chr(10) + chr(10) + typescript_code
            r = await bedrock_chat([ChatMessage(role="user", content=prompt)],
                ChatOptions(temperature=0.3, max_tokens=4096))
            return {"success": True, "explanation": r.text}
        except Exception as e:
            return {"error": str(e)}


_ts_inst = None

def get_ts_converter(event_bus=None):
    global _ts_inst
    if _ts_inst is None:
        _ts_inst = TypeScriptConverter(event_bus=event_bus)
    return _ts_inst


async def tool_ts_to_python(typescript_code, strategy_name=""):
    return await get_ts_converter().convert(typescript_code, strategy_name)

async def tool_ts_explain(typescript_code):
    return await get_ts_converter().explain_typescript(typescript_code)

