"""
Unit tests for ATRModel.compute_from_bars().
No MT5 connection required – operates on synthetic numpy arrays.
"""
import math
import numpy as np
import pytest

from app.src.core.models_atr import ATRModel, ATRResult, ATRConfig


def _make_rates(n: int, o=1900.0, h_add=5.0, l_sub=5.0, close_drift=0.0):
    """Build a structured-array of n bars (open, high, low, close, tick_volume, spread, real_volume)."""
    dtype = np.dtype([
        ("time", np.int64), ("open", np.float64), ("high", np.float64),
        ("low", np.float64), ("close", np.float64), ("tick_volume", np.int64),
        ("spread", np.int32), ("real_volume", np.int64),
    ])
    bars = np.zeros(n, dtype=dtype)
    c = o
    for i in range(n):
        bars["open"][i]  = c
        bars["high"][i]  = c + h_add
        bars["low"][i]   = c - l_sub
        bars["close"][i] = c + close_drift
        bars["time"][i]  = i * 60
        c += close_drift
    return bars


class TestInsufficientBars:
    def test_fewer_bars_than_period(self):
        cfg = ATRConfig(period=14, atr_min_points=20.0, ratio_max=4.0)
        model = ATRModel(cfg)
        rates = _make_rates(10)  # only 10 bars, need at least 15 (14+1)
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=20.0)
        assert result is None or not result.warm

    def test_exactly_period_plus_one(self):
        """ATR(14) needs period+2=16 bars: 14 complete + 1 prev_close anchor + 1 partial to exclude."""
        cfg = ATRConfig(period=14)
        model = ATRModel(cfg)
        rates = _make_rates(16, h_add=5.0, l_sub=5.0)  # period+2
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=20.0)
        assert result is not None
        assert result.warm


class TestATRFormula:
    def test_simple_constant_bars(self):
        """All bars identical H-L range, no gaps → ATR = H-L."""
        cfg = ATRConfig(period=14)
        model = ATRModel(cfg)
        # h_add=5.0, l_sub=5.0 → H-L = 10.0 price units = 1000 pts (point=0.01)
        rates = _make_rates(16, h_add=5.0, l_sub=5.0)
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=20.0)
        assert result is not None
        assert result.warm
        # TR each bar = 10.0, ATR = 10.0 / 0.01 = 1000 pts
        assert abs(result.atr_points - 1000.0) < 1.0

    def test_wilder_initialization(self):
        """First ATR = arithmetic mean of first N TRs."""
        cfg = ATRConfig(period=5)
        model = ATRModel(cfg)
        rates = _make_rates(7, h_add=3.0, l_sub=3.0, close_drift=0.0)
        # TR = H-L = 6 price units for every bar (no gaps, identical bars)
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=2.0)
        assert result is not None
        expected_pts = 6.0 / 0.01  # 600
        assert abs(result.atr_points - expected_pts) < 5.0

    def test_increasing_ranges_yields_higher_atr(self):
        """Doubling H-L range should roughly double ATR."""
        cfg = ATRConfig(period=14)
        model = ATRModel(cfg)
        r_small = _make_rates(16, h_add=2.0, l_sub=2.0)
        r_large = _make_rates(16, h_add=8.0, l_sub=8.0)
        res_small = model.compute_from_bars(r_small, point=0.01, spread_med_points=20.0)
        res_large = model.compute_from_bars(r_large, point=0.01, spread_med_points=20.0)
        assert res_large.atr_points > res_small.atr_points * 3.5

    def test_uses_n_bars_not_n_plus_one(self):
        """Last bar (current partial) must be excluded from ATR computation."""
        cfg = ATRConfig(period=4)
        model = ATRModel(cfg)
        # Bars 0-4: narrow (1 pt each side); bar 5 (current/last): very wide (1000 pts)
        rates = _make_rates(6, h_add=0.5, l_sub=0.5)
        rates["high"][-1] = rates["open"][-1] + 1000  # huge current bar
        rates["low"][-1]  = rates["open"][-1] - 1000
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=5.0)
        # ATR should be tiny (based on bars 0-4), not huge
        assert result.atr_points < 200


class TestATRDenyFlags:
    def test_deny_below_min(self):
        """ATR below deny_atr_min → deny_atr_min flag set."""
        cfg = ATRConfig(period=14, atr_min_points=2000.0, ratio_max=99.0)
        model = ATRModel(cfg)
        rates = _make_rates(16, h_add=1.0, l_sub=1.0)  # small range → ATR ≈ 200 pts
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=5.0)
        assert result.deny_atr_min is True

    def test_no_deny_above_min(self):
        """ATR above deny_atr_min → deny_atr_min flag NOT set."""
        cfg = ATRConfig(period=14, atr_min_points=50.0, ratio_max=99.0)
        model = ATRModel(cfg)
        rates = _make_rates(16, h_add=5.0, l_sub=5.0)  # ATR ≈ 1000 pts
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=5.0)
        assert result.deny_atr_min is False

    def test_deny_ratio(self):
        """spread_med_pts / ATR > ratio_max → deny_ratio flag set."""
        cfg = ATRConfig(period=14, atr_min_points=0.0, ratio_max=0.40)
        model = ATRModel(cfg)
        rates = _make_rates(16, h_add=0.1, l_sub=0.1)  # tiny ATR
        # spread_med = 1000 pts; ATR is very small → ratio huge
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=1000.0)
        assert result.deny_ratio is True

    def test_no_deny_ratio_normal(self):
        """Normal market: spread << ATR → deny_ratio NOT set."""
        cfg = ATRConfig(period=14, atr_min_points=0.0, ratio_max=0.40)
        model = ATRModel(cfg)
        rates = _make_rates(16, h_add=10.0, l_sub=10.0)  # ATR ≈ 2000 pts
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=20.0)
        assert result.deny_ratio is False


class TestATRMoreBarsImprovement:
    def test_more_bars_does_not_crash(self):
        cfg = ATRConfig(period=14)
        model = ATRModel(cfg)
        rates = _make_rates(200)
        result = model.compute_from_bars(rates, point=0.01, spread_med_points=20.0)
        assert result is not None
        assert result.warm
        assert result.atr_points > 0
