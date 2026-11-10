# Phase 1 — MT5 connection and position management

Status: complete and unit-tested.

## Delivered

- MT5 connection/disconnection, account details, symbol list, bot-owned position list, individual close, close all, SQLite trade persistence, rotating logs, and a first-launch risk disclaimer.
- The desktop UI uses the required dark navy, green, and red palette.

## Run and test

```bash
python -m pip install -r requirements.txt
pytest tests/test_phase1.py -v
python main.py
```

On a Windows trading machine install `requirements-windows.txt`, keep MT5 running and logged in, then connect from the app. MT5 credentials are not persisted by the app.

Manual BUY/SELL placement is not available in the current UI. Position closing is limited to bot-owned positions.
