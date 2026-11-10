"""Phase 5 tests for conservative closed-candle backtesting."""
from core.backtester import BacktestConfig, Backtester
from core.risk import LotSizeMode, RiskEngine, RiskSettings, SymbolConstraints
from core.strategies import Candle, Signal, SignalSide, Strategy


class AlwaysBuy(Strategy):
    name = "test"

    def evaluate(self, candles, higher):
        return Signal(SignalSide.BUY, self.name, candles[-1].time, "test", .001)


CONSTRAINTS = SymbolConstraints(.0001, .01, 10, .01, 1, .0001)


def bars():
    result = [Candle(index, 1.2, 1.201, 1.199, 1.2) for index in range(61)]
    # Signal is evaluated on bar 60, filled at bar 61 open. This candle touches
    # both levels; conservative simulation must report the stop loss.
    result.append(Candle(61, 1.3, 1.304, 1.298, 1.3))
    result.append(Candle(62, 1.3, 1.301, 1.299, 1.3))
    return result


def test_backtester_fills_next_bar_and_uses_worst_case_intrabar_exit():
    risk = RiskEngine(RiskSettings(lot_size_mode=LotSizeMode.FIXED, fixed_lot=.1))
    report = Backtester(AlwaysBuy(), risk, CONSTRAINTS, BacktestConfig(spread_points=0)).run(bars())
    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.entry_time == 61
    assert trade.exit_reason == "stop loss"
    assert trade.profit < 0


def test_backtest_report_contains_performance_metrics():
    risk = RiskEngine(RiskSettings(lot_size_mode=LotSizeMode.FIXED, fixed_lot=.1))
    report = Backtester(AlwaysBuy(), risk, CONSTRAINTS, BacktestConfig(initial_balance=1_000)).run(bars())
    assert report.initial_balance == 1_000
    assert report.final_balance < 1_000
    assert report.max_drawdown > 0
    assert report.win_rate == 0
    assert len(report.equity_curve) == 2
