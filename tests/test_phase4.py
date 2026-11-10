"""Phase 4 tests for local history filtering and performance reporting."""
from core.database import Database


def add_closed_trade(db, ticket, symbol, kind, profit, closed_at):
    db.insert_trade(ticket, symbol, kind, .1, 1.1, 1.0, 1.2, 1, "", "", "2026-01-01T10:00:00")
    db.close_trade(ticket, 1.15, profit, closed_at)


def test_history_filters_use_symbol_and_order_type(tmp_path):
    db = Database(str(tmp_path / "history.db"))
    add_closed_trade(db, 1, "EURUSD", "BUY", 10, "2026-01-01T11:00:00")
    add_closed_trade(db, 2, "EURUSD", "SELL", -5, "2026-01-01T12:00:00")
    add_closed_trade(db, 3, "XAUUSD", "BUY", 20, "2026-01-01T13:00:00")
    assert [trade["ticket"] for trade in db.get_filtered_trades("EURUSD", "BUY")] == [1]
    assert len(db.get_filtered_trades("EURUSD")) == 2


def test_performance_summary_is_calculated_from_closed_trades(tmp_path):
    db = Database(str(tmp_path / "performance.db"))
    add_closed_trade(db, 1, "EURUSD", "BUY", 100, "2026-01-01T11:00:00")
    add_closed_trade(db, 2, "EURUSD", "SELL", -40, "2026-01-01T12:00:00")
    summary = db.performance_summary()
    assert summary["closed_trades"] == 2
    assert summary["net_profit"] == 60
    assert summary["win_rate"] == 50
    assert summary["profit_factor"] == 2.5
    assert summary["max_drawdown"] == 40
    assert len(summary["equity_curve"]) == 2
