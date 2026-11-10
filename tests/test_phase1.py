"""
MyTradeBot – Phase 1 tests
Run: pytest tests/test_phase1.py -v
"""
import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch

from core.database import Database
from core.constants import OrderType, BotState, MAGIC_NUMBER


# ── Database tests ────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_db_settings_roundtrip(db):
    db.set_setting("key1", {"a": 1, "b": [1, 2, 3]})
    assert db.get_setting("key1") == {"a": 1, "b": [1, 2, 3]}


def test_db_settings_default(db):
    assert db.get_setting("missing", default=42) == 42


def test_db_insert_and_get_trade(db):
    db.insert_trade(
        ticket=100001, symbol="EURUSD", order_type="BUY",
        volume=0.1, open_price=1.08500, sl=1.08000, tp=1.09000,
        magic=MAGIC_NUMBER, comment="test", reason="unit test",
        opened_at="2024-01-01T10:00:00"
    )
    trades = db.get_trades()
    assert len(trades) == 1
    assert trades[0]["ticket"] == 100001
    assert trades[0]["symbol"] == "EURUSD"


def test_db_close_trade(db):
    db.insert_trade(
        ticket=100002, symbol="XAUUSD", order_type="SELL",
        volume=0.05, open_price=2000.0, sl=2010.0, tp=1990.0,
        magic=MAGIC_NUMBER, comment="", reason="test",
        opened_at="2024-01-01T11:00:00"
    )
    db.close_trade(100002, close_price=1995.0, profit=25.0,
                   closed_at="2024-01-01T11:30:00")
    trades = db.get_trades()
    assert trades[0]["close_price"] == 1995.0
    assert trades[0]["profit"] == 25.0


def test_db_count_trades_today(db):
    db.insert_trade(100003, "GBPUSD", "BUY", 0.1, 1.27, 0, 0,
                    MAGIC_NUMBER, "", "", "2024-06-15T09:00:00")
    db.insert_trade(100004, "GBPUSD", "SELL", 0.1, 1.27, 0, 0,
                    MAGIC_NUMBER, "", "", "2024-06-15T10:00:00")
    db.insert_trade(100005, "GBPUSD", "BUY", 0.1, 1.27, 0, 0,
                    MAGIC_NUMBER, "", "", "2024-06-16T09:00:00")
    assert db.count_trades_today("2024-06-15") == 2
    assert db.count_trades_today("2024-06-16") == 1


def test_db_daily_stats(db):
    db.upsert_daily_stats("2024-06-15", trades=5,
                          gross_profit=120.0, gross_loss=-40.0,
                          starting_balance=10000.0)
    stats = db.get_daily_stats("2024-06-15")
    assert stats["trades"] == 5
    assert stats["gross_profit"] == 120.0

    # upsert update
    db.upsert_daily_stats("2024-06-15", trades=6,
                          gross_profit=150.0, gross_loss=-40.0,
                          starting_balance=10000.0)
    stats = db.get_daily_stats("2024-06-15")
    assert stats["trades"] == 6


# ── MT5Bridge stub tests ──────────────────────────────────────────────────────

def test_bridge_not_connected_without_mt5():
    """On Linux/macOS where MT5 is unavailable, bridge must report disconnected."""
    from core.mt5_bridge import MT5Bridge, MT5_AVAILABLE
    bridge = MT5Bridge()
    assert not bridge.is_connected


def test_bridge_connect_fails_gracefully():
    from core.mt5_bridge import MT5Bridge
    bridge = MT5Bridge()
    result = bridge.connect()
    assert result is False


def test_bridge_get_positions_returns_empty_when_disconnected():
    from core.mt5_bridge import MT5Bridge
    bridge = MT5Bridge()
    assert bridge.get_positions() == []


def test_bridge_get_symbols_returns_empty_when_disconnected():
    from core.mt5_bridge import MT5Bridge
    bridge = MT5Bridge()
    assert bridge.get_symbols() == []


def test_bridge_place_order_fails_when_disconnected():
    from core.mt5_bridge import MT5Bridge, TradeResult
    bridge = MT5Bridge()
    result = bridge.place_market_order("EURUSD", OrderType.BUY, 0.1)
    assert isinstance(result, TradeResult)
    assert result.success is False
    assert "Not connected" in result.message


def test_bridge_close_all_returns_empty_when_disconnected():
    from core.mt5_bridge import MT5Bridge
    bridge = MT5Bridge()
    assert bridge.close_all_positions() == []


# ── Constants / enums ─────────────────────────────────────────────────────────

def test_order_type_values():
    assert OrderType.BUY.value  == "BUY"
    assert OrderType.SELL.value == "SELL"


def test_bot_state_values():
    assert BotState.STOPPED.value == "STOPPED"
    assert BotState.RUNNING.value == "RUNNING"


def test_magic_number_is_int():
    assert isinstance(MAGIC_NUMBER, int)
    assert MAGIC_NUMBER > 0
