"""
engine_cycle_orchestration.py

TradingCore mixin for cycle-time reconciliation, invariant checks, and pre-state-machine orchestration.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import OrderSnapshot, PositionSnapshot, SymbolSnapshot
from app.src.core.state import StateStore
from app.src.core.state import TradingState

log = logging.getLogger(__name__)


class _CycleOrchestrationHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: Any
    _state: StateStore
    _order_mgr: Any
    _double_trigger_ms: float
    _position_last_seen_mono: float | None

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _check_api_restriction_symptoms(
        self,
        live_positions: list[PositionSnapshot],
        live_orders: list[OrderSnapshot],
    ) -> None: ...
    def _enter_safe_mode(self, reason: str) -> None: ...
    def _alignment_procedure(
        self,
        positions: list[PositionSnapshot],
        orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> None: ...
    def _handle_fill(
        self,
        pos: PositionSnapshot,
        bid: float,
        ask: float,
        spread_pts: float,
        si: SymbolSnapshot,
        now_ms: float,
        mono_ms: float,
    ) -> None: ...
    def _handle_double_trigger(
        self,
        positions: list[PositionSnapshot],
        orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> None: ...


class CycleOrchestrationMixin:
    def _reconcile_terminal_state(
        self: _CycleOrchestrationHost,
        live_positions: list[PositionSnapshot],
        live_orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> tuple[list[PositionSnapshot], list[OrderSnapshot]] | None:
        my_positions = [p for p in live_positions if p.magic == self._cfg["symbol"]["magic"]]
        if len(my_positions) > 1:
            log.critical("INV-A VIOLATION: %d positions found!", len(my_positions))
            self._log_event("CRITICAL_INV_A_MULTI_POSITION", {
                "count": len(my_positions),
                "tickets": [p.ticket for p in my_positions],
            })
            self._enter_safe_mode("INV_A_multi_position")
            self._alignment_procedure(my_positions, live_orders, bid, ask, si)
            return None

        my_orders = [o for o in live_orders if o.magic == self._cfg["symbol"]["magic"]]
        if my_positions and my_orders:
            log.critical("INV-C VIOLATION: position + pending exist simultaneously!")
            self._log_event("CRITICAL_PENDING_WITH_POSITION", {
                "position_ticket": my_positions[0].ticket,
                "order_tickets": [o.ticket for o in my_orders],
            })
            for order in my_orders:
                self._adapter.order_send(self._adapter.build_cancel_request(order.ticket))
            self._state.reset_pending()

        if (
            self._state.position_ticket is not None
            and not my_positions
            and self._state.state not in (TradingState.POSITION_CONFIRM, TradingState.POSITION_ACTIVE)
        ):
            log.warning(
                "GHOST_POSITION: position_ticket=%s set but no live position in state=%s – clearing",
                self._state.position_ticket,
                self._state.state.value,
            )
            self._log_event("GHOST_POSITION_CLEARED", {
                "ticket": self._state.position_ticket,
                "state": self._state.state.value,
            })
            self._state.reset_position()
            if self._state.state not in (TradingState.ARMED, TradingState.DENY):
                self._state.state = TradingState.ARMED

        self._order_mgr.reconcile_with_terminal(my_orders)
        return my_positions, my_orders

    def _process_fill_and_double_trigger(
        self: _CycleOrchestrationHost,
        my_positions: list[PositionSnapshot],
        my_orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        now_ms: float,
        mono_ms: float,
        spread_points: float,
    ) -> bool:
        if self._state.state in (TradingState.ARMED, TradingState.DENY) and my_positions:
            self._handle_fill(my_positions[0], bid, ask, spread_points, si, now_ms, mono_ms)

        if (
            self._state.first_fill_utc_ms is not None
            and (now_ms - self._state.first_fill_utc_ms) < self._double_trigger_ms
            and len(my_positions) > 1
        ):
            self._handle_double_trigger(my_positions, my_orders, bid, ask, si)
            return True
        return False