from unittest.mock import MagicMock

from app.src.adapters.mt5_adapter import SymbolSnapshot
from app.src.core.order_manager import OrderManager, PendingConfig
from app.src.core.risk import EntryConfig
from app.src.core.state import StateStore


def _make_si() -> SymbolSnapshot:
    return SymbolSnapshot(
        name="XAUUSD",
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_stops_level=30,
        trade_freeze_level=0,
        digits=2,
        spread=25,
        trade_mode=4,
    )


def test_final_order_distance_respects_min_total_floor() -> None:
    state = StateStore()
    adapter = MagicMock()
    adapter.build_buy_stop_request.side_effect = lambda **kwargs: kwargs
    adapter.build_sell_stop_request.side_effect = lambda **kwargs: kwargs
    adapter.order_send.side_effect = [
        MagicMock(retcode=10008, order=1),
        MagicMock(retcode=10008, order=2),
    ]

    entry_cfg = EntryConfig(
        k_entry_atr=0.0,
        k_entry_spread=0.0,
        entry_offset_min_points=55.0,
        idle_offset_spread_mult=0.0,
        entry_buffer_enabled=False,
        orders_expand_points=12.0,
        min_total_offset_points=90.0,
        min_order_age_ms=0.0,
        rearm_hysteresis_pts=0.0,
    )
    pending_cfg = PendingConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    manager = OrderManager(adapter, state, entry_cfg, pending_cfg)
    si = _make_si()

    manager.ensure_dual_pending(
        tick_bid=5071.90,
        tick_ask=5072.15,
        atr_pts=100.0,
        spread_med_pts=25.0,
        si=si,
        now_ms=1000.0,
    )

    assert state.buy_stop_price == 5073.05
    assert state.sell_stop_price == 5071.00


def test_rearm_modifies_pending_in_place_without_cancel_gap() -> None:
    state = StateStore()
    state.buy_stop_ticket = 11
    state.buy_stop_price = 5073.00
    adapter = MagicMock()
    adapter.build_modify_pending_request.side_effect = lambda **kwargs: kwargs
    adapter.order_send.return_value = MagicMock(retcode=10009, order=11)

    entry_cfg = EntryConfig(
        k_entry_atr=0.0,
        k_entry_spread=0.0,
        entry_offset_min_points=60.0,
        idle_offset_spread_mult=0.0,
        entry_buffer_enabled=False,
        orders_expand_points=0.0,
        min_total_offset_points=60.0,
        min_order_age_ms=0.0,
        rearm_hysteresis_pts=0.0,
    )
    pending_cfg = PendingConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    manager = OrderManager(adapter, state, entry_cfg, pending_cfg)
    si = _make_si()

    manager.ensure_dual_pending(
        tick_bid=5072.90,
        tick_ask=5073.10,
        atr_pts=100.0,
        spread_med_pts=25.0,
        si=si,
        now_ms=2000.0,
    )

    adapter.build_modify_pending_request.assert_called_once()
    adapter.build_cancel_request.assert_not_called()
    assert state.buy_stop_ticket == 11
    assert state.buy_stop_price is not None


def test_rearm_falls_back_to_cancel_and_replace_when_modify_fails() -> None:
    state = StateStore()
    state.buy_stop_ticket = 11
    state.buy_stop_price = 5073.00
    adapter = MagicMock()
    adapter.build_modify_pending_request.side_effect = lambda **kwargs: kwargs
    adapter.build_cancel_request.side_effect = lambda ticket: {"order": ticket}
    adapter.build_buy_stop_request.side_effect = lambda **kwargs: kwargs
    adapter.build_sell_stop_request.side_effect = lambda **kwargs: kwargs
    adapter.order_send.side_effect = [
        MagicMock(retcode=10016, comment="modify_failed"),
        MagicMock(retcode=10009, order=11),
        MagicMock(retcode=10008, order=21),
        MagicMock(retcode=10008, order=22),
    ]

    entry_cfg = EntryConfig(
        k_entry_atr=0.0,
        k_entry_spread=0.0,
        entry_offset_min_points=60.0,
        idle_offset_spread_mult=0.0,
        entry_buffer_enabled=False,
        orders_expand_points=0.0,
        min_total_offset_points=60.0,
        min_order_age_ms=0.0,
        rearm_hysteresis_pts=0.0,
    )
    pending_cfg = PendingConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    manager = OrderManager(adapter, state, entry_cfg, pending_cfg)
    si = _make_si()

    manager.ensure_dual_pending(
        tick_bid=5072.90,
        tick_ask=5073.10,
        atr_pts=100.0,
        spread_med_pts=25.0,
        si=si,
        now_ms=2000.0,
    )

    adapter.build_modify_pending_request.assert_called_once()
    adapter.build_cancel_request.assert_called()
    assert state.buy_stop_ticket is not None


def test_flat_freeze_still_recenters_when_price_drift_is_large() -> None:
    state = StateStore()
    state.buy_stop_ticket = 11
    state.buy_stop_price = 5073.00
    adapter = MagicMock()
    adapter.build_modify_pending_request.side_effect = lambda **kwargs: kwargs
    adapter.order_send.return_value = MagicMock(retcode=10009, order=11)

    entry_cfg = EntryConfig(
        k_entry_atr=0.0,
        k_entry_spread=0.0,
        entry_offset_min_points=45.0,
        k_rearm_atr=0.0,
        k_rearm_spread=0.0,
        rearm_min_points=15.0,
        idle_offset_spread_mult=0.0,
        entry_buffer_enabled=False,
        orders_expand_points=0.0,
        min_total_offset_points=0.0,
        min_order_age_ms=0.0,
        rearm_hysteresis_pts=14.0,
        only_buy=True,
        flat_window_ms=20_000.0,
        flat_range_pts=40.0,
        flat_offset_pts=45.0,
        flat_freeze_enabled=True,
        flat_freeze_ttl_ms=12_000.0,
    )
    pending_cfg = PendingConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    manager = OrderManager(adapter, state, entry_cfg, pending_cfg)
    manager._flat_frozen = True
    manager._flat_freeze_until_ms = 30_000.0
    manager._tick_history = [
        (5_000.0, 5072.96),
        (10_000.0, 5073.00),
        (15_000.0, 5073.04),
        (20_000.0, 5072.98),
    ]
    si = _make_si()

    manager.ensure_dual_pending(
        tick_bid=5072.90,
        tick_ask=5073.10,
        atr_pts=100.0,
        spread_med_pts=10.0,
        si=si,
        now_ms=25_000.0,
    )

    adapter.build_modify_pending_request.assert_called_once()
    assert state.buy_stop_price == 5073.55


def test_countertrend_guard_blocks_buy_rearm_on_dominant_sell_move() -> None:
    state = StateStore()
    state.buy_stop_ticket = 11
    state.buy_stop_price = 100.80
    adapter = MagicMock()
    adapter.build_modify_pending_request.side_effect = lambda **kwargs: kwargs

    entry_cfg = EntryConfig(
        k_entry_atr=0.0,
        k_entry_spread=0.0,
        entry_offset_min_points=45.0,
        k_rearm_atr=0.0,
        k_rearm_spread=0.0,
        rearm_min_points=10.0,
        idle_offset_spread_mult=0.0,
        entry_buffer_enabled=False,
        orders_expand_points=0.0,
        min_total_offset_points=45.0,
        min_order_age_ms=0.0,
        rearm_hysteresis_pts=0.0,
        burst_max_wait_ms=0.0,
        burst_min_spread_mult=1.0,
        burst_min_abs_pts=8.0,
        countertrend_guard_window_ms=1800.0,
        countertrend_guard_atr_mult=0.0,
        countertrend_guard_min_pts=20.0,
        only_buy=True,
    )
    pending_cfg = PendingConfig(symbol="XAUUSD", magic=12345, volume=0.01)
    manager = OrderManager(adapter, state, entry_cfg, pending_cfg)
    manager._tick_history = [
        (200.0, 101.40),
        (800.0, 100.80),
        (1400.0, 100.20),
        (1700.0, 100.18),
    ]
    si = _make_si()

    manager.ensure_dual_pending(
        tick_bid=100.09,
        tick_ask=100.19,
        atr_pts=100.0,
        spread_med_pts=10.0,
        si=si,
        now_ms=2000.0,
    )

    adapter.build_modify_pending_request.assert_not_called()
    assert state.buy_stop_price == 100.80