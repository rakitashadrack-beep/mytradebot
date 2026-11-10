# MyTradeBot

MyTradeBot is a Windows desktop trading application for a trader’s own locally-running MetaTrader 5 terminal. It provides closed-candle automated strategies, non-optional risk limits, local backtesting, and an optional subscription licensing service.

> Trading CFDs and leveraged instruments can result in rapid, substantial losses. MyTradeBot does not guarantee profit, identify “correct” trades, or replace your judgment.

## What is included

- MT5 account connection, automated bot controls, bot-owned position controls, SQLite trade history, and structured logs.
- Fixed-lot or risk-percent position sizing; mandatory stop loss; spread, margin, trade-count, cooldown, loss, and drawdown gates.
- Trend, Momentum, Breakout, and Combined closed-candle strategies running once per second outside the UI thread.
- Dashboard, local history/CSV export, charts, performance reporting, and conservative backtesting.
- FastAPI licensing API with device/account binding, Vercel deployment assets, and payment-provider boundaries.
- Windows build script and Inno Setup installer source.

## Development on Linux

```bash
cd /path/to/mytradebot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pytest -q tests tests_backend
```

Linux is suitable for source development, tests, and offline backtesting. The official MT5 Python integration and live execution target are Windows.

## Windows demo setup

```powershell
cd C:\path\to\mytradebot
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-windows.txt
pytest -q tests tests_backend
python main.py
```

Open and log into MT5 Desktop with an Exness **demo** account first; then start MyTradeBot and select **Connect**. Never place broker credentials in source code, settings, or chat.

The optional browser UI runs locally at `http://127.0.0.1:8000` with `python web_main.py`. It has no user authentication and is deliberately bound to localhost; do not expose it to a network.

## Deployment and release

- Licensing API/Vercel: [Phase 6](docs/PHASE_06.md)
- Installer and release: [Phase 7](docs/PHASE_07.md)
- End-to-end deployment: [Production deployment runbook](docs/DEPLOYMENT.md)
- Daily operation: [User guide](docs/USER_GUIDE.md)
- Legal starting template: [Terms and risk disclosure](docs/TERMS_AND_RISK_DISCLOSURE_TEMPLATE.md)
- Launch gate: [Pre-launch checklist](docs/PRE_LAUNCH_CHECKLIST.md)

## Project layout

```text
core/        Trading, risk, licensing, MT5, and backtest services
ui/          PySide6 desktop screens and chart widgets
backend/     FastAPI licensing and payment integration boundary
api/         Vercel FastAPI entrypoint
installer/   Inno Setup source
scripts/     Windows build script
tests*/      Unit tests
docs/        Phase notes and operational/legal documentation
```

Assumption: all production secrets, payment accounts, code-signing certificates, DNS, and managed PostgreSQL are owned and configured by the product owner.
