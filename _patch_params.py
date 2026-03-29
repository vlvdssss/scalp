"""Patch widgets_params.py with compact config rows, positionCard, column value hierarchy, and empty-state labels."""
import pathlib

src = pathlib.Path(r'C:\Scalper\app\src\ui\widgets_params.py').read_text(encoding='utf-8')

# ── 1. Compact config rows ────────────────────────────────────────────────────
OLD_ROWS = (
    "        rows = [\n"
    "            (\"\u0422\u0440\u0435\u0439\u043b\u0438\u043d\u0433:\",\n"
    "             f\"\u0432\u0445\u043e\u0434 +{trail_act:.0f} pts   \"\n"
    "             f\"SL \u2212{trail_stop:.0f} pts \u043e\u0442 \u043f\u0438\u043a\u0430   \"\n"
    "             f\"\u0448\u0430\u0433 {trail_step:.0f} pts\"),\n"
    "            (\"Breakeven:\",\n"
    "             f\"\u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u044f ${be_act:.2f}  \u2192  SL \u043d\u0430 ${be_stop:.2f}\"),\n"
    "            (\"\u041e\u0440\u0434\u0435\u0440\u0430:\",\n"
    "             f\"\u0441\u043c\u0435\u0449\u0435\u043d\u0438\u0435 {min_offs:.0f}\u2013{max_offs:.0f} pts   \"\n"
    "             f\"\u0433\u0438\u0441\u0442\u0435\u0440\u0435\u0437\u0438\u0441 {hyst:.0f} pts   \"\n"
    "             f\"\u0432\u043e\u0437\u0440\u0430\u0441\u0442 {age_ms:.0f} ms\"),\n"
    "            (\"Capture:\",\n"
    "             f\"\u043f\u043e\u0440\u043e\u0433 {cap_floor:.0f} pts   \u043c\u043d\u043e\u0436\u0438\u0442\u0435\u043b\u044c \xd7{cap_mult}\"),\n"
    "        ]"
)
NEW_ROWS = (
    "        rows = [\n"
    "            (\"TRAILING\",\n"
    "             f\"+{trail_act:.0f} pts  |  SL \u2212{trail_stop:.0f}  |  step {trail_step:.0f}\"),\n"
    "            (\"BREAKEVEN\",\n"
    "             f\"${be_act:.2f}  \u2192  ${be_stop:.2f}\"),\n"
    "            (\"ORDERS\",\n"
    "             f\"{min_offs:.0f}\u2013{max_offs:.0f} pts  |  hyst {hyst:.0f}  |  {age_ms:.0f} ms\"),\n"
    "            (\"CAPTURE\",\n"
    "             f\"{cap_floor:.0f} pts  |  \xd7{cap_mult}\"),\n"
    "        ]"
)
assert OLD_ROWS in src, "Rows not found"
src = src.replace(OLD_ROWS, NEW_ROWS, 1)

# ── 2. positionCard frame name ────────────────────────────────────────────────
OLD_POS_FRAME = (
    "    def _make_position_panel(self) -> QFrame:\n"
    "        frame = QFrame()\n"
    "        frame.setObjectName(\"sectionCard\")\n"
    "\n"
    "        g = QGridLayout(frame)\n"
    "        g.setContentsMargins(20, 16, 20, 16)\n"
    "        g.setHorizontalSpacing(0)\n"
    "        g.setVerticalSpacing(8)"
)
NEW_POS_FRAME = (
    "    def _make_position_panel(self) -> QFrame:\n"
    "        frame = QFrame()\n"
    "        frame.setObjectName(\"positionCard\")\n"
    "        frame.setMinimumHeight(170)\n"
    "\n"
    "        g = QGridLayout(frame)\n"
    "        g.setContentsMargins(20, 18, 20, 18)\n"
    "        g.setHorizontalSpacing(0)\n"
    "        g.setVerticalSpacing(12)"
)
assert OLD_POS_FRAME in src, "Position frame block not found"
src = src.replace(OLD_POS_FRAME, NEW_POS_FRAME, 1)

# ── 3. Column value labels + empty state ──────────────────────────────────────
OLD_VALS = (
    "        self._v_side  = self._mklbl()\n"
    "        self._v_entry = self._mklbl()\n"
    "        self._v_sl    = self._mklbl()\n"
    "        self._v_be    = self._mklbl()\n"
    "        self._v_pnl   = self._mklbl()\n"
    "\n"
    "        for c, lbl in enumerate([self._v_side, self._v_entry, self._v_sl, self._v_be, self._v_pnl]):\n"
    "            g.addWidget(lbl, 2, c)\n"
    "\n"
    "        return frame"
)
NEW_VALS = (
    "        self._v_side  = self._mklbl(\"\u2014\", \"posColValuePrimary\")\n"
    "        self._v_entry = self._mklbl(\"\u2014\", \"posColValueSecondary\")\n"
    "        self._v_sl    = self._mklbl(\"\u2014\", \"posColValueSecondary\")\n"
    "        self._v_be    = self._mklbl(\"\u2014\", \"posColValueSecondary\")\n"
    "        self._v_pnl   = self._mklbl(\"\u2014\", \"posColValuePrimary\")\n"
    "\n"
    "        for c, lbl in enumerate([self._v_side, self._v_entry, self._v_sl, self._v_be, self._v_pnl]):\n"
    "            g.addWidget(lbl, 2, c)\n"
    "\n"
    "        # Empty state (shown when no position is active)\n"
    "        self._lbl_empty = QLabel(\"\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0439 \u043f\u043e\u0437\u0438\u0446\u0438\u0438\")\n"
    "        self._lbl_empty.setObjectName(\"positionEmpty\")\n"
    "        self._lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)\n"
    "        g.addWidget(self._lbl_empty, 3, 0, 1, 5)\n"
    "\n"
    "        self._lbl_empty_sub = QLabel(\"waiting for valid setup\")\n"
    "        self._lbl_empty_sub.setObjectName(\"positionEmptySub\")\n"
    "        self._lbl_empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)\n"
    "        g.addWidget(self._lbl_empty_sub, 4, 0, 1, 5)\n"
    "\n"
    "        return frame"
)
assert OLD_VALS in src, "Value labels block not found"
src = src.replace(OLD_VALS, NEW_VALS, 1)

# ── 4. _mklbl staticmethod signature ─────────────────────────────────────────
OLD_MKLBL = (
    "    @staticmethod\n"
    "    def _mklbl(text: str = \"\u2014\") -> QLabel:\n"
    "        lbl = QLabel(text)\n"
    "        lbl.setObjectName(\"posColValue\")\n"
    "        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)\n"
    "        return lbl"
)
NEW_MKLBL = (
    "    @staticmethod\n"
    "    def _mklbl(text: str = \"\u2014\", obj: str = \"posColValue\") -> QLabel:\n"
    "        lbl = QLabel(text)\n"
    "        lbl.setObjectName(obj)\n"
    "        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)\n"
    "        return lbl"
)
assert OLD_MKLBL in src, "_mklbl not found"
src = src.replace(OLD_MKLBL, NEW_MKLBL, 1)

# ── 5. update_data() empty-state guard ───────────────────────────────────────
OLD_EMPTY = (
    "        if ticket is None:\n"
    "            self._v_side.setText(\"\u043d\u0435\u0442 \u043f\u043e\u0437\u0438\u0446\u0438\u0438\")\n"
    "            self._v_side.setStyleSheet(\"\")\n"
    "            for lbl in (self._v_entry, self._v_sl, self._v_be, self._v_pnl):\n"
    "                lbl.setText(\"\u2014\")\n"
    "                lbl.setStyleSheet(\"\")\n"
    "            return"
)
NEW_EMPTY = (
    "        if ticket is None:\n"
    "            self._v_side.setText(\"\")\n"
    "            self._v_side.setStyleSheet(\"\")\n"
    "            for lbl in (self._v_entry, self._v_sl, self._v_be, self._v_pnl):\n"
    "                lbl.setText(\"\")\n"
    "                lbl.setStyleSheet(\"\")\n"
    "            self._lbl_empty.show()\n"
    "            self._lbl_empty_sub.show()\n"
    "            return\n"
    "\n"
    "        self._lbl_empty.hide()\n"
    "        self._lbl_empty_sub.hide()"
)
assert OLD_EMPTY in src, "Empty guard not found"
src = src.replace(OLD_EMPTY, NEW_EMPTY, 1)

pathlib.Path(r'C:\Scalper\app\src\ui\widgets_params.py').write_text(src, encoding='utf-8')
print("OK")
