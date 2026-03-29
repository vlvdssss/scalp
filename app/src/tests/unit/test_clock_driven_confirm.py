"""
Unit tests for P0-002: Clock-driven confirm timeout.

Proves that confirm can time-out even when NO new ticks arrive.
The clock event (on_clock_confirm) must fire on monotonic time alone.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.src.core.position_manager import PositionManager, PositionConfig
from app.src.core.state import StateStore, ConfirmContext, TradingState
from app.src.core.risk import ConfirmConfig, BEConfig, TrailConfig


def _make_pos_mgr(window_ms: float = 500.0) -> PositionManager:
    pos_cfg = PositionConfig(cancel_deadline_ms=3000.0)
    confirm_cfg = ConfirmConfig(
        window_ms=int(window_ms),
        window_ticks=20,
        k_confirm_atr=0.10,
        k_confirm_spread=0.50,
        confirm_min_points=10.0,
        cooldown_on_fail_sec=0.0,
    )
    be_cfg = BEConfig()
    trail_cfg = TrailConfig(throttle_sec=0.0)
    state = StateStore()
    state.state = TradingState.POSITION_CONFIRM
    state.confirm = ConfirmContext(
        start_monotonic_ms=0.0,
        ticks_seen=0,
        best_move_points=0.0,
    )
    adapter = MagicMock()
    adapter.build_market_close_request.return_value = {}
    adapter.order_send.return_value = MagicMock(retcode=10009)
    pm = PositionManager(
        pos_cfg=pos_cfg,
        confirm_cfg=confirm_cfg,
        be_cfg=be_cfg,
        trail_cfg=trail_cfg,
        state=state,
        adapter=adapter,
        event_cb=lambda e, d: None,
    )
    return pm


class TestClockDrivenConfirm:
    def test_no_timeout_before_window(self) -> None:
        """on_clock_confirm should NOT time out before window_ms elapses."""
        pm = _make_pos_mgr(window_ms=500.0)
        now = time.monotonic() * 1000
        pm._st.confirm = ConfirmContext(
            start_monotonic_ms=now - 100,  # only 100 ms elapsed
            ticks_seen=0,
            best_move_points=0.0,
        )
        result = pm.on_clock_confirm(now)
        assert result is None or not result.get("timed_out"), (
            "Should not time out at 100 ms when window is 500 ms"
        )

    def test_timeout_occurs_without_new_ticks(self) -> None:
        """on_clock_confirm MUST detect timeout based on elapsed monotonic ms, not ticks."""
        pm = _make_pos_mgr(window_ms=500.0)
        now = time.monotonic() * 1000
        pm._st.confirm = ConfirmContext(
            start_monotonic_ms=now - 600,  # 600 ms elapsed; window is 500 ms
            ticks_seen=0,                  # zero new ticks arrived
            best_move_points=0.0,
        )
        result = pm.on_clock_confirm(now)
        assert result is not None, "on_clock_confirm should return a result dict on timeout"
        assert result.get("timed_out"), (
            "Confirm timeout must fire based on clock alone, even with 0 new ticks"
        )

    def test_tick_progress_not_required_for_timeout(self) -> None:
        """Proves the P0-002 regression: engine MUST NOT gate confirm on is_new_tick."""
        pm = _make_pos_mgr(window_ms=200.0)
        now = time.monotonic() * 1000
        pm._st.confirm = ConfirmContext(
            start_monotonic_ms=now - 300,  # well past window
            ticks_seen=0,
            best_move_points=0.0,
        )
        # Call on_clock_confirm ZERO times via on_tick path → must still time out
        result = pm.on_clock_confirm(now)
        assert result and result.get("timed_out"), (
            "Timeout must happen via on_clock_confirm regardless of tick arrival"
        )
