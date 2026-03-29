from types import SimpleNamespace
from unittest.mock import MagicMock

from app.src.core.engine_exit_policy import ExitPolicyMixin
from app.src.core.engine_state_machine import StateMachineMixin
from app.src.core.state import ConfirmContext, StateStore, TradingState


class _Host(StateMachineMixin, ExitPolicyMixin):
    def __init__(self) -> None:
        self._state = StateStore()
        self._order_mgr = MagicMock()
        self._pos_mgr = MagicMock()
        self._position_last_seen_mono = None
        self._fake_breakout_enabled = True
        self._active_since_mono_ms = 0.0
        self._tick_active_last_clock_ms = 0.0
        self._trail_atr_pts = 33.0
        self._early_exit_enabled = True
        self._early_exit_triggered = False
        self._early_exit_window_ms = 1000.0
        self._early_exit_mfe_spread_mult = 2.0
        self._early_exit_mfe_min = 10.0
        self._trade_mfe = 0.0

        self.logged_events: list[tuple[str, dict]] = []
        self.finalize_calls: list[tuple] = []
        self.track_calls: list[tuple] = []
        self.resolve_calls: list[tuple] = []

    def _log_event(self, event: str, data: dict) -> None:
        self.logged_events.append((event, data))

    def _resolve_directional_block(
        self,
        bid: float,
        ask: float,
        atr_points: float,
        mono_ms: float,
        point: float,
    ) -> str | None:
        self.resolve_calls.append((bid, ask, atr_points, mono_ms, point))
        return "BUY"

    def _finalize_closed_trade(
        self,
        bid: float,
        ask: float,
        spread_pts: float,
        si: object,
        close_reason: str,
    ) -> None:
        self.finalize_calls.append((bid, ask, spread_pts, si, close_reason))

    def _track_mfe_mae(self, bid: float, ask: float, si: object) -> None:
        self.track_calls.append((bid, ask, si))


def _spread_res(spread_med_points: float = 12.0, spread_points: float = 15.0) -> SimpleNamespace:
    return SimpleNamespace(spread_med_points=spread_med_points, spread_points=spread_points)


def _atr_res(atr_points: float = 40.0) -> SimpleNamespace:
    return SimpleNamespace(atr_points=atr_points)


def _si(point: float = 0.01) -> SimpleNamespace:
    return SimpleNamespace(point=point)


def test_armed_state_enters_deny_and_cancels_pending() -> None:
    host = _Host()
    host._state.state = TradingState.ARMED

    host._process_armed_or_deny_state(
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        now_ms=1000.0,
        mono_ms=1100.0,
        spread_points=50.0,
        spread_res=_spread_res(),
        atr_res=_atr_res(),
        deny=True,
        deny_reasons=["spread_gate"],
        micro_guard_blocked=False,
    )

    assert host._state.state == TradingState.DENY
    host._order_mgr.cancel_all.assert_called_once()
    assert host.logged_events[0][0] == "DENY"


def test_deny_state_returns_to_armed_and_places_pending() -> None:
    host = _Host()
    host._state.state = TradingState.DENY

    host._process_armed_or_deny_state(
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        now_ms=1000.0,
        mono_ms=1100.0,
        spread_points=50.0,
        spread_res=_spread_res(spread_med_points=9.0),
        atr_res=_atr_res(atr_points=45.0),
        deny=False,
        deny_reasons=[],
        micro_guard_blocked=False,
    )

    assert host._state.state == TradingState.ARMED
    assert len(host.resolve_calls) == 1
    host._order_mgr.ensure_dual_pending.assert_called_once()


def test_confirm_state_finalizes_when_position_disappears() -> None:
    host = _Host()
    host._state.state = TradingState.POSITION_CONFIRM
    host._state.position_ticket = 123

    host._process_confirm_state(
        my_positions=[],
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        mono_ms=1200.0,
        spread_points=50.0,
        spread_res=_spread_res(),
        atr_res=_atr_res(),
    )

    assert host.finalize_calls[0][4] == "sl_hit_during_confirm"
    host._order_mgr.cancel_all.assert_called_once()


def test_armed_state_micro_guard_pause_keeps_pending_and_skips_rearm() -> None:
    host = _Host()
    host._state.state = TradingState.ARMED
    host._state.buy_stop_ticket = 101
    host._state.sell_stop_ticket = 202

    host._process_armed_or_deny_state(
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        now_ms=1000.0,
        mono_ms=1100.0,
        spread_points=50.0,
        spread_res=_spread_res(spread_med_points=9.0),
        atr_res=_atr_res(atr_points=45.0),
        deny=False,
        deny_reasons=[],
        micro_guard_blocked=True,
    )

    host._order_mgr.cancel_all.assert_not_called()
    host._order_mgr.ensure_dual_pending.assert_not_called()
    assert host._state.buy_stop_ticket == 101
    assert host._state.sell_stop_ticket == 202


def test_confirm_state_promotes_to_active_when_fake_breakout_disabled() -> None:
    host = _Host()
    host._state.state = TradingState.POSITION_CONFIRM
    host._state.position_ticket = 123
    host._state.confirm = ConfirmContext(finished=True, success=False)
    host._fake_breakout_enabled = False

    host._process_confirm_state(
        my_positions=[SimpleNamespace(ticket=123)],
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        mono_ms=2200.0,
        spread_points=50.0,
        spread_res=_spread_res(),
        atr_res=_atr_res(),
    )

    assert host._state.state == TradingState.POSITION_ACTIVE
    assert host._active_since_mono_ms == 2200.0
    host._pos_mgr.set_position_start_ms.assert_called_once_with(2200.0)


def test_active_state_triggers_early_exit_when_no_followthrough() -> None:
    host = _Host()
    host._state.state = TradingState.POSITION_ACTIVE
    host._state.position_ticket = 123
    host._active_since_mono_ms = 1000.0
    host._trade_mfe = 5.0
    host._pos_mgr.close_position_market.return_value = True

    host._process_active_state(
        my_positions=[SimpleNamespace(ticket=123)],
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        mono_ms=2501.0,
        spread_points=50.0,
        spread_res=_spread_res(spread_med_points=6.0),
        atr_res=_atr_res(),
    )

    assert host._early_exit_triggered is True
    assert host.logged_events[0][0] == "EARLY_EXIT_NO_FOLLOWTHROUGH"
    host._pos_mgr.close_position_market.assert_called_once()


def test_run_state_machine_promotes_idle_to_armed_before_dispatch() -> None:
    host = _Host()
    host._state.running = True
    host._state.state = TradingState.IDLE

    host._run_state_machine(
        my_positions=[],
        bid=2000.0,
        ask=2000.5,
        si=_si(),
        now_ms=1000.0,
        mono_ms=1200.0,
        spread_points=50.0,
        spread_res=_spread_res(),
        atr_res=_atr_res(),
        deny=False,
        deny_reasons=[],
        micro_guard_blocked=False,
    )

    assert host._state.state == TradingState.ARMED
    host._order_mgr.ensure_dual_pending.assert_called_once()