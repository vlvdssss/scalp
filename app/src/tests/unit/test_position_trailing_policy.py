from app.src.core.position_trailing_policy import (
    apply_risk_floor,
    build_trailing_candidate,
    compute_profit_points,
    compute_profit_r,
    get_classic_activation_wait_payload,
)
from app.src.core.state import Side


def test_compute_profit_points_for_buy_and_sell() -> None:
    assert compute_profit_points(Side.BUY, 1900.0, 1900.25, 1900.30, 0.01) == 25.0
    assert compute_profit_points(Side.SELL, 1900.0, 1899.70, 1899.75, 0.01) == 25.0


def test_compute_profit_r_ignores_zero_initial_sl() -> None:
    assert compute_profit_r(25.0, 0.0) == 0.0
    assert compute_profit_r(25.0, 50.0) == 0.5


def test_classic_activation_wait_payload_prefers_fixed_points_gate() -> None:
    payload = get_classic_activation_wait_payload(
        profit_pts=12.3,
        trail_activation_points=25.0,
        trail_activate_after_be_pct=50.0,
        be_s2_act_pts=20.0,
        be_activation_points=22.0,
    )

    assert payload == {
        "profit_pts": 12.3,
        "need_pts": 25.0,
        "gate": "activation_points",
    }


def test_classic_activation_wait_payload_uses_be_pct_fallback() -> None:
    payload = get_classic_activation_wait_payload(
        profit_pts=9.8,
        trail_activation_points=0.0,
        trail_activate_after_be_pct=50.0,
        be_s2_act_pts=24.0,
        be_activation_points=40.0,
    )

    assert payload == {
        "profit_pts": 9.8,
        "need_pts": 12.0,
        "be_s2_act_pts": 24.0,
        "pct": 50.0,
        "gate": "activate_pct",
    }


def test_build_trailing_candidate_tracks_extreme_and_improvement() -> None:
    buy_candidate = build_trailing_candidate(
        side=Side.BUY,
        current_extreme_price=1910.0,
        current_sl=1905.0,
        bid=1912.0,
        ask=1912.1,
        trail_dist=50.0,
        point=0.01,
        digits=2,
    )
    sell_candidate = build_trailing_candidate(
        side=Side.SELL,
        current_extreme_price=1890.0,
        current_sl=1895.0,
        bid=1887.9,
        ask=1888.0,
        trail_dist=50.0,
        point=0.01,
        digits=2,
    )

    assert buy_candidate.extreme_price == 1912.0
    assert buy_candidate.sl_candidate == 1911.5
    assert buy_candidate.improvement == 6.5

    assert sell_candidate.extreme_price == 1888.0
    assert sell_candidate.sl_candidate == 1888.5
    assert sell_candidate.improvement == 6.5


def test_apply_risk_floor_respects_side_direction() -> None:
    assert apply_risk_floor(Side.BUY, 1909.0, 1910.0, 0.01, 2) == 1910.0
    assert apply_risk_floor(Side.SELL, 1891.0, 1890.0, 0.01, 2) == 1890.0
    assert apply_risk_floor(Side.BUY, 1911.0, 1910.0, 0.01, 2) == 1911.0