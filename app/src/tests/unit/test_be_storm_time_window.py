"""
Unit tests for P0-5: BE-storm detection using rolling time window.
BEStormTracker.count_in_window() should only count events within the window.
"""
from __future__ import annotations

import pytest
from app.src.core.engine_safety_hooks import SafetyHooksMixin
from app.src.core.state import BEStormTracker


ONE_MIN_MS = 60 * 1000.0


class TestBEStormTimeWindow:
    def _make_tracker(self) -> BEStormTracker:
        return BEStormTracker()

    def test_no_events_count_zero(self):
        t = self._make_tracker()
        now = 1_000_000.0
        assert t.count_in_window(now, 30 * ONE_MIN_MS) == 0

    def test_single_recent_event_counted(self):
        t = self._make_tracker()
        now = 1_000_000.0
        t.add(now - 60_000)  # 1 minute ago
        assert t.count_in_window(now, 30 * ONE_MIN_MS) == 1

    def test_events_outside_window_not_counted(self):
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        # Add an event well outside the window
        t.add(now - window_ms - 1)
        assert t.count_in_window(now, window_ms) == 0

    def test_events_exactly_at_boundary_counted(self):
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        # Event exactly at cutoff boundary (now - window_ms)
        t.add(now - window_ms)
        assert t.count_in_window(now, window_ms) == 1

    def test_three_events_in_window_triggers_threshold(self):
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        # Three events within window
        for i in range(3):
            t.add(now - (i + 1) * ONE_MIN_MS)
        count = t.count_in_window(now, window_ms)
        assert count == 3
        assert count >= 3  # would trigger cooldown (n_be=3 threshold)

    def test_mixed_events_only_recent_counted(self):
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        # 2 old events (outside window) + 2 recent (inside window)
        t.add(now - 60 * ONE_MIN_MS)   # 60 min ago – outside
        t.add(now - 45 * ONE_MIN_MS)   # 45 min ago – outside
        t.add(now - 10 * ONE_MIN_MS)   # 10 min ago – inside
        t.add(now - 5  * ONE_MIN_MS)   # 5 min ago  – inside
        assert t.count_in_window(now, window_ms) == 2

    def test_old_events_pruned_after_count_in_window(self):
        """count_in_window() must prune old events from internal list."""
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        t.add(now - 60 * ONE_MIN_MS)   # will be pruned
        t.add(now - 10 * ONE_MIN_MS)   # will be kept
        t.count_in_window(now, window_ms)
        # After prune, only 1 event should remain
        assert len(t.events_utc_ms) == 1

    def test_no_cooldown_if_events_outside_window(self):
        """Even with many events, if they're old they shouldn't trigger the storm."""
        t = self._make_tracker()
        now = 1_000_000.0
        window_ms = 30 * ONE_MIN_MS
        n_be = 3
        # Add 10 events all outside window
        for i in range(10):
            t.add(now - (31 + i) * ONE_MIN_MS)
        count = t.count_in_window(now, window_ms)
        assert count < n_be  # should be 0, certainly not enough to trigger storm

    def test_be_storm_cooldown_enters_after_n_events_in_window(self):
        """Integration: state.be_storm after n_be events in window → count >= n_be."""
        from app.src.core.state import StateStore, BEStormTracker
        state = StateStore()
        n_be = 3
        window_min = 30
        window_ms = window_min * ONE_MIN_MS
        now = 1_000_000.0
        # Add n_be events within window
        for i in range(n_be):
            state.be_storm.add(now - (i + 1) * ONE_MIN_MS)  # 1,2,3 min ago
        count = state.be_storm.count_in_window(now, window_ms)
        assert count >= n_be

    def test_be_storm_disabled_clears_existing_cooldown(self):
        from app.src.core.state import StateStore

        class _Host(SafetyHooksMixin):
            def __init__(self) -> None:
                self._cfg = {}
                self._adapter = None
                self._order_mgr = None
                self._trade_critical_flags = []
                self._af = {
                    "be_storm": {
                        "n_be": 3,
                        "window_min": 30,
                        "cooldown_min": 0,
                    }
                }
                self._state = StateStore()
                self.logged: list[tuple[str, dict]] = []

            def _log_event(self, event: str, data: dict) -> None:
                self.logged.append((event, data))

            def _enter_safe_mode(self, reason: str) -> None:
                raise AssertionError(f"unexpected safe mode: {reason}")

        host = _Host()
        now = 1_000_000.0
        host._state.set_cooldown(3600, now, reason="BE_STORM")

        host._check_be_storm(now)

        assert host._state.cooldown_until_ms == 0.0
        assert host._state.cooldown_reason == ""
        assert host.logged == [("COOLDOWN_CLEARED", {"reason": "BE_STORM_DISABLED"})]
