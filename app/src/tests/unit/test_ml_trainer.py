"""
test_ml_trainer.py — Unit tests for ML trainer and filter.
"""

import pytest
import tempfile
import os
from pathlib import Path

from app.src.core.ml_trainer import (
    MLTrainer, MLFilter, MLFilterMode, TrainingConfig,
    TradeStats, EvaluationResult, CORE_FEATURES, get_feature_summary,
)


class TestMLFilterMode:
    """Tests for MLFilterMode enum."""
    
    def test_modes_exist(self):
        assert MLFilterMode.OFF.value == "OFF"
        assert MLFilterMode.SOFT_FILTER.value == "SOFT"
        assert MLFilterMode.HARD_FILTER.value == "HARD"


class TestTrainingConfig:
    """Tests for TrainingConfig dataclass."""
    
    def test_default_values(self):
        cfg = TrainingConfig()
        
        assert cfg.train_ratio == 0.75
        assert cfg.target_column == "label_good_trade"
        assert cfg.min_train_rows == 200
        assert cfg.soft_allow_threshold == 0.70
        assert cfg.soft_block_threshold == 0.40


class TestTradeStats:
    """Tests for TradeStats dataclass."""
    
    def test_to_dict(self):
        stats = TradeStats(
            trade_count=100,
            gross_profit=150.0,
            gross_loss=50.0,
            net_profit=100.0,
            profit_factor=3.0,
            winrate=0.65,
            avg_trade=1.0,
        )
        
        d = stats.to_dict()
        
        assert d["trade_count"] == 100
        assert d["gross_profit"] == 150.0
        assert d["profit_factor"] == 3.0
        assert d["winrate"] == 0.65


class TestEvaluationResult:
    """Tests for EvaluationResult dataclass."""
    
    def test_to_dict(self):
        result = EvaluationResult(
            accuracy=0.85,
            precision=0.75,
            recall=0.60,
            auc=0.82,
            threshold=0.55,
            rows_total=1000,
            rows_train=750,
            rows_test=250,
        )
        
        d = result.to_dict()
        
        assert d["accuracy"] == 0.85
        assert d["precision"] == 0.75
        assert d["auc"] == 0.82
        assert d["rows_total"] == 1000


class TestMLFilter:
    """Tests for MLFilter class."""
    
    def test_off_mode_allows_all(self):
        filter = MLFilter(
            model_path="nonexistent.pkl",
            mode=MLFilterMode.OFF,
        )
        
        proba, decision = filter.predict({"spread_pts": 50.0})
        
        assert proba == 1.0
        assert decision == "ALLOW"
    
    def test_no_model_allows_all(self):
        filter = MLFilter(
            model_path="nonexistent.pkl",
            mode=MLFilterMode.SOFT_FILTER,
        )
        
        # No model loaded
        assert not filter.is_loaded
        
        proba, decision = filter.predict({"spread_pts": 50.0})
        assert proba == 1.0
        assert decision == "ALLOW"
    
    def test_should_allow(self):
        filter = MLFilter(
            model_path="nonexistent.pkl",
            mode=MLFilterMode.OFF,
        )
        
        assert filter.should_allow({"spread_pts": 50.0}) is True
    
    def test_mode_change(self):
        filter = MLFilter(
            model_path="nonexistent.pkl",
            mode=MLFilterMode.OFF,
        )
        
        assert filter.mode == MLFilterMode.OFF
        
        filter.mode = MLFilterMode.SOFT_FILTER
        assert filter.mode == MLFilterMode.SOFT_FILTER


class TestCoreFeatures:
    """Tests for core feature definitions."""
    
    def test_mvp_features_in_core(self):
        mvp_features = [
            "range_last_30s_pts", "range_last_60s_pts", "range_last_180s_pts",
            "ticks_last_30s", "ticks_last_60s", "spread_atr_ratio",
            "time_to_fill_ms", "reprice_count_before_fill", "freeze_duration_ms",
            "market_state", "expansion_started", "flat_duration_sec",
        ]
        
        for feature in mvp_features:
            assert feature in CORE_FEATURES, f"MVP feature {feature} not in CORE_FEATURES"
    
    def test_session_features_in_core(self):
        assert "hour_utc" in CORE_FEATURES
        assert "day_of_week" in CORE_FEATURES
        assert "minute_of_session" in CORE_FEATURES


class TestFeatureSummary:
    """Tests for get_feature_summary helper."""
    
    def test_missing_csv(self):
        result = get_feature_summary("nonexistent.csv")
        
        assert "error" in result
        assert result["rows"] == 0
