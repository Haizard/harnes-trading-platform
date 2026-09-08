"""Deterministic server-side indicator registry for chart snapshots."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class IndicatorDefinition:
    name: str
    version: str
    warmup: int
    compute: Callable[[list[dict[str, Any]]], dict[str, Any]]


_REGISTRY: dict[str, IndicatorDefinition] = {}


def register_indicator(definition: IndicatorDefinition) -> None:
    _REGISTRY[definition.name] = definition


def list_indicators() -> list[dict[str, Any]]:
    return [{"name": item.name, "version": item.version, "warmup": item.warmup} for item in _REGISTRY.values()]


def compute_indicators(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for definition in _REGISTRY.values():
        series = definition.compute(candles)
        series.update({"name": definition.name, "version": definition.version, "warmup": definition.warmup})
        result.append(series)
    return result


def _ema(period: int) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < period:
            return {"values": [], "ready": False}
        multiplier = 2 / (period + 1)
        value = sum(float(candle["close"]) for candle in candles[:period]) / period
        values = [{"time": candles[period - 1]["bucket_time"], "value": value}]
        for candle in candles[period:]:
            value = (float(candle["close"]) - value) * multiplier + value
            values.append({"time": candle["bucket_time"], "value": value})
        return {"values": values, "ready": True}
    return compute


register_indicator(IndicatorDefinition("ema_9", "1", 9, _ema(9)))
register_indicator(IndicatorDefinition("ema_21", "1", 21, _ema(21)))
register_indicator(IndicatorDefinition("ema_50", "1", 50, _ema(50)))
register_indicator(IndicatorDefinition("ema_200", "1", 200, _ema(200)))


def _rsi(period: int = 14) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """Relative Strength Index."""
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < period + 1:
            return {"values": [], "ready": False}
        
        gains = []
        losses = []
        for i in range(1, len(candles)):
            change = float(candles[i]["close"]) - float(candles[i-1]["close"])
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        if len(gains) < period:
            return {"values": [], "ready": False}
        
        # Initial average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        values = []
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            
            values.append({
                "time": candles[i + 1]["bucket_time"],
                "value": round(rsi, 4)
            })
        
        return {"values": values, "ready": True, "overbought": 70, "oversold": 30}
    return compute


def _macd(fast: int = 12, slow: int = 26, signal: int = 9) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """MACD (Moving Average Convergence Divergence)."""
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < slow + signal:
            return {"values": [], "signal_line": [], "histogram": [], "ready": False}
        
        closes = [float(c["close"]) for c in candles]
        ema_fast = _compute_ema_values(closes, fast)
        ema_slow = _compute_ema_values(closes, slow)
        
        macd_line = []
        for i in range(len(closes)):
            if ema_fast[i] is not None and ema_slow[i] is not None:
                macd_line.append(ema_fast[i] - ema_slow[i])
            else:
                macd_line.append(None)
        
        # Signal line is EMA of MACD line
        valid_macd = [v for v in macd_line if v is not None]
        if len(valid_macd) < signal:
            return {"values": [], "signal_line": [], "histogram": [], "ready": False}
        
        signal_line = _compute_ema_values(valid_macd, signal)
        
        # Align back to original candle times
        values = []
        sig_values = []
        hist_values = []
        valid_idx = 0
        for i in range(len(candles)):
            if macd_line[i] is not None:
                values.append({"time": candles[i]["bucket_time"], "value": round(macd_line[i], 6)})
                if valid_idx < len(signal_line) and signal_line[valid_idx] is not None:
                    sig_values.append({"time": candles[i]["bucket_time"], "value": round(signal_line[valid_idx], 6)})
                    hist_values.append({"time": candles[i]["bucket_time"], "value": round(macd_line[i] - signal_line[valid_idx], 6)})
                valid_idx += 1
        
        return {
            "values": values,
            "signal_line": sig_values,
            "histogram": hist_values,
            "ready": True
        }
    return compute


def _bollinger_bands(period: int = 20, std_dev: float = 2.0) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """Bollinger Bands."""
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < period:
            return {"upper": [], "middle": [], "lower": [], "ready": False}
        
        closes = [float(c["close"]) for c in candles]
        upper = []
        middle = []
        lower = []
        
        for i in range(period - 1, len(closes)):
            window = closes[i - period + 1:i + 1]
            sma = sum(window) / period
            std = (sum((x - sma) ** 2 for x in window) / period) ** 0.5
            
            middle.append({"time": candles[i]["bucket_time"], "value": round(sma, 6)})
            upper.append({"time": candles[i]["bucket_time"], "value": round(sma + std_dev * std, 6)})
            lower.append({"time": candles[i]["bucket_time"], "value": round(sma - std_dev * std, 6)})
        
        return {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "ready": True,
            "period": period,
            "std_dev": std_dev
        }
    return compute


def _atr(period: int = 14) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """Average True Range."""
    def compute(candles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(candles) < period + 1:
            return {"values": [], "ready": False}
        
        true_ranges = []
        for i in range(1, len(candles)):
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])
            prev_close = float(candles[i-1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        
        if len(true_ranges) < period:
            return {"values": [], "ready": False}
        
        # Initial ATR is simple average
        atr = sum(true_ranges[:period]) / period
        values = [{"time": candles[period]["bucket_time"], "value": round(atr, 6)}]
        
        # Subsequent ATRs use smoothing
        for i in range(period, len(true_ranges)):
            atr = (atr * (period - 1) + true_ranges[i]) / period
            values.append({"time": candles[i + 1]["bucket_time"], "value": round(atr, 6)})
        
        return {"values": values, "ready": True, "period": period}
    return compute


def _compute_ema_values(data: list[float], period: int) -> list[float | None]:
    """Compute EMA values for a list of floats."""
    if len(data) < period:
        return [None] * len(data)
    
    result = [None] * (period - 1)
    sma = sum(data[:period]) / period
    result.append(sma)
    
    multiplier = 2 / (period + 1)
    for i in range(period, len(data)):
        ema = (data[i] - result[-1]) * multiplier + result[-1]
        result.append(ema)
    
    return result


register_indicator(IndicatorDefinition("rsi_14", "1", 15, _rsi(14)))
register_indicator(IndicatorDefinition("macd_12_26_9", "1", 35, _macd(12, 26, 9)))
register_indicator(IndicatorDefinition("bollinger_20", "1", 20, _bollinger_bands(20, 2.0)))
register_indicator(IndicatorDefinition("atr_14", "1", 15, _atr(14)))
