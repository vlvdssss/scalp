import pytest

from app.src.core.position_confirm_policy import (
    build_confirm_fail_payload,
    build_confirm_progress_payload,
    build_confirm_success_payload,
    build_confirm_tick_update,
    compute_confirm_move_points,
    evaluate_confirm_window,
    resolve_clock_confirm_threshold,
)
from app.src.core.state import Side


def test_compute_confirm_move_points_for_both_sides() -> None:
    assert compute_confirm_move_points(Side.BUY, 1900.0, 1900.2, 1900.25, 0.01) == pytest.approx(20.0)
    assert compute_confirm_move_points(Side.SELL, 1900.0, 1899.7, 1899.8, 0.01) == pytest.approx(20.0)
    assert compute_confirm_move_points(Side.BUY, None, 1900.2, 1900.25, 0.01) == 0.0


def test_evaluate_confirm_window_distinguishes_time_and_tick_limits() -> None:
    time_window = evaluate_confirm_window(600.0, 0, 500.0, 8)
    tick_window = evaluate_confirm_window(100.0, 8, 500.0, 8)

    assert time_window.timed_out is True
    assert time_window.fail_reason == "time_window"
    assert tick_window.timed_out is True
    assert tick_window.fail_reason == "tick_window"


def test_resolve_clock_confirm_threshold_uses_stored_threshold_when_present() -> None:
    assert resolve_clock_confirm_threshold(5.0, 12.0, 10.0) == 12.0
    assert resolve_clock_confirm_threshold(5.0, 0.0, 10.0) == 10.0
    assert resolve_clock_confirm_threshold(0.0, 12.0, 10.0) == 10.0


def test_build_confirm_tick_update_reports_success_or_failure() -> None:
    success = build_confirm_tick_update(
        side=Side.BUY,
        entry_price=1900.0,
        bid=1900.2,
        ask=1900.25,
        point=0.01,
        previous_best_move_points=5.0,
        threshold_points=10.0,
        elapsed_ms=100.0,
        window_ms=500.0,
        ticks_seen=1,
        window_ticks=8,
    )
    failure = build_confirm_tick_update(
        side=Side.BUY,
        entry_price=1900.0,
        bid=1900.01,
        ask=1900.06,
        point=0.01,
        previous_best_move_points=1.0,
        threshold_points=10.0,
        elapsed_ms=600.0,
        window_ms=500.0,
        ticks_seen=8,
        window_ticks=8,
    )

    assert success.best_move_points == pytest.approx(20.0)
    assert success.success is True
    assert success.fail_reason == ""

    assert failure.best_move_points == 1.0
    assert failure.success is False
    assert failure.fail_reason == "time_window"


def test_confirm_payload_builders_keep_expected_schema() -> None:
    progress = build_confirm_progress_payload(250.0, 3, 5.0, 2000.0, 8)
    success = build_confirm_success_payload(20.0, 10.0, 2, 500.0)
    fail = build_confirm_fail_payload(2.0, 10.0, 8, 2001.0, "time_window")

    assert progress["window_ticks"] == 8
    assert success["threshold"] == 10.0
    assert fail["reason"] == "time_window"