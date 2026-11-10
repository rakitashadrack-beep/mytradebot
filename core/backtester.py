"""Deterministic closed-candle backtester for validating MyTradeBot strategies.

This is an analytical tool, not a prediction engine. It intentionally fills an
entry on the next bar and assumes the stop is hit first when both exit levels are
touched in one candle, avoiding favourable look-ahead assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.risk import RiskEngine, SymbolConstraints
from core.strategies import Candle, SignalSide, Strategy


@dataclass(frozen=True)
class BacktestConfig:
    initial_balance: float = 10_000.0
    spread_points: float = 10.0
    commission_per_lot: float = 0.0
    atr_stop_multiplier: float = 1.5
    reward_to_risk: float = 2.0

    def validate(self) -> None:
        if self.initial_balance <= 0 or self.spread_points < 0 or self.commission_per_lot < 0:
            raise ValueError("balance, spread, and commission values are invalid")
        if self.atr_stop_multiplier <= 0 or self.reward_to_risk <= 0:
            raise ValueError("stop and reward multipliers must be positive")


@dataclass(frozen=True)
class BacktestTrade:
    side: SignalSide
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    volume: float
    profit: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestReport:
    initial_balance: float
    final_balance: float
    trades: tuple[BacktestTrade, ...]
    win_rate: float
    profit_factor: float | None
    max_drawdown: float
    equity_curve: tuple[tuple[int, float], ...]


class Backtester:
    def __init__(self, strategy: Strategy, risk_engine: RiskEngine,
                 constraints: SymbolConstraints, config: BacktestConfig) -> None:
        config.validate()
        constraints.validate()
        self.strategy, self.risk_engine = strategy, risk_engine
        self.constraints, self.config = constraints, config

    def run(self, candles: Sequence[Candle], higher_timeframe: Sequence[Candle] | None = None) -> BacktestReport:
        if len(candles) < 61:
            raise ValueError("at least 61 candles are required for a meaningful backtest")
        higher = list(higher_timeframe or candles)
        balance, peak, max_drawdown = self.config.initial_balance, self.config.initial_balance, 0.0
        trades: list[BacktestTrade] = []
        curve: list[tuple[int, float]] = [(candles[0].time, balance)]
        position = None
        for index in range(60, len(candles) - 1):
            current, next_bar = candles[index], candles[index + 1]
            if position:
                closed = self._try_close(position, current)
                if closed:
                    trade = self._close_trade(position, current, closed)
                    trades.append(trade)
                    balance += trade.profit
                    peak = max(peak, balance)
                    max_drawdown = max(max_drawdown, peak - balance)
                    curve.append((current.time, balance))
                    position = None
                continue
            available_higher = [bar for bar in higher if bar.time <= current.time]
            signal = self.strategy.evaluate(candles[:index + 1], available_higher)
            if signal is None or signal.atr <= 0:
                continue
            stop_points = signal.atr * self.config.atr_stop_multiplier / self.constraints.point
            decision = self.risk_engine.size_order(balance, stop_points, self.constraints)
            if not decision.allowed:
                continue
            half_spread = self.config.spread_points * self.constraints.point / 2
            entry = next_bar.open + half_spread if signal.side is SignalSide.BUY else next_bar.open - half_spread
            distance = stop_points * self.constraints.point
            position = {
                "side": signal.side, "entry_time": next_bar.time, "entry": entry,
                "volume": decision.volume,
                "sl": entry - distance if signal.side is SignalSide.BUY else entry + distance,
                "tp": entry + distance * self.config.reward_to_risk if signal.side is SignalSide.BUY else entry - distance * self.config.reward_to_risk,
            }
        if position:
            trade = self._close_trade(position, candles[-1], "end of data")
            trades.append(trade)
            balance += trade.profit
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
            curve.append((candles[-1].time, balance))
        wins = [trade.profit for trade in trades if trade.profit > 0]
        losses = [trade.profit for trade in trades if trade.profit < 0]
        gross_loss = abs(sum(losses))
        return BacktestReport(
            self.config.initial_balance, balance, tuple(trades),
            len(wins) / len(trades) * 100 if trades else 0.0,
            sum(wins) / gross_loss if gross_loss else (None if wins else 0.0),
            max_drawdown, tuple(curve),
        )

    def _try_close(self, position: dict, candle: Candle) -> str | None:
        if position["side"] is SignalSide.BUY:
            if candle.low <= position["sl"]: return "stop loss"
            if candle.high >= position["tp"]: return "take profit"
        else:
            if candle.high >= position["sl"]: return "stop loss"
            if candle.low <= position["tp"]: return "take profit"
        return None

    def _close_trade(self, position: dict, candle: Candle, reason: str) -> BacktestTrade:
        if reason == "stop loss":
            price = position["sl"]
        elif reason == "take profit":
            price = position["tp"]
        else:
            price = candle.close
        direction = 1 if position["side"] is SignalSide.BUY else -1
        gross = (price - position["entry"]) * direction * position["volume"] * self.constraints.tick_value / self.constraints.tick_size
        commission = position["volume"] * self.config.commission_per_lot
        return BacktestTrade(position["side"], position["entry_time"], candle.time,
                             position["entry"], price, position["volume"], gross - commission, reason)
