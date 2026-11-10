"""Charts and confidence widgets for MyTradeBot."""
from __future__ import annotations
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import QWidget

from ui.palette import C_CARD, C_BORDER, C_WHITE, C_MUTED, C_AMBER, C_VIOLET, C_DANGER


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
