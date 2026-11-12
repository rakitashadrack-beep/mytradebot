"""Run the optional native PySide6 desktop interface."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORMTHEME", "generic")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from core.database import Database
from core.mt5_bridge import MT5Bridge
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Inter", 10))
    window = MainWindow(MT5Bridge(), Database())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
