# MyTradeBot User Guide

## Before you start

Use a Windows PC/VPS for MT5 trading and start with an Exness demo account. Confirm that you understand the risk disclosure on first launch; no strategy guarantees a profitable trade.

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

The optional browser UI runs at `http://127.0.0.1:8000` with `python web_main.py`. It has no user authentication and is bound to localhost; do not expose it to a network.

## Subscription

For a production install, set the product owner’s License API URL in **Strategy & Risk → Subscription**. The bot validates on start and revalidates every four hours. It stops starting new automated activity when validation fails after the offline grace period.

## Emergency response

- Incorrect account/symbol: select **STOP BOT**, then disconnect.
- Unexpected open bot position: use its **Close** button after independently checking MT5.
- Large loss or abnormal behavior: use **CLOSE ALL**, stop the bot, preserve logs, and do not restart until investigated.
- Never share your MT5 master password, Exness password, license tokens, recovery codes, or payment keys.
