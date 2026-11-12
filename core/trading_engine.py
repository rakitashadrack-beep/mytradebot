"""Automatic closed-candle trading engine with per-run profit target and 80%+ confidence gate.

Every run has its own profit target. The engine hunts entries until the target
is reached, then emits a 'halt' event so the worker stops the bot automatically.
No manual trade path exists here — this is a pure automatic execution engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from core.constants import OrderType
from core.risk import RiskEngine, RiskSnapshot, SymbolConstraints
from core.signal_confidence import MIN_CONFIDENCE, score_signal
from core.strategies import SignalSide, Strategy, candles_from_rates


@dataclass(frozen=True)
class TradingConfig:
    symbol: str
    timeframe_minutes: int = 15
    higher_timeframe_minutes: int = 60
    candle_count: int = 200
    atr_stop_multiplier: float = 1.5
    reward_to_risk: float = 2.0
    run_profit_target: float = 0.0        # account-currency target for this run; 0 = unlimited
    daily_profit_target: float = 0.0      # hard daily ceiling; 0 = disabled
    trading_start_hour_utc: int = 0
    trading_end_hour_utc: int = 24
    min_confidence: float = MIN_CONFIDENCE  # 0.80 default

    def validate(self) -> None:
        if not self.symbol.strip() or self.candle_count < 60:
            raise ValueError("symbol and at least 60 candles are required")
        if self.atr_stop_multiplier <= 0 or self.reward_to_risk <= 0:
            raise ValueError("ATR stop multiplier and reward ratio must be positive")
        if self.run_profit_target < 0 or self.daily_profit_target < 0:
            raise ValueError("profit targets cannot be negative")
        if not 0 <= self.trading_start_hour_utc <= 23 or not 1 <= self.trading_end_hour_utc <= 24:
            raise ValueError("trading hours must be within UTC day boundaries")
        if not 0.5 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.5 and 1.0")


@dataclass(frozen=True)
class EngineEvent:
    level: str    # info | warning | error | trade | halt | confidence
    message: str
    confidence: float = 0.0


class TradingEngine:
    """Runs one safe evaluation per second. Worker schedules tick() every second."""

    def __init__(
        self,
        bridge,
        database,
        risk_engine: RiskEngine,
        strategy: Strategy,
        config: TradingConfig,
        license_checker: Callable[[], object] | None = None,
        license_recheck_seconds: int = 300,
    ) -> None:
        config.validate()
        self.bridge = bridge
        self.database = database
        self.risk_engine = risk_engine
        self.strategy = strategy
        self.config = config
        self._seen_signal_keys: set[str] = set()
        self.license_checker = license_checker
        self.license_recheck_seconds = license_recheck_seconds
        self._last_license_check: datetime | None = None
        self._license_allowed: bool | None = None
        self._license_message = "license check failed"
        self._run_start: datetime | None = None
        self._run_start_balance: float = 0.0

    def tick(self, now: datetime | None = None) -> list[EngineEvent]:
        now = now or datetime.now(timezone.utc)

        # Record run start balance once
        if self._run_start is None:
            self._run_start = now
            account = self.bridge.get_account_info() if self.bridge.is_connected else None
            self._run_start_balance = account.balance if account else 0.0

        # License check
        if self.license_checker:
            if (
                self._last_license_check is None
                or (now - self._last_license_check).total_seconds() >= self.license_recheck_seconds
            ):
                status = self.license_checker()
                self._last_license_check = now
                self._license_allowed = bool(getattr(status, "allowed", False))
                self._license_message = str(getattr(status, "message", "license check failed"))
            if not self._license_allowed:
                return [EngineEvent("warning", f"Paused: {self._license_message}")]

        if not self.bridge.is_connected:
            return [EngineEvent("warning", "Paused: MT5 disconnected")]

        # Check run profit target
        target_event = self._check_run_target(now)
        if target_event:
            return [target_event]

        # Manage open positions (profit-target closes)
        exit_events = self._manage_positions(now)
        if exit_events:
            return exit_events

        if not self._within_trading_hours(now):
            return [EngineEvent("info", "Paused: outside configured trading hours")]

        if not self.bridge.is_symbol_tradeable(self.config.symbol):
            return [EngineEvent("warning", f"Paused: {self.config.symbol} not tradeable")]

        entry_tf = self.bridge.get_timeframe(self.config.timeframe_minutes)
        higher_tf = self.bridge.get_timeframe(self.config.higher_timeframe_minutes)
        if entry_tf is None or higher_tf is None:
            return [EngineEvent("error", "Paused: unsupported timeframe")]

        entry_rates = self.bridge.get_candles(
            self.config.symbol, entry_tf, self.config.candle_count, closed_only=True
        )
        higher_rates = self.bridge.get_candles(
            self.config.symbol, higher_tf, self.config.candle_count, closed_only=True
        )
        if entry_rates is None or higher_rates is None:
            return [EngineEvent("warning", "Waiting: candle data unavailable")]

        entry_candles = candles_from_rates(entry_rates)
        higher_candles = candles_from_rates(higher_rates)

        signal = self.strategy.evaluate(entry_candles, higher_candles)
        if signal is None:
            return [EngineEvent("info", "Scanning: no signal yet")]

        if signal.key in self._seen_signal_keys:
            return [EngineEvent("info", "Scanning: signal already processed for this candle")]

        # Confidence gate — 80%+ required
        tick_data = self.bridge.get_tick(self.config.symbol)
        sym_info = self.bridge.get_symbol_info(self.config.symbol)
        spread_pts = 0.0
        if tick_data and sym_info and sym_info.point > 0:
            spread_pts = (tick_data.ask - tick_data.bid) / sym_info.point

        confidence = score_signal(
            signal.side,
            entry_candles,
            higher_candles,
            spread_pts,
            self.risk_engine.settings.max_spread_points,
            min_confidence=self.config.min_confidence,
        )

        conf_msg = (
            f"Confidence {confidence.score * 100:.1f}% "
            f"({confidence.passed}/{confidence.total_checks} checks)"
        )

        if not confidence.allowed:
            self._seen_signal_keys.add(signal.key)
            return [EngineEvent(
                "confidence",
                f"Signal skipped — {conf_msg} (need {self.config.min_confidence * 100:.0f}%+)",
                confidence.score,
            )]

        self._seen_signal_keys.add(signal.key)
        events = self._execute_signal(signal, now, confidence.score)
        if events and events[0].level == "trade":
            events.insert(0, EngineEvent("confidence", conf_msg, confidence.score))
        return events

    def _check_run_target(self, now: datetime) -> EngineEvent | None:
        """Halt the bot when the per-run profit target is reached."""
        if self.config.run_profit_target <= 0 or self._run_start is None:
            return None
        run_profit = self.database.realized_profit_between(
            self._run_start.isoformat(), now.isoformat()
        )
        if run_profit >= self.config.run_profit_target:
            return EngineEvent(
                "halt",
                f"Run target reached: +{run_profit:.2f} >= {self.config.run_profit_target:.2f} — bot stopped",
            )
        # Also check daily ceiling
        if self.config.daily_profit_target > 0:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            daily = self.database.realized_profit_between(day_start.isoformat(), now.isoformat())
            if daily >= self.config.daily_profit_target:
                return EngineEvent(
                    "halt",
                    f"Daily target reached: +{daily:.2f} — bot stopped",
                )
        return None

    def _manage_positions(self, now: datetime) -> list[EngineEvent]:
        """Record closed positions that MT5 has already closed (TP/SL hit)."""
        events: list[EngineEvent] = []
        for position in self.bridge.get_positions():
            tick = self.bridge.get_tick(position.symbol)
            if tick is None:
                continue
            # Only close if price has reached the TP level
            if position.tp > 0:
                current_price = tick.bid if position.order_type == "BUY" else tick.ask
                tp_hit = (
                    (position.order_type == "BUY" and current_price >= position.tp)
                    or (position.order_type == "SELL" and current_price <= position.tp)
                )
                if not tp_hit:
                    continue
            result = self.bridge.close_position(position)
            if not result.success:
                events.append(EngineEvent("error", f"Close failed ticket={position.ticket}: {result.message}"))
                continue
            close_price = tick.bid if position.order_type == "BUY" else tick.ask
            self.database.close_trade(
                position.ticket, close_price, position.profit,
                now.isoformat(timespec="seconds"),
            )
            events.append(EngineEvent(
                "trade",
                f"Closed ticket={position.ticket} profit={position.profit:+.2f}",
            ))
        return events

    def _execute_signal(self, signal, now: datetime, confidence: float) -> list[EngineEvent]:
        symbol = self.config.symbol
        info = self.bridge.get_symbol_info(symbol)
        tick = self.bridge.get_tick(symbol)
        account = self.bridge.get_account_info()
        if info is None or tick is None:
            return [EngineEvent("warning", "Paused: symbol price data unavailable")]
        try:
            constraints = SymbolConstraints(
                info.point, info.volume_min, info.volume_max,
                info.volume_step, info.trade_tick_value, info.trade_tick_size,
            )
            entry = tick.ask if signal.side is SignalSide.BUY else tick.bid
            stop_distance_points = signal.atr * self.config.atr_stop_multiplier / info.point
            if stop_distance_points <= 0:
                return [EngineEvent("warning", "Signal rejected: invalid ATR stop distance")]
            sl = (
                entry - stop_distance_points * info.point
                if signal.side is SignalSide.BUY
                else entry + stop_distance_points * info.point
            )
            tp_distance = stop_distance_points * self.config.reward_to_risk * info.point
            tp = (
                entry + tp_distance
                if signal.side is SignalSide.BUY
                else entry - tp_distance
            )
            if not self.bridge.validate_entry_levels(symbol, entry, sl, tp):
                return [EngineEvent("warning", "Signal rejected: broker stop/freeze level")]
            size = self.risk_engine.size_order(account.balance, stop_distance_points, constraints)
            if not size.allowed:
                return [EngineEvent("warning", f"Signal rejected: {size.reason}")]
            order_type = OrderType.BUY if signal.side is SignalSide.BUY else OrderType.SELL
            margin = self.bridge.estimate_margin(symbol, order_type, size.volume, entry)
            if margin is None:
                return [EngineEvent("warning", "Signal rejected: margin calculation failed")]
            decision = self.risk_engine.evaluate(
                self._risk_snapshot(account, tick, info, now),
                stop_distance_points, constraints, margin,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return [EngineEvent("error", f"Signal rejected: invalid broker data ({exc})")]

        if not decision.allowed:
            return [EngineEvent("warning", f"Signal rejected: {decision.reason}")]

        result = self.bridge.place_market_order(
            symbol, order_type, decision.volume, sl, tp,
            comment=f"MTB {self.strategy.name} {confidence * 100:.0f}%",
        )
        if not result.success:
            return [EngineEvent("error", f"Order failed: {result.message}")]

        self.database.insert_trade(
            result.ticket, symbol, order_type.value, decision.volume,
            entry, sl, tp, 20240101, self.strategy.name,
            signal.reason, now.isoformat(timespec="seconds"),
        )
        return [EngineEvent(
            "trade",
            f"{signal.reason} | ticket={result.ticket} vol={decision.volume:g} conf={confidence * 100:.0f}%",
            confidence,
        )]

    def _risk_snapshot(self, account, tick, info, now: datetime) -> RiskSnapshot:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        peak = max(
            float(self.database.get_setting("risk_peak_equity", account.equity)),
            account.equity,
        )
        self.database.set_setting("risk_peak_equity", peak)
        last = self.database.last_trade_opened_at()
        previous = datetime.fromisoformat(last) if last else None
        if previous and previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        return RiskSnapshot(
            balance=account.balance,
            equity=account.equity,
            free_margin=account.free_margin,
            open_positions=len(self.bridge.get_positions()),
            trades_today=self.database.count_trades_today(day_start.date().isoformat()),
            daily_realized_pnl=self.database.realized_profit_between(
                day_start.isoformat(), now.isoformat()
            ),
            weekly_realized_pnl=self.database.realized_profit_between(
                week_start.isoformat(), now.isoformat()
            ),
            peak_balance=peak,
            spread_points=(tick.ask - tick.bid) / info.point,
            seconds_since_last_trade=(
                (now - previous).total_seconds() if previous else None
            ),
        )

    def _within_trading_hours(self, now: datetime) -> bool:
        start, end, hour = (
            self.config.trading_start_hour_utc,
            self.config.trading_end_hour_utc,
            now.hour,
        )
        return start <= hour < end if start < end else hour >= start or hour < end
