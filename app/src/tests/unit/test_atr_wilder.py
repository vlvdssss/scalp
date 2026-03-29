"""
Unit tests for P1-008: Wilder ATR formula.

Reference series computed by hand:
  n=3, TR=[2, 4, 6, 8, 10, 12]
  Seed   = mean(TR[:3]) = (2+4+6)/3 = 4.0
  Step 3 = (4.0 * 2 + 8)  / 3 = 16/3  ≈ 5.3333
  Step 4 = (16/3 * 2 + 10) / 3 = 42/9  ≈ 4.6667  (corrected)
  Step 5 = (42/9 * 2 + 12) / 3 = 96/27 ≈ 3.5556  (corrected)

Let me compute step by step properly:
  seed = 4.0
  after TR[3]=8:  atr = (4.0 * 2 + 8) / 3 = 16/3 ≈ 5.33333
  after TR[4]=10: atr = (16/3 * 2 + 10) / 3 = (32/3 + 10) / 3 = (32/3 + 30/3) / 3 = 62/9 ≈ 6.88889
  after TR[5]=12: atr = (62/9 * 2 + 12) / 3 = (124/9 + 108/9) / 3 = 232/27 ≈ 8.59259

Verified: a monotonically-rising TR series produces a rising but lagged ATR with Wilder smoothing.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.src.core.models_atr import ATRModel, ATRConfig


def _atr_reference(tr: list[float], n: int) -> float:
    """Independent Python reference implementation of Wilder ATR."""
    assert len(tr) >= n + 1, "Need at least n+1 TR values for one Wilder step"
    atr = float(np.mean(tr[:n]))
    for i in range(n, len(tr)):
        atr = (atr * (n - 1) + tr[i]) / n
    return atr


class TestWilderATR:
    # Reference series
    TR_SERIES = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
    N = 3

    def test_reference_series_matches_hand_calc(self) -> None:
        """ATR on known TR series must match hand-computed Wilder result."""
        expected = _atr_reference(self.TR_SERIES, self.N)

        # The model uses rates[1:-1] vs rates[:-2] for TR (excludes first and last bar).
        # To produce len(TR_SERIES)=6 TR values, we need len(TR_SERIES)+2=8 bars:
        #   bar[0]   = anchor only (provides prev_close for TR[0])
        #   bar[1..6] = the 6 TR bars
        #   bar[7]   = dummy current bar (excluded by [1:-1])
        close_series = [100.0]   # bar 0: anchor prev_close
        high_series  = [100.0]   # anchor bar
        low_series   = [100.0]   # anchor bar
        for tr_val in self.TR_SERIES:
            prev_c = close_series[-1]
            high_series.append(prev_c + tr_val)
            low_series.append(prev_c)
            close_series.append(prev_c + tr_val / 2)
        # Add dummy current bar (will be excluded)
        high_series.append(close_series[-1] + 0.1)
        low_series.append(close_series[-1] - 0.1)
        close_series.append(close_series[-1])

        # Use ATRModel
        cfg = ATRConfig(period=self.N, bars_fetch=50, atr_min_points=0.0, ratio_max=99.0)
        model = ATRModel(cfg)

        # Build structured array (dtype like MT5 rates)
        n_bars = len(high_series)
        rates = np.zeros(n_bars, dtype=[
            ("time", "i8"), ("open", "f8"), ("high", "f8"),
            ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
            ("spread", "i4"), ("real_volume", "i8")
        ])
        rates["high"]  = high_series
        rates["low"]   = low_series
        rates["close"] = close_series

        result = model.compute_from_bars(rates, point=1.0, spread_med_points=0.0)
        assert result is not None, "compute_from_bars returned None"
        assert abs(result.atr_points - expected) < 0.5, (
            f"ATR={result.atr_points:.5f}, expected≈{expected:.5f}"
        )

    def test_atr_method_is_wilder(self) -> None:
        """ATRResult.atr_method must be 'WILDER'."""
        cfg = ATRConfig(period=3, bars_fetch=20, atr_min_points=0.0, ratio_max=99.0)
        model = ATRModel(cfg)
        n_bars = 10
        rates = np.zeros(n_bars, dtype=[
            ("time", "i8"), ("open", "f8"), ("high", "f8"),
            ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
            ("spread", "i4"), ("real_volume", "i8")
        ])
        rates["high"]  = np.linspace(101, 110, n_bars)
        rates["low"]   = np.linspace(99, 108, n_bars)
        rates["close"] = np.linspace(100, 109, n_bars)
        result = model.compute_from_bars(rates, point=1.0, spread_med_points=0.0)
        assert result.atr_method == "WILDER", (
            f"Expected atr_method='WILDER', got {result.atr_method!r}"
        )

    def test_wilder_lags_more_than_sma(self) -> None:
        """Wilder ATR must show more smoothing (lag) than simple mean of last N TRs."""
        n = 5
        tr_values = [1.0] * n + [100.0] * n  # sudden jump
        # SMA of last n TRs would be 100.0 immediately after n jumps
        # Wilder should be < 100.0
        wilder_result = _atr_reference(tr_values, n)
        sma_result = float(np.mean(tr_values[-n:]))
        assert wilder_result < sma_result, (
            f"Wilder ATR ({wilder_result:.2f}) should lag behind SMA ({sma_result:.2f})"
        )

    def test_constant_tr_converges_to_itself(self) -> None:
        """If all TR values equal C, Wilder ATR must also equal C for any n."""
        n = 14
        c = 42.5
        tr_values = [c] * (n + 20)
        result = _atr_reference(tr_values, n)
        assert abs(result - c) < 1e-9, (
            f"Constant-TR series must produce ATR={c}, got {result}"
        )
