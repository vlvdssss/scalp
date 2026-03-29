from types import SimpleNamespace
from unittest.mock import MagicMock

from app.src.core.engine_control_plane import ControlPlaneMixin
from app.src.core.state import StateStore


class _Host(ControlPlaneMixin):
    def __init__(self) -> None:
        self._adapter = MagicMock()
        self._state = StateStore()
        self._tg = SimpleNamespace(
            notify_breakeven=MagicMock(),
            notify_confirm_success=MagicMock(),
            notify_fake_breakout=MagicMock(),
        )
        self._ledger = MagicMock()
        self._ui_events: list[tuple[str, dict]] = []
        self._ui_cb = lambda event, data: self._ui_events.append((event, data))
        self._snapshot_path = None
        self._spec_version = "1.0.0"
        self._spec_hash = "testspec"
        self._run_id = "run-1"
        self._trade_trail_triggered = False
        self._trade_trail_updates = 0
        self._trade_trail_max_pts = 0.0
        self._trade_be_triggered = False
        self._trade_be_time_utc = ""
        self._trade_be_arm_pts = 0.0
        self._trade_be_buffer_pts = 0.0
        self._trade_critical_flags = []
        self.logged_events: list[tuple[str, dict]] = []

    def _log_event(self, event: str, data: dict) -> None:
        self.logged_events.append((event, data))

    def _handle_core_command(self, cmd) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def _enter_safe_mode(self, reason: str) -> None:
        raise NotImplementedError


def test_be_moved_marks_breakeven_bookkeeping() -> None:
    host = _Host()

    host._on_position_event(
        "BE_MOVED",
        {"sl": 5077.83, "be_arm_points": 233.7, "be_buffer_points": 35.0},
    )

    assert host._trade_be_triggered is True
    assert host._trade_be_arm_pts == 233.7
    assert host._trade_be_buffer_pts == 35.0
    assert host._state.be_trigger_count == 1
    host._tg.notify_breakeven.assert_called_once_with(5077.83)


def test_apt_trail_updates_only_when_move_happens() -> None:
    host = _Host()

    host._on_position_event("APT_TRAIL_UPDATE", {"moved": False, "profit_points": 220.0})
    assert host._trade_trail_triggered is False
    assert host._trade_trail_updates == 0

    host._on_position_event("APT_TRAIL_UPDATE", {"moved": True, "profit_points": 220.0})
    assert host._trade_trail_triggered is True
    assert host._trade_trail_updates == 1
    assert host._trade_trail_max_pts == 220.0

    host._on_position_event("TRAIL_UPDATE", {"profit_pts": 180.0})
    assert host._trade_trail_updates == 2
    assert host._trade_trail_max_pts == 220.0


def test_runner_be_sets_both_be_and_trail_flags() -> None:
    host = _Host()

    host._on_position_event("APT_RUNNER_BE_SET", {"sl": 5081.2, "be_buf_pts": 30.0, "profit_pts": 76.0})

    assert host._trade_be_triggered is True
    assert host._trade_be_buffer_pts == 30.0
    assert host._trade_trail_triggered is True
    assert host._trade_trail_updates == 1
    assert host._trade_trail_max_pts == 76.0