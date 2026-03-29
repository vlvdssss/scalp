"""
engine_exit_policy.py

TradingCore mixin for active-trade early-exit policy.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import SymbolSnapshot
from app.src.core.state import StateStore

log = logging.getLogger(__name__)


class _ExitPolicyHost(Protocol):
    _state: StateStore
    _pos_mgr: Any
    _early_exit_enabled: bool
    _early_exit_triggered: bool
    _early_exit_window_ms: float
    _early_exit_mfe_spread_mult: float
    _early_exit_mfe_min: float
    _active_since_mono_ms: float
    _trade_mfe: float

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...


class ExitPolicyMixin:
    def _apply_early_exit_policy(
        self: _ExitPolicyHost,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
        spread_med_points: float,
    ) -> None:
        if (
            not self._early_exit_enabled
            or self._early_exit_triggered
            or self._state.be_done
            or self._active_since_mono_ms <= 0
        ):
            return

        elapsed = mono_ms - self._active_since_mono_ms
        if elapsed <= self._early_exit_window_ms:
            return

        min_mfe = max(
            spread_med_points * self._early_exit_mfe_spread_mult,
            self._early_exit_mfe_min,
        )
        if self._trade_mfe >= min_mfe:
            return

        log.info(
            "EARLY_EXIT_NO_FOLLOWTHROUGH: elapsed_ms=%.0f mfe=%.1f < min_mfe=%.1f",
            elapsed,
            self._trade_mfe,
            min_mfe,
        )
        self._log_event("EARLY_EXIT_NO_FOLLOWTHROUGH", {
            "elapsed_ms": elapsed,
            "mfe_pts": self._trade_mfe,
            "min_mfe_pts": min_mfe,
            "spread_med_pts": spread_med_points,
        })
        closed_ok = self._pos_mgr.close_position_market(
            bid,
            ask,
            si,
            comment="early_exit_no_followthrough",
        )
        if closed_ok:
            self._early_exit_triggered = True
        else:
            log.warning("EARLY_EXIT close failed – will retry next window")