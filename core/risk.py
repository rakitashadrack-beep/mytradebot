"""Mandatory risk controls and broker-aware position sizing for MyTradeBot.

The module is deliberately independent of MetaTrader5 and Qt so every order path
(manual, automated, paper, and backtest) can make the same safety decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum


class LotSizeMode(str, Enum):
    FIXED = "fixed"
    RISK_PERCENT = "risk_percent"


@dataclass(frozen=True)
class RiskSettings:
    """Non-optional limits. Conservative defaults are suitable for a new account."""

    lot_size_mode: LotSizeMode = LotSizeMode.RISK_PERCENT
    fixed_lot: float = 0.01
    risk_percent_per_trade: float = 0.5
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    cooldown_seconds: int = 900
    max_daily_loss_percent: float = 2.0
    max_drawdown_percent: float = 8.0
    max_weekly_loss_percent: float = 5.0
    max_spread_points: float = 30.0
    minimum_free_margin: float = 100.0

    def validate(self) -> None:
        if self.fixed_lot <= 0:
            raise ValueError("fixed_lot must be greater than zero")
        if not 0 < self.risk_percent_per_trade <= 100:
            raise ValueError("risk_percent_per_trade must be between 0 and 100")
        if self.max_open_positions < 1 or self.max_trades_per_day < 1:
            raise ValueError("position and daily trade limits must be at least one")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        for name, value in (
            ("max_daily_loss_percent", self.max_daily_loss_percent),
            ("max_drawdown_percent", self.max_drawdown_percent),
            ("max_weekly_loss_percent", self.max_weekly_loss_percent),
        ):
            if not 0 < value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if self.max_spread_points <= 0 or self.minimum_free_margin < 0:
            raise ValueError("spread and minimum free margin values are invalid")


@dataclass(frozen=True)
class SymbolConstraints:
    """The volume and tick rules supplied by the selected MT5 symbol."""

    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    tick_value: float
    tick_size: float

    def validate(self) -> None:
        if self.point <= 0 or self.volume_min <= 0 or self.volume_max < self.volume_min:
            raise ValueError("symbol has invalid price or volume limits")
        if self.volume_step <= 0 or self.tick_value <= 0 or self.tick_size <= 0:
            raise ValueError("symbol has invalid tick or volume step data")


@dataclass(frozen=True)
class RiskSnapshot:
    balance: float
    equity: float
    free_margin: float
    open_positions: int
    trades_today: int
    daily_realized_pnl: float
    weekly_realized_pnl: float
    peak_balance: float
    spread_points: float
    seconds_since_last_trade: float | None = None


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    volume: float = 0.0


def normalize_volume(volume: float, constraints: SymbolConstraints) -> float:
    """Round down to the broker's step, without ever exceeding its maximum."""
    constraints.validate()
    if volume <= 0:
        return 0.0
    bounded = min(Decimal(str(volume)), Decimal(str(constraints.volume_max)))
    minimum = Decimal(str(constraints.volume_min))
    step = Decimal(str(constraints.volume_step))
    if bounded < minimum:
        return 0.0
    steps = ((bounded - minimum) / step).to_integral_value(rounding=ROUND_DOWN)
    normalized = minimum + steps * step
    return float(normalized.quantize(step))


def calculate_risk_volume(balance: float, risk_percent: float,
                          stop_distance_points: float,
                          constraints: SymbolConstraints) -> float:
    """Return a safe lot size for a stop distance, or zero when it cannot be sized."""
    constraints.validate()
    if balance <= 0 or not 0 < risk_percent <= 100 or stop_distance_points <= 0:
        return 0.0
    risk_money = Decimal(str(balance)) * Decimal(str(risk_percent)) / Decimal("100")
    price_distance = Decimal(str(stop_distance_points)) * Decimal(str(constraints.point))
    loss_per_lot = price_distance / Decimal(str(constraints.tick_size)) * Decimal(str(constraints.tick_value))
    if loss_per_lot <= 0:
        return 0.0
    return normalize_volume(float(risk_money / loss_per_lot), constraints)


class RiskEngine:
    """Evaluates every proposed order against limits that cannot be bypassed."""

    def __init__(self, settings: RiskSettings) -> None:
        settings.validate()
        self.settings = settings

    def size_order(self, balance: float, stop_distance_points: float,
                   constraints: SymbolConstraints) -> RiskDecision:
        if stop_distance_points <= 0:
            return RiskDecision(False, "A stop loss distance is required")
        if self.settings.lot_size_mode is LotSizeMode.FIXED:
            volume = normalize_volume(self.settings.fixed_lot, constraints)
        else:
            volume = calculate_risk_volume(balance, self.settings.risk_percent_per_trade,
                                            stop_distance_points, constraints)
        if volume <= 0:
            return RiskDecision(False, "Calculated volume is below the broker minimum")
        return RiskDecision(True, "Risk size approved", volume)

    def evaluate(self, snapshot: RiskSnapshot, stop_distance_points: float,
                 constraints: SymbolConstraints, estimated_margin: float) -> RiskDecision:
        """Return the first blocking reason, or an approved normalized volume."""
        if snapshot.balance <= 0 or snapshot.equity < 0:
            return RiskDecision(False, "Account balance or equity is invalid")
        if snapshot.open_positions >= self.settings.max_open_positions:
            return RiskDecision(False, "Maximum open positions reached")
        if snapshot.trades_today >= self.settings.max_trades_per_day:
            return RiskDecision(False, "Maximum daily trades reached")
        if snapshot.spread_points > self.settings.max_spread_points:
            return RiskDecision(False, "Spread exceeds the configured limit")
        if (snapshot.seconds_since_last_trade is not None and
                snapshot.seconds_since_last_trade < self.settings.cooldown_seconds):
            return RiskDecision(False, "Trade cooldown is active")
        daily_loss = max(0.0, -snapshot.daily_realized_pnl + max(0.0, snapshot.balance - snapshot.equity))
        if daily_loss >= snapshot.balance * self.settings.max_daily_loss_percent / 100:
            return RiskDecision(False, "Daily loss limit reached")
        if snapshot.peak_balance > 0:
            drawdown = max(0.0, snapshot.peak_balance - snapshot.equity)
            if drawdown >= snapshot.peak_balance * self.settings.max_drawdown_percent / 100:
                return RiskDecision(False, "Maximum drawdown limit reached")
        weekly_loss = max(0.0, -snapshot.weekly_realized_pnl)
        if weekly_loss >= snapshot.balance * self.settings.max_weekly_loss_percent / 100:
            return RiskDecision(False, "Weekly loss limit reached")
        if estimated_margin < 0 or snapshot.free_margin - estimated_margin < self.settings.minimum_free_margin:
            return RiskDecision(False, "Insufficient free margin")
        return self.size_order(snapshot.balance, stop_distance_points, constraints)
