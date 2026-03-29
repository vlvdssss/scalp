"""
Logs widget – live stream of JSONL events, filterable by type.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_MAX_LINES = 2000

EVENT_TYPES = [
    "ALL", "FILL", "CONFIRM_SUCCESS", "FAKE_BREAKOUT", "BREAKEVEN",
    "TRAILING_SL_UPDATE", "TRADE_CLOSED", "SAFE_MODE", "DENY",
    "DISCONNECTED", "RECONNECTED", "CYCLE_EXCEPTION", "PREFLIGHT_FAILED",
    "CRITICAL_INV_A_MULTI_POSITION", "CRITICAL_PENDING_WITH_POSITION",
    "CRITICAL_DOUBLE_TRIGGER",
]


class LogsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter = "ALL"
        self._buffer: list[tuple[str, str]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Toolbar
        bar = QHBoxLayout()
        filter_lbl = QLabel("Filter:")
        filter_lbl.setStyleSheet("color:#8EA0B8; font-size:9pt; font-weight:700;")
        bar.addWidget(filter_lbl)
        self._combo = QComboBox()
        self._combo.addItems(EVENT_TYPES)
        self._combo.currentTextChanged.connect(self._on_filter_changed)
        bar.addWidget(self._combo)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(clear_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self._text = QPlainTextEdit()
        self._text.setObjectName("logConsole")
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(_MAX_LINES)
        layout.addWidget(self._text)

    def append_event(self, event_type: str, raw_line: str) -> None:
        self._buffer.append((event_type, raw_line))
        if self._filter in ("ALL", event_type):
            self._text.appendPlainText(raw_line)

    def _on_filter_changed(self, f: str) -> None:
        self._filter = f
        self._rebuild()

    def _rebuild(self) -> None:
        self._text.clear()
        for ev_type, line in self._buffer[-_MAX_LINES:]:
            if self._filter in ("ALL", ev_type):
                self._text.appendPlainText(line)

    def _on_clear(self) -> None:
        self._buffer.clear()
        self._text.clear()
