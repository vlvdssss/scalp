from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.src.core.risk import BEConfig, round_to_step
from app.src.core.state import Side


@dataclass(frozen=True)
class BEStage1Plan:
    activation_points: float
    initial_sl_points: float
    risk_keep: float
    sl_price: float
    should_modify: bool


@dataclass(frozen=True)
class BEStage2Plan:
    activation_points: float
    buffer_points: float
    sl_price: float
    extreme_price: float


def compute_stage1_activation(spread_med_pts: float, config: BEConfig) -> float:
    return max(spread_med_pts * config.be_stage1_spread_mult, config.be_stage1_min_pts)


def compute_stage2_activation(spread_med_pts: float, config: BEConfig) -> float:
    return max(spread_med_pts * config.be_stage2_spread_mult, config.be_stage2_min_pts)


def build_stage1_plan(
    side: Side,
    entry_price: float,
    current_sl: Optional[float],
    initial_sl_points: float,
    risk_keep: float,
    activation_points: float,
    point: float,
    digits: int,
) -> Optional[BEStage1Plan]:
    if initial_sl_points <= 0:
        return None

    new_risk_points = initial_sl_points * risk_keep
    if side == Side.BUY:
        sl_price = round_to_step(entry_price - new_risk_points * point, point, digits)
        should_modify = sl_price > (current_sl or 0.0)
    else:
        sl_price = round_to_step(entry_price + new_risk_points * point, point, digits)
        resolved_current_sl = current_sl if current_sl is not None else float("inf")
        should_modify = sl_price < resolved_current_sl

    return BEStage1Plan(
        activation_points=activation_points,
        initial_sl_points=initial_sl_points,
        risk_keep=risk_keep,
        sl_price=sl_price,
        should_modify=should_modify,
    )


def build_stage2_plan(
    side: Side,
    entry_price: float,
    buffer_points: float,
    activation_points: float,
    bid: float,
    ask: float,
    point: float,
    digits: int,
) -> BEStage2Plan:
    if side == Side.BUY:
        sl_price = round_to_step(entry_price + buffer_points * point, point, digits)
        extreme_price = bid
    else:
        sl_price = round_to_step(entry_price - buffer_points * point, point, digits)
        extreme_price = ask

    return BEStage2Plan(
        activation_points=activation_points,
        buffer_points=buffer_points,
        sl_price=sl_price,
        extreme_price=extreme_price,
    )