# Phase 2 — Mandatory risk engine and lot sizing

Status: complete and unit-tested.

## Delivered

- Broker-step-normalized fixed or risk-percent lot sizing using tick value and tick size.
- Mandatory stop-loss, open-position, daily-trade, cooldown, spread, daily-loss, drawdown, weekly-loss, and free-margin gates.
- A pure-Python `RiskEngine` used by automated orders and the backtester.
- SQLite query for realised P/L over any closed-trade time window.
- Automated live orders and simulated backtest entries pass through the risk engine; there are no manual BUY/SELL controls in the current UI.

## Files

- `core/risk.py`: risk data models, sizing, and safety decisions.
- `core/database.py`: closed-trade P/L aggregation.
- `tests/test_phase2.py`: risk and database tests.

## Run and test

```bash
pytest tests/test_phase2.py -v
pytest -q
```

Assumption: a trade without a defined stop-loss is unsafe and is rejected before it reaches MT5.
