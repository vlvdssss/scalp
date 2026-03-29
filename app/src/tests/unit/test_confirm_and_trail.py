"""
Unit tests for Confirm-after-Fill logic (PositionManager.tick_confirm).
Uses Mock MT5Adapter – no real terminal required.
"""
import pytest
from unittest.mock import MagicMock, patch
from app.src.core.state import StateStore, TradingState, Side, ConfirmContext
from app.src.core.risk import ConfirmConfig, BEConfig, TrailConfig
from app.src.core.position_manager import PositionManager, PositionConfig
from app.src.adapters.mt5_adapter import PositionSnapshot, SymbolSnapshot


def _make_si() -> SymbolSnapshot:
    return SymbolSnapshot(
        name="XAUUSD", point=0.01, tick_size=0.01, tick_value=0.01,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=30, trade_freeze_level=0, digits=2, spread=5, trade_mode=4,
    )


def _make_pos_mgr(state: StateStore, events: list) -> PositionManager:
    adapter = MagicMock()
    cfg = PositionConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    conf_cfg = ConfirmConfig(
        window_ms=2000, window_ticks=8,
        k_confirm_atr=0.10, k_confirm_spread=0.50, confirm_min_points=10.0,
        cooldown_on_fail_sec=300.0,
    )
    be_cfg  = BEConfig()
    trail_cfg = TrailConfig()

    def on_event(ev, d):
        events.append((ev, d))

    return PositionManager(adapter, state, cfg, conf_cfg, be_cfg, trail_cfg, event_cb=on_event)


class TestConfirmSuccess:
    def test_confirm_success_by_move(self):
        """LONG: bid moves +20 pts (> threshold 10) → CONFIRM_SUCCESS."""
        state = StateStore()
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.confirm       = ConfirmContext(start_monotonic_ms=0.0)

        events = []
        pm  = _make_pos_mgr(state, events)
        si  = _make_si()

        # bid = entry + 20 pts = 1900.20
        pm.tick_confirm(1900.20, 1900.25, atr_pts=100.0, spread_med_pts=20.0,
                        si=si, mono_ms=500.0)

        assert state.state == TradingState.POSITION_ACTIVE
        assert state.confirm.success is True
        assert any(e[0] == "CONFIRM_SUCCESS" for e in events)

    def test_confirm_success_by_short_move(self):
        """SHORT: ask moves -20 pts below entry → CONFIRM_SUCCESS."""
        state = StateStore()
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.SELL
        state.entry_price   = 1900.00
        state.confirm       = ConfirmContext(start_monotonic_ms=0.0)

        events = []
        pm = _make_pos_mgr(state, events)
        si = _make_si()

        # ask = entry - 20 pts = 1899.80
        pm.tick_confirm(1899.75, 1899.80, atr_pts=100.0, spread_med_pts=20.0,
                        si=si, mono_ms=500.0)

        assert state.confirm.success is True


class TestConfirmFail:
    def test_fail_by_time_window(self):
        """Window expires (elapsed_ms >= window_ms) without move → FAKE_BREAKOUT."""
        state = StateStore()
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.confirm       = ConfirmContext(start_monotonic_ms=0.0)
        state.position_ticket = 1

        events = []
        pm = _make_pos_mgr(state, events)
        si = _make_si()

        # bid barely moves (1 pt < threshold 10)
        pm.tick_confirm(1900.01, 1900.06, atr_pts=100.0, spread_med_pts=20.0,
                        si=si, mono_ms=2001.0)  # elapsed > 2000ms

        assert state.confirm.finished is True
        assert state.confirm.success  is False
        assert any(e[0] == "FAKE_BREAKOUT" for e in events)

    def test_fail_by_tick_window(self):
        """8 ticks without sufficient move → FAKE_BREAKOUT."""
        state = StateStore()
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.confirm       = ConfirmContext(start_monotonic_ms=0.0)
        state.position_ticket = 1

        events = []
        pm = _make_pos_mgr(state, events)
        si = _make_si()

        # Feed 8 ticks with 1pt progress (below threshold)
        for i in range(8):
            if not state.confirm.finished:
                pm.tick_confirm(1900.01, 1900.06, atr_pts=100.0, spread_med_pts=20.0,
                                si=si, mono_ms=float(i * 100))

        assert state.confirm.finished is True
        assert state.confirm.success  is False

    def test_window_end_whichever_first(self):
        """Window ends at min(elapsed >= window_ms, ticks >= window_ticks)."""
        state = StateStore()
        state.state = TradingState.POSITION_CONFIRM
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.confirm       = ConfirmContext(start_monotonic_ms=0.0)
        state.position_ticket = 1

        events = []
        pm = _make_pos_mgr(state, events)
        si = _make_si()

        # 3 ticks, elapsed < 2000ms but bid doesn't move → not finished yet
        for i in range(3):
            pm.tick_confirm(1900.01, 1900.06, atr_pts=100.0, spread_med_pts=20.0,
                            si=si, mono_ms=float(i * 100))
        # Still within window
        assert state.confirm.finished is False

        # elapsed jumps > 2000ms → triggers
        pm.tick_confirm(1900.01, 1900.06, atr_pts=100.0, spread_med_pts=20.0,
                        si=si, mono_ms=2100.0)
        assert state.confirm.finished is True


class TestTrailingThrottle:
    def test_sl_not_updated_within_throttle(self):
        """SL must not be modified more than once per throttle_sec."""
        from app.src.core.position_manager import PositionManager
        from app.src.core.risk import TrailConfig

        state = StateStore()
        state.state         = TradingState.POSITION_ACTIVE
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.position_ticket = 99
        state.be_done       = True
        state.extreme_price = 1910.00
        state.current_sl    = 1905.00
        state.last_trailing_update_mono = 1000.0  # last update 1 second ago

        adapter = MagicMock()
        adapter.order_send.return_value = MagicMock(retcode=10009)  # RC_DONE
        si = _make_si()

        trail_cfg = TrailConfig(throttle_sec=15.0)
        pm = PositionManager(adapter, state,
                             PositionConfig(), ConfirmConfig(),
                             BEConfig(), trail_cfg,
                             event_cb=lambda e, d: None)

        # mono_ms = 1500ms → only 0.5s since last update (< 15s throttle)
        pm._update_trailing(1910.00, 1910.10, si, mono_ms=1500.0)
        adapter.order_send.assert_not_called()

    def test_sl_updated_after_throttle(self):
        """SL should be modified after throttle period."""
        state = StateStore()
        state.state         = TradingState.POSITION_ACTIVE
        state.position_side = Side.BUY
        state.entry_price   = 1900.00
        state.position_ticket = 99
        state.be_done       = True
        state.extreme_price = 1950.00
        state.current_sl    = 1900.00
        state.last_trailing_update_mono = 0.0

        adapter = MagicMock()
        adapter.order_send.return_value = MagicMock(retcode=10009)
        si = _make_si()

        trail_cfg = TrailConfig(trail_stop_points=50.0, trail_step_points=20.0, throttle_sec=15.0)
        pm = PositionManager(adapter, state, PositionConfig(), ConfirmConfig(),
                             BEConfig(), trail_cfg, event_cb=lambda e, d: None)

        # 20 seconds since last update – should trigger
        pm._update_trailing(1950.00, 1950.10, si, mono_ms=20000.0)
        adapter.order_send.assert_called_once()

    def test_classic_trailing_moves_beyond_be_after_breakeven(self):
        """After BE is done, classic trailing should keep following price with a gap."""
        state = StateStore()
        state.state = TradingState.POSITION_ACTIVE
        state.position_side = Side.BUY
        state.entry_price = 1900.00
        state.position_ticket = 99
        state.be_done = True
        state.extreme_price = 1912.00
        state.current_sl = 1906.20
        state.last_trailing_update_mono = 0.0

        adapter = MagicMock()
        adapter.order_send.return_value = MagicMock(retcode=10009)
        si = _make_si()

        trail_cfg = TrailConfig(trail_stop_points=50.0, trail_step_points=20.0, throttle_sec=0.4)
        pm = PositionManager(
            adapter,
            state,
            PositionConfig(),
            ConfirmConfig(),
            BEConfig(),
            trail_cfg,
            event_cb=lambda e, d: None,
        )

        pm._update_trailing(1915.00, 1915.10, si, mono_ms=20000.0)

        adapter.order_send.assert_called_once()
        assert state.current_sl > 1906.20

    def test_trailing_can_start_before_global_hold_guard_when_trailing_hold_elapsed(self):
        """A dedicated trailing hold should allow SL follow-up before the broader BE hold window ends."""
        state = StateStore()
        state.state = TradingState.POSITION_ACTIVE
        state.position_side = Side.BUY
        state.entry_price = 1900.00
        state.position_ticket = 99
        state.be_done = True
        state.extreme_price = 1912.00
        state.current_sl = 1906.20
        state.last_trailing_update_mono = 0.0

        adapter = MagicMock()
        adapter.order_send.return_value = MagicMock(retcode=10009)
        si = _make_si()

        events = []
        trail_cfg = TrailConfig(trail_stop_points=50.0, trail_step_points=20.0, throttle_sec=0.12)
        be_cfg = BEConfig(min_hold_ms=2000.0)
        pm = PositionManager(
            adapter,
            state,
            PositionConfig(),
            ConfirmConfig(),
            be_cfg,
            trail_cfg,
            event_cb=lambda e, d: events.append((e, d)),
        )
        pm.set_position_start_ms(100.0)

        pm.tick_active(1915.00, 1915.10, 100.0, 20.0, si, mono_ms=800.0)

        adapter.order_send.assert_called_once()
        assert state.current_sl > 1906.20
        assert not any(event == "HOLD_GUARD_ACTIVE" for event, _ in events)


@pytest.mark.skip(reason="LOCK_RUN trailing mode removed; replaced by simple fixed-point trailing")
class TestLockRunTrailing:
    def test_lock_run_does_partial_close_then_runner_be(self):
        state = StateStore()
        state.state = TradingState.POSITION_ACTIVE
        state.position_side = Side.BUY
        state.entry_price = 1900.00
        state.position_ticket = 77
        state.initial_sl_points = 20.0
        state.current_sl = 1895.00

        adapter = MagicMock()
        adapter.order_send.side_effect = [
            MagicMock(retcode=10009, price=1900.55),
            MagicMock(retcode=10009),
        ]
        adapter.build_partial_close_request.return_value = {"req": "partial"}
        adapter.build_modify_sl_request.return_value = {"req": "modify"}
        adapter.get_positions.return_value = [
            PositionSnapshot(
                ticket=77,
                type=0,
                symbol="XAUUSD",
                volume=0.10,
                price_open=1900.00,
                sl=1895.00,
                tp=0.0,
                profit=0.0,
                magic=20260225,
                comment="",
                time=0,
            )
        ]
        si = _make_si()

        events = []
        trail_cfg = TrailConfig(
            trailing_mode="LOCK_RUN",
            activation_R=1.0,
            partial_close_pct=0.5,
            be_buffer_mult_spread=1.5,
            min_be_points_lock=30.0,
            throttle_sec=0.0,
        )
        pm = PositionManager(
            adapter,
            state,
            PositionConfig(),
            ConfirmConfig(),
            BEConfig(),
            trail_cfg,
            event_cb=lambda ev, data: events.append((ev, data)),
        )

        pm._update_trailing(1900.30, 1900.31, si, mono_ms=20000.0)

        assert pm._partial_close_done is True
        assert pm._runner_be_done is True
        assert state.current_sl == 1900.30
        assert adapter.order_send.call_count == 2
        assert any(ev == "APT_PARTIAL_CLOSE_DONE" for ev, _ in events)
        assert any(ev == "APT_RUNNER_BE_SET" for ev, _ in events)
