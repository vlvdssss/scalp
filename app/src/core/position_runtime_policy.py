from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.src.core.position_trailing_policy import compute_profit_points, compute_profit_r
from app.src.core.state import Side


@dataclass(frozen=True)
class HoldGuardDecision:
    elapsed_ms: float
    min_hold_ms: float


@dataclass(frozen=True)
class APTActivationDecision:
    trail_atr_points: float
    profit_points: float
    profit_r: float
    activation_reached: bool


def evaluate_hold_guard(
    min_hold_ms: float,
    position_start_mono_ms: float,
    mono_ms: float,
) -> Optional[HoldGuardDecision]:
    if min_hold_ms <= 0 or position_start_mono_ms <= 0:
        return None
    elapsed_ms = mono_ms - position_start_mono_ms
    if elapsed_ms >= min_hold_ms:
        return None
    return HoldGuardDecision(elapsed_ms=elapsed_ms, min_hold_ms=min_hold_ms)


def build_apt_activation_decision(
    side: Side,
    entry_price: float,
    bid: float,
    ask: float,
    point: float,
    initial_sl_points: float,
    activation_r: float,
    trail_atr_points: float,
    fallback_atr_points: float,
) -> APTActivationDecision:
    profit_points = compute_profit_points(side, entry_price, bid, ask, point)
    profit_r = compute_profit_r(profit_points, initial_sl_points)
    return APTActivationDecision(
        trail_atr_points=trail_atr_points if trail_atr_points > 0.0 else fallback_atr_points,
        profit_points=profit_points,
        profit_r=profit_r,
        activation_reached=profit_r >= activation_r,
    )


def evaluate_entry_cooldown(
    no_trail_seconds_after_entry: float,
    position_start_mono_ms: float,
    mono_ms: float,
) -> Optional[float]:
    if no_trail_seconds_after_entry <= 0 or position_start_mono_ms <= 0:
        return None
    elapsed_seconds = (mono_ms - position_start_mono_ms) / 1000.0
    if elapsed_seconds >= no_trail_seconds_after_entry:
        return None
    return elapsed_seconds


def evaluate_trailing_throttle(
    mono_ms: float,
    last_trailing_update_mono: float,
    throttle_sec: float,
) -> Optional[float]:
    since_last = (mono_ms / 1000.0) - last_trailing_update_mono
    if since_last >= throttle_sec:
        return None
    return since_last


def build_apt_trail_update_payload(
    ticket: int | None,
    bid: float,
    ask: float,
    atr_points: float,
    profit_points: float,
    gap_points: float,
    step_points: float,
    current_sl: float,
    new_sl: float,
    moved: bool,
    activation_reached: bool,
    profit_r: float,
    spread_points: float,
    extreme_price: float | None,
) -> dict[str, int | float | bool | str | None]:
    return {
        "ticket": ticket,
        "bid": bid,
        "ask": ask,
        "atr_points": atr_points,
        "profit_points": profit_points,
        "gap_points": gap_points,
        "step_points": step_points,
        "current_sl": current_sl,
        "new_sl": new_sl,
        "moved": moved,
        "reason": "trail" if moved else "skip",
        "activation_reached": activation_reached,
        "profit_R": profit_r,
        "spread_points": spread_points,
        "extreme_price": extreme_price,
    }