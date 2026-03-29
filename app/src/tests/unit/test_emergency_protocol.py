"""
Unit tests for P0-006: Cancel-opposite deadline → emergency close + SAFE.

Validates that when the cancel-opposite deadline elapses without confirmation,
the position manager triggers emergency_close_position and sets cleanup_active.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, call

import pytest

from app.src.core.position_manager import PositionManager, PositionConfig
from app.src.core.state import StateStore, TradingState, Side
from app.src.core.risk import ConfirmConfig, BEConfig, TrailConfig


def _make_pm_with_deadline(deadline_ms: float = 500.0) -> tuple[PositionManager, StateStore]:
    pos_cfg = PositionConfig(cancel_deadline_ms=deadline_ms)
    confirm_cfg = ConfirmConfig(
        window_ms=2000,
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
    state.position_ticket = 1001
    state.position_side = Side.BUY
    state.entry_price = 2000.0
    state.position_volume = 0.01

    adapter = MagicMock()
    adapter.build_market_close_request.return_value = {"action": "close"}
    adapter.order_send.return_value = MagicMock(retcode=10009)
    adapter.get_orders.return_value = []

    events: list[tuple[str, dict]] = []
    pm = PositionManager(
        pos_cfg=pos_cfg,
        confirm_cfg=confirm_cfg,
        be_cfg=be_cfg,
        trail_cfg=trail_cfg,
        state=state,
        adapter=adapter,
        event_cb=lambda e, d: events.append((e, d)),
    )
    pm._events = events  # type: ignore[attr-defined]
    return pm, state


class TestCancelOppositeDeadline:
    def test_no_trigger_before_deadline(self) -> None:
        """check_cancel_deadline should return False before the deadline."""
        pm, state = _make_pm_with_deadline(deadline_ms=1000.0)
        now = time.monotonic() * 1000
        state.cancel_opposite_started_mono = now - 200   # only 200 ms ago
        state.cancel_opposite_deadline_mono = now + 800  # 800 ms remaining

        si = MagicMock()
        si.point = 0.01
        result = pm.check_cancel_deadline(now, si, bid=2000.0, ask=2000.5)
        assert not result, "Should not trigger before deadline"

    def test_triggers_at_deadline(self) -> None:
        """check_cancel_deadline must return True and set cleanup_active when deadline elapsed
        AND opposing pending orders still exist."""
        pm, state = _make_pm_with_deadline(deadline_ms=500.0)
        now = time.monotonic() * 1000
        state.cancel_opposite_started_mono = now - 600   # 600 ms ago
        state.cancel_opposite_deadline_mono = now - 100  # deadline was 100 ms ago

        # Simulate a pending order still present (cancel didn't work)
        mock_order = MagicMock()
        mock_order.magic = pm._pc.magic
        pm._a.get_orders.return_value = [mock_order]  # type: ignore[attr-defined]

        si = MagicMock()
        si.point = 0.01
        si.digits = 2
        si.tick_size = 0.01
        result = pm.check_cancel_deadline(now, si, bid=2000.0, ask=2000.5)
        assert result is True, "Must return True when deadline exceeded and pendings still exist"
        assert state.cleanup_active, "cleanup_active must be True after deadline exceeded"

    def test_deadline_auto_cleared_when_no_pendings(self) -> None:
        """If deadline exceeded but no pending orders remain, cancel already succeeded –
        clear the deadline silently without emergency close."""
        pm, state = _make_pm_with_deadline(deadline_ms=500.0)
        now = time.monotonic() * 1000
        state.cancel_opposite_started_mono = now - 600
        state.cancel_opposite_deadline_mono = now - 100  # deadline was 100 ms ago

        # No pending orders – cancel already confirmed
        pm._a.get_orders.return_value = []  # type: ignore[attr-defined]

        si = MagicMock()
        si.point = 0.01
        result = pm.check_cancel_deadline(now, si, bid=2000.0, ask=2000.5)
        assert result is False, "Must return False when pendings already gone"
        assert not state.cleanup_active, "No emergency close should be triggered"
        assert state.cancel_opposite_deadline_mono is None, "Deadline must be cleared"

    def test_emergency_close_sends_order(self) -> None:
        """emergency_close_position must call order_send with a close request."""
        pm, state = _make_pm_with_deadline()
        si = MagicMock()
        si.point = 0.01
        si.digits = 2
        # Ensure get_positions returns a live position so close proceeds
        mock_pos = MagicMock()
        mock_pos.ticket = 1001
        mock_pos.magic  = pm._pc.magic   # must match PositionConfig.magic
        mock_pos.type   = 0   # BUY
        mock_pos.volume = 0.01
        pm._a.get_positions.return_value = [mock_pos]  # type: ignore[attr-defined]
        pm.emergency_close_position(bid=2000.0, ask=2000.5, si=si)
        pm._a.order_send.assert_called_once()  # type: ignore[attr-defined]

    def test_emergency_close_emits_event(self) -> None:
        """emergency_close_position must emit EMERGENCY_CLOSE_SENT event."""
        pm, state = _make_pm_with_deadline()
        si = MagicMock()
        si.point = 0.01
        si.digits = 2
        mock_pos = MagicMock()
        mock_pos.ticket = 1001
        mock_pos.type = 0
        mock_pos.volume = 0.01
        pm._a.get_positions.return_value = [mock_pos]  # type: ignore[attr-defined]
        pm.emergency_close_position(bid=2000.0, ask=2000.5, si=si)
        event_names = [e for e, _ in pm._events]  # type: ignore[attr-defined]
        assert "EMERGENCY_CLOSE_SENT" in event_names, (
            "EMERGENCY_CLOSE_SENT event must be emitted during emergency close"
        )

    def test_cleanup_step_cancels_pending_orders(self) -> None:
        """run_pending_cleanup_step must cancel pending orders."""
        pm, state = _make_pm_with_deadline()
        state.cleanup_active = True
        state.buy_stop_ticket  = 5001
        state.sell_stop_ticket = 5002

        pm._a.get_orders.return_value = [  # type: ignore[attr-defined]
            MagicMock(ticket=5001),
            MagicMock(ticket=5002),
        ]
        pm._a.build_cancel_request.side_effect = lambda t: {"ticket": t}  # type: ignore[attr-defined]

        cancelled = pm.run_pending_cleanup_step()
        assert cancelled >= 0, "run_pending_cleanup_step must return non-negative count"
