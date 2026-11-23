"""
MyTradeBot – color palette and stylesheet.
Primary colors: Deep Amber, Electric Violet, Arctic White.
"""

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG        = "#0D0D14"      # near-black base
C_CARD      = "#13131F"      # card surface
C_BORDER    = "#2A1F4A"      # violet-tinted border

C_AMBER     = "#FF6B00"      # primary action / BUY
C_VIOLET    = "#7B2FFF"      # primary accent / SELL / active
C_VIOLET_DK = "#4B0082"      # deep violet for gradients
C_WHITE     = "#F0F4FF"      # arctic white text
C_MUTED     = "#8A8AAA"      # secondary text
C_SUCCESS   = "#FF6B00"      # amber = profit / success
C_DANGER    = "#FF2D6B"      # hot pink-red = loss / danger
C_INFO      = "#7B2FFF"      # violet = info

STYLE = f"""
QMainWindow, QWidget {{
    background: {C_BG};
    color: {C_WHITE};
    font-family: 'Segoe UI', 'SF Pro Display', Inter, Arial;
    font-size: 13px;
}}
QGroupBox {{
    border: 1px solid {C_BORDER};
    border-radius: 14px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    background: {C_CARD};
}}
QGroupBox::title {{
    color: {C_MUTED};
    subcontrol-origin: margin;
    left: 14px;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QPushButton {{
    background: {C_CARD};
    color: {C_WHITE};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    padding: 7px 18px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #1E1A35; border-color: {C_VIOLET}; }}
QPushButton#btn_start {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C_AMBER}, stop:1 #FF9500);
    color: #000;
    border: none;
    font-size: 14px;
    font-weight: 700;
    border-radius: 12px;
    padding: 10px 24px;
}}
QPushButton#btn_start:hover {{ background: #FF8C00; }}
QPushButton#btn_stop {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C_VIOLET_DK}, stop:1 {C_VIOLET});
    color: {C_WHITE};
    border: none;
    font-size: 14px;
    font-weight: 700;
    border-radius: 12px;
    padding: 10px 24px;
}}
QPushButton#btn_stop:hover {{ background: {C_VIOLET}; }}
QPushButton#btn_connect {{
    background: {C_AMBER};
    color: #000;
    border: none;
    font-weight: 700;
    border-radius: 10px;
}}
QPushButton#btn_connect:hover {{ background: #FF8C00; }}
QPushButton#btn_kill {{
    background: {C_DANGER};
    color: #fff;
    border: none;
    font-weight: 700;
    border-radius: 10px;
}}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background: #0A0A12;
    color: {C_WHITE};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 5px 10px;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {C_VIOLET};
}}
QTableWidget {{
    background: {C_CARD};
    color: {C_WHITE};
    gridline-color: {C_BORDER};
    border: none;
    border-radius: 8px;
}}
QHeaderView::section {{
    background: #0A0A12;
    color: {C_MUTED};
    border: none;
    padding: 5px;
    font-size: 11px;
    letter-spacing: 1px;
}}
QTextEdit {{
    background: #0A0A12;
    color: {C_WHITE};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    font-family: 'Cascadia Code', 'Fira Code', Consolas, monospace;
    font-size: 12px;
}}
QTabWidget::pane {{
    border: 1px solid {C_BORDER};
    border-radius: 10px;
    background: {C_BG};
}}
QTabBar::tab {{
    background: {C_CARD};
    color: {C_MUTED};
    border: 1px solid {C_BORDER};
    border-bottom: none;
    border-radius: 8px 8px 0 0;
    padding: 7px 18px;
    margin-right: 2px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: {C_BG};
    color: {C_AMBER};
    border-bottom: 2px solid {C_AMBER};
}}
QStatusBar {{ background: {C_CARD}; color: {C_MUTED}; border-top: 1px solid {C_BORDER}; }}
QScrollBar:vertical {{
    background: {C_BG}; width: 8px; border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C_BORDER}; border-radius: 4px; min-height: 20px;
}}
QSplitter::handle {{ background: {C_BORDER}; }}
"""
