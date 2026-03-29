from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.src.core.risk import round_to_step
from app.src.core.state import Side


@dataclass(frozen=True)
class TrailingCandidate:
    extreme_price: float
    sl_candidate: float
    current_sl: float
    improvement: float


def compute_profit_points(
    side: Side,
    entry_price: float,
    bid: float,
    ask: float,
    point: float,
) -> float:
    if side == Side.BUY:
        return (bid - entry_price) / point
    return (entry_price - ask) / point


def compute_profit_r(profit_pts: float, initial_sl_pts: float) -> float:
    if initial_sl_pts <= 0:
        return 0.0
    return profit_pts / initial_sl_pts


def get_classic_activation_wait_payload(
    profit_pts: float,
    trail_activation_points: float,
    trail_activate_after_be_pct: float,
    be_s2_act_pts: float,
    be_activation_points: float,
) -> Optional[dict[str, float | str]]:
    if trail_activation_points > 0:
        if profit_pts < trail_activation_points:
            return {
                "profit_pts": round(profit_pts, 1),
                "need_pts": trail_activation_points,
                "gate": "activation_points",
            }
        return None

    if trail_activate_after_be_pct > 0:
        base_pts = be_s2_act_pts if be_s2_act_pts > 0 else be_activation_points
        need_pts = base_pts * (trail_activate_after_be_pct / 100.0)
        if profit_pts < need_pts:
            return {
                "profit_pts": round(profit_pts, 1),
                "need_pts": round(need_pts, 1),
                "be_s2_act_pts": round(base_pts, 1),
                "pct": trail_activate_after_be_pct,
                "gate": "activate_pct",
            }

    return None


def build_trailing_candidate(
    side: Side,
    current_extreme_price: Optional[float],
    current_sl: Optional[float],
    bid: float,
    ask: float,
    trail_dist: float,
    point: float,
    digits: int,
) -> TrailingCandidate:
    if side == Side.BUY:
        extreme_price = bid if current_extreme_price is None or bid > current_extreme_price else current_extreme_price
        sl_candidate = round_to_step(extreme_price - trail_dist * point, point, digits)
        resolved_current_sl = current_sl or 0.0
        improvement = sl_candidate - resolved_current_sl
        return TrailingCandidate(
            extreme_price=extreme_price,
            sl_candidate=sl_candidate,
            current_sl=resolved_current_sl,
            improvement=improvement,
        )

    extreme_price = ask if current_extreme_price is None or ask < current_extreme_price else current_extreme_price
    sl_candidate = round_to_step(extreme_price + trail_dist * point, point, digits)
    resolved_current_sl = current_sl if current_sl is not None else float("inf")
    improvement = resolved_current_sl - sl_candidate
    return TrailingCandidate(
        extreme_price=extreme_price,
        sl_candidate=sl_candidate,
        current_sl=resolved_current_sl,
        improvement=improvement,
    )


def apply_risk_floor(
    side: Side,
    sl_candidate: float,
    risk_floor_sl: Optional[float],
    point: float,
    digits: int,
) -> float:
    if risk_floor_sl is None:
        return sl_candidate

    if side == Side.BUY and sl_candidate < risk_floor_sl:
        return round_to_step(risk_floor_sl, point, digits)
    if side == Side.SELL and sl_candidate > risk_floor_sl:
        return round_to_step(risk_floor_sl, point, digits)
    return sl_candidate