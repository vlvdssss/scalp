"""
ATRModel – computes ATR(N) from M1 bars fetched from MetaTrader5.

P1-008: Exact formula – Wilder's ATR (single source of truth):
  TR_i   = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
  ATR_0  = SMA(TR[0..N-1])          -- seed (first N bars)
  ATR_t  = (ATR_{t-1} * (N-1) + TR_t) / N  -- Wilder smoothing for every bar after seed

When bars_fetch > N+1, full Wilder smoothing is applied over all available bars (more
accurate). When bars_fetch == N+1 (minimum), only the SMA seed is used.

The bars array (structured numpy array) has columns:
  time, open, high, low, close, tick_volume, spread, real_volume
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class ATRConfig:
    period: int     = 14
    bars_fetch: int = 100
    atr_min_points: float = 50.0
    ratio_max: float      = 0.40


@dataclass
class ATRResult:
    atr_points: float
    warm: bool
    deny_atr_min: bool       # ATR_points < atr_min_points
    deny_ratio: bool         # spread_med/ATR_points > ratio_max
    atr_method: str = "WILDER"  # P1-008 observability


class ATRModel:
    r"""
    Computes ATR(period) in points using Wilder's smoothing.

    Formula (P1-008 – single source of truth):
        ATR_0 = SMA(TR[0..N-1])
        ATR_t = (ATR_{t-1} * (N-1) + TR_t) / N

    Call compute_from_bars() each cycle.
    """

    def __init__(self, config: ATRConfig) -> None:
        self._cfg = config
        self._last_atr_points: float = 0.0
        self._warm = False

    def compute_from_bars(
        self,
        rates: Optional[np.ndarray],  # structured array from copy_rates_from_pos
        point: float,
        spread_med_points: float = 0.0,
    ) -> ATRResult:
        """
        Compute ATR from a rates array using Wilder's smoothing.
        rates must have at least (period + 2) entries.
        """
        n = self._cfg.period
        if rates is None or len(rates) < n + 2:
            # need at least n bars for TR + 1 prev_close anchor + 1 Wilder step
            log.warning(
                "ATR: insufficient bars (%s < %s)",
                0 if rates is None else len(rates), n + 2,
            )
            return ATRResult(
                atr_points=self._last_atr_points,
                warm=False,
                deny_atr_min=True,
                deny_ratio=True,
            )

        # copy_rates_from_pos returns closed+current bars. bars[-1] may be forming.
        # TR[i] uses high[i+1], low[i+1], close[i] as prev_close.
        # We exclude bars[-1] (current) via [1:-1]/[:-2] slicing.
        h1 = rates["high"][1:-1].astype(float)
        l1 = rates["low"][1:-1].astype(float)
        c0 = rates["close"][:-2].astype(float)   # prev close

        if len(h1) < n:
            return ATRResult(
                atr_points=self._last_atr_points,
                warm=False,
                deny_atr_min=True,
                deny_ratio=True,
            )

        tr = np.maximum.reduce([
            h1 - l1,
            np.abs(h1 - c0),
            np.abs(l1 - c0),
        ])

        # P1-008: Wilder smoothing
        # Seed: SMA of first N TR values
        atr_price = float(np.mean(tr[:n]))

        # Apply Wilder recursive formula for remaining bars
        for i in range(n, len(tr)):
            atr_price = (atr_price * (n - 1) + tr[i]) / n

        if point == 0:
            return ATRResult(atr_points=0.0, warm=False, deny_atr_min=True, deny_ratio=True)

        atr_pts = atr_price / point
        self._last_atr_points = atr_pts
        self._warm = True

        deny_atr  = atr_pts < self._cfg.atr_min_points
        deny_ratio = (
            (atr_pts > 0)
            and (spread_med_points / atr_pts) > self._cfg.ratio_max
        )

        return ATRResult(
            atr_points=atr_pts,
            warm=True,
            deny_atr_min=deny_atr,
            deny_ratio=deny_ratio,
            atr_method="WILDER",
        )

    def last_atr_points(self) -> float:
        return self._last_atr_points

    def is_warm(self) -> bool:
        return self._warm
