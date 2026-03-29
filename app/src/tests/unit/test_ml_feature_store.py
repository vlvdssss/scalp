"""
test_ml_feature_store.py — Unit tests for enhanced ML feature store.
"""

import pytest
import tempfile
import os
from pathlib import Path

from app.src.core.ml_feature_store import (
    MLFeatureStore, FeatureLogger, CostConfig, TickBuffer,
    estimate_trade_costs, MarketState, FEATURE_COLUMNS,
)


class TestTickBuffer:
    """Tests for TickBuffer class."""
    
    def test_add_tick(self):
        buf = TickBuffer(max_age_sec=60.0)
        buf.add_tick(1000.0, 2000.5, 2001.0)
        buf.add_tick(2000.0, 2000.6, 2001.1)
        
        # Should have 2 ticks
        assert len(buf._ticks) == 2
    
    def test_tick_pruning(self):
        buf = TickBuffer(max_age_sec=5.0)  # 5 second window
        
        # Add old tick
        buf.add_tick(1000.0, 2000.5, 2001.0)
        # Add recent tick (6 seconds later)
        buf.add_tick(7000.0, 2000.6, 2001.1)
        
        # Old tick should be pruned
        assert len(buf._ticks) == 1
    
    def test_get_range_pts(self):
        buf = TickBuffer(max_age_sec=60.0)
        point = 0.01
        
        buf.add_tick(1000.0, 2000.0, 2000.5)
        buf.add_tick(2000.0, 2001.0, 2001.5)
        buf.add_tick(3000.0, 2000.5, 2001.0)
        
        # Range = (2001.25 - 2000.25) / 0.01 = 100 pts
        range_pts = buf.get_range_pts(30.0, 3500.0, point)
        assert range_pts > 0
    
    def test_get_tick_count(self):
        buf = TickBuffer(max_age_sec=60.0)
        
        buf.add_tick(1000.0, 2000.0, 2000.5)
        buf.add_tick(2000.0, 2001.0, 2001.5)
        buf.add_tick(3000.0, 2000.5, 2001.0)
        
        # All 3 ticks within 30 seconds of now=3500
        count = buf.get_tick_count(30.0, 3500.0)
        assert count == 3


class TestCostEstimation:
    """Tests for cost estimation functions."""
    
    def test_estimate_trade_costs(self):
        cfg = CostConfig(
            commission_per_lot_usd=7.0,
            slippage_estimate_pts=5.0,
            point_value_per_lot=1.0,
            min_profitable_net_usd=0.20,
        )
        
        costs = estimate_trade_costs(
            entry_spread_pts=20.0,
            exit_spread_pts=25.0,
            volume=0.01,
            point_value=1.0,
            cfg=cfg,
        )
        
        # Expected: (20/2 + 25/2) * 0.01 + 7 * 0.01 + 5 * 0.01
        # = (10 + 12.5) * 0.01 + 0.07 + 0.05 = 0.225 + 0.07 + 0.05 = 0.345
        assert costs > 0
        assert costs < 1.0


class TestMLFeatureStore:
    """Tests for MLFeatureStore class."""
    
    def test_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "test_features.csv")
            db_path = os.path.join(tmpdir, "test_features.db")
            
            store = MLFeatureStore(csv_path=csv_path, db_path=db_path)
            
            assert Path(csv_path).exists()
            assert Path(db_path).exists()
            
            store.close()
    
    def test_record_tick(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MLFeatureStore(
                csv_path=os.path.join(tmpdir, "test.csv"),
                db_path=os.path.join(tmpdir, "test.db"),
            )
            
            store.record_tick(1000.0, 2000.0, 2000.5)
            store.record_tick(2000.0, 2001.0, 2001.5)
            
            # Verify ticks recorded
            assert store._tick_buffer.get_tick_count(30.0, 3000.0) == 2
            
            store.close()
    
    def test_market_state_classification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MLFeatureStore(
                csv_path=os.path.join(tmpdir, "test.csv"),
                db_path=os.path.join(tmpdir, "test.db"),
            )
            
            # Add some ticks for slow market (small range)
            for i in range(10):
                store.record_tick(1000.0 + i * 100, 2000.0, 2000.1)
            
            state, expansion, compression, vol = store.classify_market_state(
                now_ms=2000.0,
                point=0.01,
                atr_pts=100.0,
            )
            
            assert state in [MarketState.SLOW, MarketState.NORMAL, MarketState.IMPULSE]
            assert isinstance(expansion, bool)
            
            store.close()
    
    def test_on_fill_creates_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MLFeatureStore(
                csv_path=os.path.join(tmpdir, "test.csv"),
                db_path=os.path.join(tmpdir, "test.db"),
            )
            
            # Add some ticks first
            for i in range(20):
                store.record_tick(1000.0 + i * 100, 2000.0 + i * 0.1, 2000.5 + i * 0.1)
            
            trade_id = store.on_fill(
                side="BUY",
                entry_price=2001.0,
                bid=2000.5,
                ask=2001.0,
                atr_pts=100.0,
                spread_pts=50.0,
                spread_med_pts=45.0,
                candle_hi=2002.0,
                candle_lo=1999.0,
                point=0.01,
                is_flat=False,
                now_utc_ms=3000.0,
            )
            
            assert trade_id.startswith("T")
            assert trade_id in store._pending
            
            store.close()


class TestFeatureLoggerBackwardCompat:
    """Tests for backward-compatible FeatureLogger wrapper."""
    
    def test_on_fill_and_close(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "test.csv")
            logger = FeatureLogger(csv_path=csv_path)
            
            # Add ticks
            for i in range(10):
                logger.record_tick(1000.0 + i * 100, 2000.0, 2000.5)
            
            logger.on_fill(
                side="BUY",
                entry_price=2001.0,
                bid=2000.5,
                ask=2001.0,
                atr_pts=100.0,
                spread_pts=50.0,
                spread_med_pts=45.0,
                candle_hi=2002.0,
                candle_lo=1999.0,
                point=0.01,
                is_flat=False,
                now_utc_ms=3000.0,
            )
            
            assert logger._current_trade_id is not None
            
            logger.on_close(
                pnl_usd=0.50,
                pnl_pts=50.0,
                mae_pts=10.0,
                mfe_pts=60.0,
                exit_reason="trailing_sl",
                be_triggered=True,
                trail_triggered=True,
                trail_updates=3,
                trail_max_pts_locked=55.0,
            )
            
            assert logger._current_trade_id is None
            
            # Verify CSV has data
            with open(csv_path, "r") as f:
                lines = f.readlines()
                assert len(lines) >= 2  # header + 1 data row
            
            logger.close()


class TestFeatureColumns:
    """Test feature column definitions."""
    
    def test_all_mvp_features_present(self):
        mvp_features = [
            "range_last_30s_pts", "range_last_60s_pts", "range_last_180s_pts",
            "ticks_last_30s", "ticks_last_60s", "spread_atr_ratio",
            "time_to_fill_ms", "reprice_count_before_fill", "freeze_duration_ms",
            "market_state", "expansion_started", "flat_duration_sec",
            "net_pnl_usd_est", "label_good_trade",
        ]
        
        for feature in mvp_features:
            assert feature in FEATURE_COLUMNS, f"MVP feature {feature} missing"
    
    def test_target_columns_present(self):
        assert "label" in FEATURE_COLUMNS
        assert "label_good_trade" in FEATURE_COLUMNS
        assert "net_pnl_usd_est" in FEATURE_COLUMNS
