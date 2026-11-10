"""
MyTradeBot – entry point.
Run: python main.py
"""
import os
import sys
os.environ.setdefault("QT_QPA_PLATFORMTHEME", "generic")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from core.mt5_bridge import MT5Bridge
from core.database import Database
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Inter", 10))

    bridge = MT5Bridge()
    db     = Database()

    window = MainWindow(bridge, db)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
