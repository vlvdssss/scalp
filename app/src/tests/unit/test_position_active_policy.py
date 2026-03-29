from app.src.core.position_active_policy import (
    build_runner_be_plan,
    compute_partial_close_volume,
    evaluate_wick_block,
)
from app.src.core.state import Side


def test_compute_partial_close_volume_rounds_and_keeps_runner() -> None:
    close_vol = compute_partial_close_volume(
        position_volume=0.10,
        partial_close_pct=0.5,
        volume_min=0.01,
        volume_step=0.01,
    )
    assert close_vol == 0.05


def test_compute_partial_close_volume_rejects_full_close() -> None:
    close_vol = compute_partial_close_volume(
        position_volume=0.01,
        partial_close_pct=1.0,
        volume_min=0.01,
        volume_step=0.01,
    )
    assert close_vol is None


def test_build_runner_be_plan_for_buy_and_sell() -> None:
    buy_plan = build_runner_be_plan(
        side=Side.BUY,
        entry_price=1900.0,
        spread_med_pts=10.0,
        be_buffer_mult_spread=1.5,
        min_be_points_lock=30.0,
        point=0.01,
        digits=2,
    )
    sell_plan = build_runner_be_plan(
        side=Side.SELL,
        entry_price=1900.0,
        spread_med_pts=40.0,
        be_buffer_mult_spread=1.5,
        min_be_points_lock=30.0,
        point=0.01,
        digits=2,
    )

    assert buy_plan.be_buffer_points == 30.0
    assert buy_plan.sl_price == 1900.3
    assert sell_plan.be_buffer_points == 60.0
    assert sell_plan.sl_price == 1899.4


def test_evaluate_wick_block_detects_adverse_buy_wick() -> None:
    decision = evaluate_wick_block(
        side=Side.BUY,
        candle_open=100.0,
        candle_high=101.0,
        candle_low=95.0,
        candle_close=101.0,
        point=0.1,
        wick_block_ratio=2.0,
    )

    assert decision is not None
    assert round(decision.wick_ratio, 2) == 5.0
    assert decision.candle_body_points == 10.0
    assert decision.adverse_wick_points == 50.0


def test_evaluate_wick_block_ignores_small_body_or_safe_ratio() -> None:
    assert evaluate_wick_block(
        side=Side.BUY,
        candle_open=100.0,
        candle_high=100.1,
        candle_low=99.9,
        candle_close=100.05,
        point=0.1,
        wick_block_ratio=2.0,
    ) is None
    assert evaluate_wick_block(
        side=Side.SELL,
        candle_open=100.0,
        candle_high=101.0,
        candle_low=99.0,
        candle_close=99.5,
        point=0.1,
        wick_block_ratio=5.0,
    ) is None