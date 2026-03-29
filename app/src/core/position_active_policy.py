from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.src.core.risk import round_to_step
from app.src.core.state import Side


@dataclass(frozen=True)
class RunnerBEPlan:
    be_buffer_points: float
    sl_price: float


@dataclass(frozen=True)
class WickBlockDecision:
    wick_ratio: float
    candle_body_points: float
    adverse_wick_points: float


def compute_partial_close_volume(
    position_volume: float,
    partial_close_pct: float,
    volume_min: float,
    volume_step: float,
) -> Optional[float]:
    step = volume_step if volume_step > 0 else 0.01
    close_volume = max(
        volume_min,
        round(round((position_volume * partial_close_pct) / step) * step, 8),
    )
    if close_volume >= position_volume:
        close_volume = round(round((position_volume - step) / step) * step, 8)
    close_volume = max(volume_min, close_volume)
    if close_volume <= 0 or close_volume >= position_volume:
        return None
    return close_volume


def build_runner_be_plan(
    side: Side,
    entry_price: float,
    spread_med_pts: float,
    be_buffer_mult_spread: float,
    min_be_points_lock: float,
    point: float,
    digits: int,
) -> RunnerBEPlan:
    be_buffer_points = max(spread_med_pts * be_buffer_mult_spread, min_be_points_lock)
    if side == Side.BUY:
        sl_price = round_to_step(entry_price + be_buffer_points * point, point, digits)
    else:
        sl_price = round_to_step(entry_price - be_buffer_points * point, point, digits)
    return RunnerBEPlan(be_buffer_points=be_buffer_points, sl_price=sl_price)


def evaluate_wick_block(
    side: Side,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    point: float,
    wick_block_ratio: float,
) -> Optional[WickBlockDecision]:
    body = abs(candle_close - candle_open)
    if body < point * 2:
        return None

    if side == Side.BUY:
        adverse_wick = min(candle_open, candle_close) - candle_low
    else:
        adverse_wick = candle_high - max(candle_open, candle_close)

    wick_ratio = adverse_wick / body if body > 0 else 0.0
    if wick_ratio <= wick_block_ratio:
        return None

    return WickBlockDecision(
        wick_ratio=wick_ratio,
        candle_body_points=body / point,
        adverse_wick_points=adverse_wick / point,
    )