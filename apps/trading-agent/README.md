# MyTradeBot Windows trading agent

This is the only component that connects to MetaTrader 5 or handles broker
credentials. Build it on Windows using `scripts/build_windows.ps1`; do not
deploy it to Vercel or Render. Configure the remote API URL privately in:

```text
%LOCALAPPDATA%\MyTradeBot\.env
MYTRADEBOT_LICENSE_API_URL=https://api.yourdomain.com
```

The agent binds to `127.0.0.1` and must not be exposed to the internet.
