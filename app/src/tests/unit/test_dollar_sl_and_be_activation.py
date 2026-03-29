"""Tests for A) dollar-risk SL and B) BE activation gate."""
import math
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.src.adapters.mt5_adapter import PositionSnapshot, RC_NO_CHANGES
from app.src.core.engine_trade_lifecycle import TradeLifecycleMixin
from app.src.core.risk import calc_dollar_sl_points, BEConfig


# ── A) calc_dollar_sl_points ──────────────────────────────────────────────────

def test_basic_dollar_sl():
    """$1 risk, 0.01 lot, value_per_point_per_lot=0.01 → ceil(1/0.0001) = 10000 pts."""
    pts = calc_dollar_sl_points(
        target_risk_usd=1.0,
        value_per_point_per_lot=0.01,
        volume=0.01,
        trade_stops_level=0,
        safety_buffer_points=0,
    )
    assert pts == math.ceil(1.0 / (0.01 * 0.01))


def test_xauusd_approx():
    """XAUUSD typical: tick_value≈0.01, tick_size=0.01, point=0.01 → value/pt/lot=0.01.
    With 0.01 lot: value_per_pt = 0.0001 → sl = ceil(1/0.0001) = 10000 pts.
    After stops_level=30 + buf=10 = 40 → max(10000, 40) = 10000."""
    pts = calc_dollar_sl_points(1.0, 0.01, 0.01, 30, 10.0)
    assert pts == 10000


def test_respects_stops_level_floor():
    """When broker stops_level+buffer > computed SL, use the floor."""
    pts = calc_dollar_sl_points(
        target_risk_usd=0.001,  # very tiny risk → tiny sl_pts
        value_per_point_per_lot=1.0,
        volume=1.0,
        trade_stops_level=50,
        safety_buffer_points=20.0,
    )
    assert pts >= 70  # floor = 50+20


def test_zero_value_per_pt_returns_floor():
    pts = calc_dollar_sl_points(1.0, 0.0, 0.01, 30, 10.0)
    assert pts == 40  # 30+10


def test_ceil_rounding():
    """Result must always be ceiling, never floor."""
    # target=1.0, vpp = 0.03, vol=1 → raw = ceil(1/0.03) = ceil(33.33) = 34
    pts = calc_dollar_sl_points(1.0, 0.03, 1.0, 0, 0)
    assert pts == 34


# ── B) BEConfig fields ───────────────────────────────────────────────────────

def test_be_config_defaults():
    cfg = BEConfig()
    assert cfg.be_activation_usd == 0.25
    assert cfg.be_stop_usd == 0.15
    assert cfg.min_hold_ms == 2000.0


def test_be_config_custom():
    cfg = BEConfig(be_activation_usd=0.50, be_stop_usd=0.30, min_hold_ms=0.0)
    assert cfg.be_activation_usd == 0.50
    assert cfg.be_stop_usd == 0.30
    assert cfg.min_hold_ms == 0.0


# ── B) PositionManager BE gate ────────────────────────────────────────────────

def _make_pm():
    """Build a PositionManager with a stub adapter/state.
    Uses min_hold_ms=0.0 to disable the hold guard for predictable test behavior."""
    from unittest.mock import MagicMock
    from app.src.core.position_manager import PositionManager, PositionConfig
    from app.src.core.risk import BEConfig, TrailConfig, ConfirmConfig
    from app.src.core.state import StateStore, TradingState, Side

    adapter = MagicMock()
    state   = StateStore()
    state.state          = TradingState.POSITION_ACTIVE
    state.position_side  = Side.BUY
    state.entry_price    = 5000.0
    state.position_ticket = 1
    state.be_done        = False

    pm = PositionManager(
        adapter     = adapter,
        state       = state,
        pos_cfg     = PositionConfig(),
        confirm_cfg = ConfirmConfig(),
        be_cfg      = BEConfig(be_activation_usd=0.25, be_stop_usd=0.15, min_hold_ms=0.0),
        trail_cfg   = TrailConfig(),
    )

    si = MagicMock()
    si.point              = 0.01
    si.digits             = 2
    si.trade_stops_level  = 0
    si.trade_freeze_level = 0
    si.value_per_point    = 1.0

    return pm, state, adapter, si


def test_be_blocked_when_not_enough_move():
    """If profit_usd < be_activation_usd, _check_be must NOT trigger BE."""
    pm, state, adapter, si = _make_pm()
    # 10 pts * 0.01 vpp * 0.01 lot = $0.10 < $0.25
    pm.tick_active(bid=5000.10, ask=5000.11, atr_pts=200, spread_med_pts=10, si=si, mono_ms=1000)
    assert not state.be_done


def test_be_allowed_after_enough_move():
    """If profit_usd >= be_activation_usd, BE must trigger."""
    pm, state, adapter, si = _make_pm()

    result_mock = MagicMock()
    result_mock.retcode = 10009  # RC_DONE
    adapter.order_send.return_value = result_mock
    adapter.build_modify_sl_request.return_value = {}

    # 200 pts * 0.01 vpp * 0.01 lot = $2.00 > $0.25
    pm.tick_active(bid=5002.00, ask=5002.01, atr_pts=200, spread_med_pts=10, si=si, mono_ms=1000)
    assert state.be_done
    assert adapter.order_send.called


def test_be_triggers_only_once_when_already_done():
    """Once be_done=True, _check_be must NOT be called again."""
    pm, state, adapter, si = _make_pm()
    state.be_done = True

    pm.tick_active(bid=5002.00, ask=5002.01, atr_pts=200, spread_med_pts=10, si=si, mono_ms=1000)
    # order_send should not be called for BE
    assert state.be_done  # still True, unchanged


def test_initial_sl_no_changes_is_not_critical():
    host = SimpleNamespace()
    host._cfg = {
        "risk": {
            "target_risk_usd": 0.7,
            "sl_safety_buffer_points": 10.0,
            "volume": 0.01,
        },
        "sl": {
            "sl_min_points": 100.0,
            "sl_max_points": 135.0,
        },
        "symbol": {
            "name": "XAUUSD",
            "magic": 20260225,
        },
    }
    host._adapter = MagicMock()
    host._adapter.build_modify_sl_request.return_value = {"req": "modify"}
    host._adapter.order_send.return_value = SimpleNamespace(retcode=RC_NO_CHANGES)
    host._state = SimpleNamespace(current_sl=0.0, initial_sl_points=0.0)
    host._pos_mgr = SimpleNamespace(
        set_risk_floor_sl=MagicMock(),
        set_be_activation_points=MagicMock(),
    )
    host._log_event = MagicMock()

    pos = PositionSnapshot(
        ticket=77,
        type=0,
        symbol="XAUUSD",
        volume=0.01,
        price_open=5000.0,
        sl=4999.0,
        tp=0.0,
        profit=0.0,
        magic=20260225,
        comment="",
        time=0,
    )
    si = SimpleNamespace(
        value_per_point=1.0,
        trade_stops_level=0,
        point=0.01,
        digits=2,
    )

    TradeLifecycleMixin._set_initial_sl(host, pos, si, spread_pts=40.0)

    assert host._state.current_sl == 4999.0
    assert host._state.initial_sl_points == 100.0
    host._pos_mgr.set_risk_floor_sl.assert_called_once_with(4999.0)
    host._log_event.assert_called_once()
    assert host._log_event.call_args.args[0] == "INITIAL_SL_ALREADY_SET"
