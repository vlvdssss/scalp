from __future__ import annotations

APP_STYLESHEET = """
/* == Base == */
QMainWindow, QWidget {
    background-color: #05070A;
    color: #EAF2FF;
    font-family: "Bahnschrift SemiCondensed", "Aptos", "Segoe UI";
    font-size: 11pt;
}
QWidget#centralShell { background-color: #05070A; }

/* == Header bar == */
QWidget#headerBar {
    background-color: #0B1118;
    border-bottom: 1px solid #1B2633;
}
QLabel#appName {
    color: #EAF2FF; font-size: 13pt; font-weight: 700;
    letter-spacing: 1px; background: transparent;
}
QLabel#appSub {
    color: #8EA0B8; font-size: 8.5pt; font-weight: 600;
    background: transparent; letter-spacing: 0.5px;
}
QLabel#badgeOnline {
    background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.35);
    border-radius: 8px; padding: 3px 10px;
    color: #22C55E; font-size: 8.5pt; font-weight: 700;
}
QLabel#badgeOffline {
    background: rgba(239,68,68,0.12); border: 1px solid rgba(239,68,68,0.35);
    border-radius: 8px; padding: 3px 10px;
    color: #EF4444; font-size: 8.5pt; font-weight: 700;
}
QLabel#badgeState {
    background: rgba(47,129,247,0.10); border: 1px solid rgba(47,129,247,0.30);
    border-radius: 8px; padding: 3px 10px;
    color: #2F81F7; font-size: 8.5pt; font-weight: 700;
}
QLabel#badgeSafe {
    background: rgba(212,160,23,0.12); border: 1px solid rgba(212,160,23,0.35);
    border-radius: 8px; padding: 3px 10px;
    color: #D4A017; font-size: 8.5pt; font-weight: 700;
}

/* == Buttons == */
QPushButton {
    background: #101722; color: #B8CAE0;
    border: 1px solid #1B2633; border-radius: 8px;
    padding: 8px 20px; font-weight: 600; font-size: 10pt;
    min-width: 72px;
}
QPushButton:hover    { background: #14202E; border-color: #2A3F57; color: #EAF2FF; }
QPushButton:pressed  { background: #0C1420; }
QPushButton:disabled { background: #0A0E14; color: #3A4D62; border-color: #131D29; }

QPushButton#startButton {
    background: rgba(22,101,52,0.60); border: 1px solid rgba(34,197,94,0.45); color: #86EFAC;
}
QPushButton#startButton:hover   { background: rgba(22,101,52,0.85); border-color: #22C55E; color: #D1FAE5; }
QPushButton#startButton:disabled { background: #0D1A13; color: #2D6040; border-color: #1A3322; }

QPushButton#stopButton {
    background: rgba(127,29,29,0.60); border: 1px solid rgba(239,68,68,0.45); color: #FCA5A5;
}
QPushButton#stopButton:hover   { background: rgba(127,29,29,0.90); border-color: #EF4444; color: #FEE2E2; }
QPushButton#stopButton:disabled { background: #1A0D0D; color: #6B2020; border-color: #2E1515; }

QPushButton#safeButton {
    background: rgba(120,80,10,0.55); border: 1px solid rgba(212,160,23,0.45); color: #FCD34D;
}
QPushButton#safeButton:hover   { background: rgba(120,80,10,0.85); border-color: #D4A017; color: #FEF3C7; }
QPushButton#safeButton:disabled { background: #130F05; color: #6B5010; border-color: #2E2508; }

QPushButton#accentButton, QPushButton#trainButton {
    background: rgba(19,51,103,0.60); border: 1px solid rgba(47,129,247,0.45); color: #93C5FD;
}
QPushButton#accentButton:hover, QPushButton#trainButton:hover {
    background: rgba(19,51,103,0.90); border-color: #2F81F7; color: #DBEAFE;
}

/* == Metric cards == */
QFrame#metricCard {
    background: #0B1118; border: 1px solid #1B2633; border-radius: 10px;
}
QFrame#metricCardAccent {
    background: #0B1118; border: 1px solid #1B3A5C; border-radius: 10px;
}
QFrame#metricCardDim {
    background: #06090D; border: 1px solid #0F1822; border-radius: 10px;
}
QFrame#metricCardPrimary {
    background: #0D1422; border: 1px solid #1D3658; border-radius: 10px;
}
QLabel#metricLabel {
    color: #8EA0B8; font-size: 7.5pt; font-weight: 700;
    letter-spacing: 0.8px; background: transparent;
}
QLabel#metricLabelDim {
    color: #2C3E50; font-size: 7pt; font-weight: 700;
    letter-spacing: 0.8px; background: transparent;
}
QLabel#valueLabel {
    color: #EAF2FF; font-size: 11pt; font-weight: 700; background: transparent;
}
QLabel#valueLabelDim {
    color: #3D5268; font-size: 10pt; font-weight: 500; background: transparent;
}
QLabel#valueLabelPrimary {
    color: #D4E8FF; font-size: 12pt; font-weight: 700; background: transparent;
}

/* backward compat */
QFrame#dashboardVSep {
    color: #1B2633; max-width: 1px; min-width: 1px; margin: 8px 6px;
}
QFrame#dashboardPanel {
    background: #0B1118; border: 1px solid #1B2633; border-radius: 12px;
}
QLabel#dashboardPanelTitle {
    color: #2F81F7; font-size: 9pt; font-weight: 700;
    letter-spacing: 0.5px; background: transparent;
}

/* == Section cards (params tab) == */
QFrame#sectionCard {
    background: #0B1118; border: 1px solid #1B2633; border-radius: 12px;
}
QFrame#positionCard {
    background: #0C1421; border: 1px solid #1B3050; border-radius: 12px;
}
QLabel#sectionTitle {
    color: #8EA0B8; font-size: 8pt; font-weight: 700;
    letter-spacing: 1px; background: transparent;
}
QLabel#paramKey  { color: #8EA0B8; font-size: 9.5pt; background: transparent; }
QLabel#paramVal  { color: #EAF2FF; font-size: 9.5pt; font-weight: 600; background: transparent; }
QLabel#positionEmpty {
    color: #3A4D62; font-size: 11pt; font-weight: 600; background: transparent;
}
QLabel#posColHeader {
    color: #8EA0B8; font-size: 8pt; font-weight: 700;
    letter-spacing: 0.5px; background: transparent;
}
QLabel#posColValue {
    color: #EAF2FF; font-size: 11pt; font-weight: 700; background: transparent;
}
QLabel#posColValuePrimary {
    color: #EAF2FF; font-size: 13pt; font-weight: 700; background: transparent;
}
QLabel#posColValueSecondary {
    color: #7090A8; font-size: 10pt; font-weight: 600; background: transparent;
}
QLabel#positionEmptySub {
    color: #253545; font-size: 9pt; background: transparent;
}

/* == Tabs == */
QTabWidget::pane {
    border: 1px solid #1B2633; border-radius: 12px;
    background: #05070A; top: -1px;
}
QTabBar::tab {
    background: transparent; color: #8EA0B8;
    border: none; border-bottom: 2px solid transparent;
    padding: 10px 26px; margin-right: 4px;
    font-weight: 600; font-size: 10pt;
}
QTabBar::tab:selected {
    color: #EAF2FF; border-bottom: 2px solid #2F81F7;
    background: rgba(47,129,247,0.05);
}
QTabBar::tab:hover:!selected { color: #B4C8DE; border-bottom: 2px solid #182A40; }

/* == Logs == */
QPlainTextEdit#logConsole {
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 9.5pt; background: #07090D;
    border: 1px solid #1B2633; border-radius: 10px;
    color: #8EA0B8; selection-background-color: #1B3A5C; padding: 6px;
}
QComboBox {
    background: #0B1118; border: 1px solid #1B2633;
    border-radius: 7px; padding: 4px 10px;
    color: #B8CAE0; font-size: 9.5pt;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox:hover { border-color: #2A3F57; }
QComboBox QAbstractItemView {
    background: #0D1520; border: 1px solid #1B2633;
    selection-background-color: #1B3A5C; color: #EAF2FF;
}

/* == Scrollbar == */
QScrollBar:vertical {
    background: #07090D; width: 6px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #1B2633; border-radius: 3px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: #2A3F57; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* == Backward compat misc == */
QGroupBox {
    background: #0B1118; border: 1px solid #1B2633;
    border-radius: 12px; margin-top: 18px;
    padding: 18px 14px 14px 14px; font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; top: 0px; padding: 0 4px;
    color: #2F81F7; background: transparent;
}
QFrame#heroPanel, QFrame#dashboardKpiCard, QFrame#dashboardMiniStat,
QFrame#dashboardAlertStrip, QFrame#dashboardPrimaryPanel {
    background: #0B1118; border: 1px solid #1B2633; border-radius: 12px;
}
QLabel#heroTitle { color: #EAF2FF; font-size: 16pt; font-weight: 700; background: transparent; }
QLabel#heroSubtitle { color: #8EA0B8; font-size: 9pt; background: transparent; }
"""
