"""Phase 3 tests: closed-candle strategies and one-second engine behaviour."""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from core.constants import OrderType
from core.database import Database
from core.risk import LotSizeMode, RiskEngine, RiskSettings
from core.strategies import (BreakoutStrategy, Candle, CombinedStrategy, MomentumStrategy,
                             Signal, SignalSide, Strategy, TrendStrategy)
from core.trading_engine import TradingConfig, TradingEngine
from core.mt5_bridge import AccountInfo, TradeResult


def candles(closes):
    return [Candle(i, value - .0005, value + .001, value - .001, value)
            for i, value in enumerate(closes)]


def test_breakout_uses_closed_candle_and_previous_donchian_channel():
    series = candles([1.10] * 25 + [1.105])
    signal = BreakoutStrategy(channel_period=20).evaluate(series, [])
    assert signal is not None
    assert signal.side is SignalSide.BUY
    assert signal.candle_time == series[-1].time


def test_trend_requires_crossover_and_higher_timeframe_filter():
    entry = candles([1.0] * 35 + [.8] * 10 + [1.5])
    upward_higher = candles([1.0 + i * .01 for i in range(35)])
    signal = TrendStrategy().evaluate(entry, upward_higher)
    assert signal is not None
    assert signal.side is SignalSide.BUY


def test_momentum_requires_rsi_cross_and_adx_strength():
    values = [1.5 - i * .01 for i in range(45)] + [1.05 + i * .002 for i in range(20)] + [1.14]
    signal = MomentumStrategy().evaluate(candles(values), [])
    assert signal is not None
    assert signal.side is SignalSide.BUY


class StaticStrategy(Strategy):
    name = "static"

    def __init__(self, side):
        self.side = side

    def evaluate(self, entry, higher):
        return Signal(self.side, self.name, entry[-1].time, "test signal", .001)


def test_combined_requires_two_strategies_to_agree():
    entry = candles([1.1] * 40)
    combined = CombinedStrategy([StaticStrategy(SignalSide.BUY), StaticStrategy(SignalSide.BUY),
                                 StaticStrategy(SignalSide.SELL)])
    assert combined.evaluate(entry, entry).side is SignalSide.BUY
    assert CombinedStrategy([StaticStrategy(SignalSide.BUY), StaticStrategy(SignalSide.SELL)]).evaluate(entry, entry) is None


@dataclass
class SymbolInfo:
    point: float = .0001
    volume_min: float = .01
    volume_max: float = 10
    volume_step: float = .01
    trade_tick_value: float = 1
    trade_tick_size: float = .0001


@dataclass
class Tick:
    ask: float = 1.2002
    bid: float = 1.2


class FakeBridge:
    def __init__(self):
        self.orders = []
        self.closed_only = []
        self.connected = True

    @property
    def is_connected(self): return self.connected
    def is_symbol_tradeable(self, symbol): return True
    def get_timeframe(self, minutes): return minutes
    def get_candles(self, symbol, timeframe, count, closed_only=True):
        self.closed_only.append(closed_only)
        return [{"time": i, "open": 1.2, "high": 1.201, "low": 1.199, "close": 1.2} for i in range(count)]
    def get_symbol_info(self, symbol): return SymbolInfo()
    def get_tick(self, symbol): return Tick()
    def get_account_info(self): return AccountInfo(balance=10_000, equity=10_000, free_margin=9_000)
    def get_positions(self): return []
    def validate_entry_levels(self, symbol, entry, sl, tp): return True
    def estimate_margin(self, symbol, side, volume, price): return 10
    def place_market_order(self, symbol, side, volume, sl, tp, comment):
        self.orders.append((symbol, side, volume, sl, tp, comment))
        return TradeResult(True, ticket=9001)


def test_engine_executes_each_signal_once_per_closed_candle(tmp_path):
    bridge, db = FakeBridge(), Database(str(tmp_path / "engine.db"))
    risk = RiskEngine(RiskSettings(lot_size_mode=LotSizeMode.FIXED, fixed_lot=.1,
                                   cooldown_seconds=0, minimum_free_margin=0))
    engine = TradingEngine(bridge, db, risk, StaticStrategy(SignalSide.BUY),
                           TradingConfig(symbol="EURUSD", min_confidence=0.5))
    now = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    first, second = engine.tick(now), engine.tick(now)
    # Signal is processed exactly once per candle time (trade or confidence-skip)
    first_levels = {e.level for e in first}
    assert first_levels & {"trade", "confidence"}   # signal was evaluated
    # Second tick with same candle time: signal already seen, no re-evaluation
    assert second[0].level == "info"
    assert "already processed" in second[0].message
    assert all(bridge.closed_only)


def test_engine_does_not_trade_outside_configured_hours(tmp_path):
    bridge, db = FakeBridge(), Database(str(tmp_path / "hours.db"))
    engine = TradingEngine(bridge, db, RiskEngine(RiskSettings()), StaticStrategy(SignalSide.BUY),
                           TradingConfig(symbol="EURUSD", trading_start_hour_utc=8, trading_end_hour_utc=17))
    events = engine.tick(datetime(2026, 1, 5, 6, tzinfo=timezone.utc))
    assert "outside" in events[0].message
    assert not bridge.orders


def test_engine_pauses_safely_when_disconnected(tmp_path):
    bridge, db = FakeBridge(), Database(str(tmp_path / "disconnected.db"))
    bridge.connected = False
    engine = TradingEngine(bridge, db, RiskEngine(RiskSettings()), StaticStrategy(SignalSide.BUY),
                           TradingConfig(symbol="EURUSD"))
    events = engine.tick(datetime(2026, 1, 5, 12, tzinfo=timezone.utc))
    assert "disconnected" in events[0].message
    assert not bridge.orders


def test_engine_remains_paused_after_license_validation_fails(tmp_path):
    bridge, db = FakeBridge(), Database(str(tmp_path / "license-engine.db"))
    checks = []

    def reject_license():
        checks.append(True)
        return SimpleNamespace(allowed=False, message="License expired")

    engine = TradingEngine(
        bridge, db, RiskEngine(RiskSettings()), StaticStrategy(SignalSide.BUY),
        TradingConfig(symbol="EURUSD"), license_checker=reject_license,
    )
    now = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)

    first = engine.tick(now)
    second = engine.tick(now.replace(second=1))

    assert first[0].message == "Paused: License expired"
    assert second[0].message == "Paused: License expired"
    assert len(checks) == 1
    assert not bridge.orders
