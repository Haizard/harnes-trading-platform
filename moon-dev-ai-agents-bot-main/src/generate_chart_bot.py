import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def generate():
    from src.bedrock_llm import bedrock_chat, ChatMessage, ChatOptions
    print("[QWEN] Calling Qwen3-Coder-Next...")

    sys_prompt = """You are Moon Dev Chart Bot Generator. Output ONLY Python code.
Use dataclasses. All functions accept list of candle dicts: [{time,open,high,low,close,volume}]"""

    prompt = """Create src/custom_chart_bots.py with:
1. ChartBotResult dataclass: markers(list), price_lines(list), panels(list)
2. detect_liquidity_sweeps(candles, lookback=20) - find swing high/low sweeps with reversal
3. detect_fvg_fills(candles, tolerance=0.002) - find FVG detection and fill tracking
4. run_custom_bots(candles) - run both and merge results

Swing detection: high[i]>high[i-1] and high[i]>high[i+1] for swing highs
Sweep: price breaks swing level then reverses within 3 candles, sweep candle wick>50pct range
FVG: gap between candle[i-1].high and candle[i+1].low (bullish) or inverse (bearish)
Markers: {time, position:aboveBar/belowBar, color, shape:arrowUp/arrowDown/circle, text}
Price lines: {price, color, lineStyle:0/1/2, title}
Colors: bull sweep #4ade80, bear sweep #f87171, FVG filled #fbbf24 dashed, FVG unfilled #a78bfa dotted
Include proper type hints and error handling. Return ONLY code."""

    r = await bedrock_chat(
        [ChatMessage(role="user", content=prompt)],
        ChatOptions(system_prompt=sys_prompt, model="qwen.qwen3-coder-next", temperature=0.2, max_tokens=8192))

    code = r.text
    bt = chr(96) * 3
    if bt + "python" in code:
        code = code.split(bt + "python")[1].split(bt)[0]
    elif bt in code:
        code = code.split(bt)[1].split(bt)[0]
    code = code.strip()

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "custom_chart_bots.py")
    with open(out, "w", encoding="utf-8") as f: f.write(code)
    compile(code, out, "exec")
    print(f"[QWEN] Generated {len(code)} chars -> {out}")
    print("[QWEN] Syntax OK")

asyncio.run(generate())
