"""
MyTradeBot – Automatic Trading Bot
Pure automatic mode: set target, bot hunts it, stops when reached.
80%+ confidence gate on every entry.
"""
from __future__ import annotations
import sys
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QDoubleSpinBox, QComboBox,
    QTableWidget, QTableWidgetItem, QTextEdit, QGroupBox,
    QSplitter, QStatusBar, QMessageBox, QHeaderView, QTabWidget,
    QFormLayout, QFileDialog, QSpinBox, QSystemTrayIcon, QStyle,
    QProgressBar, QFrame,
)

from core.constants import APP_NAME, APP_VERSION, RISK_DISCLAIMER, OrderType, BotState
from core.mt5_bridge import MT5Bridge
from core.database import Database
from core.logger import get_logger
from core.risk import RiskEngine, RiskSettings, LotSizeMode
from core.strategies import build_strategy, candles_from_rates
from core.trading_engine import TradingConfig, TradingEngine
from core.trading_worker import TradingWorker
from core.backtester import BacktestConfig, Backtester
from core.licensing import LicenseManager
from core.risk import SymbolConstraints
from ui.palette import STYLE, C_AMBER, C_VIOLET, C_WHITE, C_MUTED, C_DANGER, C_CARD, C_BORDER, C_BG
from ui.charts import CandleChart, LineChart, ConfidenceMeter

log = get_logger("ui")


class MainWindow(QMainWindow):
    stop_trading_requested = Signal()

    def __init__(self, bridge: MT5Bridge, db: Database) -> None:
        super().__init__()
        self.bridge = bridge
        self.db = db
        self._risk_settings_warning = ""
        self.risk_engine = RiskEngine(self._load_risk_settings())
        self.license_manager = LicenseManager(self.db, self.db.get_setting("license_api_url", ""))
        self._trading_thread: QThread | None = None
        self._trading_worker: TradingWorker | None = None
        self._last_confidence: float = 0.0
        self._auto_restart_pending = False
        self._auto_restart_timer = QTimer(self)
        self._auto_restart_timer.setSingleShot(True)
        self._auto_restart_timer.timeout.connect(self._restart_after_target)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1200, 780)
        self.setStyleSheet(STYLE)
        self._show_disclaimer()
        self._build_ui()
        self._setup_tray()
        self._start_refresh_timer()
        if self._risk_settings_warning:
            QMessageBox.warning(self, "Risk Settings Reset", self._risk_settings_warning)

    def _load_risk_settings(self) -> RiskSettings:
        defaults = RiskSettings()
        values = {n: self.db.get_setting(n, getattr(defaults, n))
                  for n in RiskSettings.__dataclass_fields__}
        try:
            values["lot_size_mode"] = LotSizeMode(values["lot_size_mode"])
            c = RiskSettings(**values)
            c.validate()
            return c
        except (TypeError, ValueError):
            self._risk_settings_warning = (
                "Saved risk settings were invalid. Conservative defaults are loaded; "
                "review and save your settings before starting the bot."
            )
            log.error("Saved risk settings are invalid; using conservative defaults")
            return defaults

    def _show_disclaimer(self) -> None:
        if self.db.get_setting("disclaimer_accepted"):
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Risk Disclaimer")
        dlg.setText(RISK_DISCLAIMER)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dlg.button(QMessageBox.StandardButton.Ok).setText("I Understand & Accept")
        if dlg.exec() != QMessageBox.StandardButton.Ok:
            sys.exit(0)
        self.db.set_setting("disclaimer_accepted", True)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_dashboard(), "Dashboard")
        self.tabs.addTab(self._build_chart_tab(), "Chart")
        self.tabs.addTab(self._build_history_tab(), "History")
        self.tabs.addTab(self._build_performance_tab(), "Performance")
        self.tabs.addTab(self._build_backtest_tab(), "Backtest")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.tabs.addTab(self._build_logs_tab(), "Logs")
        root.addWidget(self.tabs)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Disconnected")

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setSpacing(10)

        # Top metric cards
        metrics_row = QHBoxLayout()
        self.metric_balance  = self._metric_card("Balance")
        self.metric_equity   = self._metric_card("Equity")
        self.metric_floating = self._metric_card("Floating P/L")
        self.metric_today    = self._metric_card("Today P/L")
        self.metric_run      = self._metric_card("Run P/L")
        for card in (self.metric_balance, self.metric_equity,
                     self.metric_floating, self.metric_today, self.metric_run):
            metrics_row.addWidget(card)
        root.addLayout(metrics_row)

        # Middle: connection | bot control | confidence
        mid = QHBoxLayout()
        mid.addWidget(self._build_connect_group(), 1)
        mid.addWidget(self._build_bot_control_group(), 2)
        mid.addWidget(self._build_confidence_group(), 1)
        root.addLayout(mid)

        # Bottom: positions
        root.addWidget(self._build_positions_group(), 1)
        return page

    def _metric_card(self, title: str) -> QGroupBox:
        card = QGroupBox(title)
        layout = QVBoxLayout(card)
        lbl = QLabel("—")
        lbl.setStyleSheet(f"font-size:22px; font-weight:800; color:{C_WHITE};")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        card.value_label = lbl
        return card

    def _build_connect_group(self) -> QGroupBox:
        g = QGroupBox("MT5 Connection")
        v = QVBoxLayout(g)

        # Broker preset
        broker_row = QHBoxLayout()
        broker_row.addWidget(QLabel("Broker"))
        self.cmb_broker = QComboBox()
        self.cmb_broker.addItems(["Exness", "Other"])
        self.cmb_broker.currentTextChanged.connect(self._on_broker_changed)
        broker_row.addWidget(self.cmb_broker)
        v.addLayout(broker_row)

        self.inp_login  = QLineEdit()
        self.inp_login.setPlaceholderText("MT5 Login (account number)")
        self.inp_login.setText(str(self.db.get_setting("mt5_login", "")))

        self.inp_password = QLineEdit()
        self.inp_password.setPlaceholderText("MT5 Password")
        self.inp_password.setEchoMode(QLineEdit.EchoMode.Password)
        # Never store password in DB — user must re-enter each session

        self.inp_server = QLineEdit()
        self.inp_server.setPlaceholderText("Server (e.g. Exness-MT5Real)")
        self.inp_server.setText(str(self.db.get_setting("mt5_server", "")))

        self.inp_path = QLineEdit()
        self.inp_path.setPlaceholderText("Terminal path (optional, Windows only)")

        self.lbl_conn_status = QLabel("● Disconnected")
        self.lbl_conn_status.setStyleSheet(f"color:{C_DANGER}; font-weight:700;")

        self.btn_connect = QPushButton("Connect to MT5")
        self.btn_connect.setObjectName("btn_connect")
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_disconnect.setEnabled(False)

        for w in (self.inp_login, self.inp_password, self.inp_server, self.inp_path,
                  self.lbl_conn_status, self.btn_connect, self.btn_disconnect):
            v.addWidget(w)
        v.addStretch()
        return g

    def _build_bot_control_group(self) -> QGroupBox:
        g = QGroupBox("Automatic Bot")
        v = QVBoxLayout(g)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Symbol"))
        self.cmb_symbol = QComboBox(); self.cmb_symbol.setEditable(True)
        row1.addWidget(self.cmb_symbol, 2)
        row1.addWidget(QLabel("Strategy"))
        self.cmb_strategy = QComboBox()
        self.cmb_strategy.addItems(["Trend", "Momentum", "Breakout", "Combined"])
        self.cmb_strategy.setCurrentText(str(self.db.get_setting("strategy", "Combined")).title())
        row1.addWidget(self.cmb_strategy, 1)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Run Profit Target ($)"))
        self.spn_run_target = QDoubleSpinBox()
        self.spn_run_target.setRange(0, 1_000_000)
        self.spn_run_target.setSingleStep(10)
        self.spn_run_target.setValue(float(self.db.get_setting("run_profit_target", 50)))
        self.spn_run_target.setToolTip("Bot stops when this profit is reached, then auto-starts next run (0 = unlimited)")
        row2.addWidget(self.spn_run_target)
        row2.addWidget(QLabel("Min Confidence %"))
        self.spn_confidence = QDoubleSpinBox()
        self.spn_confidence.setRange(50, 100)
        self.spn_confidence.setSingleStep(5)
        self.spn_confidence.setValue(float(self.db.get_setting("min_confidence_pct", 80)))
        row2.addWidget(self.spn_confidence)
        v.addLayout(row2)

        # Auto-restart toggle
        auto_row = QHBoxLayout()
        from PySide6.QtWidgets import QCheckBox
        self.chk_auto_restart = QCheckBox("Auto-restart next run when target hit")
        self.chk_auto_restart.setChecked(bool(self.db.get_setting("auto_restart_runs", True)))
        self.chk_auto_restart.setStyleSheet(f"color:{C_WHITE};")
        auto_row.addWidget(self.chk_auto_restart)
        auto_row.addStretch()
        v.addLayout(auto_row)

        # Run counter
        self._run_count = 0
        self.lbl_run_count = QLabel("Run: 0")
        self.lbl_run_count.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        v.addWidget(self.lbl_run_count)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶  START BOT")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.clicked.connect(self._on_start_bot)
        self.btn_stop = QPushButton("■  STOP BOT")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.clicked.connect(self._on_stop_bot)
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        v.addLayout(btn_row)

        self.btn_kill = QPushButton("⚠  EMERGENCY STOP & CLOSE ALL")
        self.btn_kill.setObjectName("btn_kill")
        self.btn_kill.clicked.connect(self._on_kill_switch)
        v.addWidget(self.btn_kill)

        note = QLabel("Bot only enters when signal confidence ≥ configured minimum. "
                       "Stops automatically when run target is reached, then starts next run.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        v.addWidget(note)
        v.addStretch()
        return g

    def _build_confidence_group(self) -> QGroupBox:
        g = QGroupBox("Signal Confidence")
        v = QVBoxLayout(g)
        self.confidence_meter = ConfidenceMeter()
        v.addWidget(self.confidence_meter)
        self.lbl_confidence_detail = QLabel("—")
        self.lbl_confidence_detail.setWordWrap(True)
        self.lbl_confidence_detail.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        self.lbl_confidence_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.lbl_confidence_detail)
        v.addStretch()
        return g

    def _build_positions_group(self) -> QGroupBox:
        g = QGroupBox("Open Positions")
        v = QVBoxLayout(g)
        self.tbl_positions = QTableWidget(0, 7)
        self.tbl_positions.setHorizontalHeaderLabels(
            ["Ticket", "Symbol", "Type", "Volume", "Open Price", "P/L", "Action"])
        self.tbl_positions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_positions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v.addWidget(self.tbl_positions)
        return g

    # ── Other tabs ────────────────────────────────────────────────────────────

    def _build_chart_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        note = QLabel("Chart is informational only and does not predict prices.")
        note.setStyleSheet(f"color:{C_MUTED};")
        v.addWidget(note)
        self.candle_chart = CandleChart()
        v.addWidget(self.candle_chart)
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.history_symbol = QLineEdit(); self.history_symbol.setPlaceholderText("Symbol (optional)")
        self.history_type = QComboBox(); self.history_type.addItems(["All types", "BUY", "SELL"])
        btn_refresh = QPushButton("Refresh"); btn_refresh.clicked.connect(self._refresh_history)
        btn_export  = QPushButton("Export CSV"); btn_export.clicked.connect(self._export_history)
        for w in (self.history_symbol, self.history_type, btn_refresh, btn_export):
            filters.addWidget(w)
        filters.addStretch()
        v.addLayout(filters)
        self.tbl_history = QTableWidget(0, 8)
        self.tbl_history.setHorizontalHeaderLabels(
            ["Ticket", "Opened", "Symbol", "Type", "Volume", "Open", "Close", "P/L"])
        self.tbl_history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v.addWidget(self.tbl_history)
        return page

    def _build_performance_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        row = QHBoxLayout()
        self.perf_labels: dict[str, QLabel] = {}
        for name in ("Closed Trades", "Net P/L", "Win Rate", "Profit Factor", "Max Drawdown", "Avg Win/Loss"):
            card = self._metric_card(name)
            self.perf_labels[name] = card.value_label
            row.addWidget(card)
        v.addLayout(row)
        self.equity_chart = LineChart("Closed-trade equity curve")
        v.addWidget(self.equity_chart)
        note = QLabel("Metrics use locally recorded closed bot trades only. Past results do not guarantee future results.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{C_MUTED};")
        v.addWidget(note)
        return page

    def _build_backtest_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        ctrl = QHBoxLayout()
        self.bt_bars   = self._int_spin(1000, 100, 10000)
        self.bt_spread = self._dbl_spin(10, 0, 100000, 0.1)
        self.bt_comm   = self._dbl_spin(0, 0, 10000, 0.01)
        btn_run = QPushButton("Run Backtest"); btn_run.setObjectName("btn_connect")
        btn_run.clicked.connect(self._run_backtest)
        for lbl, w in (("Bars", self.bt_bars), ("Spread pts", self.bt_spread),
                        ("Commission/lot", self.bt_comm)):
            ctrl.addWidget(QLabel(lbl)); ctrl.addWidget(w)
        ctrl.addWidget(btn_run); ctrl.addStretch()
        v.addLayout(ctrl)
        self.bt_result = QLabel("Connect MT5, choose a symbol, then run a backtest.")
        self.bt_result.setWordWrap(True)
        self.bt_result.setStyleSheet(f"color:{C_WHITE}; font-size:14px;")
        v.addWidget(self.bt_result)
        self.bt_chart = LineChart("Backtest equity curve")
        v.addWidget(self.bt_chart)
        warn = QLabel("Conservative simulation: entries on next bar, stop-loss assumed first on conflicting candles.")
        warn.setWordWrap(True); warn.setStyleSheet(f"color:{C_MUTED};")
        v.addWidget(warn)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        h = QHBoxLayout(page)

        strat = QGroupBox("Strategy")
        sf = QFormLayout(strat)
        self.set_strategy = QComboBox(); self.set_strategy.addItems(["Trend","Momentum","Breakout","Combined"])
        self.set_strategy.setCurrentText(str(self.db.get_setting("strategy","combined")).title())
        self.set_tf = QComboBox(); self.set_tf.addItems(["5","15","30","60"])
        self.set_tf.setCurrentText(str(self.db.get_setting("timeframe_minutes",15)))
        self.set_htf = QComboBox(); self.set_htf.addItems(["15","30","60","240"])
        self.set_htf.setCurrentText(str(self.db.get_setting("higher_timeframe_minutes",60)))
        self.set_min_conf = self._dbl_spin(float(self.db.get_setting("min_confidence_pct",80)),50,100,5)
        self.set_run_target = self._dbl_spin(float(self.db.get_setting("run_profit_target",50)),0,1000000,10)
        sf.addRow("Mode", self.set_strategy)
        sf.addRow("Entry timeframe (min)", self.set_tf)
        sf.addRow("Trend timeframe (min)", self.set_htf)
        sf.addRow("Min confidence (%)", self.set_min_conf)
        sf.addRow("Run profit target", self.set_run_target)

        risk = QGroupBox("Risk Controls")
        rf = QFormLayout(risk)
        self.set_lot_mode   = QComboBox(); self.set_lot_mode.addItems(["Risk percent","Fixed lot"])
        self.set_lot_mode.setCurrentIndex(1 if self.db.get_setting("lot_size_mode","risk_percent")=="fixed" else 0)
        self.set_risk_pct   = self._dbl_spin(self.db.get_setting("risk_percent_per_trade",.5),.01,10,.01)
        self.set_fixed_lot  = self._dbl_spin(self.db.get_setting("fixed_lot",.01),.01,100,.01)
        self.set_max_pos    = self._int_spin(self.db.get_setting("max_open_positions",1),1,20)
        self.set_max_trades = self._int_spin(self.db.get_setting("max_trades_per_day",3),1,100)
        self.set_spread     = self._dbl_spin(self.db.get_setting("max_spread_points",30),.1,100000,.1)
        self.set_daily_loss = self._dbl_spin(self.db.get_setting("max_daily_loss_percent",2),.1,100,.1)
        self.set_drawdown   = self._dbl_spin(self.db.get_setting("max_drawdown_percent",8),.1,100,.1)
        self.set_weekly     = self._dbl_spin(self.db.get_setting("max_weekly_loss_percent",5),.1,100,.1)
        self.set_cooldown   = self._int_spin(self.db.get_setting("cooldown_seconds",900),0,86400)
        self.set_margin     = self._dbl_spin(self.db.get_setting("minimum_free_margin",100),0,1000000,10)
        for lbl, w in (
            ("Lot sizing", self.set_lot_mode), ("Risk per trade (%)", self.set_risk_pct),
            ("Fixed lot", self.set_fixed_lot), ("Max positions", self.set_max_pos),
            ("Max trades/day", self.set_max_trades), ("Max spread (pts)", self.set_spread),
            ("Daily loss limit (%)", self.set_daily_loss), ("Max drawdown (%)", self.set_drawdown),
            ("Weekly loss limit (%)", self.set_weekly), ("Cooldown (sec)", self.set_cooldown),
            ("Min free margin", self.set_margin),
        ):
            rf.addRow(lbl, w)

        ctrl = QVBoxLayout()
        presets = QGroupBox("Risk Presets")
        pl = QVBoxLayout(presets)
        for name in ("Conservative","Balanced","Aggressive"):
            b = QPushButton(name); b.clicked.connect(lambda _,p=name: self._apply_preset(p))
            pl.addWidget(b)
        ctrl.addWidget(presets)
        lic = QGroupBox("Subscription")
        lf = QFormLayout(lic)
        self.set_lic_url = QLineEdit(str(self.db.get_setting("license_api_url","")))
        self.set_lic_url.setPlaceholderText("https://licenses.yourdomain.com")
        lf.addRow("License API", self.set_lic_url)
        ctrl.addWidget(lic)
        btn_save = QPushButton("Save Settings"); btn_save.setObjectName("btn_connect")
        btn_save.clicked.connect(self._save_settings)
        btn_disc = QPushButton("View Risk Disclosure")
        btn_disc.clicked.connect(lambda: QMessageBox.information(self,"Risk Disclosure",RISK_DISCLAIMER))
        ctrl.addWidget(btn_save); ctrl.addWidget(btn_disc); ctrl.addStretch()

        h.addWidget(strat,1); h.addWidget(risk,2); h.addLayout(ctrl,1)
        return page

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        row = QHBoxLayout()
        self.log_level = QComboBox()
        self.log_level.addItems(["All","Info","Warning","Error","Trade","Confidence"])
        btn_clear = QPushButton("Clear"); btn_clear.clicked.connect(lambda: self.txt_log.clear())
        row.addWidget(QLabel("Level")); row.addWidget(self.log_level)
        row.addWidget(btn_clear); row.addStretch()
        v.addLayout(row)
        self.txt_log = QTextEdit(); self.txt_log.setReadOnly(True)
        v.addWidget(self.txt_log)
        return page

    @staticmethod
    def _dbl_spin(value, mn, mx, step) -> QDoubleSpinBox:
        w = QDoubleSpinBox(); w.setRange(mn, mx); w.setSingleStep(step); w.setValue(float(value))
        return w

    @staticmethod
    def _int_spin(value, mn, mx) -> QSpinBox:
        w = QSpinBox(); w.setRange(mn, mx); w.setValue(int(value))
        return w

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot()
    def _on_broker_changed(self, broker: str) -> None:
        if broker == "Exness":
            # Populate common Exness server names as a hint
            current = self.inp_server.text().strip()
            if not current:
                self.inp_server.setPlaceholderText("e.g. Exness-MT5Real, Exness-MT5Trial")

    @Slot()
    def _on_connect(self) -> None:
        path     = self.inp_path.text().strip()
        login_txt = self.inp_login.text().strip()
        login    = int(login_txt) if login_txt.isdigit() else 0
        password = self.inp_password.text()          # never stored
        server   = self.inp_server.text().strip()

        if not login:
            QMessageBox.warning(self, "Login Required",
                                "Enter your MT5 account number (login) before connecting.")
            return

        # Save non-sensitive fields for convenience
        self.db.set_setting("mt5_login", login_txt)
        self.db.set_setting("mt5_server", server)

        ok = self.bridge.connect(path=path, login=login, password=password, server=server)
        if ok:
            self.lbl_conn_status.setText("● Connected")
            self.lbl_conn_status.setStyleSheet(f"color:{C_AMBER}; font-weight:700;")
            self._log("Connected to MT5", "green")
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self._refresh_symbols()
            self._refresh_account()
            self.status_bar.showMessage("Connected")
            # Clear password from memory after use
            self.inp_password.clear()
        else:
            self.lbl_conn_status.setText("● Connection Failed")
            self.lbl_conn_status.setStyleSheet(f"color:{C_DANGER}; font-weight:700;")
            self._log("Connection failed — check MT5 is running and credentials are correct", "red")
            QMessageBox.warning(self, "Connection Failed",
                                "Could not connect to MT5.\n\n"
                                "Make sure:\n"
                                "• MetaTrader 5 is installed and running\n"
                                "• Your login, password, and server are correct\n"
                                "• For Exness: use server like 'Exness-MT5Real' or 'Exness-MT5Trial'")

    @Slot()
    def _on_disconnect(self) -> None:
        self._on_stop_bot()
        if self._trading_thread and not self._trading_thread.wait(5000):
            QMessageBox.warning(
                self, "Trading Worker Still Active",
                "The trading worker is still stopping. MT5 was not disconnected; try again shortly.",
            )
            return
        self.bridge.disconnect()
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.lbl_conn_status.setText("● Disconnected")
        self.lbl_conn_status.setStyleSheet(f"color:{C_DANGER}; font-weight:700;")
        self.status_bar.showMessage("Disconnected")
        self._log("Disconnected from MT5", "orange")

    @Slot()
    def _on_start_bot(self) -> None:
        if not self.bridge.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect to MT5 before starting the bot.")
            return
        symbol = self.cmb_symbol.currentText().strip()
        if not symbol:
            QMessageBox.warning(self, "No Symbol", "Select a symbol before starting the bot.")
            return
        if self._trading_thread is not None:
            return
        status = self.license_manager.validate(self.bridge.get_account_info().login)
        if not status.allowed:
            QMessageBox.warning(self, "License Required", status.message)
            return
        run_target = self.spn_run_target.value()
        min_conf   = self.spn_confidence.value() / 100.0
        try:
            engine = TradingEngine(
                self.bridge, self.db, self.risk_engine,
                build_strategy(self.cmb_strategy.currentText()),
                TradingConfig(
                    symbol=symbol,
                    timeframe_minutes=int(self.db.get_setting("timeframe_minutes", 15)),
                    higher_timeframe_minutes=int(self.db.get_setting("higher_timeframe_minutes", 60)),
                    run_profit_target=run_target,
                    min_confidence=min_conf,
                ),
                license_checker=lambda: self.license_manager.validate(
                    self.bridge.get_account_info().login),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Start", str(exc))
            return
        thread = QThread(self)
        worker = TradingWorker(engine)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.log.connect(self._on_engine_log)
        worker.status.connect(self.status_bar.showMessage)
        worker.run_target_hit.connect(self._on_run_target_hit)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self.stop_trading_requested.connect(worker.stop)
        self._trading_thread, self._trading_worker = thread, worker
        self._run_count += 1
        self.lbl_run_count.setText(f"Run: {self._run_count}")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.cmb_strategy.setEnabled(False)
        thread.start()
        target_str = f"  |  target: {run_target:.2f}" if run_target > 0 else ""
        self._log(f"Run #{self._run_count} started: {self.cmb_strategy.currentText()} on {symbol}{target_str}  |  min confidence: {min_conf*100:.0f}%", "green")

    @Slot()
    def _on_stop_bot(self) -> None:
        if self._auto_restart_timer.isActive():
            self._auto_restart_timer.stop()
            self._auto_restart_pending = False
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_stop.setText("■  STOP BOT")
            self.status_bar.showMessage("Stopped")
            self._log("Automatic restart cancelled", "orange")
            return
        if self._trading_thread is None:
            return
        self.stop_trading_requested.emit()
        self.btn_stop.setEnabled(False)
        self.status_bar.showMessage("Stopping…")

    @Slot(str, str, float)
    def _on_engine_log(self, message: str, level: str, confidence: float) -> None:
        color_map = {"trade": "green", "error": "red", "warning": "orange",
                     "halt": "red", "confidence": "violet", "info": "white"}
        self._log(message, color_map.get(level, "white"))
        if level == "confidence" and confidence > 0:
            self._last_confidence = confidence
            self.confidence_meter.set_value(confidence)
            self.lbl_confidence_detail.setText(f"{confidence*100:.1f}%")
        if level == "trade":
            self._notify("Trade Opened", message)
            self.confidence_meter.set_value(confidence)

    @Slot()
    def _on_run_target_hit(self) -> None:
        """Called when a run profit target is reached. Auto-restart if enabled."""
        self._auto_restart_pending = self.chk_auto_restart.isChecked()

    @Slot()
    def _on_thread_finished(self) -> None:
        thread = self._trading_thread
        self._trading_thread = None
        self._trading_worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("■  STOP BOT")
        self.cmb_strategy.setEnabled(True)
        if thread:
            thread.deleteLater()
        if getattr(self, "_auto_restart_pending", False):
            self._auto_restart_pending = False
            self._log(f"Run #{self._run_count} target hit — starting next run automatically", "green")
            self.status_bar.showMessage("Auto-restarting next run…")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_stop.setText("Cancel Restart")
            self._auto_restart_pending = True
            self._auto_restart_timer.start(2000)
        else:
            self.status_bar.showMessage("Stopped")
            self._log("Bot stopped", "orange")

    @Slot()
    def _restart_after_target(self) -> None:
        self._auto_restart_pending = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("■  STOP BOT")
        self._on_start_bot()

    @Slot()
    def _on_kill_switch(self) -> None:
        if QMessageBox.question(
            self, "Emergency Stop",
            "Stop the bot and close every bot-owned position?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._on_stop_bot()
        if self._trading_thread and not self._trading_thread.wait(5000):
            QMessageBox.critical(
                self, "Trading Worker Still Active",
                "The trading worker did not stop in time. No positions were closed. "
                "Wait for it to stop, then try the emergency action again.",
            )
            return
        results = self.bridge.close_all_positions()
        for r in results:
            self._log(f"Close ticket={r.ticket}: {'OK' if r.success else r.message}",
                      "green" if r.success else "red")
        self._log("Emergency stop activated", "red")

    def closeEvent(self, event) -> None:
        if self._trading_thread:
            self._on_stop_bot()
            if not self._trading_thread.wait(5000):
                QMessageBox.warning(
                    self, "Trading Worker Still Active",
                    "The trading worker is still stopping. Please wait and close the app again.",
                )
                event.ignore()
                return
        self.bridge.disconnect()
        event.accept()

    # ── Settings helpers ──────────────────────────────────────────────────────

    def _apply_preset(self, name: str) -> None:
        presets = {
            "Conservative": (.5, 1, 3, 2, 8, 5, 900),
            "Balanced":     (1.0, 2, 6, 4, 12, 8, 600),
            "Aggressive":   (2.0, 3, 10, 6, 18, 12, 300),
        }
        risk, pos, trades, daily, dd, weekly, cd = presets[name]
        self.set_lot_mode.setCurrentIndex(0)
        self.set_risk_pct.setValue(risk)
        self.set_max_pos.setValue(pos)
        self.set_max_trades.setValue(trades)
        self.set_daily_loss.setValue(daily)
        self.set_drawdown.setValue(dd)
        self.set_weekly.setValue(weekly)
        self.set_cooldown.setValue(cd)
        self._log(f"Applied {name.lower()} preset — save to activate.", "white")

    @Slot()
    def _save_settings(self) -> None:
        values = {
            "strategy": self.set_strategy.currentText().lower(),
            "timeframe_minutes": int(self.set_tf.currentText()),
            "higher_timeframe_minutes": int(self.set_htf.currentText()),
            "min_confidence_pct": self.set_min_conf.value(),
            "run_profit_target": self.set_run_target.value(),
            "lot_size_mode": LotSizeMode.RISK_PERCENT.value if self.set_lot_mode.currentIndex()==0 else LotSizeMode.FIXED.value,
            "risk_percent_per_trade": self.set_risk_pct.value(),
            "fixed_lot": self.set_fixed_lot.value(),
            "max_open_positions": self.set_max_pos.value(),
            "max_trades_per_day": self.set_max_trades.value(),
            "max_spread_points": self.set_spread.value(),
            "max_daily_loss_percent": self.set_daily_loss.value(),
            "max_drawdown_percent": self.set_drawdown.value(),
            "max_weekly_loss_percent": self.set_weekly.value(),
            "cooldown_seconds": self.set_cooldown.value(),
            "minimum_free_margin": self.set_margin.value(),
        }
        try:
            risk_vals = {k: values[k] for k in RiskSettings.__dataclass_fields__}
            risk_vals["lot_size_mode"] = LotSizeMode(risk_vals["lot_size_mode"])
            s = RiskSettings(**risk_vals)
            s.validate()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Invalid Settings", str(exc))
            return
        for k, v in values.items():
            self.db.set_setting(k, v)
        self.db.set_setting("license_api_url", self.set_lic_url.text().strip())
        self.db.set_setting("auto_restart_runs", self.chk_auto_restart.isChecked())
        self.license_manager = LicenseManager(self.db, self.set_lic_url.text().strip())
        self.risk_engine = RiskEngine(s)
        self.cmb_strategy.setCurrentText(self.set_strategy.currentText().title())
        self.spn_confidence.setValue(self.set_min_conf.value())
        self.spn_run_target.setValue(self.set_run_target.value())
        self._log("Settings saved.", "green")
        QMessageBox.information(self, "Saved", "Settings will apply to the next bot run.")

    # ── Refresh helpers ───────────────────────────────────────────────────────

    def _start_refresh_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_all)
        self._timer.start(2000)

    @Slot()
    def _refresh_all(self) -> None:
        if self.bridge.is_connected:
            self._refresh_account()
            self._refresh_positions()
            self._refresh_chart()
        self._refresh_history()
        self._refresh_performance()

    def _refresh_account(self) -> None:
        info = self.bridge.get_account_info()
        self.metric_balance.value_label.setText(f"{info.balance:,.2f} {info.currency}")
        self.metric_equity.value_label.setText(f"{info.equity:,.2f} {info.currency}")
        fl = info.equity - info.balance
        self.metric_floating.value_label.setText(f"{fl:+,.2f}")
        self.metric_floating.value_label.setStyleSheet(
            f"font-size:22px;font-weight:800;color:{'#FF6B00' if fl>=0 else '#FF2D6B'};")
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_pnl = self.db.realized_profit_between(day_start.isoformat(), now.isoformat())
        self.metric_today.value_label.setText(f"{today_pnl:+,.2f}")
        self.metric_today.value_label.setStyleSheet(
            f"font-size:22px;font-weight:800;color:{'#FF6B00' if today_pnl>=0 else '#FF2D6B'};")
        # Run P/L
        engine = getattr(self._trading_worker, "engine", None) if self._trading_worker else None
        if engine and engine._run_start:
            run_pnl = self.db.realized_profit_between(engine._run_start.isoformat(), now.isoformat())
            self.metric_run.value_label.setText(f"{run_pnl:+,.2f}")
            self.metric_run.value_label.setStyleSheet(
                f"font-size:22px;font-weight:800;color:{'#FF6B00' if run_pnl>=0 else '#FF2D6B'};")
        else:
            self.metric_run.value_label.setText("—")

    def _refresh_symbols(self) -> None:
        syms = self.bridge.get_symbols()
        self.cmb_symbol.clear()
        self.cmb_symbol.addItems(syms[:500])

    def _refresh_positions(self) -> None:
        positions = self.bridge.get_positions()
        self.tbl_positions.setRowCount(len(positions))
        for row, pos in enumerate(positions):
            color = C_AMBER if pos.order_type == "BUY" else C_VIOLET
            for col, text in enumerate([
                str(pos.ticket), pos.symbol, pos.order_type,
                f"{pos.volume:.2f}", f"{pos.open_price:.5f}", f"{pos.profit:+.2f}",
            ]):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setForeground(QColor(color))
                if col == 5:
                    item.setForeground(QColor(C_AMBER if pos.profit >= 0 else C_DANGER))
                self.tbl_positions.setItem(row, col, item)
            btn = QPushButton("Close")
            btn.setStyleSheet(f"background:{C_DANGER};color:#fff;border-radius:4px;padding:2px 8px;")
            t = pos.ticket
            btn.clicked.connect(lambda _, tk=t: self._close_single(tk))
            self.tbl_positions.setCellWidget(row, 6, btn)

    def _close_single(self, ticket: int) -> None:
        pos = next((p for p in self.bridge.get_positions() if p.ticket == ticket), None)
        if pos:
            r = self.bridge.close_position(pos)
            self._log(f"Close {ticket}: {'OK' if r.success else r.message}",
                      "green" if r.success else "red")

    def _refresh_chart(self) -> None:
        symbol = self.cmb_symbol.currentText().strip()
        tf = self.bridge.get_timeframe(15)
        if not symbol or tf is None:
            return
        rates = self.bridge.get_candles(symbol, tf, count=60, closed_only=True)
        if rates is not None:
            self.candle_chart.set_candles(candles_from_rates(rates))

    @Slot()
    def _refresh_history(self) -> None:
        ot = self.history_type.currentText()
        rows = self.db.get_filtered_trades(self.history_symbol.text(),
                                           "" if ot == "All types" else ot)
        self.tbl_history.setRowCount(len(rows))
        for ri, trade in enumerate(rows):
            vals = (trade["ticket"], trade["opened_at"], trade["symbol"], trade["order_type"],
                    f'{trade["volume"]:.2f}', f'{trade["open_price"]:.5f}',
                    f'{trade["close_price"]:.5f}' if trade["closed_at"] else "—",
                    f'{trade["profit"]:+.2f}')
            for ci, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                if ci == 3:
                    item.setForeground(QColor(C_AMBER if val == "BUY" else C_VIOLET))
                if ci == 7:
                    item.setForeground(QColor(C_AMBER if trade["profit"] >= 0 else C_DANGER))
                self.tbl_history.setItem(ri, ci, item)

    @Slot()
    def _export_history(self) -> None:
        fname, _ = QFileDialog.getSaveFileName(self, "Export", "mytradebot-history.csv", "CSV (*.csv)")
        if not fname:
            return
        rows = self.db.get_filtered_trades(
            self.history_symbol.text(),
            "" if self.history_type.currentText() == "All types" else self.history_type.currentText())
        with open(fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ticket"])
            w.writeheader(); w.writerows(rows)
        self._log(f"Exported {len(rows)} trades to {Path(fname).name}", "green")

    def _refresh_performance(self) -> None:
        s = self.db.performance_summary()
        factor = s["profit_factor"]
        self.perf_labels["Closed Trades"].setText(str(s["closed_trades"]))
        self.perf_labels["Net P/L"].setText(f'{s["net_profit"]:+.2f}')
        self.perf_labels["Win Rate"].setText(f'{s["win_rate"]:.1f}%')
        self.perf_labels["Profit Factor"].setText("—" if factor is None else f"{factor:.2f}")
        self.perf_labels["Max Drawdown"].setText(f'{s["max_drawdown"]:.2f}')
        self.perf_labels["Avg Win/Loss"].setText(f'{s["average_win"]:.2f} / {s["average_loss"]:.2f}')
        self.equity_chart.set_values([v for _, v in s["equity_curve"]])

    @Slot()
    def _run_backtest(self) -> None:
        if not self.bridge.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect MT5 first.")
            return
        symbol = self.cmb_symbol.currentText().strip()
        etf = self.bridge.get_timeframe(int(self.db.get_setting("timeframe_minutes", 15)))
        htf = self.bridge.get_timeframe(int(self.db.get_setting("higher_timeframe_minutes", 60)))
        info = self.bridge.get_symbol_info(symbol)
        acct = self.bridge.get_account_info()
        if not symbol or etf is None or htf is None or info is None:
            QMessageBox.warning(self, "Unavailable", "Choose a valid symbol and timeframes.")
            return
        rates  = self.bridge.get_candles(symbol, etf, self.bt_bars.value(), closed_only=True)
        hrates = self.bridge.get_candles(symbol, htf, self.bt_bars.value(), closed_only=True)
        if rates is None or hrates is None:
            QMessageBox.warning(self, "Unavailable", "MT5 did not return enough candles.")
            return
        try:
            constraints = SymbolConstraints(info.point, info.volume_min, info.volume_max,
                                            info.volume_step, info.trade_tick_value, info.trade_tick_size)
            report = Backtester(
                build_strategy(self.cmb_strategy.currentText()), self.risk_engine, constraints,
                BacktestConfig(initial_balance=acct.balance or 10000,
                               spread_points=self.bt_spread.value(),
                               commission_per_lot=self.bt_comm.value()),
            ).run(candles_from_rates(rates), candles_from_rates(hrates))
        except (ValueError, TypeError, AttributeError) as exc:
            QMessageBox.warning(self, "Backtest Failed", str(exc))
            return
        factor = "—" if report.profit_factor is None else f"{report.profit_factor:.2f}"
        self.bt_result.setText(
            f"{len(report.trades)} trades  |  Final: {report.final_balance:,.2f}  |  "
            f"Win rate: {report.win_rate:.1f}%  |  Profit factor: {factor}  |  "
            f"Max drawdown: {report.max_drawdown:,.2f}"
        )
        self.bt_chart.set_values([b for _, b in report.equity_curve])
        self._log(f"Backtest: {symbol} — {len(report.trades)} simulated trades.", "white")

    # ── Tray / log helpers ────────────────────────────────────────────────────

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    def _notify(self, title: str, message: str) -> None:
        if self.tray:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    def _log(self, msg: str, color: str = "white") -> None:
        color_hex = {
            "green": C_AMBER, "red": C_DANGER, "orange": "#FFA040",
            "violet": C_VIOLET, "white": C_WHITE,
        }.get(color, C_WHITE)
        level_map = {"green": "Trade", "red": "Error", "orange": "Warning",
                     "violet": "Confidence", "white": "Info"}
        level = level_map.get(color, "Info")
        if self.log_level.currentText() not in {"All", level}:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(
            f'<span style="color:{C_MUTED}">[{ts}]</span> '
            f'<span style="color:{color_hex}">{msg}</span>'
        )
        log.info(msg)
