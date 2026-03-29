"""
feature_logger.py — ML Feature Logger (Step 1 of ML_PLAN.md)

Records one row per completed trade to logs/ml_features.csv.
Each row = features at FILL time + outcome at TRADE_CLOSED time.

Usage in engine.py:
    # At FILL:
    self._feature_logger.on_fill(side, entry_price, bid, ask,
                                  atr_pts, spread_pts, spread_med_pts,
                                  candle_hi, candle_lo, si_point, is_flat,
                                  now_utc_ms)

    # At TRADE_CLOSED:
    self._feature_logger.on_close(pnl_usd, pnl_pts, mae_pts, mfe_pts,
                                   hold_sec, exit_reason, be_triggered,
                                   cost_threshold=0.20)
"""

from __future__ import annotations

import csv
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# UTC hours when each session starts (rough boundaries for XAUUSD)
_SESSION_BOUNDARIES = {
    "ASIA":    (0, 8),    # 00:00–08:00 UTC
    "LONDON":  (7, 12),   # 07:00–12:00 UTC
    "OVERLAP": (12, 16),  # 12:00–16:00 UTC  (London+NY)
    "NY":      (16, 21),  # 16:00–21:00 UTC
    "QUIET":   (21, 24),  # 21:00–24:00 UTC
}

_SESSION_OPEN_HOUR = {
    "ASIA": 0, "LONDON": 7, "OVERLAP": 12, "NY": 16, "QUIET": 21
}

# CSV columns — order matters, matches DictWriter fieldnames
_COLUMNS = [
    # ── Identifiers ──
    "trade_id",
    "fill_utc",
    # ── Market context ──
    "hour_utc",
    "day_of_week",          # 0=Mon … 4=Fri
    "session",              # ASIA / LONDON / OVERLAP / NY / QUIET
    "minute_of_session",    # minutes elapsed since session open
    # ── Volatility / spread ──
    "atr_pts",
    "spread_pts",           # actual spread at fill
    "spread_med_pts",       # median spread
    "rel_spread",           # spread_med / atr
    "candle_range_pts",     # last M1 high-low
    "candle_range_ratio",   # candle_range / atr
    # ── Entry pattern ──
    "side",                 # 0=BUY 1=SELL
    "offset_pts",           # |entry - mid| / point
    "is_flat",              # 1 if flat-detector was active
    # ── Recent history ──
    "last_trade_pnl_usd",
    "last_trade_side",          # 0=BUY 1=SELL -1=unknown
    "wins_last_5",              # count of wins in last 5 trades
    "time_since_last_trade_sec",
    # ── Trailing/BE history (pre-trade rolling stats) ──
    "trail_rate_last_5",        # fraction of last 5 trades that triggered trailing (0.0–1.0)
    "avg_mfe_last_5",           # average MFE pts in last 5 trades
    "be_rate_last_5",           # fraction of last 5 trades that triggered BE
    # ── Outcome (filled at TRADE_CLOSED) ──
    "pnl_usd",
    "pnl_pts",
    "mae_pts",
    "mfe_pts",
    "hold_sec",
    "exit_reason",
    "be_triggered",             # 0/1
    "trail_triggered",          # 0/1 — did trailing ever move SL this trade
    "trail_updates",            # number of SL moves by trailing
    "trail_max_pts_locked",     # max profit_pts at time of any trail update
    "label",                    # 1 if pnl_usd >= cost threshold, else 0
]


def _get_session(hour: int) -> str:
    """Map UTC hour to session name."""
    if 21 <= hour or hour < 0:
        return "QUIET"
    if 12 <= hour < 16:
        return "OVERLAP"
    if 16 <= hour < 21:
        return "NY"
    if 7 <= hour < 12:
        return "LONDON"
    return "ASIA"  # 0–7


def _minute_of_session(hour: int, minute: int, session: str) -> int:
    """Minutes elapsed since the session's nominal open."""
    open_hour = _SESSION_OPEN_HOUR.get(session, 0)
    total = hour * 60 + minute
    session_start = open_hour * 60
    diff = total - session_start
    # Handle crossing midnight for ASIA
    return diff if diff >= 0 else diff + 24 * 60


class FeatureLogger:
    """
    Records market features at trade entry and outcome at trade close.
    Thread-safety: called only from the engine core thread; no locking needed.
    """

    # Cost threshold for label generation (override in config)
    # $0.005 = any trade with even tiny positive profit counts as WIN (label=1)
    # Previously was 0.20 which required 200pts profit — too high for scalping!
    DEFAULT_COST_THRESHOLD: float = 0.005

    def __init__(self, csv_path: str = "logs/ml_features.csv") -> None:
        self._csv_path = Path(csv_path)
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)

        self._write_header = not self._csv_path.exists()
        self._file = open(self._csv_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=_COLUMNS)
        if self._write_header:
            self._writer.writeheader()
            self._file.flush()

        # Pending record: filled at on_fill, completed at on_close
        self._pending: Optional[dict] = None
        self._fill_mono: float = 0.0    # monotonic time of fill for hold_sec

        # Rolling history of last trades (for last_trade_* features)
        self._history: deque[dict] = deque(maxlen=10)
        self._last_close_mono: float = 0.0

        self._trade_counter: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def on_fill(
        self,
        side: str,                  # "BUY" or "SELL"
        entry_price: float,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_pts: float,
        spread_med_pts: float,
        candle_hi: float,           # last M1 candle high (raw price)
        candle_lo: float,           # last M1 candle low  (raw price)
        point: float,               # symbol point size
        is_flat: bool,
        now_utc_ms: float,          # UTC epoch ms
    ) -> None:
        """Call this immediately when a FILL is detected."""
        if point <= 0:
            point = 0.01

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

        # History features
        last_pnl = self._history[-1]["pnl_usd"] if self._history else 0.0
        last_side_raw = self._history[-1]["side"] if self._history else ""
        last_side = (0 if last_side_raw == "BUY" else 1) if last_side_raw else -1
        recent5 = list(self._history)[-5:]
        wins_last_5 = sum(1 for t in recent5 if t["pnl_usd"] > 0)
        trail_rate_last_5 = (
            sum(1 for t in recent5 if t.get("trail_triggered", 0)) / len(recent5)
            if recent5 else 0.0
        )
        avg_mfe_last_5 = (
            sum(t.get("mfe_pts", 0.0) for t in recent5) / len(recent5)
            if recent5 else 0.0
        )
        be_rate_last_5 = (
            sum(1 for t in recent5 if t.get("be_triggered", 0)) / len(recent5)
            if recent5 else 0.0
        )
        now_mono = time.monotonic()
        time_since_last = (
            now_mono - self._last_close_mono
            if self._last_close_mono > 0
            else 9999.0
        )

        self._trade_counter += 1
        self._pending = {
            "trade_id": self._trade_counter,
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
            "side": 0 if side == "BUY" else 1,
            "offset_pts": round(offset_pts, 2),
            "is_flat": int(is_flat),
            "last_trade_pnl_usd": round(last_pnl, 4),
            "last_trade_side": last_side,
            "wins_last_5": wins_last_5,
            "time_since_last_trade_sec": round(time_since_last, 1),
            "trail_rate_last_5": round(trail_rate_last_5, 3),
            "avg_mfe_last_5": round(avg_mfe_last_5, 2),
            "be_rate_last_5": round(be_rate_last_5, 3),
            # Outcome — to be filled by on_close
            "pnl_usd": None,
            "pnl_pts": None,
            "mae_pts": None,
            "mfe_pts": None,
            "hold_sec": None,
            "exit_reason": None,
            "be_triggered": None,
            "trail_triggered": None,
            "trail_updates": None,
            "trail_max_pts_locked": None,
            "label": None,
        }
        self._fill_mono = now_mono
        log.debug("FeatureLogger.on_fill: trade_id=%s side=%s session=%s atr=%.1f rel_spread=%.3f",
                  self._trade_counter, side, session, atr_pts, rel_spread)

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
        """Call this when TRADE_CLOSED fires. Completes and writes the row."""
        if self._pending is None:
            log.debug("FeatureLogger.on_close: no pending record (fill not seen), skipping")
            return

        hold_sec = round(time.monotonic() - self._fill_mono, 1)
        label = 1 if pnl_usd >= cost_threshold else 0

        self._pending.update({
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
            "label": label,
        })

        try:
            self._writer.writerow(self._pending)
            self._file.flush()
            log.debug("FeatureLogger.on_close: trade_id=%s pnl=%.4f label=%d",
                      self._pending["trade_id"], pnl_usd, label)
        except Exception as exc:
            log.error("FeatureLogger write error: %s", exc)

        # Update rolling history (keep trail/be/mfe for pre-trade rolling stats)
        self._history.append({
            "pnl_usd": pnl_usd,
            "side": "BUY" if self._pending["side"] == 0 else "SELL",
            "trail_triggered": int(trail_triggered),
            "be_triggered": int(be_triggered),
            "mfe_pts": mfe_pts,
        })
        self._last_close_mono = time.monotonic()
        self._pending = None

    def close(self) -> None:
        """Flush and close the CSV file (call on engine shutdown)."""
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass
