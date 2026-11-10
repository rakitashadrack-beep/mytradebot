"""Signal confidence scorer — returns 0.0–1.0.

A trade is only allowed when confidence >= MIN_CONFIDENCE (default 0.80).
Eight independent checks each contribute a weighted vote; the final score
is the weighted sum of passing checks divided by the total possible weight.

Checks (all use closed candles only):
  1. EMA trend alignment on entry timeframe          weight 2
  2. EMA trend alignment on higher timeframe         weight 2
  3. RSI momentum direction                          weight 1
  4. ADX trend strength >= 20                        weight 1
  5. Donchian channel breakout confirmation          weight 1
  6. ATR volatility is meaningful (> 0.0001×price)  weight 1
  7. Candle body direction matches signal            weight 1
  8. Spread is below configured maximum              weight 1
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.strategies import (
    Candle, SignalSide, atr, adx, ema, rsi,
)

MIN_CONFIDENCE: float = 0.80


@dataclass(frozen=True)
class ConfidenceResult:
    score: float          # 0.0 – 1.0
    passed: int
    total_checks: int
    reasons: tuple[str, ...]
    allowed: bool


def score_signal(
    side: SignalSide,
    candles: Sequence[Candle],
    higher: Sequence[Candle],
    spread_points: float,
    max_spread_points: float,
    fast: int = 10,
    slow: int = 30,
    rsi_period: int = 14,
    adx_period: int = 14,
    channel_period: int = 20,
    min_confidence: float = MIN_CONFIDENCE,
) -> ConfidenceResult:
    reasons: list[str] = []
    weights: list[int] = []
    scores: list[float] = []

    def check(weight: int, passed: bool, label: str) -> None:
        weights.append(weight)
        scores.append(weight if passed else 0)
        reasons.append(f"{'✓' if passed else '✗'} {label}")

    closes = [c.close for c in candles]
    h_closes = [c.close for c in higher]

    # 1. Entry-TF EMA alignment
    if len(closes) >= slow + 2:
        f, s = ema(closes, fast), ema(closes, slow)
        aligned = (f[-1] > s[-1]) if side is SignalSide.BUY else (f[-1] < s[-1])
        check(2, aligned, f"Entry EMA{fast}>{slow}" if side is SignalSide.BUY else f"Entry EMA{fast}<{slow}")
    else:
        check(2, False, "Entry EMA: insufficient candles")

    # 2. Higher-TF EMA alignment
    if len(h_closes) >= slow:
        hf, hs = ema(h_closes, fast), ema(h_closes, slow)
        h_aligned = (hf[-1] > hs[-1]) if side is SignalSide.BUY else (hf[-1] < hs[-1])
        check(2, h_aligned, "Higher-TF EMA aligned")
    else:
        check(2, False, "Higher-TF EMA: insufficient candles")

    # 3. RSI momentum
    if len(candles) >= rsi_period + 2:
        rsi_vals = rsi(candles, rsi_period)
        rsi_ok = (rsi_vals[-1] > 50) if side is SignalSide.BUY else (rsi_vals[-1] < 50)
        check(1, rsi_ok, f"RSI {rsi_vals[-1]:.1f} {'> 50' if side is SignalSide.BUY else '< 50'}")
    else:
        check(1, False, "RSI: insufficient candles")

    # 4. ADX strength
    if len(candles) >= adx_period * 2 + 2:
        adx_vals = adx(candles, adx_period)
        strong = adx_vals[-1] >= 20 if adx_vals else False
        check(1, strong, f"ADX {adx_vals[-1]:.1f} >= 20" if adx_vals else "ADX: no value")
    else:
        check(1, False, "ADX: insufficient candles")

    # 5. Donchian breakout
    if len(candles) >= channel_period + 1:
        channel = candles[-channel_period - 1:-1]
        high = max(c.high for c in channel)
        low = min(c.low for c in channel)
        broke = (candles[-1].close > high) if side is SignalSide.BUY else (candles[-1].close < low)
        check(1, broke, "Donchian breakout confirmed")
    else:
        check(1, False, "Donchian: insufficient candles")

    # 6. ATR volatility meaningful
    if len(candles) >= 15:
        atr_vals = atr(candles)
        meaningful = atr_vals[-1] > candles[-1].close * 0.0001 if atr_vals else False
        check(1, meaningful, f"ATR {atr_vals[-1]:.5f} meaningful")
    else:
        check(1, False, "ATR: insufficient candles")

    # 7. Candle body direction
    last = candles[-1]
    body_ok = (last.close > last.open) if side is SignalSide.BUY else (last.close < last.open)
    check(1, body_ok, "Candle body direction matches")

    # 8. Spread acceptable
    check(1, spread_points <= max_spread_points, f"Spread {spread_points:.1f} <= {max_spread_points:.1f}")

    total_weight = sum(weights)
    achieved = sum(scores)
    score = achieved / total_weight if total_weight > 0 else 0.0

    return ConfidenceResult(
        score=round(score, 4),
        passed=sum(1 for s in scores if s > 0),
        total_checks=len(weights),
        reasons=tuple(reasons),
        allowed=score >= min_confidence,
    )
