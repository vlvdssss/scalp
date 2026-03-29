"""
Unit tests for formula functions (risk.py) – no MT5 dependency.
"""
import pytest
from app.src.core.risk import (
    EntryConfig, BEConfig, TrailConfig, ConfirmConfig,
    calc_entry_offset, calc_rearm_threshold,
    calc_confirm_move,
    calc_pnl_points, calc_value_per_point, round_to_step,
)


EC = EntryConfig(
    k_entry_atr=0.30, k_entry_spread=1.20, entry_offset_min_points=30.0,
    k_rearm_atr=0.15, k_rearm_spread=0.60, rearm_min_points=15.0,
)
BC = BEConfig()
TC = TrailConfig()
CC = ConfirmConfig(window_ms=2000, window_ticks=8, k_confirm_atr=0.10,
                   k_confirm_spread=0.50, confirm_min_points=10.0)


class TestEntryOffset:
    def test_minimum_floor(self):
        """When ATR and spread are tiny, min floor dominates."""
        assert calc_entry_offset(1.0, 1.0, EC) == 30.0

    def test_atr_dominates(self):
        atr_pts = 200.0
        spread_med = 20.0
        result = calc_entry_offset(atr_pts, spread_med, EC)
        assert result == pytest.approx(0.30 * 200.0)  # 60.0

    def test_spread_dominates(self):
        atr_pts = 10.0
        spread_med = 50.0
        result = calc_entry_offset(atr_pts, spread_med, EC)
        assert result == pytest.approx(1.20 * 50.0)  # 60.0

    def test_all_equal(self):
        # Both k_entry_atr*ATR and k_entry_spread*spread reach 30
        result = calc_entry_offset(100.0, 25.0, EC)
        assert result == pytest.approx(max(30.0, 1.2 * 25.0))


class TestRearm:
    def test_floor(self):
        assert calc_rearm_threshold(1.0, 1.0, EC) == 15.0

    def test_atr_path(self):
        result = calc_rearm_threshold(200.0, 0.0, EC)
        assert result == pytest.approx(0.15 * 200.0)


class TestConfirm:
    def test_floor(self):
        assert calc_confirm_move(0.0, 0.0, CC) == 10.0

    def test_atr_path(self):
        result = calc_confirm_move(200.0, 0.0, CC)
        assert result == pytest.approx(0.10 * 200.0)


class TestBEConfig:
    def test_defaults(self):
        assert BC.be_activation_usd == 0.25
        assert BC.be_stop_usd == 0.15
        assert BC.min_hold_ms == 2000.0

    def test_custom(self):
        cfg = BEConfig(be_activation_usd=0.50, be_stop_usd=0.30)
        assert cfg.be_activation_usd == 0.50
        assert cfg.be_stop_usd == 0.30


class TestTrailConfig:
    def test_defaults(self):
        assert TC.trail_activation_points == 50.0
        assert TC.trail_stop_points == 20.0
        assert TC.trail_step_points == 20.0

    def test_custom(self):
        cfg = TrailConfig(trail_activation_points=100.0, trail_stop_points=30.0)
        assert cfg.trail_activation_points == 100.0
        assert cfg.trail_stop_points == 30.0


class TestPnL:
    def test_long_profit(self):
        pts = calc_pnl_points(1900.0, 1910.0, 0.01, "BUY")
        assert pts == pytest.approx(1000.0)  # 10 / 0.01

    def test_short_profit(self):
        pts = calc_pnl_points(1900.0, 1890.0, 0.01, "SELL")
        assert pts == pytest.approx(1000.0)

    def test_long_loss(self):
        pts = calc_pnl_points(1900.0, 1895.0, 0.01, "BUY")
        assert pts == pytest.approx(-500.0)

    def test_zero_point_guard(self):
        assert calc_pnl_points(100.0, 200.0, 0.0, "BUY") == 0.0


class TestValuePerPoint:
    def test_standard(self):
        # tick_value=1.0, point=0.01, tick_size=0.01 → 1.0
        assert calc_value_per_point(1.0, 0.01, 0.01) == pytest.approx(1.0)

    def test_xauusd_like(self):
        # XAUUSD: tick_value=0.01, point=0.01, tick_size=0.01
        assert calc_value_per_point(0.01, 0.01, 0.01) == pytest.approx(0.01)

    def test_zero_tick_size(self):
        assert calc_value_per_point(1.0, 0.01, 0.0) == 0.0


class TestRoundToStep:
    def test_round_to_point(self):
        result = round_to_step(1900.123456789, 0.01, 5)
        assert result == pytest.approx(1900.12)

    def test_zero_step(self):
        assert round_to_step(1900.5, 0.0) == 1900.5
