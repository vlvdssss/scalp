"""
Unit tests for SpreadMedianModel – no MT5 dependency.
"""
import pytest
from app.src.core.models_spread import SpreadMedianModel, SpreadConfig


CFG = SpreadConfig(
    rolling_window_sec=300.0,
    k_maxspread=2.5,
    maxspread_min=30.0,
    maxspread_cap=200.0,
    k_spike=3.5,
)


def _make_model() -> SpreadMedianModel:
    return SpreadMedianModel(CFG)


class TestSpreadMedian:
    def test_empty_window(self):
        m = _make_model()
        assert m.get_spread_med() == 0.0

    def test_single_sample(self):
        m = _make_model()
        r = m.update(50.0, 1_000_000)
        assert r.spread_med_points == pytest.approx(50.0)
        assert r.warm is True

    def test_odd_median(self):
        m = _make_model()
        for i, v in enumerate([10.0, 30.0, 20.0]):
            m.update(v, float(i * 1000))
        assert m.get_spread_med() == pytest.approx(20.0)

    def test_even_median(self):
        m = _make_model()
        for i, v in enumerate([10.0, 20.0, 30.0, 40.0]):
            m.update(v, float(i * 1000))
        assert m.get_spread_med() == pytest.approx(25.0)

    def test_rolling_eviction(self):
        """Samples older than 300s should be evicted."""
        m = _make_model()
        # Old sample at t=0
        m.update(1000.0, 0.0)
        # New sample at t=301s (in ms = 301000)
        r = m.update(20.0, 301_000)
        assert r.spread_med_points == pytest.approx(20.0)

    def test_warmup_before_filled(self):
        m = _make_model()
        assert m.sample_count() == 0
        m.update(40.0, 0.0)
        assert m.sample_count() == 1

    def test_max_spread_clamp_low(self):
        """max_spread should not go below maxspread_min."""
        m = _make_model()
        r = m.update(10.0, 0.0)   # med=10, raw=25 < min=30 → clamp to 30
        assert r.max_spread_points == pytest.approx(CFG.maxspread_min)

    def test_max_spread_clamp_high(self):
        """max_spread should not exceed maxspread_cap."""
        m = _make_model()
        r = m.update(200.0, 0.0)  # med=200, raw=500 > cap=200 → clamp to 200
        assert r.max_spread_points == pytest.approx(CFG.maxspread_cap)

    def test_deny_spread_true(self):
        m = _make_model()
        m.update(50.0, 0.0)
        r = m.update(500.0, 1000.0)  # 500 >> max_spread (capped at 200) → deny
        assert r.deny_spread is True

    def test_deny_spread_false(self):
        m = _make_model()
        for _ in range(10):
            m.update(50.0, 0.0)
        r = m.update(60.0, 100.0)
        assert r.deny_spread is False

    def test_spike_detection(self):
        m = _make_model()
        m.update(20.0, 0.0)
        m.update(20.0, 500.0)
        r = m.update(20.0 * 3.5 + 1.0, 1000.0)  # above k_spike * med
        assert r.is_spike is True

    def test_no_spike(self):
        m = _make_model()
        m.update(20.0, 0.0)
        r = m.update(25.0, 1000.0)
        assert r.is_spike is False
