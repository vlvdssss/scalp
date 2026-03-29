import pytest

from app.src.core.position_be_policy import (
    build_stage1_plan,
    build_stage2_plan,
    compute_stage1_activation,
    compute_stage2_activation,
)
from app.src.core.risk import BEConfig
from app.src.core.state import Side

pytestmark = pytest.mark.skip(
    reason="2-stage BE policy removed; BE is now simple USD-threshold-only"
)

def test_compute_be_stage_activations_respect_spread_and_minimums() -> None:
    cfg = BEConfig(
        be_stage1_spread_mult=1.5,
        be_stage1_min_pts=14.0,
        be_stage2_spread_mult=2.0,
        be_stage2_min_pts=22.0,
    )

    assert compute_stage1_activation(10.0, cfg) == 15.0
    assert compute_stage1_activation(5.0, cfg) == 14.0
    assert compute_stage2_activation(10.0, cfg) == 22.0
    assert compute_stage2_activation(20.0, cfg) == 40.0


def test_build_stage1_plan_buy_and_sell() -> None:
    buy_plan = build_stage1_plan(
        side=Side.BUY,
        entry_price=1900.0,
        current_sl=1890.0,
        initial_sl_points=100.0,
        risk_keep=0.4,
        activation_points=14.0,
        point=0.01,
        digits=2,
    )
    sell_plan = build_stage1_plan(
        side=Side.SELL,
        entry_price=1900.0,
        current_sl=1915.0,
        initial_sl_points=100.0,
        risk_keep=0.4,
        activation_points=14.0,
        point=0.01,
        digits=2,
    )

    assert buy_plan is not None
    assert buy_plan.sl_price == 1899.6
    assert buy_plan.should_modify is True

    assert sell_plan is not None
    assert sell_plan.sl_price == 1900.4
    assert sell_plan.should_modify is True


def test_build_stage1_plan_returns_none_without_initial_sl() -> None:
    assert build_stage1_plan(
        side=Side.BUY,
        entry_price=1900.0,
        current_sl=None,
        initial_sl_points=0.0,
        risk_keep=0.4,
        activation_points=14.0,
        point=0.01,
        digits=2,
    ) is None


def test_build_stage2_plan_sets_side_specific_extreme_and_sl() -> None:
    buy_plan = build_stage2_plan(
        side=Side.BUY,
        entry_price=1900.0,
        buffer_points=10.0,
        activation_points=22.0,
        bid=1901.25,
        ask=1901.3,
        point=0.01,
        digits=2,
    )
    sell_plan = build_stage2_plan(
        side=Side.SELL,
        entry_price=1900.0,
        buffer_points=10.0,
        activation_points=22.0,
        bid=1898.7,
        ask=1898.75,
        point=0.01,
        digits=2,
    )

    assert buy_plan.sl_price == 1900.1
    assert buy_plan.extreme_price == 1901.25
    assert sell_plan.sl_price == 1899.9
    assert sell_plan.extreme_price == 1898.75