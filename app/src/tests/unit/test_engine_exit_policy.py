from unittest.mock import MagicMock

from app.src.core.engine_exit_policy import ExitPolicyMixin
from app.src.core.state import StateStore


class _Host(ExitPolicyMixin):
    def __init__(self) -> None:
        self._state = StateStore()
        self._pos_mgr = MagicMock()
        self._early_exit_enabled = True
        self._early_exit_triggered = False
        self._early_exit_window_ms = 1000.0
        self._early_exit_mfe_spread_mult = 2.0
        self._early_exit_mfe_min = 10.0
        self._active_since_mono_ms = 1000.0
        self._trade_mfe = 0.0
        self.events: list[tuple[str, dict]] = []

    def _log_event(self, event: str, data: dict) -> None:
        self.events.append((event, data))


def test_early_exit_triggers_close_when_followthrough_is_too_weak() -> None:
    host = _Host()
    host._pos_mgr.close_position_market.return_value = True

    host._apply_early_exit_policy(
        bid=2000.0,
        ask=2000.5,
        si=MagicMock(),
        mono_ms=2500.0,
        spread_med_points=4.0,
    )

    assert host._early_exit_triggered is True
    assert host.events[0][0] == "EARLY_EXIT_NO_FOLLOWTHROUGH"
    host._pos_mgr.close_position_market.assert_called_once()


def test_early_exit_skips_when_trade_already_has_enough_mfe() -> None:
    host = _Host()
    host._trade_mfe = 20.0

    host._apply_early_exit_policy(
        bid=2000.0,
        ask=2000.5,
        si=MagicMock(),
        mono_ms=2500.0,
        spread_med_points=4.0,
    )

    assert host._early_exit_triggered is False
    assert host.events == []
    host._pos_mgr.close_position_market.assert_not_called()


def test_early_exit_skips_when_be_already_done() -> None:
    host = _Host()
    host._state.be_done = True

    host._apply_early_exit_policy(
        bid=2000.0,
        ask=2000.5,
        si=MagicMock(),
        mono_ms=2500.0,
        spread_med_points=4.0,
    )

    assert host._early_exit_triggered is False
    assert host.events == []
    host._pos_mgr.close_position_market.assert_not_called()