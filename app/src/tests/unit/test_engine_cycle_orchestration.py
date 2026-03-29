from types import SimpleNamespace
from unittest.mock import MagicMock

from app.src.core.engine_cycle_orchestration import CycleOrchestrationMixin
from app.src.core.state import StateStore, TradingState


class _Host(CycleOrchestrationMixin):
    def __init__(self) -> None:
        self._cfg = {"symbol": {"magic": 777}}
        self._state = StateStore()
        self._adapter = MagicMock()
        self._order_mgr = MagicMock()
        self._double_trigger_ms = 500.0
        self._position_last_seen_mono = None

        self.logged_events: list[tuple[str, dict]] = []
        self.safe_mode_reasons: list[str] = []
        self.alignment_calls: list[tuple] = []
        self.fill_calls: list[tuple] = []
        self.double_trigger_calls: list[tuple] = []
        self.api_symptom_checks: list[tuple[list, list]] = []

    def _log_event(self, event: str, data: dict) -> None:
        self.logged_events.append((event, data))

    def _check_api_restriction_symptoms(self, live_positions: list, live_orders: list) -> None:
        self.api_symptom_checks.append((live_positions, live_orders))

    def _enter_safe_mode(self, reason: str) -> None:
        self.safe_mode_reasons.append(reason)

    def _alignment_procedure(
        self,
        positions: list,
        orders: list,
        bid: float,
        ask: float,
        si: object,
    ) -> None:
        self.alignment_calls.append((positions, orders, bid, ask, si))

    def _handle_fill(
        self,
        pos: object,
        bid: float,
        ask: float,
        spread_pts: float,
        si: object,
        now_ms: float,
        mono_ms: float,
    ) -> None:
        self.fill_calls.append((pos, bid, ask, spread_pts, si, now_ms, mono_ms))

    def _handle_double_trigger(
        self,
        positions: list,
        orders: list,
        bid: float,
        ask: float,
        si: object,
    ) -> None:
        self.double_trigger_calls.append((positions, orders, bid, ask, si))


def _position(ticket: int, magic: int = 777, pos_type: int = 0, volume: float = 0.01) -> SimpleNamespace:
    return SimpleNamespace(ticket=ticket, magic=magic, type=pos_type, volume=volume)


def _order(ticket: int, magic: int = 777) -> SimpleNamespace:
    return SimpleNamespace(ticket=ticket, magic=magic)


def test_reconcile_inv_a_enters_safe_mode_and_stops() -> None:
    host = _Host()
    positions = [_position(1), _position(2)]
    orders = [_order(11)]

    result = host._reconcile_terminal_state(positions, orders, bid=2000.0, ask=2000.5, si=object())

    assert result is None
    assert host.safe_mode_reasons == ["INV_A_multi_position"]
    assert len(host.alignment_calls) == 1
    assert host.logged_events[0][0] == "CRITICAL_INV_A_MULTI_POSITION"


def test_reconcile_inv_c_cancels_pending_and_resets_pending_state() -> None:
    host = _Host()
    host._state.buy_stop_ticket = 101
    host._state.sell_stop_ticket = 202
    position = _position(1)
    orders = [_order(11), _order(12)]
    host._adapter.build_cancel_request.side_effect = lambda ticket: {"ticket": ticket}

    result = host._reconcile_terminal_state([position], orders, bid=2000.0, ask=2000.5, si=object())

    assert result is not None
    my_positions, my_orders = result
    assert my_positions == [position]
    assert my_orders == orders
    assert host._adapter.order_send.call_count == 2
    assert host._state.buy_stop_ticket is None
    assert host._state.sell_stop_ticket is None
    assert host.logged_events[0][0] == "CRITICAL_PENDING_WITH_POSITION"
    host._order_mgr.reconcile_with_terminal.assert_called_once_with(orders)


def test_reconcile_clears_ghost_position_outside_active_states() -> None:
    host = _Host()
    host._state.position_ticket = 999
    host._state.state = TradingState.IDLE

    result = host._reconcile_terminal_state([], [], bid=2000.0, ask=2000.5, si=object())

    assert result == ([], [])
    assert host._state.position_ticket is None
    assert host._state.state == TradingState.ARMED
    assert host.logged_events[0][0] == "GHOST_POSITION_CLEARED"


def test_process_fill_and_double_trigger_calls_fill_for_armed_state() -> None:
    host = _Host()
    host._state.state = TradingState.ARMED
    position = _position(1)

    handled = host._process_fill_and_double_trigger(
        my_positions=[position],
        my_orders=[],
        bid=2000.0,
        ask=2000.5,
        si=object(),
        now_ms=1000.0,
        mono_ms=1200.0,
        spread_points=50.0,
    )

    assert handled is False
    assert len(host.fill_calls) == 1
    assert host.fill_calls[0][0] == position


def test_process_fill_and_double_trigger_triggers_double_guard() -> None:
    host = _Host()
    host._state.state = TradingState.POSITION_CONFIRM
    host._state.first_fill_utc_ms = 1000.0
    positions = [_position(1), _position(2)]
    orders = [_order(11)]

    handled = host._process_fill_and_double_trigger(
        my_positions=positions,
        my_orders=orders,
        bid=2000.0,
        ask=2000.5,
        si=object(),
        now_ms=1200.0,
        mono_ms=1300.0,
        spread_points=50.0,
    )

    assert handled is True
    assert len(host.double_trigger_calls) == 1
    assert host.double_trigger_calls[0][0] == positions
    assert host.double_trigger_calls[0][1] == orders