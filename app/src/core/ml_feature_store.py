"""
ml_feature_store.py — Enhanced ML Feature Store with SQLite + CSV dual storage.

Records comprehensive trade features for ML training:
  - Market context features (range, ticks, spread ratios)
  - Order behavior features (time_to_fill, reprices, freeze)
  - Market state features (SLOW/NORMAL/IMPULSE classification)
  - Trade context features (rolling stats from recent trades)
  - Cost estimation and proper target labeling

Usage:
    store = MLFeatureStore(cfg)
    store.on_signal(signal_id, market_data)  # at pending placement
    store.on_fill(signal_id, fill_data)       # at order fill
    store.on_close(signal_id, outcome_data)   # at trade close
"""

from __future__ import annotations

import csv
import logging
import math
import os
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_SESSION_BOUNDARIES = {
    "ASIA":    (0, 8),
    "LONDON":  (7, 12),
    "OVERLAP": (12, 16),
    "NY":      (16, 21),
    "QUIET":   (21, 24),
}

_SESSION_OPEN_HOUR = {"ASIA": 0, "LONDON": 7, "OVERLAP": 12, "NY": 16, "QUIET": 21}


def _get_session(hour: int) -> str:
    if 21 <= hour or hour < 0:
        return "QUIET"
    if 12 <= hour < 16:
        return "OVERLAP"
    if 16 <= hour < 21:
        return "NY"
    if 7 <= hour < 12:
        return "LONDON"
    return "ASIA"


def _minute_of_session(hour: int, minute: int, session: str) -> int:
    open_hour = _SESSION_OPEN_HOUR.get(session, 0)
    diff = (hour * 60 + minute) - (open_hour * 60)
    return diff if diff >= 0 else diff + 24 * 60


# ── Market State Classification ───────────────────────────────────────────────

class MarketState:
    SLOW = "SLOW"
    NORMAL = "NORMAL"
    IMPULSE = "IMPULSE"


# ── Cost Estimation ───────────────────────────────────────────────────────────

@dataclass
class CostConfig:
    """Configuration for trade cost estimation."""
    commission_per_lot_usd: float = 15.0         # round-turn commission (~$0.15 for 0.01 lot)
    slippage_estimate_pts: float = 5.0           # expected slippage in points
    point_value_per_lot: float = 1.0             # value per point per lot (XAUUSD)
    min_profitable_net_usd: float = 0.20         # threshold for "good trade"


def estimate_trade_costs(
    entry_spread_pts: float,
    exit_spread_pts: float,
    volume: float,
    point_value: float,
    cfg: CostConfig,
) -> float:
    """
    Estimate total trade costs in USD.
    
    Includes:
      - Entry spread cost (half spread)
      - Exit spread cost (half spread)  
      - Commission (round-turn)
      - Slippage estimate
    """
    spread_cost_entry = (entry_spread_pts / 2.0) * volume * point_value
    spread_cost_exit = (exit_spread_pts / 2.0) * volume * point_value
    commission = cfg.commission_per_lot_usd * volume
    slippage = cfg.slippage_estimate_pts * volume * point_value
    return spread_cost_entry + spread_cost_exit + commission + slippage


# ── Feature Columns Definition ────────────────────────────────────────────────

FEATURE_COLUMNS = [
    # ── Identifiers ──
    "signal_id",
    "trade_id",
    "fill_utc",
    
    # ── Session/Time ──
    "hour_utc",
    "day_of_week",
    "session",
    "minute_of_session",
    
    # ── Volatility / spread (existing) ──
    "atr_pts",
    "spread_pts",
    "spread_med_pts",
    "rel_spread",                    # spread_med / atr
    "candle_range_pts",
    "candle_range_ratio",            # candle_range / atr
    
    # ── NEW: Market range features ──
    "range_last_30s_pts",            # price range in last 30 seconds
    "range_last_60s_pts",            # price range in last 60 seconds
    "range_last_180s_pts",           # price range in last 180 seconds
    
    # ── NEW: Tick activity features ──
    "ticks_last_10s",                # tick count in last 10 seconds
    "ticks_last_30s",                # tick count in last 30 seconds
    "ticks_last_60s",                # tick count in last 60 seconds
    
    # ── NEW: Spread ratio features ──
    "spread_atr_ratio",              # current spread / ATR
    "spread_range_ratio_30s",        # spread / range_30s
    "spread_range_ratio_60s",        # spread / range_60s
    
    # ── Entry pattern ──
    "side",                          # 0=BUY 1=SELL
    "offset_pts",                    # distance from mid at signal
    "is_flat",                       # 1 if flat-detector active
    
    # ── NEW: Order behavior features ──
    "time_to_fill_ms",               # time from signal to fill
    "reprice_count_before_fill",     # how many times order was repriced
    "cancel_recreate_count",         # how many cancel+recreate cycles
    "freeze_duration_ms",            # total time in FREEZE state before fill
    "was_frozen_before_fill",        # 1 if order was frozen at any point
    "state_before_fill",             # CHASE / PULL_IN / FREEZE / NORMAL
    "time_since_last_reprice_ms",    # time since last reprice before fill
    
    # ── NEW: Market state features ──
    "market_state",                  # SLOW / NORMAL / IMPULSE
    "expansion_started",             # 1 if volatility expansion detected
    "flat_duration_sec",             # seconds in flat before signal
    "flat_range_pts",                # range of flat consolidation
    "compression_ratio",             # current_range / avg_range
    "volatility_expansion_ratio",    # current_vol / avg_vol
    
    # ── Recent trade history ──
    "last_trade_pnl_usd",
    "last_trade_side",
    "wins_last_5",
    "time_since_last_trade_sec",
    "trail_rate_last_5",
    "avg_mfe_last_5",
    "be_rate_last_5",
    
    # ── NEW: Extended trade context ──
    "prev_5_trades_winrate",         # win rate of last 5 trades
    "prev_10_trades_winrate",        # win rate of last 10 trades
    "prev_5_trades_avg_pnl",         # average PnL of last 5 trades
    "prev_10_trades_avg_pnl",        # average PnL of last 10 trades
    "prev_5_trades_avg_mfe",         # average MFE of last 5 trades
    "prev_5_trades_avg_mae",         # average MAE of last 5 trades
    
    # ── Outcome (filled at close) ──
    "pnl_usd",
    "pnl_pts",
    "mae_pts",
    "mfe_pts",
    "hold_sec",
    "exit_reason",
    "be_triggered",
    "trail_triggered",
    "trail_updates",
    "trail_max_pts_locked",
    
    # ── NEW: Cost-adjusted outcome ──
    "entry_spread_pts",              # spread at entry
    "exit_spread_pts",               # spread at exit
    "estimated_costs_usd",           # total estimated costs
    "net_pnl_usd_est",               # pnl_usd - estimated_costs
    
    # ── Target labels ──
    "label",                         # legacy: pnl > 0
    "label_good_trade",              # NEW: net_pnl_usd_est > min_profitable
]


# ── Tick Buffer for Range/Tick calculations ───────────────────────────────────

class TickBuffer:
    """
    Maintains a sliding window of tick data for range/tick calculations.
    Thread-safe: designed to be called from single engine thread.
    """
    
    def __init__(self, max_age_sec: float = 300.0) -> None:
        self._max_age_ms = max_age_sec * 1000.0
        self._ticks: Deque[Tuple[float, float, float]] = deque()  # (ms, bid, ask)
    
    def add_tick(self, now_ms: float, bid: float, ask: float) -> None:
        """Add a new tick and prune old ones."""
        self._ticks.append((now_ms, bid, ask))
        cutoff = now_ms - self._max_age_ms
        while self._ticks and self._ticks[0][0] < cutoff:
            self._ticks.popleft()
    
    def get_range_pts(self, window_sec: float, now_ms: float, point: float) -> float:
        """Get price range in points over the last window_sec seconds."""
        cutoff = now_ms - window_sec * 1000.0
        prices = []
        for ts, bid, ask in self._ticks:
            if ts >= cutoff:
                mid = (bid + ask) / 2.0
                prices.append(mid)
        if len(prices) < 2:
            return 0.0
        return (max(prices) - min(prices)) / point if point > 0 else 0.0
    
    def get_tick_count(self, window_sec: float, now_ms: float) -> int:
        """Count ticks in the last window_sec seconds."""
        cutoff = now_ms - window_sec * 1000.0
        return sum(1 for ts, _, _ in self._ticks if ts >= cutoff)
    
    def get_avg_spread_pts(self, window_sec: float, now_ms: float, point: float) -> float:
        """Get average spread in points over window."""
        cutoff = now_ms - window_sec * 1000.0
        spreads = []
        for ts, bid, ask in self._ticks:
            if ts >= cutoff:
                spreads.append((ask - bid) / point if point > 0 else 0.0)
        return sum(spreads) / len(spreads) if spreads else 0.0


# ── SQLite Storage ────────────────────────────────────────────────────────────

class TradeFeatureDB:
    """SQLite storage for trade features."""
    
    def __init__(self, db_path: str = "logs/trade_features.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        
        # Create table with all feature columns
        cols_def = ", ".join(
            f"{col} REAL" if col not in ("signal_id", "trade_id", "fill_utc", "session", 
                                          "exit_reason", "state_before_fill", "market_state")
            else f"{col} TEXT"
            for col in FEATURE_COLUMNS
        )
        
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS trade_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {cols_def},
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Index for time-based queries
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fill_utc ON trade_features(fill_utc)
        """)
        self._conn.commit()
    
    def insert(self, record: Dict[str, Any]) -> None:
        """Insert a complete trade feature record."""
        if self._conn is None:
            return
        cols = [c for c in FEATURE_COLUMNS if c in record]
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(cols)
        values = [record.get(c) for c in cols]
        
        try:
            self._conn.execute(
                f"INSERT INTO trade_features ({col_names}) VALUES ({placeholders})",
                values
            )
            self._conn.commit()
        except Exception as e:
            log.error("TradeFeatureDB insert error: %s", e)
    
    def get_all_records(self) -> List[Dict[str, Any]]:
        """Get all records as list of dicts."""
        if self._conn is None:
            return []
        cursor = self._conn.execute(
            f"SELECT {', '.join(FEATURE_COLUMNS)} FROM trade_features ORDER BY fill_utc"
        )
        return [dict(zip(FEATURE_COLUMNS, row)) for row in cursor.fetchall()]
    
    def close(self) -> None:
        if self._conn:
            self._conn.close()


# ── Main Feature Store ────────────────────────────────────────────────────────

class MLFeatureStore:
    """
    Enhanced ML Feature Store with comprehensive feature logging.
    
    Supports:
      - SQLite + CSV dual storage
      - Market range/tick features
      - Order behavior tracking
      - Market state classification
      - Cost-adjusted target labels
    """
    
    def __init__(
        self,
        csv_path: str = "logs/ml_features.csv",
        db_path: str = "logs/trade_features.db",
        cost_cfg: Optional[CostConfig] = None,
    ) -> None:
        self._csv_path = Path(csv_path)
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        
        # CSV writer
        self._write_header = not self._csv_path.exists()
        self._csv_file = open(self._csv_path, "a", newline="", encoding="utf-8")
        self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=FEATURE_COLUMNS)
        if self._write_header:
            self._csv_writer.writeheader()
            self._csv_file.flush()
        
        # SQLite storage
        self._db = TradeFeatureDB(db_path)
        
        # Cost config
        self._cost_cfg = cost_cfg or CostConfig()
        
        # Tick buffer for range/tick calculations
        self._tick_buffer = TickBuffer(max_age_sec=300.0)
        
        # Pending records (signal_id -> partial record)
        self._pending: Dict[str, Dict[str, Any]] = {}
        
        # Trade history for context features
        self._trade_history: Deque[Dict[str, Any]] = deque(maxlen=20)
        self._last_close_mono: float = 0.0
        
        # Order behavior tracking
        self._signal_start_ms: Dict[str, float] = {}
        self._signal_reprice_count: Dict[str, int] = {}
        self._signal_cancel_recreate: Dict[str, int] = {}
        self._signal_freeze_start: Dict[str, float] = {}
        self._signal_freeze_total_ms: Dict[str, float] = {}
        self._signal_was_frozen: Dict[str, bool] = {}
        self._signal_last_reprice_ms: Dict[str, float] = {}
        self._signal_state: Dict[str, str] = {}
        
        # Flat state tracking
        self._flat_start_mono: float = 0.0
        self._flat_range_at_signal: float = 0.0
        
        # Trade counter
        self._trade_counter: int = 0
        self._signal_counter: int = 0
    
    # ── Tick Recording ────────────────────────────────────────────────────────
    
    def record_tick(self, now_ms: float, bid: float, ask: float) -> None:
        """Record tick for range/tick calculations. Call this every tick cycle."""
        self._tick_buffer.add_tick(now_ms, bid, ask)
    
    # ── Order Behavior Tracking ───────────────────────────────────────────────
    
    def on_signal_placed(self, signal_id: str, now_ms: float) -> None:
        """Call when pending order is placed."""
        self._signal_start_ms[signal_id] = now_ms
        self._signal_reprice_count[signal_id] = 0
        self._signal_cancel_recreate[signal_id] = 0
        self._signal_freeze_total_ms[signal_id] = 0.0
        self._signal_was_frozen[signal_id] = False
        self._signal_last_reprice_ms[signal_id] = now_ms
        self._signal_state[signal_id] = "NORMAL"
    
    def on_reprice(self, signal_id: str, now_ms: float) -> None:
        """Call when pending order is repriced (modified)."""
        if signal_id in self._signal_reprice_count:
            self._signal_reprice_count[signal_id] += 1
            self._signal_last_reprice_ms[signal_id] = now_ms
    
    def on_cancel_recreate(self, signal_id: str) -> None:
        """Call when pending is cancelled and recreated."""
        if signal_id in self._signal_cancel_recreate:
            self._signal_cancel_recreate[signal_id] += 1
    
    def on_freeze_start(self, signal_id: str, now_ms: float) -> None:
        """Call when order enters FREEZE state."""
        self._signal_freeze_start[signal_id] = now_ms
        self._signal_was_frozen[signal_id] = True
        self._signal_state[signal_id] = "FREEZE"
    
    def on_freeze_end(self, signal_id: str, now_ms: float) -> None:
        """Call when order exits FREEZE state."""
        if signal_id in self._signal_freeze_start and self._signal_freeze_start[signal_id] > 0:
            duration = now_ms - self._signal_freeze_start[signal_id]
            self._signal_freeze_total_ms[signal_id] = (
                self._signal_freeze_total_ms.get(signal_id, 0.0) + duration
            )
            self._signal_freeze_start[signal_id] = 0.0
    
    def on_state_change(self, signal_id: str, new_state: str) -> None:
        """Track order state changes (CHASE, PULL_IN, FREEZE, NORMAL)."""
        self._signal_state[signal_id] = new_state
    
    # ── Flat State Tracking ───────────────────────────────────────────────────
    
    def on_flat_enter(self, now_mono: float, flat_range_pts: float) -> None:
        """Call when flat detector triggers."""
        if self._flat_start_mono == 0.0:
            self._flat_start_mono = now_mono
        self._flat_range_at_signal = flat_range_pts
    
    def on_flat_exit(self) -> None:
        """Call when flat ends."""
        self._flat_start_mono = 0.0
    
    # ── Market State Classification ───────────────────────────────────────────
    
    def classify_market_state(
        self,
        now_ms: float,
        point: float,
        atr_pts: float,
    ) -> Tuple[str, bool, float, float]:
        """
        Classify current market state.
        
        Returns:
            (market_state, expansion_started, compression_ratio, vol_expansion_ratio)
        """
        range_30s = self._tick_buffer.get_range_pts(30.0, now_ms, point)
        range_60s = self._tick_buffer.get_range_pts(60.0, now_ms, point)
        range_180s = self._tick_buffer.get_range_pts(180.0, now_ms, point)
        ticks_30s = self._tick_buffer.get_tick_count(30.0, now_ms)
        
        # Normalize ranges by ATR
        if atr_pts > 0:
            range_ratio = range_30s / atr_pts
            long_range_ratio = range_180s / atr_pts if range_180s > 0 else 1.0
        else:
            range_ratio = 0.0
            long_range_ratio = 1.0
        
        # Compression ratio: short-term range vs long-term
        compression_ratio = range_30s / range_180s if range_180s > 10 else 1.0
        
        # Volatility expansion detection
        avg_range_per_min = range_180s / 3.0 if range_180s > 0 else 1.0
        vol_expansion = range_30s / (avg_range_per_min * 0.5) if avg_range_per_min > 0 else 1.0
        expansion_started = vol_expansion > 2.0 and ticks_30s > 20
        
        # Market state classification
        if range_ratio < 0.15 and ticks_30s < 15:
            market_state = MarketState.SLOW
        elif range_ratio > 0.5 or (ticks_30s > 40 and range_ratio > 0.25):
            market_state = MarketState.IMPULSE
        else:
            market_state = MarketState.NORMAL
        
        return market_state, expansion_started, compression_ratio, vol_expansion
    
    # ── Trade Context Features ────────────────────────────────────────────────
    
    def _calc_context_features(self) -> Dict[str, float]:
        """Calculate rolling stats from trade history."""
        history = list(self._trade_history)
        
        last_5 = history[-5:] if len(history) >= 5 else history
        last_10 = history[-10:] if len(history) >= 10 else history
        
        def winrate(trades: List[Dict]) -> float:
            if not trades:
                return 0.0
            return sum(1 for t in trades if t.get("pnl_usd", 0) > 0) / len(trades)
        
        def avg_val(trades: List[Dict], key: str) -> float:
            if not trades:
                return 0.0
            vals = [t.get(key, 0.0) for t in trades]
            return sum(vals) / len(vals) if vals else 0.0
        
        return {
            "prev_5_trades_winrate": round(winrate(last_5), 4),
            "prev_10_trades_winrate": round(winrate(last_10), 4),
            "prev_5_trades_avg_pnl": round(avg_val(last_5, "pnl_usd"), 4),
            "prev_10_trades_avg_pnl": round(avg_val(last_10, "pnl_usd"), 4),
            "prev_5_trades_avg_mfe": round(avg_val(last_5, "mfe_pts"), 2),
            "prev_5_trades_avg_mae": round(avg_val(last_5, "mae_pts"), 2),
        }
    
    # ── Main API ──────────────────────────────────────────────────────────────
    
    def on_fill(
        self,
        side: str,
        entry_price: float,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_pts: float,
        spread_med_pts: float,
        candle_hi: float,
        candle_lo: float,
        point: float,
        is_flat: bool,
        now_utc_ms: float,
        signal_id: Optional[str] = None,
    ) -> str:
        """
        Record features at fill time.
        
        Returns: trade_id for later matching with on_close.
        """
        if point <= 0:
            point = 0.01
        
        self._trade_counter += 1
        trade_id = f"T{self._trade_counter}"
        
        if signal_id is None:
            self._signal_counter += 1
            signal_id = f"S{self._signal_counter}"
        
        dt = datetime.fromtimestamp(now_utc_ms / 1000.0, tz=timezone.utc)
        hour = dt.hour
        minute = dt.minute
        session = _get_session(hour)
        min_sess = _minute_of_session(hour, minute, session)
        
        mid = (bid + ask) / 2.0
        offset_pts = abs(entry_price - mid) / point
        candle_range_pts = abs(candle_hi - candle_lo) / point
        rel_spread = spread_med_pts / atr_pts if atr_pts > 0 else 0.0
        candle_range_ratio = candle_range_pts / atr_pts if atr_pts > 0 else 0.0
        
        # ── New market features ──
        range_30s = self._tick_buffer.get_range_pts(30.0, now_utc_ms, point)
        range_60s = self._tick_buffer.get_range_pts(60.0, now_utc_ms, point)
        range_180s = self._tick_buffer.get_range_pts(180.0, now_utc_ms, point)
        
        ticks_10s = self._tick_buffer.get_tick_count(10.0, now_utc_ms)
        ticks_30s = self._tick_buffer.get_tick_count(30.0, now_utc_ms)
        ticks_60s = self._tick_buffer.get_tick_count(60.0, now_utc_ms)
        
        spread_atr_ratio = spread_pts / atr_pts if atr_pts > 0 else 0.0
        spread_range_30s = spread_pts / range_30s if range_30s > 0 else 0.0
        spread_range_60s = spread_pts / range_60s if range_60s > 0 else 0.0
        
        # ── Order behavior features ──
        time_to_fill = now_utc_ms - self._signal_start_ms.get(signal_id, now_utc_ms)
        reprice_count = self._signal_reprice_count.get(signal_id, 0)
        cancel_recreate = self._signal_cancel_recreate.get(signal_id, 0)
        freeze_duration = self._signal_freeze_total_ms.get(signal_id, 0.0)
        was_frozen = 1 if self._signal_was_frozen.get(signal_id, False) else 0
        state_before = self._signal_state.get(signal_id, "NORMAL")
        time_since_reprice = now_utc_ms - self._signal_last_reprice_ms.get(signal_id, now_utc_ms)
        
        # ── Market state features ──
        market_state, expansion_started, compression_ratio, vol_expansion = \
            self.classify_market_state(now_utc_ms, point, atr_pts)
        
        flat_duration_sec = 0.0
        if is_flat and self._flat_start_mono > 0:
            flat_duration_sec = (time.monotonic() - self._flat_start_mono)
        
        # ── History features ──
        history = list(self._trade_history)
        last_pnl = history[-1].get("pnl_usd", 0.0) if history else 0.0
        last_side_raw = history[-1].get("side", "") if history else ""
        last_side = 0 if last_side_raw == "BUY" else (1 if last_side_raw == "SELL" else -1)
        
        recent_5 = history[-5:] if history else []
        wins_last_5 = sum(1 for t in recent_5 if t.get("pnl_usd", 0) > 0)
        trail_rate_5 = sum(1 for t in recent_5 if t.get("trail_triggered")) / len(recent_5) if recent_5 else 0.0
        avg_mfe_5 = sum(t.get("mfe_pts", 0) for t in recent_5) / len(recent_5) if recent_5 else 0.0
        be_rate_5 = sum(1 for t in recent_5 if t.get("be_triggered")) / len(recent_5) if recent_5 else 0.0
        
        now_mono = time.monotonic()
        time_since_last = now_mono - self._last_close_mono if self._last_close_mono > 0 else 9999.0
        
        context_features = self._calc_context_features()
        
        # Build record
        record = {
            "signal_id": signal_id,
            "trade_id": trade_id,
            "fill_utc": dt.isoformat(),
            "hour_utc": hour,
            "day_of_week": dt.weekday(),
            "session": session,
            "minute_of_session": min_sess,
            
            "atr_pts": round(atr_pts, 2),
            "spread_pts": round(spread_pts, 2),
            "spread_med_pts": round(spread_med_pts, 2),
            "rel_spread": round(rel_spread, 4),
            "candle_range_pts": round(candle_range_pts, 2),
            "candle_range_ratio": round(candle_range_ratio, 4),
            
            "range_last_30s_pts": round(range_30s, 2),
            "range_last_60s_pts": round(range_60s, 2),
            "range_last_180s_pts": round(range_180s, 2),
            
            "ticks_last_10s": ticks_10s,
            "ticks_last_30s": ticks_30s,
            "ticks_last_60s": ticks_60s,
            
            "spread_atr_ratio": round(spread_atr_ratio, 4),
            "spread_range_ratio_30s": round(spread_range_30s, 4),
            "spread_range_ratio_60s": round(spread_range_60s, 4),
            
            "side": 0 if side == "BUY" else 1,
            "offset_pts": round(offset_pts, 2),
            "is_flat": int(is_flat),
            
            "time_to_fill_ms": round(time_to_fill, 1),
            "reprice_count_before_fill": reprice_count,
            "cancel_recreate_count": cancel_recreate,
            "freeze_duration_ms": round(freeze_duration, 1),
            "was_frozen_before_fill": was_frozen,
            "state_before_fill": state_before,
            "time_since_last_reprice_ms": round(time_since_reprice, 1),
            
            "market_state": market_state,
            "expansion_started": 1 if expansion_started else 0,
            "flat_duration_sec": round(flat_duration_sec, 1),
            "flat_range_pts": round(self._flat_range_at_signal, 2),
            "compression_ratio": round(compression_ratio, 4),
            "volatility_expansion_ratio": round(vol_expansion, 4),
            
            "last_trade_pnl_usd": round(last_pnl, 4),
            "last_trade_side": last_side,
            "wins_last_5": wins_last_5,
            "time_since_last_trade_sec": round(time_since_last, 1),
            "trail_rate_last_5": round(trail_rate_5, 3),
            "avg_mfe_last_5": round(avg_mfe_5, 2),
            "be_rate_last_5": round(be_rate_5, 3),
            
            **context_features,
            
            # Entry spread for cost calculation
            "entry_spread_pts": round(spread_pts, 2),
        }
        
        self._pending[trade_id] = record
        
        # Clean up signal tracking
        for d in (self._signal_start_ms, self._signal_reprice_count, 
                  self._signal_cancel_recreate, self._signal_freeze_start,
                  self._signal_freeze_total_ms, self._signal_was_frozen,
                  self._signal_last_reprice_ms, self._signal_state):
            d.pop(signal_id, None)
        
        log.debug(
            "MLFeatureStore.on_fill: trade_id=%s side=%s market_state=%s range_30s=%.1f ticks_30s=%d",
            trade_id, side, market_state, range_30s, ticks_30s
        )
        
        return trade_id
    
    def on_close(
        self,
        trade_id: str,
        pnl_usd: float,
        pnl_pts: float,
        mae_pts: float,
        mfe_pts: float,
        exit_reason: str,
        be_triggered: bool,
        trail_triggered: bool = False,
        trail_updates: int = 0,
        trail_max_pts_locked: float = 0.0,
        exit_spread_pts: float = 0.0,
        volume: float = 0.01,
        point_value: float = 1.0,
    ) -> None:
        """Complete and write the trade record."""
        record = self._pending.pop(trade_id, None)
        if record is None:
            log.debug("MLFeatureStore.on_close: no pending record for %s", trade_id)
            return
        
        # Calculate costs and net PnL
        entry_spread = record.get("entry_spread_pts", 0.0)
        costs = estimate_trade_costs(
            entry_spread_pts=entry_spread,
            exit_spread_pts=exit_spread_pts,
            volume=volume,
            point_value=point_value,
            cfg=self._cost_cfg,
        )
        net_pnl = pnl_usd - costs
        
        # Labels
        label_legacy = 1 if pnl_usd > 0 else 0
        label_good = 1 if net_pnl > self._cost_cfg.min_profitable_net_usd else 0
        
        hold_sec = round(time.monotonic() - self._last_close_mono, 1) if self._last_close_mono > 0 else 0.0
        
        record.update({
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pts": round(pnl_pts, 2),
            "mae_pts": round(mae_pts, 2),
            "mfe_pts": round(mfe_pts, 2),
            "hold_sec": hold_sec,
            "exit_reason": exit_reason,
            "be_triggered": int(be_triggered),
            "trail_triggered": int(trail_triggered),
            "trail_updates": trail_updates,
            "trail_max_pts_locked": round(trail_max_pts_locked, 2),
            
            "exit_spread_pts": round(exit_spread_pts, 2),
            "estimated_costs_usd": round(costs, 4),
            "net_pnl_usd_est": round(net_pnl, 4),
            
            "label": label_legacy,
            "label_good_trade": label_good,
        })
        
        # Write to CSV
        try:
            self._csv_writer.writerow(record)
            self._csv_file.flush()
        except Exception as e:
            log.error("MLFeatureStore CSV write error: %s", e)
        
        # Write to SQLite
        try:
            self._db.insert(record)
        except Exception as e:
            log.error("MLFeatureStore DB write error: %s", e)
        
        # Update trade history
        self._trade_history.append({
            "pnl_usd": pnl_usd,
            "side": "BUY" if record.get("side") == 0 else "SELL",
            "trail_triggered": trail_triggered,
            "be_triggered": be_triggered,
            "mfe_pts": mfe_pts,
            "mae_pts": mae_pts,
        })
        self._last_close_mono = time.monotonic()
        
        log.debug(
            "MLFeatureStore.on_close: trade_id=%s pnl=%.2f net=%.2f costs=%.2f label_good=%d",
            trade_id, pnl_usd, net_pnl, costs, label_good
        )
    
    def close(self) -> None:
        """Close file handles."""
        try:
            self._csv_file.flush()
            self._csv_file.close()
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass


# ── Backward Compatibility Wrapper ────────────────────────────────────────────

class FeatureLogger:
    """
    Backward-compatible wrapper around MLFeatureStore.
    Provides the old API (on_fill, on_close) while using new enhanced features.
    """
    
    DEFAULT_COST_THRESHOLD: float = 0.005
    
    def __init__(self, csv_path: str = "logs/ml_features.csv") -> None:
        self._store = MLFeatureStore(csv_path=csv_path)
        self._current_trade_id: Optional[str] = None
        self._fill_mono: float = 0.0
    
    def record_tick(self, now_ms: float, bid: float, ask: float) -> None:
        """Record tick for range/activity calculations."""
        self._store.record_tick(now_ms, bid, ask)
    
    def on_fill(
        self,
        side: str,
        entry_price: float,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_pts: float,
        spread_med_pts: float,
        candle_hi: float,
        candle_lo: float,
        point: float,
        is_flat: bool,
        now_utc_ms: float,
    ) -> None:
        """Record features at fill time (backward compatible)."""
        self._current_trade_id = self._store.on_fill(
            side=side,
            entry_price=entry_price,
            bid=bid,
            ask=ask,
            atr_pts=atr_pts,
            spread_pts=spread_pts,
            spread_med_pts=spread_med_pts,
            candle_hi=candle_hi,
            candle_lo=candle_lo,
            point=point,
            is_flat=is_flat,
            now_utc_ms=now_utc_ms,
        )
        self._fill_mono = time.monotonic()
    
    def on_close(
        self,
        pnl_usd: float,
        pnl_pts: float,
        mae_pts: float,
        mfe_pts: float,
        exit_reason: str,
        be_triggered: bool,
        trail_triggered: bool = False,
        trail_updates: int = 0,
        trail_max_pts_locked: float = 0.0,
        cost_threshold: float = DEFAULT_COST_THRESHOLD,
    ) -> None:
        """Record outcome at close time (backward compatible)."""
        if self._current_trade_id is None:
            log.debug("FeatureLogger.on_close: no pending record")
            return
        
        self._store.on_close(
            trade_id=self._current_trade_id,
            pnl_usd=pnl_usd,
            pnl_pts=pnl_pts,
            mae_pts=mae_pts,
            mfe_pts=mfe_pts,
            exit_reason=exit_reason,
            be_triggered=be_triggered,
            trail_triggered=trail_triggered,
            trail_updates=trail_updates,
            trail_max_pts_locked=trail_max_pts_locked,
        )
        self._current_trade_id = None
    
    def close(self) -> None:
        self._store.close()
