import pytest

from app.src.core.position_runtime_policy import (
    build_apt_activation_decision,
    build_apt_trail_update_payload,
    evaluate_entry_cooldown,
    evaluate_hold_guard,
    evaluate_trailing_throttle,
)
from app.src.core.state import Side


def test_evaluate_hold_guard_returns_block_only_within_window() -> None:
    decision = evaluate_hold_guard(min_hold_ms=5000.0, position_start_mono_ms=1000.0, mono_ms=3000.0)
    assert decision is not None
    assert decision.elapsed_ms == 2000.0
    assert evaluate_hold_guard(min_hold_ms=5000.0, position_start_mono_ms=1000.0, mono_ms=7000.0) is None


def test_build_apt_activation_decision_uses_trail_atr_override() -> None:
    decision = build_apt_activation_decision(
        side=Side.BUY,
        entry_price=1900.0,
        bid=1901.0,
        ask=1901.1,
        point=0.01,
        initial_sl_points=50.0,
        activation_r=1.5,
        trail_atr_points=33.0,
        fallback_atr_points=100.0,
    )
    assert decision.trail_atr_points == 33.0
    assert decision.profit_points == 100.0
    assert decision.profit_r == 2.0
    assert decision.activation_reached is True


def test_entry_cooldown_and_throttle_helpers_return_elapsed_values() -> None:
    assert evaluate_entry_cooldown(30.0, 1000.0, 15000.0) == 14.0
    assert evaluate_entry_cooldown(30.0, 1000.0, 35000.0) is None
    assert evaluate_trailing_throttle(1500.0, 1.2, 1.0) == pytest.approx(0.3)
    assert evaluate_trailing_throttle(5000.0, 1.2, 1.0) is None


def test_build_apt_trail_update_payload_keeps_reason_consistent() -> None:
    payload = build_apt_trail_update_payload(
        ticket=7,
        bid=1900.1,
        ask=1900.2,
        atr_points=33.0,
        profit_points=180.0,
        gap_points=120.0,
        step_points=40.0,
        current_sl=1899.0,
        new_sl=1900.0,
        moved=True,
        activation_reached=True,
        profit_r=1.8,
        spread_points=10.0,
        extreme_price=1900.5,
    )
    assert payload["reason"] == "trail"
    assert payload["profit_R"] == 1.8
    assert payload["extreme_price"] == 1900.5