"""
ASCII Candlestick Chart Generator for Telegram
Renders OHLCV data as text-based charts that display correctly in Telegram.

Usage:
    from src.ascii_chart import render_chart
    chart = render_chart(df, symbol="TJR", num_candles=30)
    telegram.send(chart)
"""

import pandas as pd
from datetime import datetime


def render_chart(df: pd.DataFrame, symbol: str = "TOKEN",
                 num_candles: int = 30, width: int = 40) -> str:
    """
    Render an ASCII candlestick chart from OHLCV DataFrame.
    
    Args:
        df: DataFrame with Open, High, Low, Close, Volume columns
        symbol: Token symbol for header
        num_candles: Number of recent candles to show
        width: Chart width in characters
    
    Returns:
        Multi-line string with the chart
    """
    if df is None or len(df) < 2:
        return f"📊 {symbol}\nNo chart data available yet."

    # Take last N candles
    data = df.tail(num_candles).copy()

    # Chart dimensions
    chart_height = 12
    ohlc_width = width - 8  # Leave room for labels

    # Get price range
    highs = data["High"].values
    lows = data["Low"].values
    closes = data["Close"].values

    price_max = float(max(highs))
    price_min = float(min(lows))
    price_range = price_max - price_min

    if price_range == 0:
        price_range = price_max * 0.01  # 1% fallback

    # Build chart grid
    grid = [[" " for _ in range(ohlc_width)] for _ in range(chart_height)]

    # Place each candle
    candle_width = max(1, ohlc_width // len(data))

    for idx, (_, row) in enumerate(data.iterrows()):
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])

        # Map prices to chart rows
        high_row = int((price_max - h) / price_range * (chart_height - 1))
        low_row = int((price_max - l) / price_range * (chart_height - 1))
        open_row = int((price_max - o) / price_range * (chart_height - 1))
        close_row = int((price_max - c) / price_range * (chart_height - 1))

        high_row = max(0, min(chart_height - 1, high_row))
        low_row = max(0, min(chart_height - 1, low_row))
        open_row = max(0, min(chart_height - 1, open_row))
        close_row = max(0, min(chart_height - 1, close_row))

        col = min(idx, ohlc_width - 1)
        is_green = c >= o

        # Draw wick
        for r in range(high_row, low_row + 1):
            if 0 <= r < chart_height and 0 <= col < ohlc_width:
                if grid[r][col] == " ":
                    grid[r][col] = "│"

        # Draw body
        body_top = min(open_row, close_row)
        body_bottom = max(open_row, close_row)
        for r in range(body_top, body_bottom + 1):
            if 0 <= r < chart_height and 0 <= col < ohlc_width:
                grid[r][col] = "█" if is_green else "░"

        # Mark close with arrow
        if 0 <= close_row < chart_height and 0 <= col < ohlc_width:
            grid[close_row][col] = "▶" if is_green else "◀"

    # Build output
    lines = []

    # Header
    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if len(closes) > 1 else last_close
    change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close > 0 else 0
    change_emoji = "🟢" if change_pct >= 0 else "🔴"

    lines.append(f"📊 {symbol} — {change_emoji} {change_pct:+.2f}%")
    lines.append(f"Price: ${last_close:.8f}")
    lines.append("")

    # Price labels + chart
    for row_idx in range(chart_height):
        price_at_row = price_max - (row_idx / (chart_height - 1)) * price_range
        row_str = "".join(grid[row_idx])

        # Only show price labels on certain rows
        if row_idx == 0:
            label = f"${price_max:.6f}"
        elif row_idx == chart_height - 1:
            label = f"${price_min:.6f}"
        elif row_idx == chart_height // 2:
            mid = (price_max + price_min) / 2
            label = f"${mid:.6f}"
        else:
            label = "         "

        lines.append(f" {label} │{row_str}")

    # X-axis
    lines.append(f"          └{'─' * ohlc_width}")

    # Time labels
    if len(data) >= 2:
        first_time = data.index[0]
        last_time = data.index[-1]
        if hasattr(first_time, 'strftime'):
            t1 = first_time.strftime('%H:%M')
            t2 = last_time.strftime('%H:%M')
        else:
            t1 = str(first_time)[:5]
            t2 = str(last_time)[:5]
        padding = ohlc_width - len(t1) - len(t2)
        lines.append(f"          {t1}{' ' * max(0, padding)}{t2}")

    # Volume bar
    vol = data["Volume"].values
    vol_max = max(vol) if max(vol) > 0 else 1
    last_vol = vol[-1]
    vol_bar_len = int((last_vol / vol_max) * 20)
    vol_bar = "▓" * vol_bar_len + "░" * (20 - vol_bar_len)
    lines.append(f"\nVolume: {vol_bar} {last_vol:,.0f}")

    # Summary
    total_vol = sum(vol)
    avg_close = sum(closes) / len(closes)
    lines.append(f"Avg: ${avg_close:.8f} | Total Vol: {total_vol:,.0f}")

    return "\n".join(lines)


def render_mini_chart(df: pd.DataFrame, num_candles: int = 20) -> str:
    """
    Render a tiny sparkline-style chart (single line).
    Good for inline messages where space is limited.
    """
    if df is None or len(df) < 2:
        return "📊 No data"

    closes = df["Close"].tail(num_candles).values
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx > mn else 1

    blocks = " ▁▂▃▄▅▆▇█"
    sparkline = ""
    for c in closes:
        idx = int((c - mn) / rng * (len(blocks) - 1))
        sparkline += blocks[idx]

    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    emoji = "📈" if last >= prev else "📉"

    return f"{emoji} {sparkline} ${last:.8f}"
