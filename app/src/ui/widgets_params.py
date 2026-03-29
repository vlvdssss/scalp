from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ParamsWidget(QWidget):
    """Two-section display: active config + current open position."""

    def __init__(self, cfg: dict, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)
        root.addWidget(self._make_config_panel())
        root.addWidget(self._make_position_panel(), 1)

    # ── Config panel (static at startup) ─────────────────────────────────────────────

    def _make_config_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionCard")

        g = QGridLayout(frame)
        g.setContentsMargins(20, 14, 20, 14)
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(6)

        tr = self._cfg.get("trailing", {})
        be = self._cfg.get("breakeven", {})
        en = self._cfg.get("entry", {})

        trail_act  = float(tr.get("trail_activation_points", 0))
        trail_stop = float(tr.get("trail_stop_points", 0))
        trail_step = float(tr.get("trail_step_points", 0))
        be_act     = float(be.get("be_activation_usd", 0))
        be_stop    = float(be.get("be_stop_usd", 0))
        min_offs   = float(en.get("min_total_offset_points", 0))
        max_offs   = float(en.get("offset_abs_max_points", 0))
        hyst       = float(en.get("rearm_hysteresis_pts", 0))
        age_ms     = float(en.get("min_order_age_ms", 0))
        cap_floor  = float(en.get("impulse_capture_floor_pts", 0))
        cap_mult   = en.get("impulse_capture_spread_mult", 0)

        hdr = QLabel("АКТИВНЫЕ  ПАРАМЕТРЫ")
        hdr.setObjectName("sectionTitle")
        g.addWidget(hdr, 0, 0, 1, 2)

        rows = [
            ("TRAILING",
             f"+{trail_act:.0f} pts  |  SL −{trail_stop:.0f}  |  step {trail_step:.0f}"),
            ("BREAKEVEN",
             f"${be_act:.2f}  →  ${be_stop:.2f}"),
            ("ORDERS",
             f"{min_offs:.0f}–{max_offs:.0f} pts  |  hyst {hyst:.0f}  |  {age_ms:.0f} ms"),
            ("CAPTURE",
             f"{cap_floor:.0f} pts  |  ×{cap_mult}"),
        ]
        for i, (key, val) in enumerate(rows, start=1):
            k = QLabel(key)
            k.setObjectName("paramKey")
            k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v = QLabel(val)
            v.setObjectName("paramVal")
            g.addWidget(k, i, 0)
            g.addWidget(v, i, 1)

        g.setColumnStretch(1, 1)
        return frame

    # ── Position panel (live) ───────────────────────────────────────────────────

    def _make_position_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("positionCard")
        frame.setMinimumHeight(170)

        g = QGridLayout(frame)
        g.setContentsMargins(20, 18, 20, 18)
        g.setHorizontalSpacing(0)
        g.setVerticalSpacing(12)

        hdr = QLabel("ТЕКУЩАЯ  ПОЗИЦИЯ")
        hdr.setObjectName("sectionTitle")
        g.addWidget(hdr, 0, 0, 1, 5)

        cols = [
            "Направление",
            "Вход",
            "SL текущий",
            "Breakeven",
            "P&L",
        ]
        for c, txt in enumerate(cols):
            lbl = QLabel(txt)
            lbl.setObjectName("posColHeader")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            g.addWidget(lbl, 1, c)
            g.setColumnStretch(c, 1)

        self._v_side  = self._mklbl("—", "posColValuePrimary")
        self._v_entry = self._mklbl("—", "posColValueSecondary")
        self._v_sl    = self._mklbl("—", "posColValueSecondary")
        self._v_be    = self._mklbl("—", "posColValueSecondary")
        self._v_pnl   = self._mklbl("—", "posColValuePrimary")

        for c, lbl in enumerate([self._v_side, self._v_entry, self._v_sl, self._v_be, self._v_pnl]):
            g.addWidget(lbl, 2, c)

        # Empty state (shown when no position is active)
        self._lbl_empty = QLabel("Нет активной позиции")
        self._lbl_empty.setObjectName("positionEmpty")
        self._lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        g.addWidget(self._lbl_empty, 3, 0, 1, 5)

        self._lbl_empty_sub = QLabel("waiting for valid setup")
        self._lbl_empty_sub.setObjectName("positionEmptySub")
        self._lbl_empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        g.addWidget(self._lbl_empty_sub, 4, 0, 1, 5)

        return frame

    @staticmethod
    def _mklbl(text: str = "—", obj: str = "posColValue") -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName(obj)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    # ── Live update (called from _set_status on every tick) ──────────────────────────

    def update_data(self, data: dict) -> None:
        ticket  = data.get("position_ticket")
        side    = data.get("position_side")
        entry   = data.get("entry_price")
        sl      = data.get("current_sl")
        be_done = bool(data.get("be_done", False))
        balance = float(data.get("balance") or 0.0)
        equity  = float(data.get("equity") or 0.0)

        if ticket is None:
            self._v_side.setText("")
            self._v_side.setStyleSheet("")
            for lbl in (self._v_entry, self._v_sl, self._v_be, self._v_pnl):
                lbl.setText("")
                lbl.setStyleSheet("")
            self._lbl_empty.show()
            self._lbl_empty_sub.show()
            return

        self._lbl_empty.hide()
        self._lbl_empty_sub.hide()

        # Direction
        side_str = str(side) if side else "?"
        clr = "#22C55E" if side_str == "BUY" else "#EF4444"
        self._v_side.setText(side_str)
        self._v_side.setStyleSheet(f"color:{clr}; font-weight:700;")

        # Entry / SL
        self._v_entry.setText(f"{entry:.2f}" if entry is not None else "—")
        self._v_sl.setText(f"{sl:.2f}" if sl is not None else "—")

        # Breakeven
        if be_done:
            self._v_be.setText("✓  ДА")
            self._v_be.setStyleSheet("color:#22C55E; font-weight:700;")
        else:
            self._v_be.setText("ожидание")
            self._v_be.setStyleSheet("color:#8EA0B8;")

        # Live P&L = equity - balance (unrealised)
        pnl = round(equity - balance, 2) if balance else 0.0
        if pnl > 0.0:
            self._v_pnl.setText(f"+${pnl:.2f}")
            self._v_pnl.setStyleSheet("color:#22C55E; font-weight:800;")
        elif pnl < 0.0:
            self._v_pnl.setText(f"−${abs(pnl):.2f}")
            self._v_pnl.setStyleSheet("color:#EF4444; font-weight:800;")
        else:
            self._v_pnl.setText("$0.00")
            self._v_pnl.setStyleSheet("")
