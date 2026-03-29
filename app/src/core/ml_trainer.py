"""
ml_trainer.py — Enhanced ML Training with cost-adjusted targets and comprehensive metrics.

Provides:
  - Time-based train/test split (no data leakage)
  - Training on label_good_trade (net profitability after costs)
  - Comprehensive evaluation metrics
  - Baseline vs ML-filtered comparison
  - Feature importance analysis
  - Soft/Hard/Off filter modes
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


# ── Filter Modes ──────────────────────────────────────────────────────────────

class MLFilterMode(Enum):
    """ML Filter operating modes."""
    OFF = "OFF"                # Model disabled, no impact on trading
    SOFT_FILTER = "SOFT"       # Model logs recommendations, doesn't block
    HARD_FILTER = "HARD"       # Model actively blocks low-probability trades


# ── Training Configuration ────────────────────────────────────────────────────

@dataclass
class TrainingConfig:
    """Configuration for ML training."""
    # Train/Test split
    train_ratio: float = 0.75              # 75% train, 25% test (time-based)
    
    # Target column
    target_column: str = "label_good_trade"  # Net profitability target
    
    # Minimum data requirements
    min_train_rows: int = 200
    min_test_rows: int = 50
    
    # Model parameters
    n_estimators: int = 100
    max_depth: int = 4
    learning_rate: float = 0.05
    subsample: float = 0.8
    
    # Threshold selection
    min_precision_target: float = 0.60     # Minimum precision for threshold selection
    threshold_range: Tuple[float, float] = (0.40, 0.80)
    
    # Filter mode thresholds
    soft_allow_threshold: float = 0.70     # Allow if proba >= this
    soft_block_threshold: float = 0.40     # Block if proba <= this
    hard_threshold: float = 0.50           # Hard cutoff for HARD mode
    
    # Cost estimation for comparison
    commission_per_round_turn: float = 7.0
    slippage_estimate_pts: float = 5.0
    volume: float = 0.01
    point_value: float = 1.0


# ── Feature Selection ─────────────────────────────────────────────────────────

# Core features to use in training (MVP set from plan)
CORE_FEATURES = [
    # Session/Time
    "hour_utc", "day_of_week", "minute_of_session",
    
    # Volatility basic
    "atr_pts", "spread_pts", "spread_med_pts", "rel_spread",
    "candle_range_pts", "candle_range_ratio",
    
    # Market range (NEW MVP)
    "range_last_30s_pts", "range_last_60s_pts", "range_last_180s_pts",
    
    # Tick activity (NEW MVP)
    "ticks_last_30s", "ticks_last_60s",
    
    # Spread ratios (NEW MVP)
    "spread_atr_ratio",
    
    # Entry pattern
    "side", "offset_pts", "is_flat",
    
    # Order behavior (NEW MVP)
    "time_to_fill_ms", "reprice_count_before_fill",
    "freeze_duration_ms", "was_frozen_before_fill",
    
    # Market state (NEW MVP)
    "market_state", "expansion_started", "flat_duration_sec",
    
    # Trade context
    "wins_last_5", "time_since_last_trade_sec",
    "trail_rate_last_5", "avg_mfe_last_5", "be_rate_last_5",
    "prev_5_trades_winrate", "prev_5_trades_avg_pnl",
]


# ── Evaluation Metrics ────────────────────────────────────────────────────────

@dataclass
class TradeStats:
    """Trading statistics for a set of trades."""
    trade_count: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    winrate: float = 0.0
    avg_trade: float = 0.0
    estimated_costs: float = 0.0
    net_after_costs: float = 0.0
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "trade_count": self.trade_count,
            "gross_profit": round(self.gross_profit, 2),
            "gross_loss": round(self.gross_loss, 2),
            "net_profit": round(self.net_profit, 2),
            "profit_factor": round(self.profit_factor, 3),
            "winrate": round(self.winrate, 4),
            "avg_trade": round(self.avg_trade, 4),
            "estimated_costs": round(self.estimated_costs, 2),
            "net_after_costs": round(self.net_after_costs, 2),
        }


@dataclass
class EvaluationResult:
    """Complete evaluation result from training."""
    # Model metrics
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    auc: float = 0.0
    threshold: float = 0.5
    
    # Data info
    rows_total: int = 0
    rows_train: int = 0
    rows_test: int = 0
    good_trades_train: int = 0
    bad_trades_train: int = 0
    good_trades_test: int = 0
    bad_trades_test: int = 0
    neutral_dropped: int = 0
    
    # Feature info
    feature_count: int = 0
    feature_importance: Dict[str, float] = field(default_factory=dict)
    top_features: List[Tuple[str, float]] = field(default_factory=list)
    
    # Confusion matrix
    confusion_matrix: List[List[int]] = field(default_factory=lambda: [[0, 0], [0, 0]])
    
    # Trading comparison
    baseline_stats: TradeStats = field(default_factory=TradeStats)
    filtered_stats: TradeStats = field(default_factory=TradeStats)
    blocked_stats: TradeStats = field(default_factory=TradeStats)
    
    # Filter stats
    allowed_trade_ratio: float = 0.0
    blocked_trade_ratio: float = 0.0
    
    # Training metadata
    trained_at: str = ""
    target_used: str = ""
    cost_estimate_used: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "auc": round(self.auc, 4),
            "threshold": round(self.threshold, 3),
            "rows_total": self.rows_total,
            "rows_train": self.rows_train,
            "rows_test": self.rows_test,
            "good_trades_train": self.good_trades_train,
            "bad_trades_train": self.bad_trades_train,
            "neutral_dropped": self.neutral_dropped,
            "feature_count": self.feature_count,
            "top_features": self.top_features[:10],
            "confusion_matrix": self.confusion_matrix,
            "baseline_stats": self.baseline_stats.to_dict(),
            "filtered_stats": self.filtered_stats.to_dict(),
            "blocked_stats": self.blocked_stats.to_dict(),
            "allowed_trade_ratio": round(self.allowed_trade_ratio, 4),
            "blocked_trade_ratio": round(self.blocked_trade_ratio, 4),
            "trained_at": self.trained_at,
            "target_used": self.target_used,
            "cost_estimate_used": self.cost_estimate_used,
        }


# ── ML Filter Model ───────────────────────────────────────────────────────────

class MLFilter:
    """
    ML Filter for entry quality prediction.
    
    Supports three modes:
      - OFF: No filtering, model not used
      - SOFT_FILTER: Logs recommendations, doesn't block trades
      - HARD_FILTER: Actively blocks low-probability trades
    """
    
    def __init__(
        self,
        model_path: str = "models/ml_filter_v2.pkl",
        mode: MLFilterMode = MLFilterMode.SOFT_FILTER,
        cfg: Optional[TrainingConfig] = None,
    ):
        self._model_path = Path(model_path)
        self._mode = mode
        self._cfg = cfg or TrainingConfig()
        
        self._model: Any = None
        self._feature_cols: List[str] = []
        self._threshold: float = 0.5
        self._metrics: Dict[str, Any] = {}
        self._loaded = False
        
        self._load_model()
    
    @property
    def mode(self) -> MLFilterMode:
        return self._mode
    
    @mode.setter
    def mode(self, value: MLFilterMode) -> None:
        self._mode = value
        log.info("MLFilter mode changed to: %s", value.value)
    
    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._model is not None
    
    @property
    def metrics(self) -> Dict[str, Any]:
        return self._metrics
    
    def _load_model(self) -> bool:
        """Load model from disk."""
        if not self._model_path.exists():
            log.debug("MLFilter: model file not found: %s", self._model_path)
            return False
        
        try:
            with open(self._model_path, "rb") as f:
                payload = pickle.load(f)
            
            self._model = payload.get("model")
            self._feature_cols = payload.get("feature_cols", [])
            self._threshold = payload.get("threshold", 0.5)
            self._metrics = payload.get("metrics", {})
            self._loaded = True
            
            log.info(
                "MLFilter loaded: features=%d threshold=%.2f auc=%.3f",
                len(self._feature_cols),
                self._threshold,
                self._metrics.get("auc", 0),
            )
            return True
        except Exception as e:
            log.error("MLFilter load error: %s", e)
            return False
    
    def predict(self, features: Dict[str, Any]) -> Tuple[float, str]:
        """
        Predict trade quality.
        
        Returns:
            (probability, decision) where decision is "ALLOW", "BLOCK", or "NEUTRAL"
        """
        if self._mode == MLFilterMode.OFF:
            return 1.0, "ALLOW"
        
        if not self.is_loaded:
            return 1.0, "ALLOW"  # No model = allow all
        
        try:
            # Prepare feature vector
            X = self._prepare_features(features)
            if X is None:
                return 1.0, "ALLOW"
            
            # Get probability
            proba = float(self._model.predict_proba(X)[0, 1])
            
            # Make decision based on mode
            if self._mode == MLFilterMode.SOFT_FILTER:
                if proba >= self._cfg.soft_allow_threshold:
                    decision = "ALLOW"
                elif proba <= self._cfg.soft_block_threshold:
                    decision = "BLOCK"
                else:
                    decision = "NEUTRAL"
            else:  # HARD_FILTER
                decision = "ALLOW" if proba >= self._cfg.hard_threshold else "BLOCK"
            
            return proba, decision
            
        except Exception as e:
            log.error("MLFilter predict error: %s", e)
            return 1.0, "ALLOW"
    
    def _prepare_features(self, features: Dict[str, Any]) -> Optional[np.ndarray]:
        """Prepare feature vector for prediction."""
        try:
            import pandas as pd
            
            # Encode categorical features
            feat_copy = dict(features)
            if "side" in feat_copy and isinstance(feat_copy["side"], str):
                feat_copy["side"] = 1 if feat_copy["side"] == "BUY" else 0
            if "market_state" in feat_copy:
                state_map = {"SLOW": 0, "NORMAL": 1, "IMPULSE": 2}
                feat_copy["market_state"] = state_map.get(feat_copy["market_state"], 1)
            if "session" in feat_copy:
                sess_map = {"ASIA": 0, "LONDON": 1, "OVERLAP": 2, "NY": 3, "QUIET": 4}
                feat_copy["session_enc"] = sess_map.get(feat_copy["session"], 2)
            
            # Build feature vector in correct order
            X = []
            for col in self._feature_cols:
                val = feat_copy.get(col, 0.0)
                if val is None:
                    val = 0.0
                X.append(float(val))
            
            return np.array([X])
            
        except Exception as e:
            log.error("MLFilter feature prep error: %s", e)
            return None
    
    def should_allow(self, features: Dict[str, Any]) -> bool:
        """Quick check if trade should be allowed."""
        if self._mode == MLFilterMode.OFF:
            return True
        proba, decision = self.predict(features)
        if self._mode == MLFilterMode.SOFT_FILTER:
            return decision != "BLOCK"  # Allow unless explicitly blocked
        return decision == "ALLOW"


# ── ML Trainer ────────────────────────────────────────────────────────────────

class MLTrainer:
    """
    ML model trainer with comprehensive evaluation.
    
    Features:
      - Time-based train/test split
      - Cost-adjusted target (label_good_trade)
      - Baseline vs filtered comparison
      - Feature importance analysis
    """
    
    def __init__(
        self,
        csv_path: str = "logs/ml_features.csv",
        model_path: str = "models/ml_filter_v2.pkl",
        cfg: Optional[TrainingConfig] = None,
    ):
        self._csv_path = Path(csv_path)
        self._model_path = Path(model_path)
        self._cfg = cfg or TrainingConfig()
    
    def train(self, progress_cb=None) -> EvaluationResult:
        """
        Train model and return comprehensive evaluation.
        
        Args:
            progress_cb: Optional callback(pct: int, msg: str) for progress updates
        
        Returns:
            EvaluationResult with all metrics and comparisons
        """
        result = EvaluationResult()
        
        def emit(pct: int, msg: str):
            if progress_cb:
                progress_cb(pct, msg)
            log.info("Training [%d%%]: %s", pct, msg)
        
        try:
            emit(5, "Loading data...")
            
            import pandas as pd
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.metrics import (
                accuracy_score, precision_score, recall_score,
                f1_score, roc_auc_score, confusion_matrix
            )
            
            # Load data
            if not self._csv_path.exists():
                raise ValueError(f"CSV not found: {self._csv_path}")
            
            df = pd.read_csv(self._csv_path)
            result.rows_total = len(df)
            
            if len(df) < self._cfg.min_train_rows:
                raise ValueError(f"Need at least {self._cfg.min_train_rows} rows, got {len(df)}")
            
            emit(10, f"Loaded {len(df)} rows")
            
            # ── Prepare target ───────────────────────────────────────────────
            target_col = self._cfg.target_column
            if target_col not in df.columns:
                # Fallback to legacy label
                log.warning("Target column %s not found, using 'label'", target_col)
                target_col = "label"
            
            result.target_used = target_col
            
            # Handle neutral trades (pnl between 0 and threshold)
            if "net_pnl_usd_est" in df.columns:
                # Drop neutral trades (in grey zone)
                neutral_mask = (df["net_pnl_usd_est"] > 0) & (df["net_pnl_usd_est"] <= 0.20)
                result.neutral_dropped = int(neutral_mask.sum())
                df = df[~neutral_mask].copy()
                emit(15, f"Dropped {result.neutral_dropped} neutral trades")
            
            # ── Prepare features ─────────────────────────────────────────────
            emit(20, "Preparing features...")
            
            # Select available features
            feature_cols = [c for c in CORE_FEATURES if c in df.columns]
            
            # Encode categorical features
            if "side" in df.columns:
                df["side"] = (df["side"].astype(str) == "BUY").astype(int) | \
                             (df["side"].astype(str) == "0").astype(int)
            
            if "market_state" in df.columns:
                state_map = {"SLOW": 0, "NORMAL": 1, "IMPULSE": 2}
                df["market_state"] = df["market_state"].map(state_map).fillna(1).astype(int)
            
            if "session" in df.columns:
                sess_map = {"ASIA": 0, "LONDON": 1, "OVERLAP": 2, "NY": 3, "QUIET": 4}
                df["session_enc"] = df["session"].map(sess_map).fillna(2).astype(int)
                if "session_enc" not in feature_cols:
                    feature_cols.append("session_enc")
            
            X = df[feature_cols].fillna(0)
            y = df[target_col]
            
            result.feature_count = len(feature_cols)
            emit(25, f"Using {len(feature_cols)} features")
            
            # ── Time-based split ─────────────────────────────────────────────
            emit(30, "Splitting data (time-based)...")
            
            split_idx = int(len(df) * self._cfg.train_ratio)
            X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
            y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
            df_test = df.iloc[split_idx:]
            
            result.rows_train = len(X_train)
            result.rows_test = len(X_test)
            result.good_trades_train = int((y_train == 1).sum())
            result.bad_trades_train = int((y_train == 0).sum())
            result.good_trades_test = int((y_test == 1).sum())
            result.bad_trades_test = int((y_test == 0).sum())
            
            # Log target distribution (important for class balance)
            good_pct_train = result.good_trades_train / len(y_train) * 100 if len(y_train) > 0 else 0
            good_pct_test = result.good_trades_test / len(y_test) * 100 if len(y_test) > 0 else 0
            log.info("Target distribution: Train %.1f%% good, Test %.1f%% good", good_pct_train, good_pct_test)
            
            if good_pct_train < 20 or good_pct_train > 80:
                log.warning("Class imbalance detected: %.1f%% good trades. Consider adjusting min_profitable_net_usd or costs.", good_pct_train)
            
            if len(X_test) < self._cfg.min_test_rows:
                log.warning("Test set small: %d rows", len(X_test))
            
            emit(35, f"Train: {len(X_train)} ({good_pct_train:.0f}% good), Test: {len(X_test)} ({good_pct_test:.0f}% good)")
            
            # ── Train model ──────────────────────────────────────────────────
            emit(40, "Training GradientBoosting...")
            
            model = GradientBoostingClassifier(
                n_estimators=self._cfg.n_estimators,
                max_depth=self._cfg.max_depth,
                learning_rate=self._cfg.learning_rate,
                subsample=self._cfg.subsample,
                random_state=42,
            )
            
            model.fit(X_train, y_train)
            emit(70, "Model trained, evaluating...")
            
            # ── Evaluate ─────────────────────────────────────────────────────
            proba = model.predict_proba(X_test)[:, 1]
            
            # Threshold sweep for precision target
            best_thresh, best_prec = 0.5, 0.0
            for t in np.arange(self._cfg.threshold_range[0], 
                               self._cfg.threshold_range[1], 0.02):
                pred = (proba >= t).astype(int)
                if pred.sum() > 5:
                    p = precision_score(y_test, pred, zero_division=0)
                    if p >= self._cfg.min_precision_target and p > best_prec:
                        best_prec, best_thresh = p, t
            
            y_pred = (proba >= best_thresh).astype(int)
            
            result.accuracy = float(accuracy_score(y_test, y_pred))
            result.precision = float(precision_score(y_test, y_pred, zero_division=0))
            result.recall = float(recall_score(y_test, y_pred, zero_division=0))
            result.f1 = float(f1_score(y_test, y_pred, zero_division=0))
            try:
                result.auc = float(roc_auc_score(y_test, proba))
            except:
                result.auc = 0.5
            result.threshold = float(best_thresh)
            
            cm = confusion_matrix(y_test, y_pred)
            result.confusion_matrix = cm.tolist()
            
            emit(80, f"AUC={result.auc:.3f} Precision={result.precision:.3f}")
            
            # ── Feature importance ───────────────────────────────────────────
            importances = model.feature_importances_
            result.feature_importance = {
                col: round(float(imp), 4) 
                for col, imp in zip(feature_cols, importances)
            }
            sorted_imp = sorted(
                result.feature_importance.items(),
                key=lambda x: x[1],
                reverse=True
            )
            result.top_features = sorted_imp[:15]
            
            # ── Trading comparison ───────────────────────────────────────────
            emit(85, "Computing trading stats...")
            
            # Get PnL columns for test set
            pnl_col = "pnl_usd" if "pnl_usd" in df_test.columns else None
            net_pnl_col = "net_pnl_usd_est" if "net_pnl_usd_est" in df_test.columns else None
            
            if pnl_col:
                # Baseline stats (all trades)
                pnl_vals = np.asarray(df_test[pnl_col].values)
                net_vals = np.asarray(df_test[net_pnl_col].values) if net_pnl_col and net_pnl_col in df_test.columns else None
                result.baseline_stats = self._calc_trade_stats(pnl_vals, net_vals)
                
                # Filtered stats (allowed by model)
                allowed_mask = y_pred == 1
                if allowed_mask.any():
                    pnl_allowed = df_test[pnl_col][allowed_mask].to_numpy(dtype=float)
                    net_allowed = df_test[net_pnl_col][allowed_mask].to_numpy(dtype=float) if net_pnl_col and net_pnl_col in df_test.columns else None
                    result.filtered_stats = self._calc_trade_stats(pnl_allowed, net_allowed)
                
                # Blocked stats (rejected by model)
                blocked_mask = y_pred == 0
                if blocked_mask.any():
                    pnl_blocked = df_test[pnl_col][blocked_mask].to_numpy(dtype=float)
                    net_blocked = df_test[net_pnl_col][blocked_mask].to_numpy(dtype=float) if net_pnl_col and net_pnl_col in df_test.columns else None
                    result.blocked_stats = self._calc_trade_stats(pnl_blocked, net_blocked)
                
                result.allowed_trade_ratio = float(allowed_mask.sum() / len(y_pred)) if len(y_pred) > 0 else 0
                result.blocked_trade_ratio = 1.0 - result.allowed_trade_ratio
            
            result.cost_estimate_used = f"commission={self._cfg.commission_per_round_turn}, slippage={self._cfg.slippage_estimate_pts}pts"
            result.trained_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            
            # ── Save model ───────────────────────────────────────────────────
            emit(90, "Saving model...")
            
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "model": model,
                "feature_cols": feature_cols,
                "threshold": best_thresh,
                "metrics": result.to_dict(),
            }
            with open(self._model_path, "wb") as f:
                pickle.dump(payload, f)
            
            emit(100, "Training complete!")
            
            return result
            
        except Exception as e:
            log.exception("Training error: %s", e)
            raise
    
    def _calc_trade_stats(
        self,
        pnl_values: np.ndarray,
        net_pnl_values: Optional[np.ndarray] = None,
    ) -> TradeStats:
        """Calculate trading statistics for a set of trades."""
        stats = TradeStats()
        
        if len(pnl_values) == 0:
            return stats
        
        stats.trade_count = len(pnl_values)
        
        wins = pnl_values[pnl_values > 0]
        losses = pnl_values[pnl_values <= 0]
        
        stats.gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        stats.gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        stats.net_profit = float(pnl_values.sum())
        
        if stats.gross_loss > 0:
            stats.profit_factor = stats.gross_profit / stats.gross_loss
        else:
            stats.profit_factor = stats.gross_profit if stats.gross_profit > 0 else 0.0
        
        stats.winrate = len(wins) / len(pnl_values)
        stats.avg_trade = float(pnl_values.mean())
        
        # Net after costs
        if net_pnl_values is not None and len(net_pnl_values) > 0:
            stats.net_after_costs = float(net_pnl_values.sum())
            stats.estimated_costs = stats.net_profit - stats.net_after_costs
        else:
            # Estimate costs
            cost_per_trade = (
                self._cfg.commission_per_round_turn * self._cfg.volume +
                self._cfg.slippage_estimate_pts * self._cfg.volume * self._cfg.point_value
            )
            stats.estimated_costs = cost_per_trade * stats.trade_count
            stats.net_after_costs = stats.net_profit - stats.estimated_costs
        
        return stats


# ── Helper Functions ──────────────────────────────────────────────────────────

def get_feature_summary(csv_path: str = "logs/ml_features.csv") -> Dict[str, Any]:
    """Get summary of available features in CSV."""
    try:
        import pandas as pd
        
        path = Path(csv_path)
        if not path.exists():
            return {"error": "CSV not found", "rows": 0}
        
        df = pd.read_csv(csv_path)
        
        mvp_features = [
            "range_last_30s_pts", "range_last_60s_pts", "range_last_180s_pts",
            "ticks_last_30s", "ticks_last_60s", "spread_atr_ratio",
            "time_to_fill_ms", "reprice_count_before_fill", "freeze_duration_ms",
            "market_state", "expansion_started", "flat_duration_sec",
            "net_pnl_usd_est", "label_good_trade",
        ]
        
        available_mvp = [f for f in mvp_features if f in df.columns]
        missing_mvp = [f for f in mvp_features if f not in df.columns]
        
        # Basic stats
        label_col = "label_good_trade" if "label_good_trade" in df.columns else "label"
        good_count = int(df[label_col].sum()) if label_col in df.columns else 0
        bad_count = len(df) - good_count
        
        return {
            "rows": len(df),
            "columns": list(df.columns),
            "mvp_features_available": available_mvp,
            "mvp_features_missing": missing_mvp,
            "good_trades": good_count,
            "bad_trades": bad_count,
            "winrate": good_count / len(df) if len(df) > 0 else 0,
        }
    except Exception as e:
        return {"error": str(e), "rows": 0}


def load_filter(
    model_path: str = "models/ml_filter_v2.pkl",
    mode: MLFilterMode = MLFilterMode.SOFT_FILTER,
) -> MLFilter:
    """Load ML filter with specified mode."""
    return MLFilter(model_path=model_path, mode=mode)
