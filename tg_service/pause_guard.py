"""
PauseGuard — loss-streak and time-window auto-pause module.

Does NOT touch the trading engine.
Listens to engine events via bridge callback.
When a threshold is hit → calls bridge.safe_mode().
Config is refreshed from bridge.get_settings() on each trade closed event.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

log = logging.getLogger(__name__)


class PauseGuard:
    """
    Monitors trade outcomes. Two independent modes:

    Streak mode:
        If N consecutive losing trades → safe_mode

    Window mode:
        If cumulative loss within X minutes ≥ threshold → safe_mode
    """

    def __init__(self, bridge) -> None:
        self._bridge = bridge
        # Streak
        self._loss_streak      = 0
        # Window: deque of (timestamp_ms, pnl_usd)
        self._trade_window: deque[tuple[float, float]] = deque()
        # Status
        self._pause_until_ms   = 0.0
        self._pause_reason     = ""
        self._total_triggers   = 0

    # ── Event listener interface ───────────────────────────────────────────────

    def on_event(self, event: str, data: Any) -> None:
        if event != "TRADE_CLOSED":
            return
        if not isinstance(data, dict):
            return
        try:
            self._handle_trade(data)
        except Exception as exc:
            log.error("PauseGuard error: %s", exc)

    def _handle_trade(self, data: dict) -> None:
        settings = self._bridge.get_settings()
        raw_pnl  = data.get("pnl_usd", data.get("profit", 0)) or 0
        pnl      = float(raw_pnl)
        is_loss  = pnl < 0
        now_ms   = time.time() * 1000

        # ── Streak ────────────────────────────────────────────────────────────
        if settings.get("pause_streak_enabled", False):
            if is_loss:
                self._loss_streak += 1
            else:
                self._loss_streak = 0

            limit = int(settings.get("pause_streak_limit", 3))
            if self._loss_streak >= limit:
                pause_min = int(settings.get("pause_streak_minutes", 30))
                self._trigger(f"streak {self._loss_streak} losses", pause_min)
                self._loss_streak = 0
                return  # don't also check window on same trade

        # ── Window ────────────────────────────────────────────────────────────
        if settings.get("pause_window_enabled", False):
            self._trade_window.append((now_ms, pnl))
            # evict old entries
            window_ms = int(settings.get("pause_window_minutes", 60)) * 60_000
            cutoff    = now_ms - window_ms
            while self._trade_window and self._trade_window[0][0] < cutoff:
                self._trade_window.popleft()

            window_loss = sum(p for _, p in self._trade_window if p < 0)
            threshold   = float(settings.get("pause_window_loss_usd", 3.0))
            if abs(window_loss) >= threshold:
                pause_min = int(settings.get("pause_window_pause_minutes", 60))
                self._trigger(f"window loss {abs(window_loss):.2f}$", pause_min)
                self._trade_window.clear()

    def _trigger(self, reason: str, pause_minutes: int) -> None:
        self._pause_until_ms = time.time() * 1000 + pause_minutes * 60_000
        self._pause_reason   = reason
        self._total_triggers += 1
        log.warning("PauseGuard: triggering safe mode — %s (%d min)", reason, pause_minutes)
        try:
            self._bridge.safe_mode()
        except Exception as exc:
            log.error("PauseGuard: safe_mode call failed: %s", exc)

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        now_ms = time.time() * 1000
        paused = now_ms < self._pause_until_ms
        remaining_s = max(0.0, (self._pause_until_ms - now_ms) / 1000) if paused else 0.0
        return {
            "paused":       paused,
            "remaining_s":  int(remaining_s),
            "pause_reason": self._pause_reason if paused else "",
            "loss_streak":  self._loss_streak,
            "total_triggers": self._total_triggers,
        }
