# Phase 3 — Strategies and automated trading loop

Status: complete and unit-tested.

## Delivered

- A one-second Qt worker runs outside the UI thread and has explicit START/STOP controls.
- Trend, Momentum, Breakout, and Combined strategies share a common `Strategy` interface.
- Trend uses EMA(10)/EMA(30) crossovers and a higher-timeframe EMA filter; Momentum uses RSI and ADX; Breakout uses a 20-candle Donchian channel and ATR confirmation.
- Candle retrieval deliberately excludes the current, still-forming candle. A signal is keyed by strategy, side, and candle time, so it is processed no more than once.
- Every automated entry has an ATR-derived stop loss and take profit, validates broker stop/freeze distances and trade mode, calculates terminal margin, then passes all Phase 2 risk gates before MT5 receives an order.
- MT5 API calls are serialized with a re-entrant lock because the UI and worker share one terminal connection.

## Files

```text
core/strategies.py       Indicators, signals, and four pluggable strategies
core/trading_engine.py   Closed-candle decision and risk-gated execution
core/trading_worker.py   Non-blocking one-second Qt scheduler
core/mt5_bridge.py       Timeframes, tradeability/level checks, serialized MT5 access
ui/main_window.py        Strategy selector and START/STOP controls
tests/test_phase3.py     Strategy and engine unit tests
```

## Setup and run

```bash
python -m pip install -r requirements.txt
python main.py
```

On Windows, install `requirements-windows.txt`, launch and sign in to the locally installed MT5 terminal, connect in MyTradeBot, choose a symbol and strategy, then select **START BOT**. Use **STOP BOT** before disconnecting or closing the application.

The automated loop is live trading when attached to a live MT5 account; test with a demo account before use.

## Tests

```bash
python -m py_compile main.py core/*.py ui/*.py
pytest tests/test_phase3.py -v
pytest -q
```

Assumption: the initial conservative automated configuration is M15 entries, H1 trend filtering, a 1.5× ATR stop, a 2:1 reward-to-risk target, and UTC trading hours; configurable strategy settings are Phase 4 work.
