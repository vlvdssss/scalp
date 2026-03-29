"""
StateStore – canonical trading state container.

* All fields are plain-Python; JSON-serialisable.
* On every critical state change, call snapshot() → persists to disk via
  persistence.py so the app can recover after a crash.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Mode and State enums ──────────────────────────────────────────────────────

class SystemMode(str, Enum):
    NORMAL   = "NORMAL"
    SAFE     = "SAFE"
    COOLDOWN = "COOLDOWN"

class TradingState(str, Enum):
    IDLE              = "IDLE"
    ARMED             = "ARMED"
    DENY              = "DENY"
    POSITION_CONFIRM  = "POSITION_CONFIRM"
    POSITION_ACTIVE   = "POSITION_ACTIVE"
    RECOVERY          = "RECOVERY"
    EMERGENCY         = "EMERGENCY"

class Side(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


# ── Confirm context ───────────────────────────────────────────────────────────

@dataclass
class ConfirmContext:
    start_monotonic_ms: float  = 0.0
    ticks_seen: int            = 0
    best_move_points: float    = 0.0
    finished: bool             = False
    success: bool              = False
    # P0-002 forensics
    elapsed_ms_at_finish: float    = 0.0
    threshold_points_at_finish: float = 0.0
    fail_reason: str               = ""  # "time_window" | "tick_window"


# ── BE storm tracker ─────────────────────────────────────────────────────────

@dataclass
class BEStormTracker:
    """Tracks BE events in a rolling window."""
    events_utc_ms: list[float] = field(default_factory=list)

    def add(self, now_ms: float) -> None:
        self.events_utc_ms.append(now_ms)

    def count_in_window(self, now_ms: float, window_ms: float) -> int:
        cutoff = now_ms - window_ms
        self.events_utc_ms = [t for t in self.events_utc_ms if t >= cutoff]
        return len(self.events_utc_ms)


# ── Main StateStore ───────────────────────────────────────────────────────────

@dataclass
class StateStore:
    # ── System-level ─────────────────────────────────────────────────────────
    mode: SystemMode    = SystemMode.NORMAL
    state: TradingState = TradingState.IDLE
    running: bool       = False

    # ── Pending order tickets ─────────────────────────────────────────────────
    buy_stop_ticket:  Optional[int]   = None
    sell_stop_ticket: Optional[int]   = None
    buy_stop_price:   Optional[float] = None
    sell_stop_price:  Optional[float] = None
    pending_placed_at_utc_ms: Optional[float] = None

    # ── Position ──────────────────────────────────────────────────────────────
    position_ticket: Optional[int]   = None
    position_side:   Optional[Side]  = None
    entry_price:     Optional[float] = None
    position_volume: float           = 0.0
    current_sl:      Optional[float] = None
    be_done:         bool            = False
    extreme_price:   Optional[float] = None   # max favorable bid/ask since entry
    initial_sl_points: float         = 0.0    # A) SL distance used at fill (for BE activation calc)

    # ── Confirm context ───────────────────────────────────────────────────────
    confirm: ConfirmContext = field(default_factory=ConfirmContext)

    # ── Timers & counters ─────────────────────────────────────────────────────
    last_trailing_update_mono: float   = 0.0
    cooldown_until_ms:         float   = 0.0
    cooldown_reason:           str     = ""
    last_rearm_utc_ms:         float   = 0.0
    last_tick_time_msc:        int     = 0

    # ── BE storm ──────────────────────────────────────────────────────────────
    be_storm: BEStormTracker = field(default_factory=BEStormTracker)

    # ── Deny reasons (updated each cycle) ────────────────────────────────────
    deny_reasons: list[str] = field(default_factory=list)

    # ── Consecutive budget overruns ───────────────────────────────────────────
    budget_overrun_count: int = 0

    # ── Recovery warmup ───────────────────────────────────────────────────────
    recovery_warmup_until_mono: float = 0.0

    # ── Double-trigger guard ──────────────────────────────────────────────────
    first_fill_utc_ms: Optional[float] = None

    # ── Last closed trade (for counter-trend offset) ──────────────────────────
    last_closed_side: Optional[Side] = None
    last_closed_mono_ms: float = 0.0

    # ── Cancel-opposite deadline (P0-006) ────────────────────────────────────
    cancel_opposite_started_mono: Optional[float] = None   # monotonic ms
    cancel_opposite_deadline_mono: Optional[float] = None  # monotonic ms
    cleanup_active: bool = False  # pending-cleanup loop running

    # ── Spec identity (P0-001) ───────────────────────────────────────────────
    spec_version: str = ""
    spec_hash: str = ""

    # ── BE trigger tracking (P0-007) ────────────────────────────────────────
    be_trigger_count: int = 0       # lifetime count of BE triggers

    def is_in_cooldown(self, now_ms: float) -> bool:
        return now_ms < self.cooldown_until_ms

    def set_cooldown(
        self,
        duration_sec: float,
        now_ms: Optional[float] = None,
        *,
        reason: str = "",
    ) -> None:
        now_ms = now_ms or (time.time() * 1000)
        self.cooldown_until_ms = now_ms + duration_sec * 1000
        self.cooldown_reason = reason

    def clear_cooldown(self) -> None:
        self.cooldown_until_ms = 0.0
        self.cooldown_reason = ""

    def reset_position(self) -> None:
        self.position_ticket = None
        self.position_side   = None
        self.entry_price     = None
        self.position_volume = 0.0
        self.current_sl      = None
        self.be_done         = False
        self.extreme_price   = None
        self.initial_sl_points = 0.0
        self.confirm         = ConfirmContext()
        self.first_fill_utc_ms = None
        self.cancel_opposite_started_mono = None
        self.cancel_opposite_deadline_mono = None
        self.cleanup_active = False

    def reset_pending(self) -> None:
        self.buy_stop_ticket     = None
        self.sell_stop_ticket    = None
        self.buy_stop_price      = None
        self.sell_stop_price     = None
        self.pending_placed_at_utc_ms = None

    def set_safe_mode(self) -> None:
        self.mode  = SystemMode.SAFE
        self.state = TradingState.IDLE

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for snapshotting."""
        d = asdict(self)
        # Convert enums to strings
        d["mode"]           = self.mode.value
        d["state"]          = self.state.value
        d["position_side"]  = self.position_side.value if self.position_side else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StateStore":
        """Restore from snapshot dict."""
        obj = cls()
        obj.mode           = SystemMode(d.get("mode", SystemMode.NORMAL))
        obj.state          = TradingState(d.get("state", TradingState.IDLE))
        obj.running        = bool(d.get("running", False))
        obj.buy_stop_ticket  = d.get("buy_stop_ticket")
        obj.sell_stop_ticket = d.get("sell_stop_ticket")
        obj.buy_stop_price   = d.get("buy_stop_price")
        obj.sell_stop_price  = d.get("sell_stop_price")
        obj.pending_placed_at_utc_ms = d.get("pending_placed_at_utc_ms")
        obj.position_ticket = d.get("position_ticket")
        side = d.get("position_side")
        obj.position_side   = Side(side) if side else None
        obj.entry_price     = d.get("entry_price")
        obj.position_volume = float(d.get("position_volume", 0.0))
        obj.current_sl      = d.get("current_sl")
        obj.be_done         = bool(d.get("be_done", False))
        obj.extreme_price   = d.get("extreme_price")
        obj.initial_sl_points = float(d.get("initial_sl_points", 0.0))
        confirm_d = d.get("confirm", {})
        obj.confirm = ConfirmContext(**confirm_d) if confirm_d else ConfirmContext()
        obj.last_trailing_update_mono = float(d.get("last_trailing_update_mono", 0.0))
        obj.cooldown_until_ms = float(d.get("cooldown_until_ms", 0.0))
        obj.cooldown_reason = str(d.get("cooldown_reason", ""))
        obj.last_rearm_utc_ms = float(d.get("last_rearm_utc_ms", 0.0))
        obj.last_tick_time_msc = int(d.get("last_tick_time_msc", 0))
        be_d = d.get("be_storm", {})
        obj.be_storm = BEStormTracker(events_utc_ms=be_d.get("events_utc_ms", []))
        obj.deny_reasons = d.get("deny_reasons", [])
        obj.budget_overrun_count = int(d.get("budget_overrun_count", 0))
        obj.recovery_warmup_until_mono = float(d.get("recovery_warmup_until_mono", 0.0))
        obj.first_fill_utc_ms = d.get("first_fill_utc_ms")
        obj.cancel_opposite_started_mono = d.get("cancel_opposite_started_mono")
        obj.cancel_opposite_deadline_mono = d.get("cancel_opposite_deadline_mono")
        obj.cleanup_active = bool(d.get("cleanup_active", False))
        obj.spec_version = str(d.get("spec_version", ""))
        obj.spec_hash = str(d.get("spec_hash", ""))
        obj.be_trigger_count = int(d.get("be_trigger_count", 0))
        return obj

    def save_snapshot(self, path: Path) -> None:
        import logging as _log_mod
        import os
        _log = _log_mod.getLogger(__name__)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            # On Windows (e.g. OneDrive-synced folder) .replace() can fail with
            # [WinError 5] if the destination is open/locked.
            # Fallback: delete destination first, then rename.
            try:
                tmp.replace(path)
            except OSError:
                try:
                    if path.exists():
                        os.remove(path)
                    tmp.rename(path)
                except OSError:
                    # Last resort: write directly without atomic rename
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except OSError:
                        pass
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(self.to_dict(), f, indent=2)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("StateStore snapshot failed: %s", exc)

    @classmethod
    def load_snapshot(cls, path: Path) -> Optional["StateStore"]:
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return cls.from_dict(d)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("StateStore load failed: %s", exc)
            return None
