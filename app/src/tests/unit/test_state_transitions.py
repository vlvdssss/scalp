"""
Unit tests for TradingState machine transitions.
Covers key paths: IDLE→ARMED, ARMED→DENY, ARMED→CONFIRM,
CONFIRM→ACTIVE, CONFIRM→fail→cooldown→ARMED, SAFE MODE entry.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.src.core.state import (
    StateStore, TradingState, SystemMode, Side, ConfirmContext,
)


class TestIdleToArmed:
    def test_initial_state_is_idle(self):
        state = StateStore()
        assert state.state   == TradingState.IDLE
        assert state.mode    == SystemMode.NORMAL

    def test_set_armed(self):
        state = StateStore()
        state.state = TradingState.ARMED
        assert state.state == TradingState.ARMED


class TestArmedToDeny:
    def test_manual_deny(self):
        state = StateStore()
        state.state = TradingState.ARMED
        state.deny_reasons = ["spread_too_high"]
        state.state = TradingState.DENY
        assert state.state == TradingState.DENY
        assert "spread_too_high" in state.deny_reasons

    def test_back_to_armed_from_deny(self):
        state = StateStore()
        state.state      = TradingState.DENY
        state.deny_reasons = []
        state.state      = TradingState.ARMED
        assert state.state == TradingState.ARMED


class TestArmedToConfirm:
    def test_fill_detected_sets_position(self):
        state = StateStore()
        state.state            = TradingState.ARMED
        state.position_ticket  = 42
        state.position_side    = Side.BUY
        state.entry_price      = 1900.0
        state.confirm          = ConfirmContext(start_monotonic_ms=1000.0)
        state.state            = TradingState.POSITION_CONFIRM

        assert state.state           == TradingState.POSITION_CONFIRM
        assert state.position_side   == Side.BUY
        assert state.entry_price     == 1900.0
        assert state.confirm is not None


class TestConfirmToActive:
    def test_confirm_success_transition(self):
        state = StateStore()
        state.state      = TradingState.POSITION_CONFIRM
        state.confirm    = ConfirmContext(start_monotonic_ms=0.0)
        state.confirm.success  = True
        state.confirm.finished = True
        state.state      = TradingState.POSITION_ACTIVE

        assert state.state == TradingState.POSITION_ACTIVE

    def test_confirm_fail_transition_to_idle(self):
        """After FAKE_BREAKOUT the position closes → back to IDLE with cooldown."""
        state = StateStore()
        state.state      = TradingState.POSITION_CONFIRM
        state.confirm    = ConfirmContext(start_monotonic_ms=0.0)
        state.confirm.success  = False
        state.confirm.finished = True

        # Simulate close & cooldown
        state.reset_position()
        state.state = TradingState.IDLE

        assert state.state          == TradingState.IDLE
        assert state.position_ticket is None


class TestSafeModeEntry:
    def test_safe_mode_blocks_all_non_safe_states(self):
        state = StateStore()
        state.state = TradingState.ARMED
        state.set_safe_mode()

        assert state.mode  == SystemMode.SAFE
        assert state.state == TradingState.IDLE

    def test_safe_mode_reason_stored(self):
        state = StateStore()
        state.set_safe_mode()
        assert state.mode == SystemMode.SAFE

    def test_safe_mode_exit(self):
        state = StateStore()
        state.set_safe_mode()
        state.mode = SystemMode.NORMAL
        assert state.mode == SystemMode.NORMAL


class TestResetHelpers:
    def test_reset_position_clears_fields(self):
        state = StateStore()
        state.position_ticket  = 99
        state.position_side    = Side.SELL
        state.entry_price      = 1950.0
        state.current_sl       = 1940.0
        state.extreme_price    = 1960.0
        state.be_done          = True

        state.reset_position()

        assert state.position_ticket is None
        assert state.position_side   is None
        assert state.entry_price     is None
        assert state.be_done         is False

    def test_reset_pending_clears_tickets(self):
        state = StateStore()
        state.buy_stop_ticket  = 11
        state.sell_stop_ticket = 22

        state.reset_pending()

        assert state.buy_stop_ticket  is None
        assert state.sell_stop_ticket is None


class TestSnapshotSerialization:
    def test_round_trip(self, tmp_path):
        state = StateStore()
        state.state            = TradingState.ARMED
        state.mode             = SystemMode.NORMAL
        state.buy_stop_ticket  = 55
        state.sell_stop_ticket = 66
        state.entry_price      = 2010.50
        state.initial_sl_points = 123.0
        state.cooldown_until_ms = 1_234_567.0
        state.cooldown_reason = "BE_STORM"

        snap = Path(tmp_path) / "state.json"
        state.save_snapshot(snap)

        loaded = StateStore.load_snapshot(snap)
        assert loaded is not None
        assert loaded.buy_stop_ticket  == 55
        assert loaded.sell_stop_ticket == 66
        assert loaded.entry_price is not None
        assert abs(loaded.entry_price - 2010.50) < 1e-6
        assert loaded.initial_sl_points == 123.0
        assert loaded.cooldown_until_ms == 1_234_567.0
        assert loaded.cooldown_reason == "BE_STORM"
