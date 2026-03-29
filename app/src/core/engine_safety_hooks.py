"""
engine_safety_hooks.py

TradingCore mixin for safety-related hooks and escalations around the trading loop.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import OrderSnapshot, PositionSnapshot, SymbolSnapshot, RetcodeAction

log = logging.getLogger(__name__)


class _SafetyHooksHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: Any
    _state: Any
    _order_mgr: Any
    _af: dict[str, Any]
    _trade_critical_flags: list[str]

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _enter_safe_mode(self, reason: str) -> None: ...


class SafetyHooksMixin:
    def _check_api_restriction_symptoms(
        self: _SafetyHooksHost,
        live_positions: list,
        live_orders: list,
    ) -> None:
        """Detect symptom patterns suggesting Python API restriction is active."""
        known_ticket = self._state.position_ticket
        known_pendings = self._state.buy_stop_ticket or self._state.sell_stop_ticket

        if known_ticket is not None and not live_positions:
            self._log_event("CRITICAL_API_RESTRICTION_SYMPTOM", {
                "symptom": "position_missing",
                "expected_ticket": known_ticket,
            })
            if "api_restriction" not in self._trade_critical_flags:
                self._trade_critical_flags.append("api_restriction")

        if known_pendings and not live_orders:
            self._log_event("API_RESTRICTION_SYMPTOM_ORDERS", {
                "symptom": "pending_orders_missing",
            })

    def _handle_double_trigger(
        self: _SafetyHooksHost,
        positions: list[PositionSnapshot],
        orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> None:
        log.critical("CRITICAL_DOUBLE_TRIGGER detected!")
        self._log_event("CRITICAL_DOUBLE_TRIGGER", {
            "positions": [{"ticket": p.ticket, "side": p.type} for p in positions],
            "orders": [{"ticket": o.ticket} for o in orders],
        })
        for p in positions:
            close_price = ask if p.type == 1 else bid
            req = self._adapter.build_market_close_request(
                symbol=self._cfg["symbol"]["name"],
                ticket=p.ticket,
                volume=p.volume,
                pos_type=p.type,
                price=close_price,
                magic=self._cfg["symbol"]["magic"],
                comment="double_trigger_emergency",
            )
            self._adapter.order_send(req)
        for o in orders:
            self._adapter.order_send(self._adapter.build_cancel_request(o.ticket))
        self._enter_safe_mode("double_trigger_emergency")

    def _check_be_storm(self: _SafetyHooksHost, now_ms: float) -> None:
        """Detect excessive BE triggers in a rolling window and enter cooldown."""
        af = self._af
        bs_cfg = af.get("be_storm", {})
        n_be = bs_cfg.get("n_be", af.get("n_be", 3))
        w_min = bs_cfg.get("window_min", af.get("w_be_min", 30))
        t_min = bs_cfg.get("cooldown_min", af.get("t_cooldown_min", 60))
        if n_be <= 0 or w_min <= 0 or t_min <= 0:
            if self._state.cooldown_reason == "BE_STORM":
                self._state.clear_cooldown()
                self._log_event("COOLDOWN_CLEARED", {
                    "reason": "BE_STORM_DISABLED",
                })
            return
        w_ms = w_min * 60 * 1000
        count = self._state.be_storm.count_in_window(now_ms, w_ms)
        if count >= n_be and not self._state.is_in_cooldown(now_ms):
            log.warning("BE storm: %d BE events in %.0f min → cooldown %d min", count, w_min, t_min)
            self._state.set_cooldown(t_min * 60, now_ms, reason="BE_STORM")
            self._log_event("COOLDOWN_ENTER", {
                "reason": "BE_STORM",
                "count": count,
                "window_min": w_min,
                "cooldown_min": t_min,
            })

    def _on_retcode_policy(self: _SafetyHooksHost, action: str, retcode: int) -> None:
        """Escalate hard retcodes into safe mode or deny-wait handling."""
        self._log_event("RETCODE_POLICY_ESCALATION", {"action": action, "retcode": retcode})
        if action == RetcodeAction.HARD_BLOCK.value:
            self._enter_safe_mode(f"retcode_hard_block:{retcode}")
        elif action == RetcodeAction.DENY_WAIT.value:
            log.warning("DENY_WAIT retcode=%s – cancelling pending", retcode)
            self._order_mgr.cancel_all()
            self._state.reset_pending()
        elif action == "OP_DEADLINE_EXCEEDED":
            self._enter_safe_mode("op_deadline_exceeded")