"""
engine_clockwork.py

TradingCore mixin for tick-independent clock processing and safety watchdogs.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import MT5Adapter, PositionSnapshot, SymbolSnapshot
from app.src.core.state import StateStore, TradingState

log = logging.getLogger(__name__)


class _ClockworkHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: MT5Adapter
    _state: StateStore
    _pos_mgr: Any
    _order_mgr: Any
    _last_atr_pts: float
    _last_spread_med_pts: float
    _trail_atr_pts: float
    _tick_active_last_clock_ms: float
    _TICK_ACTIVE_CLOCK_MS: float
    _position_last_seen_mono: float | None
    _GHOST_WD_MS: float

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _finalize_closed_trade(
        self,
        bid: float,
        ask: float,
        spread_pts: float,
        si: SymbolSnapshot,
        close_reason: str,
    ) -> None: ...


class ClockworkMixin:
    def _clock_event(
        self: _ClockworkHost,
        mono_ms: float,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> None:
        """Process time-driven logic every cycle regardless of tick freshness."""
        st = self._state.state
        if st == TradingState.POSITION_CONFIRM:
            result = self._pos_mgr.on_clock_confirm(mono_ms)
            if result and result.get("timed_out"):
                self._log_event("CONFIRM_TIMEOUT_CLOCK", {
                    "elapsed_ms": result.get("elapsed_ms"),
                    "window_ms": result.get("window_ms"),
                    "ticks_seen": result.get("ticks_seen"),
                })

        if (
            self._state.state in (TradingState.POSITION_CONFIRM, TradingState.POSITION_ACTIVE)
            and self._state.cancel_opposite_deadline_mono is not None
        ):
            deadline_exceeded = self._pos_mgr.check_cancel_deadline(mono_ms, si, bid, ask)
            if deadline_exceeded:
                self._log_event("CANCEL_OPPOSITE_DEADLINE_EXCEEDED", {
                    "deadline_mono": self._state.cancel_opposite_deadline_mono,
                    "now_mono": mono_ms,
                    "cleanup_active": self._state.cleanup_active,
                })

        if self._state.cleanup_active:
            cancelled = self._pos_mgr.run_pending_cleanup_step()
            if cancelled > 0:
                self._log_event("CLEANUP_CANCELLED_PENDING", {"count": cancelled})

        if (
            self._state.state == TradingState.POSITION_ACTIVE
            and bid > 0.0
            and (mono_ms - self._tick_active_last_clock_ms) >= self._TICK_ACTIVE_CLOCK_MS
        ):
            self._pos_mgr.tick_active(
                bid,
                ask,
                self._last_atr_pts,
                self._last_spread_med_pts,
                si,
                mono_ms,
                trail_atr_pts=self._trail_atr_pts,
            )
            self._tick_active_last_clock_ms = mono_ms

        if (
            self._state.state in (TradingState.POSITION_CONFIRM, TradingState.POSITION_ACTIVE)
            and self._state.position_ticket is not None
            and self._position_last_seen_mono is not None
            and (mono_ms - self._position_last_seen_mono) > self._GHOST_WD_MS
        ):
            live_pos = self._adapter.get_positions(self._cfg["symbol"]["name"])
            magic = self._cfg["symbol"]["magic"]
            my_live = [p for p in (live_pos or []) if p.magic == magic]
            if not my_live:
                not_seen_sec = (mono_ms - self._position_last_seen_mono) / 1000.0
                log.critical(
                    "GHOST_POSITION_WATCHDOG: ticket=%s not seen for %.0fs in state=%s – forcing finalize",
                    self._state.position_ticket,
                    not_seen_sec,
                    self._state.state.value,
                )
                self._log_event("GHOST_POSITION_WATCHDOG_TRIGGER", {
                    "ticket": self._state.position_ticket,
                    "state": self._state.state.value,
                    "not_seen_sec": round(not_seen_sec, 1),
                })
                self._position_last_seen_mono = None
                try:
                    self._order_mgr.cancel_all(si)
                    self._finalize_closed_trade(bid, ask, 0.0, si, "ghost_position_watchdog")
                except Exception as exc:
                    log.error(
                        "ghost_position_watchdog finalize error: %s – forcing state reset",
                        exc,
                    )
                    self._state.reset_position()
                    self._state.reset_pending()
                    self._state.state = TradingState.ARMED
            else:
                self._position_last_seen_mono = mono_ms