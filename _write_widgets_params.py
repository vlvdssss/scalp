"""Writer script – generates widgets_params.py. Delete after use."""
import pathlib

CONTENT = '''\
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
        root.addWidget(self._make_position_panel())
        root.addStretch(1)

    # \u2500\u2500 Config panel (static at startup) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _make_config_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionCard")

        g = QGridLayout(frame)
        g.setContentsMargins(20, 16, 20, 16)
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(8)

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

        hdr = QLabel("\u0410\u041a\u0422\u0418\u0412\u041d\u042b\u0415  \u041f\u0410\u0420\u0410\u041c\u0415\u0422\u0420\u042b")
        hdr.setObjectName("sectionTitle")
        g.addWidget(hdr, 0, 0, 1, 2)

        rows = [
            ("\u0422\u0440\u0435\u0439\u043b\u0438\u043d\u0433:",
             f"\u0432\u0445\u043e\u0434 +{trail_act:.0f} pts   "
             f"SL \u2212{trail_stop:.0f} pts \u043e\u0442 \u043f\u0438\u043a\u0430   "
             f"\u0448\u0430\u0433 {trail_step:.0f} pts"),
            ("Breakeven:",
             f"\u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u044f ${be_act:.2f}  \u2192  SL \u043d\u0430 ${be_stop:.2f}"),
            ("\u041e\u0440\u0434\u0435\u0440\u0430:",
             f"\u0441\u043c\u0435\u0449\u0435\u043d\u0438\u0435 {min_offs:.0f}\u2013{max_offs:.0f} pts   "
             f"\u0433\u0438\u0441\u0442\u0435\u0440\u0435\u0437\u0438\u0441 {hyst:.0f} pts   "
             f"\u0432\u043e\u0437\u0440\u0430\u0441\u0442 {age_ms:.0f} ms"),
            ("Capture:",
             f"\u043f\u043e\u0440\u043e\u0433 {cap_floor:.0f} pts   \u043c\u043d\u043e\u0436\u0438\u0442\u0435\u043b\u044c \xd7{cap_mult}"),
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

    # \u2500\u2500 Position panel (live) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _make_position_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionCard")

        g = QGridLayout(frame)
        g.setContentsMargins(20, 16, 20, 16)
        g.setHorizontalSpacing(0)
        g.setVerticalSpacing(8)

        hdr = QLabel("\u0422\u0415\u041a\u0423\u0429\u0410\u042f  \u041f\u041e\u0417\u0418\u0426\u0418\u042f")
        hdr.setObjectName("sectionTitle")
        g.addWidget(hdr, 0, 0, 1, 5)

        cols = [
            "\u041d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
            "\u0412\u0445\u043e\u0434",
            "SL \u0442\u0435\u043a\u0443\u0449\u0438\u0439",
            "Breakeven",
            "P&L",
        ]
        for c, txt in enumerate(cols):
            lbl = QLabel(txt)
            lbl.setObjectName("posColHeader")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            g.addWidget(lbl, 1, c)
            g.setColumnStretch(c, 1)

        self._v_side  = self._mklbl()
        self._v_entry = self._mklbl()
        self._v_sl    = self._mklbl()
        self._v_be    = self._mklbl()
        self._v_pnl   = self._mklbl()

        for c, lbl in enumerate([self._v_side, self._v_entry, self._v_sl, self._v_be, self._v_pnl]):
            g.addWidget(lbl, 2, c)

        return frame

    @staticmethod
    def _mklbl(text: str = "\u2014") -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("posColValue")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    # \u2500\u2500 Live update (called from _set_status on every tick) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def update_data(self, data: dict) -> None:
        ticket  = data.get("position_ticket")
        side    = data.get("position_side")
        entry   = data.get("entry_price")
        sl      = data.get("current_sl")
        be_done = bool(data.get("be_done", False))
        balance = float(data.get("balance") or 0.0)
        equity  = float(data.get("equity") or 0.0)

        if ticket is None:
            self._v_side.setText("\u043d\u0435\u0442 \u043f\u043e\u0437\u0438\u0446\u0438\u0438")
            self._v_side.setStyleSheet("")
            for lbl in (self._v_entry, self._v_sl, self._v_be, self._v_pnl):
                lbl.setText("\u2014")
                lbl.setStyleSheet("")
            return

        # Direction
        side_str = str(side) if side else "?"
        clr = "#22C55E" if side_str == "BUY" else "#EF4444"
        self._v_side.setText(side_str)
        self._v_side.setStyleSheet(f"color:{clr}; font-weight:700;")

        # Entry / SL
        self._v_entry.setText(f"{entry:.2f}" if entry is not None else "\u2014")
        self._v_sl.setText(f"{sl:.2f}" if sl is not None else "\u2014")

        # Breakeven
        if be_done:
            self._v_be.setText("\u2713  \u0414\u0410")
            self._v_be.setStyleSheet("color:#22C55E; font-weight:700;")
        else:
            self._v_be.setText("\u043e\u0436\u0438\u0434\u0430\u043d\u0438\u0435")
            self._v_be.setStyleSheet("color:#8EA0B8;")

        # Live P&L = equity - balance (unrealised)
        pnl = round(equity - balance, 2) if balance else 0.0
        if pnl > 0.0:
            self._v_pnl.setText(f"+${pnl:.2f}")
            self._v_pnl.setStyleSheet("color:#22C55E; font-weight:800;")
        elif pnl < 0.0:
            self._v_pnl.setText(f"\u2212${abs(pnl):.2f}")
            self._v_pnl.setStyleSheet("color:#EF4444; font-weight:800;")
        else:
            self._v_pnl.setText("$0.00")
            self._v_pnl.setStyleSheet("")
'''

pathlib.Path(r'C:\Scalper\app\src\ui\widgets_params.py').write_text(CONTENT, encoding='utf-8')
print("OK")
