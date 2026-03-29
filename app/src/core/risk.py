"""
risk.py – formula helpers for entry/BE/trailing level computation.

All formulas are normalised here to eliminate duplication and facilitate
unit-testing without MT5.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class EntryConfig:
    k_entry_atr: float           = 0.30
    k_entry_spread: float        = 1.20
    entry_offset_min_points: float = 30.0
    k_rearm_atr: float           = 0.15
    k_rearm_spread: float        = 0.60
    rearm_min_points: float      = 15.0
    # SL for freshly placed pending orders
    k_sl_atr: float              = 0.80
    k_sl_spread: float           = 3.00
    sl_min_points: float         = 80.0
    sl_max_points: float         = 0.0   # hard cap on SL distance in pts (0 = no cap)
    # Aggressive mode
    mode: str                    = "balanced"   # conservative|balanced|aggressive
    offset_cap_atr: float        = 0.25         # aggressive: cap offset = cap_atr * ATR
    rearm_hysteresis_pts: float  = 0.0          # skip rearm if drift < hysteresis
    min_order_age_ms: float      = 0.0          # min ms between cancel/replace
    burst_min_spread_mult: float = 0.8          # burst filter: delta >= mult*spread_med
    burst_min_abs_pts: float     = 6.0          # burst filter: absolute floor
    burst_max_wait_ms: float     = 5000.0       # if unplaced for this long, skip burst check
    impulse_atr_mult: float      = 0.35         # impulse age: threshold = mult*ATR
    impulse_dur_ms: float        = 2000.0       # impulse age: min duration
    countertrend_guard_window_ms: float = 1800.0  # detect short dominant move before chasing countertrend
    countertrend_guard_atr_mult: float = 0.10     # threshold = max(min_pts, atr*mult)
    countertrend_guard_min_pts: float = 24.0      # absolute floor for dominant-move guard
    # Noise-ratio offset multiplier
    noise_window_ms: float       = 3000.0       # tick window to measure noise (ms)
    noise_ratio_high: float      = 2.6          # NoiseRatio >= this → mult_high
    noise_ratio_mid: float       = 2.1          # NoiseRatio >= this → mult_mid
    noise_mult_high: float       = 1.6          # offset multiplier for high noise
    noise_mult_mid: float        = 1.3          # offset multiplier for mid noise
    # Direction filter
    only_buy: bool               = False        # when True, skip SELL STOP placement
    # Smart Offset v1: dynamic min/max bounds
    offset_min_spread_mult: float = 2.0         # min_offset = max(spread*mult, min_points_floor)
    offset_max_spread_mult: float = 6.0         # max_offset = max(spread*mult, cap_atr*ATR)
    # Trailing Pending: two-mode offset
    idle_offset_spread_mult: float    = 5.0     # idle: wide offset = max(spread*mult, floor)
    impulse_capture_delta_pts: float  = 8.0     # delta in 300ms that triggers capture mode
    impulse_capture_spread_mult: float = 1.5    # capture: narrow offset = max(spread*mult, floor)
    impulse_capture_floor_pts: float  = 10.0    # capture: absolute min offset (pts)
    impulse_capture_dur_ms: float     = 3000.0  # stay in capture mode for N ms
    # ── Entry buffer (anti-microstopper): push order further from price ────────
    entry_buffer_enabled: bool        = True
    entry_buffer_spread_mult: float   = 2.0    # buffer = max(spread*mult, atr*atr_mult, floor)
    entry_buffer_atr_mult: float      = 0.25
    fixed_min_buffer: float           = 60.0   # absolute floor in points
    min_total_offset_points: float    = 0.0    # hard floor for final order distance from current price
    # ── Orders expand: fixed extra offset added to both BUY/SELL STOP ─────────
    orders_expand_points: float       = 0.0    # add N pts to BUY STOP, subtract N pts from SELL STOP    # ── Absolute max offset: hard ceiling regardless of ATR spike ──────────
    offset_abs_max_points: float      = 0.0    # 0 = no hard cap (use ATR-based cap only)    # ── Flat detector + freeze ─────────────────────────────────────────────────
    flat_window_ms: float             = 20000.0  # look-back to detect consolidation (ms)
    flat_range_pts: float             = 25.0     # range <= this → flat/consolidation
    flat_offset_pts: float            = 40.0     # tighter order distance in flat mode (pts)
    flat_freeze_enabled: bool         = True     # freeze orders after placing in flat mode
    flat_freeze_ttl_ms: float         = 30000.0  # max freeze duration before refresh (ms)
    # ── Counter-trend post-close offset: push opposite side further for N sec ──
    counter_trend_extra_points: float = 0.0   # extra offset pts for the opposite direction
    counter_trend_window_sec: float   = 5.0   # window after close to apply extra offset


@dataclass
class BEConfig:
    # Simplified BE: fire when profit_usd >= be_activation_usd, lock be_stop_usd profit
    be_activation_usd: float = 0.25   # BE fires when profit >= this USD (e.g. 0.25 = $0.25 = 25 pts)
    be_stop_usd: float       = 0.15   # SL moves to entry + this USD profit equivalent
    min_hold_ms: float       = 2000.0 # no BE action in first N ms after fill


@dataclass
class TrailConfig:
    # Simplified trailing: activate at fixed pts from entry, trail with fixed gap + step
    trail_activation_points: float = 50.0   # trailing starts when profit >= X pts from entry
    trail_stop_points: float       = 20.0   # SL = extreme_price - trail_stop_points
    trail_step_points: float       = 20.0   # only move SL if improvement >= trail_step_points
    throttle_sec: float            = 0.5    # max 1 SL update per throttle_sec


@dataclass
class ConfirmConfig:
    window_ms: int               = 2000
    window_ticks: int            = 8
    k_confirm_atr: float         = 0.10
    k_confirm_spread: float      = 0.50
    confirm_min_points: float    = 10.0
    cooldown_on_fail_sec: float  = 300.0


# ── Formula functions (pure; no side-effects) ─────────────────────────────────

def calc_entry_offset(
    atr_pts: float,
    spread_med_pts: float,
    cfg: EntryConfig,
) -> float:
    """ENTRY_OFFSET_POINTS.
    aggressive mode: clamp(base, min, cap_atr*ATR) to avoid late-entry on high ATR.
    balanced/conservative: max(k_atr*ATR, k_spread*spread, min).
    """
    base = max(
        cfg.k_entry_atr * atr_pts,
        cfg.k_entry_spread * spread_med_pts,
    )
    if cfg.mode == "aggressive" and atr_pts > 0:
        cap = cfg.offset_cap_atr * atr_pts
        return max(cfg.entry_offset_min_points, min(base, cap))
    return max(base, cfg.entry_offset_min_points)


def calc_rearm_threshold(
    atr_pts: float,
    spread_med_pts: float,
    cfg: EntryConfig,
) -> float:
    """REARM_THRESHOLD_POINTS = max(k_rearm_atr*ATR, k_rearm_spread*spread_med, min)"""
    return max(
        cfg.k_rearm_atr * atr_pts,
        cfg.k_rearm_spread * spread_med_pts,
        cfg.rearm_min_points,
    )


def calc_sl_distance(
    atr_pts: float,
    spread_med_pts: float,
    cfg: EntryConfig,
) -> float:
    """SL_DISTANCE_POINTS = max(k_sl_atr*ATR, k_sl_spread*spread_med, sl_min_points)
    Capped at sl_max_points when sl_max_points > 0.
    """
    dist = max(
        cfg.k_sl_atr * atr_pts,
        cfg.k_sl_spread * spread_med_pts,
        cfg.sl_min_points,
    )
    if cfg.sl_max_points > 0:
        dist = min(dist, cfg.sl_max_points)
    return dist


def calc_confirm_move(
    atr_pts: float,
    spread_med_pts: float,
    cfg: ConfirmConfig,
) -> float:
    """CONFIRM_MOVE_POINTS = max(k_confirm_atr*ATR, k_confirm_spread*spread_med, min)"""
    return max(
        cfg.k_confirm_atr * atr_pts,
        cfg.k_confirm_spread * spread_med_pts,
        cfg.confirm_min_points,
    )


def calc_entry_buffer(
    atr_pts: float,
    spread_med_pts: float,
    cfg: "EntryConfig",
) -> float:
    """Extra buffer to push pending orders further from price.
    If entry_buffer_enabled=False, returns 0. Otherwise:
    buffer = max(entry_buffer_spread_mult*spread, entry_buffer_atr_mult*ATR, fixed_min_buffer).
    """
    if not cfg.entry_buffer_enabled:
        return 0.0
    return max(
        cfg.entry_buffer_spread_mult * spread_med_pts,
        cfg.entry_buffer_atr_mult * atr_pts,
        cfg.fixed_min_buffer,
    )


def calc_dollar_sl_points(
    target_risk_usd: float,
    value_per_point_per_lot: float,
    volume: float,
    trade_stops_level: int,
    safety_buffer_points: float = 10.0,
) -> int:
    """
    A) Compute initial SL distance in points so that the loss at SL == target_risk_usd.

    value_per_point_per_lot = tick_value * point / tick_size  (from SymbolSnapshot)
    Returns integer points; always >= trade_stops_level + safety_buffer_points.
    """
    import math
    value_per_point = value_per_point_per_lot * volume
    if value_per_point <= 0:
        return int(trade_stops_level + safety_buffer_points)
    raw = math.ceil(target_risk_usd / value_per_point)
    min_pts = int(trade_stops_level + safety_buffer_points)
    return max(raw, min_pts)



def calc_pnl_points(
    entry_price: float,
    exit_price: float,
    point: float,
    side: str,
) -> float:
    """Signed PnL in points (positive = profit)."""
    if point == 0:
        return 0.0
    raw = (exit_price - entry_price) / point
    return raw if side == "BUY" else -raw


def calc_value_per_point(tick_value: float, point: float, tick_size: float) -> float:
    """value_per_point = tick_value * point / tick_size"""
    if tick_size == 0:
        return 0.0
    return tick_value * point / tick_size


def round_to_step(value: float, step: float, digits: int = 5) -> float:
    """Round price to nearest step (e.g., symbol.point) for MT5 validity."""
    if step == 0:
        return value
    return round(round(value / step) * step, digits)
