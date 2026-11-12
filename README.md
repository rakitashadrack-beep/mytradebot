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

`python main.py` starts the unified browser app and opens `http://127.0.0.1:8000`. Keep the terminal open. The browser app includes account sign-in, plans, MT5 connection, bot controls, history, performance, and settings. The optional native window remains available with `python desktop_main.py`.

Open MT5 Desktop with an Exness **demo** account first, then connect from the MyTradeBot browser page. Broker credentials are sent only to the local Windows app and the password field is cleared after connection. Never place broker credentials in source code, settings, or chat.

The app binds only to this computer. Do not expose it to a network. Phone access through a VPS relay is not implemented yet; the customer's Windows PC must be running to execute MT5 trades. Anyone can create an account without a setup token and view the app workspace. Automated trading requires a confirmed paid subscription; the existing `masonrakitafx@gmail.com` developer account receives free testing access. Daraja checkout is currently disabled because payment credentials and verified callbacks are not configured, so customers cannot currently pay. Keep `PAID_PLANS_ENABLED=false` until the M-Pesa sandbox and callbacks have been tested; only then set it to `true` in the private `.env`.

### Account access and news

Start the app with `python main.py`, choose **Create an account**, and register with your email and a unique password. Registration and app viewing do not require a setup token. The existing developer account receives free access after restarting the app; all other accounts remain view-only until the payment service confirms an active subscription.

The Dashboard includes visible **START BOT** and **STOP BOT** controls. NFP/CPI post-release context is available under **News** when a Trading Economics key is configured. It compares actual results with consensus forecasts and shows a cautious USD bias; it is not a guaranteed prediction, pair-specific order, or automatic news trade. Add your key privately as `TRADING_ECONOMICS_API_KEY` in the ignored `.env` file. See the [Trading Economics calendar API documentation](https://docs.tradingeconomics.com/economic_calendar/snapshot/). Never share API keys, broker passwords, or account passwords.

### Phone installation and security

The browser app has an installable PWA shell. A phone can install it from Chrome (Android) or Add to Home Screen in Safari (iPhone) **only after the app is deployed on a public HTTPS domain**. The current `python main.py` server deliberately binds to this computer only and cannot be reached from a phone. Do not port-forward it, use `0.0.0.0`, or expose it through a public tunnel: the local trading-control service is not designed as an internet-facing MT5 agent. Safe remote trading needs a separately authenticated PC agent and relay, which are not implemented.

After a secure HTTPS deployment exists, open its URL on Android in Chrome and choose **Install app** (or **Add to Home screen**) from the browser menu. On iPhone, open the URL in Safari, tap **Share**, then **Add to Home Screen**. These steps install the PWA; they do not publish it in an app store.

The current build is not published to Google Play and has no signed Android App Bundle. Play Store publication will need a Play Console owner account, a production HTTPS domain, domain/app-link verification, signed packaging, store assets, privacy disclosures, and a production security review. A PWA install is not a Play Store listing.

The private one-time administrator bootstrap token is separate: it is needed only to create the configured administrator account. Keep `mytradebot-licenses.db` private. Do not use a real-money account for initial tests.

Do not use a real-money account for initial tests. Keep M-Pesa checkout disabled until its credentials and sandbox callbacks are configured and independently verified.

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
