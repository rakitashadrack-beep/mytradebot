"""
MyTradeBot – Phase 1
SQLite persistence: trades, settings, daily stats.
"""
from __future__ import annotations
import sqlite3
import json
from contextlib import contextmanager
from core.constants import DB_PATH
from core.logger import get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket      INTEGER UNIQUE,
    symbol      TEXT    NOT NULL,
    order_type  TEXT    NOT NULL,
    volume      REAL    NOT NULL,
    open_price  REAL    NOT NULL,
    close_price REAL    DEFAULT 0,
    sl          REAL    DEFAULT 0,
    tp          REAL    DEFAULT 0,
    profit      REAL    DEFAULT 0,
    magic       INTEGER,
    comment     TEXT,
    reason      TEXT,
    opened_at   TEXT    NOT NULL,
    closed_at   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date        TEXT PRIMARY KEY,
    trades      INTEGER DEFAULT 0,
    gross_profit REAL   DEFAULT 0,
    gross_loss   REAL   DEFAULT 0,
    starting_balance REAL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self._path = path
        self._init()

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        log.debug("Database ready at %s", self._path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Trades ────────────────────────────────────────────────────────────────

    def insert_trade(self, ticket: int, symbol: str, order_type: str,
                     volume: float, open_price: float, sl: float, tp: float,
                     magic: int, comment: str, reason: str, opened_at: str) -> None:
        sql = """INSERT OR IGNORE INTO trades
                 (ticket,symbol,order_type,volume,open_price,sl,tp,magic,comment,reason,opened_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
        with self._conn() as conn:
            conn.execute(sql, (ticket, symbol, order_type, volume,
                               open_price, sl, tp, magic, comment, reason, opened_at))

    def close_trade(self, ticket: int, close_price: float,
                    profit: float, closed_at: str) -> None:
        sql = """UPDATE trades SET close_price=?, profit=?, closed_at=?
                 WHERE ticket=?"""
        with self._conn() as conn:
            conn.execute(sql, (close_price, profit, closed_at, ticket))

    def get_trades(self, limit: int = 500) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_filtered_trades(self, symbol: str = "", order_type: str = "",
                            limit: int = 1_000) -> list[dict]:
        """Return recent local bot trades using parameterised, user-facing filters."""
        clauses, values = [], []
        if symbol.strip():
            clauses.append("symbol = ?")
            values.append(symbol.strip())
        if order_type in {"BUY", "SELL"}:
            clauses.append("order_type = ?")
            values.append(order_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 10_000)))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM trades{where} ORDER BY opened_at DESC LIMIT ?", values
            ).fetchall()
        return [dict(row) for row in rows]

    def performance_summary(self) -> dict:
        """Calculate local closed-trade performance without claiming future results."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT profit, closed_at FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at"
            ).fetchall()
        profits = [float(row["profit"]) for row in rows]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        running, peak, max_drawdown = 0.0, 0.0, 0.0
        curve = []
        for row, profit in zip(rows, profits):
            running += profit
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)
            curve.append((str(row["closed_at"]), running))
        return {
            "closed_trades": len(profits),
            "net_profit": running,
            "win_rate": (len(wins) / len(profits) * 100) if profits else 0.0,
            "profit_factor": (gross_profit / gross_loss) if gross_loss else (0.0 if not gross_profit else None),
            "max_drawdown": max_drawdown,
            "average_win": (gross_profit / len(wins)) if wins else 0.0,
            "average_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "equity_curve": curve,
        }

    def count_trades_today(self, date_str: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE opened_at LIKE ?",
                (f"{date_str}%",)
            ).fetchone()
        return row[0] if row else 0

    def realized_profit_between(self, start_iso: str, end_iso: str) -> float:
        """Return realised P/L for closed bot trades in an inclusive time window."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(profit), 0) FROM trades
                   WHERE closed_at IS NOT NULL AND closed_at >= ? AND closed_at < ?""",
                (start_iso, end_iso),
            ).fetchone()
        return float(row[0])

    def last_trade_opened_at(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT opened_at FROM trades ORDER BY opened_at DESC LIMIT 1"
            ).fetchone()
        return str(row[0]) if row else None

    # ── Settings ──────────────────────────────────────────────────────────────

    def set_setting(self, key: str, value) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (key, json.dumps(value))
            )

    def get_setting(self, key: str, default=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return default
        return json.loads(row[0])

    def get_all_settings(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    # ── Daily stats ───────────────────────────────────────────────────────────

    def upsert_daily_stats(self, date_str: str, trades: int,
                           gross_profit: float, gross_loss: float,
                           starting_balance: float) -> None:
        sql = """INSERT INTO daily_stats (date,trades,gross_profit,gross_loss,starting_balance)
                 VALUES (?,?,?,?,?)
                 ON CONFLICT(date) DO UPDATE SET
                   trades=excluded.trades,
                   gross_profit=excluded.gross_profit,
                   gross_loss=excluded.gross_loss"""
        with self._conn() as conn:
            conn.execute(sql, (date_str, trades, gross_profit,
                               gross_loss, starting_balance))

    def get_daily_stats(self, date_str: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_stats WHERE date=?", (date_str,)
            ).fetchone()
        return dict(row) if row else None
