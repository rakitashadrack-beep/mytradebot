# MyTradeBot User Guide

## Before you start

Use a Windows PC/VPS for MT5 trading and start with an Exness demo account. Confirm that you understand the risk disclosure on first launch; no strategy guarantees a profitable trade.

## Charts

The **Charts** tab contains the TradingView market chart, the selected symbol's MT5 closed-candle chart, and the closed-trade and backtest equity curves. TradingView requires an internet connection. Its displayed prices come from TradingView data providers and may differ from your broker's MT5 feed; displaying a TradingView chart does not connect an account or place trades. Enter a TradingView ticker such as `FX:EURUSD` or `OANDA:XAUUSD` and select **Load TradingView Chart**. Selecting an MT5 forex or gold symbol fills in a common TradingView ticker automatically; verify it or edit it if your instrument uses a different exchange/feed.

## Connect MT5

1. Start MetaTrader 5 Desktop and log in to one demo account.
2. Start MyTradeBot, open **Dashboard**, and select **Connect**.
3. Confirm the displayed account number, broker/server, balance, and symbol before any action.
4. Select your broker’s exact symbol name—suffixes such as `EURUSDm` are normal.

## Manual trading

Manual BUY/SELL placement is not available in the current app. The dashboard can close bot-owned positions; verify any position in MT5 before closing it.

## Automated trading

1. In **Strategy & Risk**, select a strategy and use **Conservative** first.
2. Save settings; choose the same symbol on Dashboard.
3. Select **START BOT**. The bot runs every second but trades only on new, valid, closed-candle signals.
4. Select **STOP BOT** to halt new automated orders. Use **CLOSE ALL** only when you intend to close every bot-owned position.

## Backtesting and review

Use **Backtest** while MT5 is connected. Specify realistic spread and commission, then inspect drawdown, trade count, win rate, and the equity curve. In **History**, filter records and export CSV. Backtest results and demo results are research, not forecasts.

The unified browser UI runs at `http://127.0.0.1:8000` with `python main.py`. Account creation and workspace viewing are open; bot connection and execution require payment-confirmed access or the configured developer account. It is bound to localhost; do not expose it to a network.

## Subscription

For a licensed install, set the product owner’s License API URL in **Strategy & Risk → Subscription** and save. You can open the account portal or sign in and check access from the same section. Automated trading requires a provider-confirmed active paid subscription, except for the configured developer test account; a free trial does not unlock the bot. Any signed-in account can view the browser workspace, but the bot remains locked without entitlement. On first activation, sign in with the MyTradeBot account that owns the entitlement to bind the MT5 account and device. The bot revalidates every five minutes and fails closed if the licensing service is unavailable. The web dashboard includes START/STOP controls and a News page that compares NFP/CPI actuals with consensus forecasts when a Trading Economics API key is configured; it shows bias context, not a guaranteed prediction or automatic news order. Paid checkout is currently disabled, so do not accept payments until payment credentials and callbacks have been tested.

## Emergency response

- Incorrect account/symbol: select **STOP BOT**, then disconnect.
- Unexpected open bot position: use its **Close** button after independently checking MT5.
- Large loss or abnormal behavior: use **CLOSE ALL**, stop the bot, preserve logs, and do not restart until investigated.
- Never share your MT5 master password, Exness password, license tokens, recovery codes, or payment keys.
