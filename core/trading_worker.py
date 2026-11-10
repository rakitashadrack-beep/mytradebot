"""Qt worker that schedules TradingEngine.tick every second off the UI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class TradingWorker(QObject):
    status = Signal(str)
    log = Signal(str, str, float)   # message, level, confidence
    finished = Signal()
    run_target_hit = Signal()       # emitted when a run profit target is reached

    def __init__(self, engine) -> None:
        super().__init__()
        self.engine = engine
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        if self._timer is not None:
            return
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.run_once)
        self._timer.start()
        self.status.emit("Running: scanning for signal")
        self.run_once()

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        self.status.emit("Stopped")
        self.finished.emit()

    @Slot()
    def run_once(self) -> None:
        for event in self.engine.tick():
            self.log.emit(event.message, event.level, event.confidence)
            if event.level == "trade":
                self.status.emit(f"Trade open — {event.message[:60]}")
            elif event.level == "confidence":
                self.status.emit(event.message)
            elif event.level in {"warning", "error"}:
                self.status.emit(event.message)
            elif event.level == "halt":
                self.status.emit(event.message)
                self.run_target_hit.emit()
                self.stop()
                return
            else:
                self.status.emit("Running: scanning for signal")
