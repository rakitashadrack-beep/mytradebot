"""Unit tests for Phase 2 mandatory risk management."""
from core.risk import (
    LotSizeMode, RiskEngine, RiskSettings, RiskSnapshot, SymbolConstraints,
    calculate_risk_volume, normalize_volume,
)
from core.database import Database
import pytest

FX = SymbolConstraints(
    point=0.00001, volume_min=0.01, volume_max=100.0, volume_step=0.01,
    tick_value=1.0, tick_size=0.00001,
)


def snapshot(**changes):
    values = dict(
        balance=10_000.0, equity=10_000.0, free_margin=9_000.0,
        open_positions=0, trades_today=0, daily_realized_pnl=0.0,
        weekly_realized_pnl=0.0, peak_balance=10_000.0, spread_points=10.0,
        seconds_since_last_trade=None,
    )
    values.update(changes)
    return RiskSnapshot(**values)


def test_normalize_volume_rounds_down_to_broker_step():
    assert normalize_volume(0.037, FX) == 0.03
    assert normalize_volume(500, FX) == 100.0
    assert normalize_volume(0.009, FX) == 0.0


def test_risk_percentage_lot_calculation_uses_tick_value():
    assert calculate_risk_volume(10_000, 1, 100, FX) == 1.0


def test_risk_percentage_rejects_volume_below_broker_minimum():
    assert calculate_risk_volume(100, 0.5, 1_000, FX) == 0.0


def test_fixed_lot_is_normalized():
    engine = RiskEngine(RiskSettings(lot_size_mode=LotSizeMode.FIXED, fixed_lot=0.057))
    result = engine.evaluate(snapshot(), 100, FX, estimated_margin=50)
    assert result.allowed and result.volume == 0.05


def test_stop_loss_is_required_for_every_order():
    result = RiskEngine(RiskSettings()).evaluate(snapshot(), 0, FX, estimated_margin=50)
    assert not result.allowed
    assert "stop loss" in result.reason.lower()


def test_risk_limits_block_orders():
    engine = RiskEngine(RiskSettings(max_open_positions=1, max_trades_per_day=2))
    assert "open positions" in engine.evaluate(snapshot(open_positions=1), 100, FX, 50).reason
    assert "daily trades" in engine.evaluate(snapshot(trades_today=2), 100, FX, 50).reason
    assert "Spread" in engine.evaluate(snapshot(spread_points=31), 100, FX, 50).reason
    assert "Daily loss" in engine.evaluate(snapshot(daily_realized_pnl=-200), 100, FX, 50).reason
    drawdown_engine = RiskEngine(RiskSettings(max_daily_loss_percent=20))
    assert "drawdown" in drawdown_engine.evaluate(snapshot(equity=9_200), 100, FX, 50).reason
    assert "Weekly loss" in engine.evaluate(snapshot(weekly_realized_pnl=-500), 100, FX, 50).reason


def test_cooldown_and_margin_block_orders():
    engine = RiskEngine(RiskSettings(cooldown_seconds=900, minimum_free_margin=100))
    assert "cooldown" in engine.evaluate(snapshot(seconds_since_last_trade=899), 100, FX, 50).reason.lower()
    assert "margin" in engine.evaluate(snapshot(free_margin=120), 100, FX, 50).reason.lower()


def test_database_realized_profit_window(tmp_path):
    db = Database(str(tmp_path / "risk.db"))
    db.insert_trade(1, "EURUSD", "BUY", .01, 1, 0, 0, 1, "", "", "2026-01-01T10:00:00")
    db.close_trade(1, 1.1, 12.5, "2026-01-02T10:00:00")
    assert db.realized_profit_between("2026-01-02T00:00:00", "2026-01-03T00:00:00") == 12.5
