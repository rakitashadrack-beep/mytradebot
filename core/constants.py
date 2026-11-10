"""
MyTradeBot – Phase 1
Shared constants and enums used across the entire application.
"""
from enum import Enum

APP_NAME    = "MyTradeBot"
APP_VERSION = "1.0.0"
MAGIC_NUMBER   = 20240101   # tags every bot-owned order
DEFAULT_DEVIATION = 20      # max slippage in points
LOG_DIR  = "logs"
DB_PATH  = "mytradebot.db"

RISK_DISCLAIMER = (
    "RISK DISCLAIMER\n\n"
    "Trading foreign exchange, CFDs, and other leveraged products carries a HIGH level "
    "of risk and may NOT be suitable for all investors. You may lose more than your "
    "initial deposit. Past performance is NOT indicative of future results. "
    "MyTradeBot does NOT guarantee profits. Trade only with money you can afford to lose.\n\n"
    "By clicking 'I Understand & Accept' you confirm that you have read, understood, "
    "and accepted this disclaimer in full."
)


class OrderType(Enum):
    BUY  = "BUY"
    SELL = "SELL"


class BotState(Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED  = "PAUSED"
    ERROR   = "ERROR"
