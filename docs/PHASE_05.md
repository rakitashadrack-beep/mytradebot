# Phase 5 — Conservative historical backtesting

Status: complete and unit-tested.

## Delivered

- A deterministic historical backtester using the same strategy interface and broker volume/tick rules as live trading.
- Entries occur on the bar after a closed-candle signal, avoiding look-ahead bias.
- Spread and per-lot commission are included in the simulation.
- If stop loss and take profit are both crossed in one historical candle, the backtester records the stop loss first; this deliberately avoids overstating performance.
- A Backtest screen loads closed MT5 historical bars, shows equity curve, trade count, final balance, win rate, profit factor, and maximum drawdown.
- Drawdown protection now records peak equity rather than peak balance, so floating losses are included in the live safety limit.

## Files

```text
core/backtester.py     Backtest model, execution simulation, and report
core/trading_engine.py Peak-equity drawdown tracking
ui/main_window.py      Backtest controls and report presentation
tests/test_phase5.py   Backtest bias and report tests
```

## Windows setup and run

Open **PowerShell** in the project folder, then run:

```powershell
cd C:\path\to\mytradebot
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-windows.txt
python main.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` once in that PowerShell window, then activate the environment again.

Use `requirements-windows.txt`, not `requirements.txt`, on the MT5 computer because it installs the official MetaTrader5 package.

## Tests

```powershell
python -m py_compile main.py core\*.py ui\*.py
pytest -q
```

Assumption: a historical candle cannot reveal which intrabar price happened first, so conflicting stop/target candles are treated as losses for a more conservative result.
