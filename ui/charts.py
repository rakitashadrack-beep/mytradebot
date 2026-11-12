"""Charts and confidence widgets for MyTradeBot."""
from __future__ import annotations
import math
import json
import re
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QLinearGradient
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.palette import C_CARD, C_BORDER, C_WHITE, C_MUTED, C_AMBER, C_VIOLET, C_DANGER


_FOREX_CURRENCIES = {
    "AUD", "CAD", "CHF", "CNH", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "ILS", "JPY", "MXN", "NOK", "NZD", "PLN", "SEK",
    "SGD", "THB", "TRY", "USD", "ZAR",
}


def tradingview_symbol_for_mt5(symbol: str) -> str:
    """Map common MT5 forex symbols to TradingView symbols; preserve other tickers."""
    ticker = symbol.strip().upper()
    if ticker.startswith(("XAUUSD", "XAGUSD")):
        return f"OANDA:{ticker[:6]}"
    pair = ticker[:6]
    if len(pair) == 6 and pair[:3] in _FOREX_CURRENCIES and pair[3:] in _FOREX_CURRENCIES:
        return f"FX:{pair}"
    return ticker


class TradingViewChart(QWidget):
    """TradingView's hosted interactive market chart."""

    _INTERVALS = {"1", "5", "15", "30", "60", "240", "D", "W", "M"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setMinimumHeight(500)
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        layout.addWidget(self.view)

    def load_chart(self, symbol: str, interval: str) -> None:
        symbol = symbol.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,80}", symbol):
            raise ValueError("Enter a TradingView symbol such as FX:EURUSD or OANDA:XAUUSD.")
        if interval not in self._INTERVALS:
            raise ValueError(f"Unsupported TradingView interval: {interval}")

        options = json.dumps({
            "autosize": True,
            "allow_symbol_change": True,
            "calendar": False,
            "details": False,
            "hide_side_toolbar": False,
            "hide_top_toolbar": False,
            "hide_legend": False,
            "hide_volume": True,
            "interval": interval,
            "locale": "en",
            "save_image": False,
            "style": "1",
            "symbol": symbol,
            "theme": "dark",
            "timezone": "Etc/UTC",
            "withdateranges": True,
        }).replace("<", "\\u003c")
        html = f"""<!doctype html>
<html>
<head><meta charset="utf-8">
<style>
html, body, .tradingview-widget-container {{ width:100%; height:100%; margin:0; }}
.tradingview-widget-container__widget {{ width:100%; height:calc(100% - 32px); }}
.tradingview-widget-copyright {{ height:32px; font:12px Arial,sans-serif; text-align:center; }}
</style></head>
<body>
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <div class="tradingview-widget-copyright">
    <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">Charts by TradingView</a>
  </div>
  <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
  {options}
  </script>
</div>
</body>
</html>"""
        self.view.setHtml(html, QUrl("https://www.tradingview.com/"))


class ConfidenceMeter(QWidget):
    """Arc gauge showing signal confidence 0–100%."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self.setMinimumSize(160, 100)
        self.setMaximumHeight(120)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(C_CARD))

        cx, cy = self.width() / 2, self.height() * 0.72
        r = min(self.width(), self.height() * 1.4) * 0.38
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)

        # Background arc
        pen = QPen(QColor(C_BORDER), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 210 * 16, -240 * 16)

        # Value arc
        pct = self._value
        color = QColor(C_AMBER) if pct >= 0.8 else (QColor(C_VIOLET) if pct >= 0.5 else QColor(C_DANGER))
        pen2 = QPen(color, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        span = int(-240 * 16 * pct)
        p.drawArc(rect, 210 * 16, span)

        # Text
        p.setPen(QPen(QColor(C_WHITE)))
        font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(
            QRectF(cx - r, cy - r * 0.6, r * 2, r),
            Qt.AlignmentFlag.AlignCenter,
            f"{pct * 100:.0f}%",
        )
        p.setPen(QPen(QColor(C_MUTED)))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(
            QRectF(cx - r, cy + r * 0.35, r * 2, 20),
            Qt.AlignmentFlag.AlignCenter,
            "CONFIDENCE",
        )


class LineChart(QWidget):
    """Equity curve line chart."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.values: list[float] = []
        self.setMinimumHeight(180)

    def set_values(self, values) -> None:
        self.values = [float(v) for v in values]
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(C_CARD))
        p.setPen(QPen(QColor(C_MUTED)))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(14, 22, self.title)
        area = self.rect().adjusted(14, 34, -14, -16)
        if len(self.values) < 2:
            p.setPen(QPen(QColor(C_WHITE)))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "No data yet")
            return
        low, high = min(self.values), max(self.values)
        spread = high - low or 1.0
        pts = [
            QPointF(
                area.left() + i * area.width() / (len(self.values) - 1),
                area.bottom() - (v - low) / spread * area.height(),
            )
            for i, v in enumerate(self.values)
        ]
        rising = self.values[-1] >= self.values[0]
        color = QColor(C_AMBER) if rising else QColor(C_DANGER)
        p.setPen(QPen(color, 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        # Dot at last point
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(pts[-1], 4, 4)


class CandleChart(QWidget):
    """M15 closed-candle chart."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.candles = []
        self.setMinimumHeight(240)

    def set_candles(self, candles) -> None:
        self.candles = list(candles)[-60:]
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(C_CARD))
        p.setPen(QPen(QColor(C_MUTED)))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(14, 22, "Price chart — closed candles")
        area = self.rect().adjusted(14, 34, -14, -16)
        if not self.candles:
            p.setPen(QPen(QColor(C_WHITE)))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Connect MT5 to load candles")
            return
        low = min(float(c.low) for c in self.candles)
        high = max(float(c.high) for c in self.candles)
        spread = high - low or 1.0
        width = max(2, area.width() / len(self.candles) * 0.65)

        def y(price):
            return area.bottom() - (float(price) - low) / spread * area.height()

        for i, c in enumerate(self.candles):
            x = area.left() + (i + 0.5) * area.width() / len(self.candles)
            rising = c.close >= c.open
            color = QColor(C_AMBER) if rising else QColor(C_VIOLET)
            p.setPen(QPen(color, 1))
            p.drawLine(QPointF(x, y(c.high)), QPointF(x, y(c.low)))
            top, bot = sorted((y(c.open), y(c.close)))
            p.fillRect(
                int(x - width / 2), int(top),
                max(1, int(width)), max(1, int(bot - top)),
                color,
            )
