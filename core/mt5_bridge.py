"""
MyTradeBot – Phase 1
MT5 connection manager: connect, disconnect, account info, symbol info.
Assumption: MetaTrader5 package is Windows-only; on Linux it raises ImportError
which we catch so the rest of the app (UI, tests) can still import this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import wraps
from threading import RLock

from core.logger import get_logger
from core.constants import MAGIC_NUMBER, DEFAULT_DEVIATION, OrderType

log = get_logger("mt5_bridge")


def synchronized(method):
    """Serialize terminal calls; MetaTrader5 is a process-global native API."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._api_lock:
            return method(self, *args, **kwargs)
    return wrapped

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None          # type: ignore
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 package not available on this platform. Running in stub mode.")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AccountInfo:
    login:       int   = 0
    name:        str   = ""
    server:      str   = ""
    broker:      str   = ""
    currency:    str   = "USD"
    balance:     float = 0.0
    equity:      float = 0.0
    margin:      float = 0.0
    free_margin: float = 0.0
    leverage:    int   = 0
    is_hedging:  bool  = False


@dataclass
class PositionInfo:
    ticket:     int
    symbol:     str
    order_type: str    # "BUY" or "SELL"
    volume:     float
    open_price: float
    sl:         float
    tp:         float
    profit:     float
    magic:      int
    comment:    str = ""


@dataclass
class TradeResult:
    success: bool
    ticket:  int  = 0
    retcode: int  = 0
    message: str  = ""


# ── MT5 Bridge ────────────────────────────────────────────────────────────────

class MT5Bridge:
    """Thin wrapper around the MetaTrader5 Python package."""

    def __init__(self) -> None:
        self._connected = False
        self._api_lock = RLock()

    # ── Connection ────────────────────────────────────────────────────────────

    @synchronized
    def connect(self, path: str = "", login: int = 0,
                password: str = "", server: str = "") -> bool:
        if not MT5_AVAILABLE:
            log.error("MetaTrader5 package not installed.")
            return False

        kwargs: dict = {}
        if path:     kwargs["path"]     = path
        if login:    kwargs["login"]    = login
        if password: kwargs["password"] = password
        if server:   kwargs["server"]   = server

        if not mt5.initialize(**kwargs):
            log.error("MT5 initialize failed: %s", mt5.last_error())
            return False

        self._connected = True
        info = self.get_account_info()
        log.info("Connected | login=%s server=%s broker=%s balance=%.2f %s",
                 info.login, info.server, info.broker, info.balance, info.currency)
        return True

    @synchronized
    def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False
            log.info("MT5 disconnected.")

    @property
    @synchronized
    def is_connected(self) -> bool:
        if not MT5_AVAILABLE or not self._connected:
            return False
        return mt5.terminal_info() is not None

    # ── Account ───────────────────────────────────────────────────────────────

    @synchronized
    def get_account_info(self) -> AccountInfo:
        if not self.is_connected:
            return AccountInfo()
        a = mt5.account_info()
        if a is None:
            return AccountInfo()
        return AccountInfo(
            login       = a.login,
            name        = a.name,
            server      = a.server,
            broker      = a.company,
            currency    = a.currency,
            balance     = a.balance,
            equity      = a.equity,
            margin      = a.margin,
            free_margin = a.margin_free,
            leverage    = a.leverage,
            is_hedging  = (a.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING),
        )

    # ── Symbols ───────────────────────────────────────────────────────────────

    @synchronized
    def get_symbols(self) -> list[str]:
        if not self.is_connected:
            return []
        syms = mt5.symbols_get()
        return [s.name for s in syms] if syms else []

    @synchronized
    def get_symbol_info(self, symbol: str):
        if not self.is_connected:
            return None
        mt5.symbol_select(symbol, True)
        return mt5.symbol_info(symbol)

    @synchronized
    def get_tick(self, symbol: str):
        if not self.is_connected:
            return None
        return mt5.symbol_info_tick(symbol)

    @synchronized
    def estimate_margin(self, symbol: str, order_type: OrderType,
                        volume: float, price: float) -> float | None:
        """Ask the terminal for required margin; never guess broker leverage rules."""
        if not self.is_connected:
            return None
        mt5_type = mt5.ORDER_TYPE_BUY if order_type == OrderType.BUY else mt5.ORDER_TYPE_SELL
        margin = mt5.order_calc_margin(mt5_type, symbol, volume, price)
        return float(margin) if margin is not None else None

    @synchronized
    def get_timeframe(self, minutes: int) -> int | None:
        """Map supported minute intervals to the terminal's timeframe constants."""
        if not MT5_AVAILABLE:
            return None
        timeframes = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5,
                      15: mt5.TIMEFRAME_M15, 30: mt5.TIMEFRAME_M30,
                      60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
                      1440: mt5.TIMEFRAME_D1}
        return timeframes.get(minutes)

    @synchronized
    def get_candles(self, symbol: str, timeframe: int, count: int = 500,
                    closed_only: bool = True):
        """Fetch candles, excluding the still-forming candle by default."""
        if not self.is_connected:
            return None
        return mt5.copy_rates_from_pos(symbol, timeframe, 1 if closed_only else 0, count)

    @synchronized
    def is_symbol_tradeable(self, symbol: str) -> bool:
        info = self.get_symbol_info(symbol)
        return bool(info and info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL)

    @synchronized
    def validate_entry_levels(self, symbol: str, entry_price: float,
                              sl: float, tp: float) -> bool:
        """Ensure initial SL/TP respect the broker stop and freeze distances."""
        info = self.get_symbol_info(symbol)
        if info is None or info.point <= 0:
            return False
        minimum = max(info.trade_stops_level, info.trade_freeze_level) * info.point
        return (sl > 0 and abs(entry_price - sl) >= minimum and
                (tp == 0 or abs(entry_price - tp) >= minimum))

    # ── Open positions ────────────────────────────────────────────────────────

    @synchronized
    def get_positions(self, magic: int = MAGIC_NUMBER) -> list[PositionInfo]:
        if not self.is_connected:
            return []
        raw = mt5.positions_get()
        if raw is None:
            return []
        result = []
        for p in raw:
            if magic and p.magic != magic:
                continue
            result.append(PositionInfo(
                ticket     = p.ticket,
                symbol     = p.symbol,
                order_type = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                volume     = p.volume,
                open_price = p.price_open,
                sl         = p.sl,
                tp         = p.tp,
                profit     = p.profit,
                magic      = p.magic,
                comment    = p.comment,
            ))
        return result

    # ── Order execution ───────────────────────────────────────────────────────

    @synchronized
    def place_market_order(
        self,
        symbol:     str,
        order_type: OrderType,
        volume:     float,
        sl:         float = 0.0,
        tp:         float = 0.0,
        comment:    str   = "MyTradeBot",
        deviation:  int   = DEFAULT_DEVIATION,
    ) -> TradeResult:
        if not self.is_connected:
            return TradeResult(False, message="Not connected to MT5")

        sym_info = self.get_symbol_info(symbol)
        if sym_info is None:
            return TradeResult(False, message=f"Symbol {symbol} not found")

        tick = self.get_tick(symbol)
        if tick is None:
            return TradeResult(False, message="Could not get tick data")

        filling  = self._get_filling_mode(sym_info)
        mt5_type = mt5.ORDER_TYPE_BUY  if order_type == OrderType.BUY else mt5.ORDER_TYPE_SELL
        price    = tick.ask            if order_type == OrderType.BUY else tick.bid

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       float(volume),
            "type":         mt5_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    deviation,
            "magic":        MAGIC_NUMBER,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        log.info("Placing %s %s vol=%.2f price=%.5f sl=%.5f tp=%.5f",
                 order_type.value, symbol, volume, price, sl, tp)
        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            msg = result.comment if result else mt5.last_error()
            log.error("Order failed: retcode=%s msg=%s",
                      getattr(result, "retcode", "?"), msg)
            return TradeResult(False, retcode=getattr(result, "retcode", -1), message=str(msg))

        log.info("Order filled: ticket=%s", result.order)
        return TradeResult(True, ticket=result.order,
                           retcode=result.retcode, message="OK")

    @synchronized
    def close_position(self, position: PositionInfo,
                       deviation: int = DEFAULT_DEVIATION) -> TradeResult:
        if not self.is_connected:
            return TradeResult(False, message="Not connected")

        sym_info = self.get_symbol_info(position.symbol)
        tick     = self.get_tick(position.symbol)
        if sym_info is None or tick is None:
            return TradeResult(False, message="Symbol/tick unavailable")

        filling    = self._get_filling_mode(sym_info)
        close_type = mt5.ORDER_TYPE_SELL if position.order_type == "BUY" else mt5.ORDER_TYPE_BUY
        price      = tick.bid            if position.order_type == "BUY" else tick.ask

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       position.symbol,
            "volume":       position.volume,
            "type":         close_type,
            "position":     position.ticket,
            "price":        price,
            "deviation":    deviation,
            "magic":        MAGIC_NUMBER,
            "comment":      "MyTradeBot close",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        log.info("Closing ticket=%s %s %s vol=%.2f",
                 position.ticket, position.order_type, position.symbol, position.volume)
        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            msg = result.comment if result else mt5.last_error()
            log.error("Close failed: retcode=%s msg=%s",
                      getattr(result, "retcode", "?"), msg)
            return TradeResult(False, retcode=getattr(result, "retcode", -1), message=str(msg))

        log.info("Position closed: ticket=%s", position.ticket)
        return TradeResult(True, ticket=position.ticket,
                           retcode=result.retcode, message="OK")

    @synchronized
    def close_all_positions(self) -> list[TradeResult]:
        """Close every bot-owned open position."""
        return [self.close_position(pos) for pos in self.get_positions()]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_filling_mode(self, sym_info) -> int:
        """Pick the first filling mode the symbol supports (FOK -> IOC -> Return)."""
        if sym_info.filling_mode & mt5.SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        if sym_info.filling_mode & mt5.SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN
