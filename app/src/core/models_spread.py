"""
SpreadMedianModel – 5-minute rolling median of spread in points.

Responsibilities:
  - Accept (spread_points, timestamp_ms) samples
  - Maintain rolling 5-minute window (drop entries older than window)
  - Compute spread_med (median) and MAX_SPREAD_POINTS with adaptive clamp
  - Detect spread spikes
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class SpreadConfig:
    rolling_window_sec: float = 300.0    # 5 minutes
    k_maxspread: float        = 2.5
    maxspread_min: float      = 30.0     # points
    maxspread_cap: float      = 200.0    # points
    k_spike: float            = 3.5


@dataclass
class SpreadResult:
    spread_points: float
    spread_med_points: float
    max_spread_points: float
    is_spike: bool
    deny_spread: bool
    warm: bool                  # True once window has ≥1 sample


class SpreadMedianModel:
    """
    Rolling-median spread model with adaptive MAX_SPREAD computation.
    """

    def __init__(self, config: SpreadConfig) -> None:
        self._cfg = config
        self._samples: deque[tuple[float, float]] = deque()  # (timestamp_ms, spread_points)
        self._window_ms = config.rolling_window_sec * 1000.0

    def update(self, spread_points: float, timestamp_ms: float) -> SpreadResult:
        """
        Feed a new spread observation. Returns computed SpreadResult.
        threshold_points: current spread against which deny is evaluated.
        """
        # Evict samples outside rolling window
        cutoff = timestamp_ms - self._window_ms
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        self._samples.append((timestamp_ms, spread_points))

        spread_med = self._compute_median()
        max_spread = self._compute_max_spread(spread_med)
        is_spike   = spread_points > self._cfg.k_spike * spread_med if spread_med > 0 else False
        deny       = spread_points > max_spread

        return SpreadResult(
            spread_points=spread_points,
            spread_med_points=spread_med,
            max_spread_points=max_spread,
            is_spike=is_spike,
            deny_spread=deny,
            warm=len(self._samples) > 0,
        )

    def get_spread_med(self) -> float:
        return self._compute_median()

    def get_max_spread(self) -> float:
        return self._compute_max_spread(self._compute_median())

    def sample_count(self) -> int:
        return len(self._samples)

    # ── Private ──────────────────────────────────────────────────────────────

    def _compute_median(self) -> float:
        if not self._samples:
            return 0.0
        values = sorted(s[1] for s in self._samples)
        n = len(values)
        mid = n // 2
        if n % 2 == 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2.0

    def _compute_max_spread(self, spread_med: float) -> float:
        if spread_med <= 0:
            return self._cfg.maxspread_cap
        raw = self._cfg.k_maxspread * spread_med
        return max(self._cfg.maxspread_min, min(raw, self._cfg.maxspread_cap))
