from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.src.core.state import Side


@dataclass(frozen=True)
class ConfirmWindowDecision:
    elapsed_ms: float
    time_expired: bool
    ticks_expired: bool
    timed_out: bool
    fail_reason: str


@dataclass(frozen=True)
class ConfirmTickUpdate:
    move_points: float
    best_move_points: float
    elapsed_ms: float
    threshold_points: float
    success: bool
    fail_reason: str


def compute_confirm_move_points(
    side: Side,
    entry_price: Optional[float],
    bid: float,
    ask: float,
    point: float,
) -> float:
    if entry_price is None:
        return 0.0
    if side == Side.BUY:
        return (bid - entry_price) / point
    return (entry_price - ask) / point


def evaluate_confirm_window(
    elapsed_ms: float,
    ticks_seen: int,
    window_ms: float,
    window_ticks: int,
) -> ConfirmWindowDecision:
    time_expired = elapsed_ms >= window_ms
    ticks_expired = ticks_seen >= window_ticks
    timed_out = time_expired or ticks_expired
    fail_reason = "time_window" if time_expired else "tick_window" if ticks_expired else ""
    return ConfirmWindowDecision(
        elapsed_ms=elapsed_ms,
        time_expired=time_expired,
        ticks_expired=ticks_expired,
        timed_out=timed_out,
        fail_reason=fail_reason,
    )


def resolve_clock_confirm_threshold(
    best_move_points: float,
    threshold_points_at_finish: float,
    confirm_min_points: float,
) -> float:
    if best_move_points > 0:
        return threshold_points_at_finish if threshold_points_at_finish > 0 else confirm_min_points
    return confirm_min_points


def build_confirm_tick_update(
    side: Side,
    entry_price: Optional[float],
    bid: float,
    ask: float,
    point: float,
    previous_best_move_points: float,
    threshold_points: float,
    elapsed_ms: float,
    window_ms: float,
    ticks_seen: int,
    window_ticks: int,
) -> ConfirmTickUpdate:
    move_points = compute_confirm_move_points(side, entry_price, bid, ask, point)
    best_move_points = move_points if move_points > previous_best_move_points else previous_best_move_points
    window = evaluate_confirm_window(elapsed_ms, ticks_seen, window_ms, window_ticks)
    success = best_move_points >= threshold_points
    fail_reason = "" if success or not window.timed_out else window.fail_reason
    return ConfirmTickUpdate(
        move_points=move_points,
        best_move_points=best_move_points,
        elapsed_ms=elapsed_ms,
        threshold_points=threshold_points,
        success=success,
        fail_reason=fail_reason,
    )


def build_confirm_progress_payload(
    elapsed_ms: float,
    ticks_seen: int,
    best_move_points: float,
    window_ms: float,
    window_ticks: int,
) -> dict[str, float | int]:
    return {
        "elapsed_ms": elapsed_ms,
        "ticks_seen": ticks_seen,
        "best_move_points": best_move_points,
        "window_ms": window_ms,
        "window_ticks": window_ticks,
    }


def build_confirm_success_payload(
    best_move_points: float,
    threshold: float,
    ticks_seen: int,
    elapsed_ms: float,
) -> dict[str, float | int]:
    return {
        "best_move_points": best_move_points,
        "threshold": threshold,
        "ticks_seen": ticks_seen,
        "elapsed_ms": elapsed_ms,
    }


def build_confirm_fail_payload(
    best_move_points: float,
    threshold: float,
    ticks_seen: int,
    elapsed_ms: float,
    reason: str,
) -> dict[str, float | int | str]:
    return {
        "best_move_points": best_move_points,
        "threshold": threshold,
        "ticks_seen": ticks_seen,
        "elapsed_ms": elapsed_ms,
        "reason": reason,
    }