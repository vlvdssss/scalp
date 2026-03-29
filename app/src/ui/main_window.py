from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.src.ui.widgets_logs    import LogsWidget
from app.src.ui.widgets_params  import ParamsWidget
from app.src.ui.settings_dialog import SettingsDialog
from app.src.ui.theme           import APP_STYLESHEET

log = logging.getLogger(__name__)


# ── Worker thread wrapper ──────────────────────────────────────────────────────

class CoreWorker(QThread):
    """Runs TradingCore in a dedicated thread; forwards events to GUI via signals."""
    ui_signal  = Signal(str, object)   # (event_type, data)
    log_signal = Signal(str, str)      # (event_type, raw_json)

    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self._cfg  = cfg
        self._core = None

    def run(self) -> None:
        from app.src.core.engine import TradingCore
        self._core = TradingCore(self._cfg, ui_callback=self._on_event)
        self._core.start()
        self.exec()

    def _on_event(self, event: str, data: Any) -> None:
        import json as _j
        try:
            raw = _j.dumps({"event": event, **data}, default=str) if isinstance(data, dict) else str(data)
        except Exception:
            raw = str(data)
        self.ui_signal.emit(event, data)
        self.log_signal.emit(event, raw)

    def stop_core(self) -> None:
        if self._core:
            self._core.stop()
        self.quit()

    def safe_mode(self) -> None:
        if self._core:
            self._core.request_safe_mode("manual_gui")

    def cancel_pendings(self) -> None:
        if self._core:
            self._core.request_cancel_pendings()

    def close_position(self) -> None:
        if self._core:
            self._core.request_close_position()

    def close_all(self) -> None:
        if self._core:
            self._core.request_close_all()

    def get_state(self) -> dict:
        if self._core:
            return self._core.get_state_snapshot()
        return {}


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    _CONN_ON  = ("background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.35);"
                 "border-radius:8px; padding:3px 10px; color:#22C55E;"
                 "font-size:8.5pt; font-weight:700;")
    _CONN_OFF = ("background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.35);"
                 "border-radius:8px; padding:3px 10px; color:#EF4444;"
                 "font-size:8.5pt; font-weight:700;")
    _STATE_SS = ("background:rgba(47,129,247,.10); border:1px solid rgba(47,129,247,.30);"
                 "border-radius:8px; padding:3px 10px; color:#2F81F7;"
                 "font-size:8.5pt; font-weight:700;")

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self._cfg    = cfg
        self._worker: CoreWorker | None = None

        self.setWindowTitle("XAUUSD Scalper")
        self.resize(1060, 680)

        self._build_central()
        self._apply_theme()
        self._set_status({"state": "IDLE"})

    # ── Header bar ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setObjectName("headerBar")
        hdr.setFixedHeight(58)
        row = QHBoxLayout(hdr)
        row.setContentsMargins(20, 0, 16, 0)
        row.setSpacing(10)

        # Left: app name + subtitle
        name = QLabel("XAUUSD Scalper")
        name.setObjectName("appName")
        sub  = QLabel("v2.0  •  MT5 AutoTrader")
        sub.setObjectName("appSub")
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(name)
        left.addWidget(sub)
        row.addLayout(left)
        row.addSpacing(14)

        # Status badges
        self._badge_conn  = QLabel("OFFLINE")
        self._badge_state = QLabel("IDLE")
        self._badge_conn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge_conn.setStyleSheet(self._CONN_OFF)
        self._badge_state.setStyleSheet(self._STATE_SS)
        row.addWidget(self._badge_conn)
        row.addWidget(self._badge_state)
        row.addStretch(1)

        # Buttons (right side)
        self._btn_start = QPushButton("▶  START")
        self._btn_stop  = QPushButton("■  STOP")
        self._btn_safe  = QPushButton("⚠  SAFE")
        self._btn_set   = QPushButton("⚙  Settings")

        self._btn_start.setObjectName("startButton")
        self._btn_stop.setObjectName("stopButton")
        self._btn_safe.setObjectName("safeButton")
        self._btn_set.setObjectName("accentButton")

        self._btn_start.setToolTip("Запустить бота и подключиться к MT5")
        self._btn_stop.setToolTip("Полностью остановить бота")
        self._btn_safe.setToolTip(
            "SAFE \u0440\u0435\u0436\u0438\u043c \u2014 \u043d\u0435\u043c\u0435\u0434\u043b\u0435\u043d\u043d\u043e "
            "\u043e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0432\u0441\u0435 \u043e\u0440\u0434\u0435\u0440\u0430 "
            "\u0438 \u0437\u0430\u043a\u0440\u044b\u0442\u044c \u043f\u043e\u0437\u0438\u0446\u0438\u044e.\n"
            "\u0411\u043e\u0442 \u043e\u0441\u0442\u0430\u0451\u0442\u0441\u044f \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u043d\u044b\u043c, "
            "\u043c\u043e\u0436\u043d\u043e \u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u0447\u0435\u0440\u0435\u0437 START."
        )
        self._btn_set.setToolTip("Изменить параметры стратегии")

        for btn in (self._btn_start, self._btn_stop, self._btn_safe, self._btn_set):
            row.addWidget(btn)

        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_safe.clicked.connect(self._on_safe)
        self._btn_set.clicked.connect(self._on_settings)

        self._update_button_states(running=False)
        return hdr

    # ── Central area ─────────────────────────────────────────────────────────────────

    def _build_central(self) -> None:
        shell = QWidget()
        shell.setObjectName("centralShell")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        # Metric strip
        strip_outer = QWidget()
        strip_outer.setMaximumHeight(92)
        sr = QHBoxLayout(strip_outer)
        sr.setContentsMargins(14, 8, 14, 8)
        sr.setSpacing(8)

        def _card(label: str, variant: str = "normal") -> QLabel:
            _frames = {"normal": "metricCard",     "dim":     "metricCardDim",
                       "accent": "metricCardAccent", "primary": "metricCardPrimary"}
            _lkeys  = {"normal": "metricLabel",     "dim":     "metricLabelDim",
                       "accent": "metricLabel",      "primary": "metricLabel"}
            _vals   = {"normal": "valueLabel",      "dim":     "valueLabelDim",
                       "accent": "valueLabel",       "primary": "valueLabelPrimary"}
            card = QFrame()
            card.setObjectName(_frames[variant])
            vl = QVBoxLayout(card)
            vl.setContentsMargins(10, 6, 10, 6)
            vl.setSpacing(2)
            lk = QLabel(label)
            lk.setObjectName(_lkeys[variant])
            lk.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lv = QLabel("—")
            lv.setObjectName(_vals[variant])
            lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(lk)
            vl.addWidget(lv)
            sr.addWidget(card, 1)
            return lv

        self._lbl_bid     = _card("BID",          variant="dim")
        self._lbl_ask     = _card("ASK",          variant="dim")
        self._lbl_spread  = _card("SPREAD",       variant="dim")
        self._lbl_state   = _card("STATE",        variant="dim")
        self._lbl_bal     = _card("BALANCE",      variant="primary")
        self._lbl_equity  = _card("EQUITY",       variant="primary")
        self._lbl_dpnl    = _card("SESSION P&&L", variant="accent")
        self._lbl_dtrades = _card("TRADES",       variant="dim")
        self._lbl_conn    = _card("MT5",          variant="accent")

        layout.addWidget(strip_outer)

        # Tabs
        self._params = ParamsWidget(self._cfg)
        self._logs   = LogsWidget()

        tabs = QTabWidget()
        tabs.addTab(self._params, "Параметры")
        tabs.addTab(self._logs,   "Логи")

        tab_wrap = QWidget()
        tw = QVBoxLayout(tab_wrap)
        tw.setContentsMargins(14, 6, 14, 12)
        tw.addWidget(tabs)
        layout.addWidget(tab_wrap, 1)

        self.setCentralWidget(shell)

    # ── Status helpers ─────────────────────────────────────────────────────────────

    def _set_status(self, data: dict) -> None:
        bid     = data.get("bid", 0.0)
        ask     = data.get("ask", 0.0)
        state   = str(data.get("state", "—"))
        conn    = bool(data.get("connected", False))
        bal     = float(data.get("balance", 0.0))
        equity  = float(data.get("equity", 0.0))
        spread  = float(data.get("spread_points", 0.0))
        dpnl    = float(data.get("session_pnl", 0.0))
        dtrades = int(data.get("daily_total_trades", 0))
        dwr     = float(data.get("daily_winrate", 0.0))

        self._lbl_bid.setText(f"{bid:.2f}" if isinstance(bid, float) else str(bid))
        self._lbl_ask.setText(f"{ask:.2f}" if isinstance(ask, float) else str(ask))
        self._lbl_spread.setText(f"{spread:.0f}")
        self._lbl_state.setText(state)
        self._lbl_bal.setText(f"${bal:,.2f}" if bal else "—")
        self._lbl_equity.setText(f"${equity:,.2f}" if equity else "—")
        self._lbl_dtrades.setText(f"{dtrades}  ({dwr*100:.0f}%)" if dtrades else "0")

        if dpnl > 0:
            self._lbl_dpnl.setText(f"+${dpnl:.2f}")
            self._lbl_dpnl.setStyleSheet("color:#22C55E; font-weight:800;")
        elif dpnl < 0:
            self._lbl_dpnl.setText(f"-${abs(dpnl):.2f}")
            self._lbl_dpnl.setStyleSheet("color:#EF4444; font-weight:800;")
        else:
            self._lbl_dpnl.setText("$0.00")
            self._lbl_dpnl.setStyleSheet("")

        if conn:
            self._lbl_conn.setText("ONLINE")
            self._lbl_conn.setStyleSheet("color:#22C55E; font-weight:700;")
            self._badge_conn.setText("ONLINE")
            self._badge_conn.setStyleSheet(self._CONN_ON)
        else:
            self._lbl_conn.setText("OFFLINE")
            self._lbl_conn.setStyleSheet("color:#EF4444; font-weight:700;")
            self._badge_conn.setText("OFFLINE")
            self._badge_conn.setStyleSheet(self._CONN_OFF)

        self._badge_state.setText(state)
        self._badge_state.setStyleSheet(self._STATE_SS)

        self._params.update_data(data)

    def _apply_theme(self) -> None:
        self.setStyleSheet(APP_STYLESHEET)

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_start(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Info", "Already running.")
            return
        self._worker = CoreWorker(self._cfg, self)
        self._worker.ui_signal.connect(self._on_core_event)
        self._worker.log_signal.connect(self._on_log_event)
        self._worker.start()
        self._set_status({"state": "STARTING"})
        self._update_button_states(running=True)
        log.info("CoreWorker started")

    def _on_stop(self) -> None:
        if self._worker:
            self._worker.stop_core()
            self._worker.wait(8000)
            self._worker = None
        self._set_status({"state": "IDLE"})
        self._update_button_states(running=False)

    def _on_safe(self) -> None:
        if self._worker:
            self._worker.safe_mode()

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self._cfg = dlg.get_updated_config()
            try:
                cfg_path = Path("config/default.yaml")
                import yaml  # type: ignore
                with open(cfg_path, "w") as f:
                    yaml.dump(self._cfg, f)
            except Exception as exc:
                log.warning("Could not save config: %s", exc)

    # ── Core event slots ──────────────────────────────────────────────────────

    @Slot(str, object)
    def _on_core_event(self, event: str, data: Any) -> None:
        if event == "state_update" and isinstance(data, dict):
            self._set_status(data)
        elif event == "safe_mode":
            reason = data.get("reason", "") if isinstance(data, dict) else str(data)
            QMessageBox.warning(self, "SAFE MODE", f"SAFE MODE activated:\n{reason}")
        elif event == "preflight_error":
            QMessageBox.critical(self, "Preflight Error", str(data))
        elif event == "disconnected":
            self._set_status({"state": "DISCONNECTED"})

    @Slot(str, str)
    def _on_log_event(self, event_type: str, raw: str) -> None:
        self._logs.append_event(event_type, raw)

    # ── Button state management ───────────────────────────────────────────────

    def _update_button_states(self, running: bool) -> None:
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._btn_safe.setEnabled(running)

    # ── Close event ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._on_stop()
        super().closeEvent(event)
