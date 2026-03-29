"""
Settings dialog – simplified 6-tab interface.
Supports import/export of JSON presets.
"""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QCheckBox,
    QSizePolicy,
)


def _dbl(val: float, lo: float, hi: float, step: float = 0.01, decimals: int = 4) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setSingleStep(step)
    sb.setDecimals(decimals)
    sb.setValue(val)
    sb.setMinimumWidth(190)
    sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return sb


def _int_spin(val: int, lo: int, hi: int) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(lo, hi)
    sb.setValue(val)
    sb.setMinimumWidth(190)
    sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return sb


def _form(parent: QWidget | None = None) -> QFormLayout:
    form = QFormLayout(parent)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    return form


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.resize(920, 720)
        self.setMinimumSize(860, 660)
        self._cfg = cfg
        self._widgets: dict[str, Any] = {}
        self._build_ui()
        self._load_from_cfg(cfg)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        layout.addWidget(tabs)

        tabs.addTab(self._build_risk_tab(),      "Risk")
        tabs.addTab(self._build_entry_tab(),     "Entry")
        tabs.addTab(self._build_be_tab(),        "Breakeven")
        tabs.addTab(self._build_trailing_tab(),  "Trailing")
        tabs.addTab(self._build_time_tab(),      "Time")
        tabs.addTab(self._build_telegram_tab(),  "Telegram")

        # Buttons
        btn_row = QHBoxLayout()
        preset_load = QPushButton("Load Preset…")
        preset_save = QPushButton("Save Preset…")
        preset_load.clicked.connect(self._on_load_preset)
        preset_save.clicked.connect(self._on_save_preset)
        btn_row.addWidget(preset_load)
        btn_row.addWidget(preset_save)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._apply_tooltips()

    # ── Tooltips ───────────────────────────────────────────────────

    _TOOLTIPS: dict[str, str] = {
        # ― Risk ――――――――――――――――――――――――――――――――――――――――――――――
        "risk.volume": (
            "Объём позиции в лотах.\n"
            "0.01 лот на XAUUSD = ~$0.01 за пункт."
        ),
        "risk.target_risk": (
            "Целевой риск на сделку в USD.\n"
            "На основе этого значения автоматически рассчитывается размер SL."
        ),
        "risk.sl_min": (
            "Минимальный SL в пунктах.\n"
            "Если расчётный SL меньше этого — берётся это значение."
        ),
        "risk.sl_max": (
            "Максимальный SL в пунктах.\n"
            "Если расчётный SL больше этого — ограничивается до max."
        ),
        "risk.emergency_sl": (
            "Аварийный SL в пунктах.\n"
            "Ставится немедленно при открытии, если стандартный SL не удался."
        ),
        # ― Entry ――――――――――――――――――――――――――――――――――――――――――――
        "entry.offset_min": (
            "Минимальное расстояние ордера от текущей цены (пунктов).\n"
            "Чем дальше ордер — меньше ложных срабатываний, но хуже цена входа."
        ),
        "entry.order_age": (
            "Минимальный возраст ордера (мс) перед тем как его заполнение будет засчитано.\n"
            "Защита от мгновенного двойного входа."
        ),
        "entry.rearm_hyst": (
            "Гистерезис переставления ордеров (пунктов).\n"
            "Цена должна отойти на это расстояние прежде чем ордера будут передвинуты.\n"
            "Предотвращает излишнее колыхание ордеров при пильных рынках."
        ),
        # ― Breakeven ―――――――――――――――――――――――――――――――――――――――
        "be.activation": (
            "Прибыль в USD при которой SL переносится в безубыток.\n"
            "Текущее значение: $0.35 (±35 пунктов)."
        ),
        "be.stop": (
            "После активации BE — SL ставится на entry + эта сумма (USD).\n"
            "Фиксирует минимальную прибыль вместо нуля.\n"
            "Текущее значение: $0.20 (±20 пунктов)."
        ),
        "be.hold_ms": (
            "Минимальное время удержания позиции (мс) перед разрешением BE.\n"
            "Предотвращает преждевременное срабатывание после входа."
        ),
        # ― Trailing ――――――――――――――――――――――――――――――――――――――――
        "tr.activation": (
            "Трейлинг стоп включается когда цена ушла на это число пунктов от входа.\n"
            "Текущее значение: 80 пунктов."
        ),
        "tr.stop": (
            "Расстояние трейлинг-стопа от максимума цены (пунктов).\n"
            "Чем меньше — теснее стоп, быстрее фиксирует прибыль.\n"
            "Текущее значение: 30 пунктов."
        ),
        "tr.step": (
            "Минимальный шаг обновления трейлинга (пунктов).\n"
            "SL движется только если цена улучшает SL на не менее чем этот шаг."
        ),
        "tr.throttle": (
            "Минимальный интервал между отправкой запросов на изменение SL (сек).\n"
            "Снижает нагрузку на API брокера при быстром рынке."
        ),
        # ― Time ―――――――――――――――――――――――――――――――――――――――――――――
        "session.close_block": (
            "За сколько минут до закрытия рынка прекратить открытие новых позиций.\n"
            "0 = торговать до самого закрытия."
        ),
        "session.open_block": (
            "Сколько минут ждать после открытия рынка перед первым входом.\n"
            "Даёт рынку время ‘прогреться’ после открытия."
        ),
        "time.cooldown_after_close": (
            "Пауза после любого закрытия позиции (сек).\n"
            "Работает помимо cooldown после потери/прибыли."
        ),
        "time.cooldown_after_loss": (
            "Дополнительная пауза после убыточной сделки (сек).\n"
            "Даёт рынку время стабилизироваться."
        ),
        "time.cooldown_after_win": (
            "Пауза после прибыльной сделки (сек).\n"
            "0 = сразу ищем следующий вход."
        ),
        "time.confirm_cooldown": (
            "Пауза если позиция закрылась как fake breakout (сек).\n"
            "Предотвращает повторные входы в плохих условиях."
        ),
        # ― Telegram ―――――――――――――――――――――――――――――――――――――――
        "tg.enabled": "Включить отправку уведомлений о сделках через Telegram.",
        "tg.token": (
            "API токен Telegram бота.\n"
            "Получить у @BotFather командой /newbot."
        ),
        "tg.chat_id": (
            "ID чата или пользователя для получения уведомлений.\n"
            "Можно узнать через @userinfobot."
        ),
    }

    def _apply_tooltips(self) -> None:
        for key, tip in self._TOOLTIPS.items():
            w = self._widgets.get(key)
            if w is not None:
                w.setToolTip(tip)

    def _build_risk_tab(self) -> QWidget:
        w = QWidget()
        form = _form(w)
        r = self._cfg.get("risk", {})
        sl = self._cfg.get("sl", {})
        self._widgets["risk.volume"]       = _dbl(r.get("volume", 0.01), 0.01, 100.0, 0.01, 2)
        self._widgets["risk.target_risk"]  = _dbl(r.get("target_risk_usd", 0.55), 0.01, 50.0, 0.05, 2)
        self._widgets["risk.sl_min"]       = _dbl(sl.get("sl_min_points", 65.0), 5.0, 500.0, 5.0, 1)
        self._widgets["risk.sl_max"]       = _dbl(sl.get("sl_max_points", 90.0), 10.0, 1000.0, 5.0, 1)
        self._widgets["risk.emergency_sl"] = _dbl(r.get("emergency_sl_points", 100.0), 20.0, 2000.0, 10.0, 1)
        for k, lbl in [
            ("risk.volume",       "Volume (lots)"),
            ("risk.target_risk",  "Target risk ($)"),
            ("risk.sl_min",       "SL min (pts)"),
            ("risk.sl_max",       "SL max (pts)"),
            ("risk.emergency_sl", "Emergency SL (pts)"),
        ]:
            form.addRow(lbl, self._widgets[k])
        return w

    def _build_entry_tab(self) -> QWidget:
        w = QWidget()
        form = _form(w)
        e = self._cfg.get("entry", {})
        r = self._cfg.get("rearm", {})
        self._widgets["entry.offset_min"] = _dbl(e.get("entry_offset_min_points", 36.0), 5.0, 500.0, 5.0, 1)
        self._widgets["entry.order_age"]  = _dbl(e.get("min_order_age_ms", 3000.0), 100.0, 60000.0, 500.0, 0)
        self._widgets["entry.rearm_hyst"] = _dbl(r.get("rearm_hysteresis_pts", 25.0), 1.0, 200.0, 5.0, 1)
        for k, lbl in [
            ("entry.offset_min", "Order distance (pts)"),
            ("entry.order_age",  "Update interval (ms)"),
            ("entry.rearm_hyst", "Rearm hysteresis (pts)"),
        ]:
            form.addRow(lbl, self._widgets[k])
        return w

    def _build_be_tab(self) -> QWidget:
        w = QWidget()
        form = _form(w)
        b = self._cfg.get("breakeven", {})
        self._widgets["be.activation"] = _dbl(b.get("be_activation_usd", 0.25), 0.01, 10.0, 0.05, 2)
        self._widgets["be.stop"]       = _dbl(b.get("be_stop_usd", 0.15), 0.01, 10.0, 0.05, 2)
        self._widgets["be.hold_ms"]    = _dbl(b.get("min_hold_ms", 2000.0), 0.0, 30000.0, 500.0, 0)
        for k, lbl in [
            ("be.activation", "Activation ($)"),
            ("be.stop",       "Lock profit ($)"),
            ("be.hold_ms",    "Hold guard (ms)"),
        ]:
            form.addRow(lbl, self._widgets[k])
        return w

    def _build_trailing_tab(self) -> QWidget:
        w = QWidget()
        form = _form(w)
        t = self._cfg.get("trailing", {})
        self._widgets["tr.activation"] = _dbl(t.get("trail_activation_points", 50.0), 5.0, 1000.0, 5.0, 1)
        self._widgets["tr.stop"]       = _dbl(t.get("trail_stop_points", 20.0), 5.0, 500.0, 5.0, 1)
        self._widgets["tr.step"]       = _dbl(t.get("trail_step_points", 20.0), 1.0, 200.0, 5.0, 1)
        self._widgets["tr.throttle"]   = _dbl(t.get("throttle_sec", 0.5), 0.1, 60.0, 0.1, 1)
        for k, lbl in [
            ("tr.activation", "Activation (pts from entry)"),
            ("tr.stop",       "Stop gap (pts)"),
            ("tr.step",       "Step (pts)"),
            ("tr.throttle",   "Throttle (sec)"),
        ]:
            form.addRow(lbl, self._widgets[k])
        return w

    def _section_label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("dashboardPanelTitle")
        return lb

    def _build_time_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        # ---- Session block ----
        root.addWidget(self._section_label("Session block (before market open / close)"))
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine); sep1.setObjectName("metricLabel")
        root.addWidget(sep1)
        f1 = _form()
        s = self._cfg.get("session", {})
        self._widgets["session.close_block"] = _int_spin(s.get("market_close_block_min", 15), 0, 240)
        self._widgets["session.open_block"]  = _int_spin(s.get("market_open_block_min",  15), 0, 240)
        f1.addRow("Stop before close (min)", self._widgets["session.close_block"])
        f1.addRow("Start after open (min)",  self._widgets["session.open_block"])
        f1_w = QWidget(); f1_w.setLayout(f1)
        root.addWidget(f1_w)

        # ---- Cooldown after trade ----
        root.addWidget(self._section_label("Cooldown after trade (seconds)"))
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine); sep2.setObjectName("metricLabel")
        root.addWidget(sep2)
        f2 = _form()
        self._widgets["time.cooldown_after_close"] = _dbl(self._cfg.get("cooldown_after_close_sec", 20.0), 0.0, 3600.0, 5.0, 0)
        self._widgets["time.cooldown_after_loss"]  = _dbl(self._cfg.get("cooldown_after_loss_sec",  75.0), 0.0, 3600.0, 5.0, 0)
        self._widgets["time.cooldown_after_win"]   = _dbl(self._cfg.get("cooldown_after_win_sec",   0.0),  0.0, 3600.0, 5.0, 0)
        f2.addRow("After any close (sec)",  self._widgets["time.cooldown_after_close"])
        f2.addRow("After loss (sec)",       self._widgets["time.cooldown_after_loss"])
        f2.addRow("After win (sec)",        self._widgets["time.cooldown_after_win"])
        f2_w = QWidget(); f2_w.setLayout(f2)
        root.addWidget(f2_w)

        # ---- Confirm / deny cooldown ----
        root.addWidget(self._section_label("Deny / confirm cooldown"))
        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine); sep3.setObjectName("metricLabel")
        root.addWidget(sep3)
        f3 = _form()
        c = self._cfg.get("confirm", {})
        self._widgets["time.confirm_cooldown"] = _dbl(c.get("cooldown_on_fail_sec", 120.0), 0.0, 3600.0, 10.0, 0)
        f3.addRow("Confirm fail cooldown (sec)", self._widgets["time.confirm_cooldown"])
        f3_w = QWidget(); f3_w.setLayout(f3)
        root.addWidget(f3_w)

        root.addStretch(1)
        return w

    def _build_telegram_tab(self) -> QWidget:
        w = QWidget()
        form = _form(w)
        t = self._cfg.get("telegram", {})
        self._widgets["tg.enabled"]  = QCheckBox()
        self._widgets["tg.enabled"].setChecked(bool(t.get("enabled", False)))
        self._widgets["tg.token"]    = QLineEdit(t.get("bot_token", ""))
        self._widgets["tg.chat_id"]  = QLineEdit(t.get("chat_id", ""))
        self._widgets["tg.token"].setMinimumWidth(220)
        self._widgets["tg.chat_id"].setMinimumWidth(220)
        form.addRow("Enabled",   self._widgets["tg.enabled"])
        form.addRow("Bot Token", self._widgets["tg.token"])
        form.addRow("Chat ID",   self._widgets["tg.chat_id"])
        return w

    def _load_from_cfg(self, cfg: dict) -> None:
        pass  # values are loaded in _build_* methods directly from self._cfg

    def get_updated_config(self) -> dict:
        cfg = dict(self._cfg)

        def _set(d, sect, field, val):
            d.setdefault(sect, {})[field] = val

        def _v(key):
            widget = self._widgets.get(key)
            if widget is None:
                return None
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                return widget.value()
            if isinstance(widget, QCheckBox):
                return widget.isChecked()
            if isinstance(widget, QLineEdit):
                return widget.text()
            return None

        _set(cfg, "risk",      "volume",                  _v("risk.volume"))
        _set(cfg, "risk",      "target_risk_usd",         _v("risk.target_risk"))
        _set(cfg, "sl",        "sl_min_points",           _v("risk.sl_min"))
        _set(cfg, "sl",        "sl_max_points",           _v("risk.sl_max"))
        _set(cfg, "risk",      "emergency_sl_points",     _v("risk.emergency_sl"))
        _set(cfg, "entry",     "entry_offset_min_points", _v("entry.offset_min"))
        _set(cfg, "entry",     "min_order_age_ms",        _v("entry.order_age"))
        _set(cfg, "rearm",     "rearm_hysteresis_pts",    _v("entry.rearm_hyst"))
        _set(cfg, "breakeven", "be_activation_usd",       _v("be.activation"))
        _set(cfg, "breakeven", "be_stop_usd",             _v("be.stop"))
        _set(cfg, "breakeven", "min_hold_ms",             _v("be.hold_ms"))
        _set(cfg, "trailing",  "trail_activation_points", _v("tr.activation"))
        _set(cfg, "trailing",  "trail_stop_points",       _v("tr.stop"))
        _set(cfg, "trailing",  "trail_step_points",       _v("tr.step"))
        _set(cfg, "trailing",  "throttle_sec",            _v("tr.throttle"))
        _set(cfg, "session",   "market_close_block_min",  _v("session.close_block"))
        _set(cfg, "session",   "market_open_block_min",   _v("session.open_block"))
        cfg["cooldown_after_close_sec"] = _v("time.cooldown_after_close")
        cfg["cooldown_after_loss_sec"]  = _v("time.cooldown_after_loss")
        cfg["cooldown_after_win_sec"]   = _v("time.cooldown_after_win")
        _set(cfg, "confirm",   "cooldown_on_fail_sec",    _v("time.confirm_cooldown"))
        _set(cfg, "telegram",  "enabled",   _v("tg.enabled"))
        _set(cfg, "telegram",  "bot_token", _v("tg.token"))
        _set(cfg, "telegram",  "chat_id",   _v("tg.chat_id"))
        return cfg

    def _on_accept(self) -> None:
        self.accept()

    def _on_load_preset(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Load Preset", "", "JSON (*.json)")
        if fn:
            try:
                with open(fn) as f:
                    preset = json.load(f)  # noqa: F841
                QMessageBox.information(self, "Preset", f"Loaded: {fn}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _on_save_preset(self) -> None:
        fn, _ = QFileDialog.getSaveFileName(self, "Save Preset", "preset.json", "JSON (*.json)")
        if fn:
            try:
                cfg = self.get_updated_config()
                with open(fn, "w") as f:
                    json.dump(cfg, f, indent=2)
                QMessageBox.information(self, "Preset", f"Saved: {fn}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
