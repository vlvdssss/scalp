"""
engine_state_machine.py

TradingCore mixin for the main trading state machine branches.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import PositionSnapshot, SymbolSnapshot
from app.src.core.models_atr import ATRResult
from app.src.core.models_spread import SpreadResult
from app.src.core.state import StateStore
from app.src.core.state import SystemMode, TradingState

log = logging.getLogger(__name__)


class _StateMachineHost(Protocol):
    _state: StateStore
    _order_mgr: Any
    _pos_mgr: Any
    _position_last_seen_mono: float | None
    _fake_breakout_enabled: bool
    _active_since_mono_ms: float
    _tick_active_last_clock_ms: float
    _trail_atr_pts: float
    _early_exit_enabled: bool
    _early_exit_triggered: bool
    _early_exit_window_ms: float
    _early_exit_mfe_spread_mult: float
    _early_exit_mfe_min: float
    _trade_mfe: float

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _resolve_directional_block(
        self,
        bid: float,
        ask: float,
        atr_points: float,
        mono_ms: float,
        point: float,
    ) -> object | None: ...
    def _finalize_closed_trade(
        self,
        bid: float,
        ask: float,
        spread_pts: float,
        si: SymbolSnapshot,
        close_reason: str,
    ) -> None: ...
    def _track_mfe_mae(self, bid: float, ask: float, si: SymbolSnapshot) -> None: ...
    def _apply_early_exit_policy(
        self,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
        spread_med_points: float,
    ) -> None: ...


class StateMachineMixin:
    def _process_armed_or_deny_state(
        self: _StateMachineHost,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        now_ms: float,
        mono_ms: float,
        spread_points: float,
        spread_res: SpreadResult,
        atr_res: ATRResult,
        deny: bool,
        deny_reasons: list[str],
        micro_guard_blocked: bool,
    ) -> None:
        st = self._state.state
        if deny:
            if st != TradingState.DENY:
                self._state.state = TradingState.DENY
                self._order_mgr.cancel_all(si)
                self._state.reset_pending()
                self._log_event("DENY", {
                    "reasons": deny_reasons,
                    "bid": bid,
                    "ask": ask,
                    "spread_points": spread_points,
                    "spread_med_points": spread_res.spread_med_points,
                    "atr_points": atr_res.atr_points,
                })
            return

        if st == TradingState.DENY:
            self._state.state = TradingState.ARMED
            log.info("Deny lifted, returning to ARMED")

        if self._state.state == TradingState.ARMED:
            if micro_guard_blocked:
                return
            blocked_side = self._resolve_directional_block(
                bid=bid,
                ask=ask,
                atr_points=atr_res.atr_points,
                mono_ms=mono_ms,
                point=si.point,
            )
            self._order_mgr.ensure_dual_pending(
                bid,
                ask,
                atr_res.atr_points,
                spread_res.spread_med_points,
                si,
                now_ms,
                blocked_side=blocked_side,
            )

    def _process_confirm_state(
        self: _StateMachineHost,
        my_positions: list[PositionSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
        spread_points: float,
        spread_res: SpreadResult,
        atr_res: ATRResult,
    ) -> None:
        if not my_positions and self._state.position_ticket is not None:
            log.warning(
                "Position %s closed externally during CONFIRM – finalizing",
                self._state.position_ticket,
            )
            self._order_mgr.cancel_all(si)
            self._position_last_seen_mono = None
            try:
                self._finalize_closed_trade(bid, ask, spread_points, si, "sl_hit_during_confirm")
            except Exception as exc:
                log.error(
                    "finalize_closed_trade (sl_hit_during_confirm) error: %s – forcing reset",
                    exc,
                )
                self._state.reset_position()
                self._state.reset_pending()
                self._state.state = TradingState.ARMED
            return

        if my_positions:
            self._position_last_seen_mono = mono_ms

        self._pos_mgr.on_tick_confirm_progress(
            bid,
            ask,
            atr_res.atr_points,
            spread_res.spread_med_points,
            si,
            mono_ms,
        )
        self._track_mfe_mae(bid, ask, si)
        if self._state.confirm.finished and not self._state.confirm.success:
            if self._fake_breakout_enabled:
                self._finalize_closed_trade(bid, ask, spread_points, si, "fake_breakout")
            else:
                log.info("Confirm timeout but fake_breakout_enabled=false – promoting to ACTIVE")
                self._state.state = TradingState.POSITION_ACTIVE
                self._active_since_mono_ms = mono_ms
                self._pos_mgr.set_position_start_ms(mono_ms)

    def _process_active_state(
        self: _StateMachineHost,
        my_positions: list[PositionSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
        spread_points: float,
        spread_res: SpreadResult,
        atr_res: ATRResult,
    ) -> None:
        if self._active_since_mono_ms == 0.0:
            self._active_since_mono_ms = mono_ms
            self._pos_mgr.set_position_start_ms(mono_ms)

        if my_positions:
            self._position_last_seen_mono = mono_ms

        self._pos_mgr.tick_active(
            bid,
            ask,
            atr_res.atr_points,
            spread_res.spread_med_points,
            si,
            mono_ms,
            trail_atr_pts=self._trail_atr_pts,
        )
        self._tick_active_last_clock_ms = mono_ms
        self._track_mfe_mae(bid, ask, si)
        self._apply_early_exit_policy(
            bid=bid,
            ask=ask,
            si=si,
            mono_ms=mono_ms,
            spread_med_points=spread_res.spread_med_points,
        )

        if not my_positions and self._state.position_ticket is not None:
            self._position_last_seen_mono = None
            try:
                self._finalize_closed_trade(bid, ask, spread_points, si, "sl_or_external")
            except Exception as exc:
                log.error(
                    "finalize_closed_trade (sl_or_external) error: %s – forcing reset",
                    exc,
                )
                self._state.reset_position()
                self._state.reset_pending()
                self._state.state = TradingState.ARMED

    def _run_state_machine(
        self: _StateMachineHost,
        my_positions: list[PositionSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        now_ms: float,
        mono_ms: float,
        spread_points: float,
        spread_res: SpreadResult,
        atr_res: ATRResult,
        deny: bool,
        deny_reasons: list[str],
        micro_guard_blocked: bool,
    ) -> None:
        st = self._state.state

        if st == TradingState.IDLE and self._state.running:
            self._state.state = TradingState.ARMED
            st = TradingState.ARMED

        if st in (TradingState.ARMED, TradingState.DENY):
            self._process_armed_or_deny_state(
                bid,
                ask,
                si,
                now_ms,
                mono_ms,
                spread_points,
                spread_res,
                atr_res,
                deny,
                deny_reasons,
                micro_guard_blocked,
            )
        elif st == TradingState.POSITION_CONFIRM:
            self._process_confirm_state(
                my_positions,
                bid,
                ask,
                si,
                mono_ms,
                spread_points,
                spread_res,
                atr_res,
            )
        elif st == TradingState.POSITION_ACTIVE:
            self._process_active_state(
                my_positions,
                bid,
                ask,
                si,
                mono_ms,
                spread_points,
                spread_res,
                atr_res,
            )