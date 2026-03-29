from types import SimpleNamespace
from unittest.mock import MagicMock

from app.src.core.engine_deny_policy import DenyPolicyMixin
from app.src.core.engine_trade_lifecycle import TradeLifecycleMixin
from app.src.core.state import Side, StateStore, TradingState


class _LifecycleHost(TradeLifecycleMixin):
    def __init__(self) -> None:
        self._cfg = {"risk": {"volume": 1.0}}
        self._state = StateStore()
        self._state.position_ticket = 1
        self._state.position_side = Side.BUY
        self._state.entry_price = 100.0
        self._state.position_volume = 1.0
        self._order_mgr = MagicMock()
        self._pos_mgr = MagicMock()
        self._feature_logger = MagicMock()
        self._ledger = MagicMock()
        self._micro_guard = MagicMock()
        self._tg = MagicMock()
        self._run_id = "run"
        self._spec_version = "1.0.0"
        self._spec_hash = "hash"
        self._fake_breakout_enabled = False
        self._position_last_seen_mono = None
        self._trade_entry_spread_pts = 10.0
        self._trade_entry_price_for_record = 100.0
        self._trade_mae = 0.0
        self._trade_mfe = 0.0
        self._active_since_mono_ms = 0.0
        self._early_exit_triggered = False
        self._last_atr_pts = 0.0
        self._last_spread_med_pts = 0.0
        self._last_candle_hi = 0.0
        self._last_candle_lo = 0.0
        self._last_is_flat = False
        self._last_known_bid = 0.0
        self._last_known_ask = 0.0
        self._trade_be_triggered = False
        self._trade_be_time_utc = ""
        self._trade_be_arm_pts = 0.0
        self._trade_be_buffer_pts = 0.0
        self._trade_trail_triggered = False
        self._trade_trail_updates = 0
        self._trade_trail_max_pts = 0.0
        self._trade_critical_flags = []
        self._dir_cooldown_sec = 5.0
        self._dir_cooldown_until_ms = 0.0
        self._dir_cooldown_block_side = None
        self._dir_cooldown_entry_mid = 0.0
        self._cooldown_after_win_sec = 0.0
        self._cooldown_after_loss_sec = 75.0
        self._deny_only_on_loss = True
        self._profit_continuation_window_sec = 4.0
        self._profit_continuation_require_managed_exit = True
        self._profit_continuation_until_ms = 0.0
        self._profit_continuation_side = None
        self._rate_limit_window_sec = 15.0
        self._closed_trade_times = []
        self.logged_events: list[tuple[str, dict]] = []

    def _log_event(self, event: str, data: dict[str, object]) -> None:
        self.logged_events.append((event, data))


class _DenyHost(DenyPolicyMixin):
    def __init__(self) -> None:
        self._cfg = {}
        self._state = StateStore()
        self._session = MagicMock()
        self._session.is_blocked.return_value = (False, "")
        self._max_trades_per_min = 1
        self._rate_limit_window_sec = 15.0
        self._closed_trade_times = []
        self._profit_continuation_until_ms = 10_000.0
        self._profit_continuation_side = Side.BUY
        self._dir_cooldown_until_ms = 0.0
        self._dir_cooldown_block_side = None
        self._dir_cooldown_entry_mid = 0.0
        self._dir_cooldown_burst_atr_mult = 0.45

    def _log_event(self, event: str, data: dict[str, object]) -> None:
        return None


def _si() -> SimpleNamespace:
    return SimpleNamespace(point=0.01, tick_value=0.01, tick_size=0.01, digits=2)


def test_profitable_trailing_exit_sets_same_side_continuation_without_deny() -> None:
    host = _LifecycleHost()
    host._trade_trail_triggered = True

    host._finalize_closed_trade(
        bid=100.50,
        ask=100.55,
        spread_pts=5.0,
        si=_si(),
        reason="sl_or_external",
    )

    assert host._state.state == TradingState.ARMED
    assert host._state.cooldown_until_ms == 0.0
    assert host._profit_continuation_side == Side.BUY
    assert host._profit_continuation_until_ms > 0.0
    assert host._dir_cooldown_block_side is None
    assert host._closed_trade_times == []
    assert any(event == "PROFIT_CONTINUATION_SET" for event, _ in host.logged_events)


def test_losing_exit_keeps_loss_deny_and_does_not_set_continuation() -> None:
    host = _LifecycleHost()

    host._finalize_closed_trade(
        bid=99.50,
        ask=99.55,
        spread_pts=5.0,
        si=_si(),
        reason="sl_or_external",
    )

    assert host._state.state == TradingState.ARMED
    assert host._state.cooldown_until_ms > 0.0
    assert host._state.cooldown_reason == "POST_CLOSE_LOSS"
    assert host._profit_continuation_side is None
    assert host._dir_cooldown_block_side == Side.BUY
    assert len(host._closed_trade_times) == 1


def test_profitable_unmanaged_exit_sets_continuation_when_config_allows_it() -> None:
    host = _LifecycleHost()
    host._profit_continuation_require_managed_exit = False

    host._finalize_closed_trade(
        bid=100.50,
        ask=100.55,
        spread_pts=5.0,
        si=_si(),
        reason="sl_or_external",
    )

    assert host._state.cooldown_until_ms == 0.0
    assert host._profit_continuation_side == Side.BUY
    assert host._profit_continuation_until_ms > 0.0
    assert any(event == "PROFIT_CONTINUATION_SET" for event, _ in host.logged_events)


def test_profit_continuation_blocks_opposite_side_until_window_expires() -> None:
    host = _DenyHost()

    blocked = host._resolve_directional_block(
        bid=100.20,
        ask=100.25,
        atr_points=50.0,
        mono_ms=5_000.0,
        point=0.01,
    )

    assert blocked == Side.SELL

    blocked_after_expiry = host._resolve_directional_block(
        bid=100.20,
        ask=100.25,
        atr_points=50.0,
        mono_ms=11_000.0,
        point=0.01,
    )

    assert blocked_after_expiry is None
    assert host._profit_continuation_side is None