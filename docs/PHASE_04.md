# Phase 4 — Full desktop dashboard

Status: complete and unit-tested.

## Delivered

- A modern dark desktop interface using the required navy, green, red, neutral text, rounded-card visual system.
- Dashboard metrics for balance, equity, floating P/L, today’s realised P/L, bot state, positions, connection, and automation controls.
- An MT5 M15 closed-candle chart and a local closed-trade equity curve, both rendered without extra chart dependencies.
- Searchable/filterable local trade history and CSV export.
- Performance metrics: closed trades, net P/L, win rate, profit factor, drawdown, and average win/loss, with an explicit past-performance disclosure.
- Strategy and mandatory-risk settings, input bounds, Conservative/Balanced/Aggressive presets, persisted locally and applied to new orders.
- Live log view with level filtering, risk-disclosure access, and desktop trade notifications where the operating system supports a system tray.

## Files

```text
ui/main_window.py      Tabbed dashboard and user interaction
ui/charts.py           Dependency-free candle and equity chart widgets
core/database.py       Filtered history and local performance calculations
tests/test_phase4.py   History and performance-report unit tests
```

## Setup, run, and test

```bash
python -m pip install -r requirements.txt
python main.py
pytest tests/test_phase4.py -v
pytest -q
```

For live MT5 use, run this on Windows with `requirements-windows.txt`, connect an already logged-in MT5 terminal, and test the full flow with a demo account first.

Manual BUY/SELL dealing is not available in the current UI. Charts and performance reports use locally recorded data; the historical simulation and broker-data backtester are Phase 5.
