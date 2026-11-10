"""Closed-candle trading strategies used by the Phase 3 execution loop."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import fabs
from typing import Iterable, Mapping, Sequence


class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    side: SignalSide
    strategy: str
    candle_time: int
    reason: str
    atr: float

    @property
    def key(self) -> str:
        return f"{self.strategy}:{self.side.value}:{self.candle_time}"


def candles_from_rates(rates: Iterable[object]) -> list[Candle]:
    """Convert MT5 named records, dictionaries, or test data to immutable candles."""
    converted: list[Candle] = []
    for row in rates:
        def value(name: str) -> float:
            if isinstance(row, Mapping):
                return float(row[name])
            try:
                return float(row[name])  # numpy structured MT5 rows
            except (TypeError, IndexError, KeyError):
                return float(getattr(row, name))
        converted.append(Candle(int(value("time")), value("open"), value("high"),
                                value("low"), value("close")))
    return converted


def ema(values: Sequence[float], period: int) -> list[float]:
    if period < 1 or len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return [result[0]] * (period - 1) + result


def rma(values: Sequence[float], period: int) -> list[float]:
    if period < 1 or len(values) < period:
        return []
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append((result[-1] * (period - 1) + value) / period)
    return [result[0]] * (period - 1) + result


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    if not candles:
        return []
    values = [candles[0].high - candles[0].low]
    for previous, current in zip(candles, candles[1:]):
        values.append(max(current.high - current.low, fabs(current.high - previous.close),
                          fabs(current.low - previous.close)))
    return values


def atr(candles: Sequence[Candle], period: int = 14) -> list[float]:
    return rma(true_ranges(candles), period)


def rsi(candles: Sequence[Candle], period: int = 14) -> list[float]:
    if len(candles) < period + 1:
        return []
    changes = [candles[i].close - candles[i - 1].close for i in range(1, len(candles))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain, avg_loss = rma(gains, period), rma(losses, period)
    values = []
    for gain, loss in zip(avg_gain, avg_loss):
        values.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return [values[0]] * period + values


def adx(candles: Sequence[Candle], period: int = 14) -> list[float]:
    if len(candles) < period * 2 + 1:
        return []
    plus_dm, minus_dm = [0.0], [0.0]
    for previous, current in zip(candles, candles[1:]):
        up, down = current.high - previous.high, previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    smoothed_tr = rma(true_ranges(candles), period)
    plus = rma(plus_dm, period)
    minus = rma(minus_dm, period)
    dx = []
    for tr, p, m in zip(smoothed_tr, plus, minus):
        plus_di, minus_di = (100 * p / tr, 100 * m / tr) if tr else (0.0, 0.0)
        denominator = plus_di + minus_di
        dx.append(100 * fabs(plus_di - minus_di) / denominator if denominator else 0.0)
    return rma(dx, period)


class Strategy:
    name = "base"

    def evaluate(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle]) -> Signal | None:
        raise NotImplementedError


class TrendStrategy(Strategy):
    name = "trend"

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        self.fast, self.slow = fast, slow

    def evaluate(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle]) -> Signal | None:
        if len(candles) < self.slow + 2 or len(higher_timeframe) < self.slow:
            return None
        fast, slow = ema([c.close for c in candles], self.fast), ema([c.close for c in candles], self.slow)
        h_fast = ema([c.close for c in higher_timeframe], self.fast)[-1]
        h_slow = ema([c.close for c in higher_timeframe], self.slow)[-1]
        current_atr = atr(candles)[-1]
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1] and h_fast > h_slow:
            return Signal(SignalSide.BUY, self.name, candles[-1].time,
                          f"BUY: EMA{self.fast} crossed above EMA{self.slow}; higher trend up", current_atr)
        if fast[-2] >= slow[-2] and fast[-1] < slow[-1] and h_fast < h_slow:
            return Signal(SignalSide.SELL, self.name, candles[-1].time,
                          f"SELL: EMA{self.fast} crossed below EMA{self.slow}; higher trend down", current_atr)
        return None


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(self, rsi_period: int = 14, adx_period: int = 14, min_adx: float = 20) -> None:
        self.rsi_period, self.adx_period, self.min_adx = rsi_period, adx_period, min_adx

    def evaluate(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle]) -> Signal | None:
        values, strength, ranges = rsi(candles, self.rsi_period), adx(candles, self.adx_period), atr(candles)
        if not values or not strength or len(values) < 2 or strength[-1] < self.min_adx:
            return None
        if values[-2] <= 55 < values[-1]:
            return Signal(SignalSide.BUY, self.name, candles[-1].time,
                          f"BUY: RSI crossed 55, ADX {strength[-1]:.1f}", ranges[-1])
        if values[-2] >= 45 > values[-1]:
            return Signal(SignalSide.SELL, self.name, candles[-1].time,
                          f"SELL: RSI crossed 45, ADX {strength[-1]:.1f}", ranges[-1])
        return None


class BreakoutStrategy(Strategy):
    name = "breakout"

    def __init__(self, channel_period: int = 20, atr_period: int = 14, min_atr_ratio: float = 0.0001) -> None:
        self.channel_period, self.atr_period, self.min_atr_ratio = channel_period, atr_period, min_atr_ratio

    def evaluate(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle]) -> Signal | None:
        if len(candles) < self.channel_period + 1:
            return None
        ranges = atr(candles, self.atr_period)
        if not ranges or ranges[-1] / candles[-1].close < self.min_atr_ratio:
            return None
        channel = candles[-self.channel_period - 1:-1]
        high, low = max(c.high for c in channel), min(c.low for c in channel)
        if candles[-1].close > high:
            return Signal(SignalSide.BUY, self.name, candles[-1].time,
                          f"BUY: Donchian {self.channel_period} breakout with ATR confirmation", ranges[-1])
        if candles[-1].close < low:
            return Signal(SignalSide.SELL, self.name, candles[-1].time,
                          f"SELL: Donchian {self.channel_period} breakout with ATR confirmation", ranges[-1])
        return None


class CombinedStrategy(Strategy):
    name = "combined"

    def __init__(self, strategies: Sequence[Strategy] | None = None) -> None:
        self.strategies = list(strategies or (TrendStrategy(), MomentumStrategy(), BreakoutStrategy()))

    def evaluate(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle]) -> Signal | None:
        signals = [signal for strategy in self.strategies
                   if (signal := strategy.evaluate(candles, higher_timeframe)) is not None]
        for side in SignalSide:
            agreed = [signal for signal in signals if signal.side is side]
            if len(agreed) >= 2:
                names = ", ".join(signal.strategy for signal in agreed)
                return Signal(side, self.name, candles[-1].time,
                              f"{side.value}: combined agreement from {names}", agreed[0].atr)
        return None


def build_strategy(name: str) -> Strategy:
    strategies = {"trend": TrendStrategy, "momentum": MomentumStrategy,
                  "breakout": BreakoutStrategy, "combined": CombinedStrategy}
    try:
        return strategies[name.lower()]()
    except KeyError as exc:
        raise ValueError(f"Unknown strategy: {name}") from exc
