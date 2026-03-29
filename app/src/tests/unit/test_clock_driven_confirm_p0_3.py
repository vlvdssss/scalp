"""
Unit tests for P0-3: confirm/TTL clock_event fires even when tick=None.

The engine must advance confirms and TTL timers even when get_tick() returns None.
Tests use PositionManager.on_clock_confirm() directly.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.src.core.state import StateStore, TradingState, Side, ConfirmContext
from app.src.core.position_manager import PositionManager, PositionConfig
from app.src.core.risk import ConfirmConfig, BEConfig, TrailConfig


def _make_position_manager(window_ms: int = 500, window_ticks: int = 5):
    """Build a PositionManager with short confirm window for testing."""
    adapter = MagicMock()
    state = StateStore()

    pos_cfg = PositionConfig(
        symbol="XAUUSD",
        magic=1234,
        volume=0.01,
        emergency_sl_points=500.0,
        cancel_deadline_ms=3000.0,
    )
    confirm_cfg = ConfirmConfig(
        window_ms=window_ms,
        window_ticks=window_ticks,
        k_confirm_atr=0.10,
        k_confirm_spread=0.50,
        confirm_min_points=10.0,
        cooldown_on_fail_sec=0.0,
    )
    be_cfg = BEConfig()
    trail_cfg = TrailConfig()
    events: list[tuple] = []
    pm = PositionManager(
        adapter, state, pos_cfg, confirm_cfg, be_cfg, trail_cfg,
        event_cb=lambda ev, data: events.append((ev, data)),
    )
    return pm, state, events


class TestClockEventRunsWhenTickIsNone:
    """
    P0-3: Confirm timeout MUST fire via on_clock_confirm() even when
    no new tick has arrived (tick=None scenario).
    """

    def test_confirm_timeout_fires_on_clock_when_elapsed(self):
        """
        Simulate: fill at t=0, confirm window=100ms, then clock called at t=200ms
        with no tick.  on_clock_confirm should report timed_out=True.
        """
        pm, state, events = _make_position_manager(window_ms=100, window_ticks=20)

        start_mono = 1000.0  # ms
        ctx = ConfirmContext(
            start_monotonic_ms=start_mono,
            ticks_seen=0,
            best_move_points=0.0,
        )
        state.confirm = ctx
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price = 2000.0

        # Clock fires at start + 200ms (well past 100ms window)
        now_mono = start_mono + 200.0
        result = pm.on_clock_confirm(now_mono)

        assert result is not None, "on_clock_confirm should return a result dict"
        assert result.get("timed_out") is True, "Confirm should have timed out"

    def test_confirm_not_timed_out_before_window_expires(self):
        """Early clock call (within window) must not report timed_out."""
        pm, state, events = _make_position_manager(window_ms=500, window_ticks=20)

        start_mono = 1000.0
        ctx = ConfirmContext(
            start_monotonic_ms=start_mono,
            ticks_seen=0,
            best_move_points=0.0,
        )
        state.confirm = ctx
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price = 2000.0

        # Clock fires at start + 100ms (still within 500ms window)
        now_mono = start_mono + 100.0
        result = pm.on_clock_confirm(now_mono)

        # Either None (not finished yet) or timed_out=False
        if result is not None:
            assert result.get("timed_out") is not True

    def test_confirm_timeout_clock_independent_of_ticks(self):
        """
        P0-3: Confirm must timeout purely by elapsed time even if ticks_seen == 0.
        """
        pm, state, events = _make_position_manager(window_ms=50, window_ticks=100)

        start_mono = 5000.0
        ctx = ConfirmContext(
            start_monotonic_ms=start_mono,
            ticks_seen=0,   # No ticks at all
            best_move_points=0.0,
        )
        state.confirm = ctx
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.SELL
        state.entry_price = 1950.0

        # Elapsed >> window_ms, zero ticks
        result = pm.on_clock_confirm(start_mono + 200.0)

        assert result is not None
        assert result.get("timed_out") is True
        assert result.get("ticks_seen") == 0
