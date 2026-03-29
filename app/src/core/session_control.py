"""
SessionControl – UTC-based session block manager.

Rules:
  * market_close_block_min: block trading N minutes before broker session closes
  * market_open_block_min:  block trading N minutes after broker session opens
  * Optional explicit trading_sessions list: [{start: "HH:MM", end: "HH:MM"}]

All timestamps evaluated in UTC (per MT5 API convention).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class SessionWindow:
    """A named UTC trading session window."""
    start_hm: str   # "HH:MM"
    end_hm: str     # "HH:MM"

    def start_time(self) -> time:
        h, m = map(int, self.start_hm.split(":"))
        return time(h, m)

    def end_time(self) -> time:
        h, m = map(int, self.end_hm.split(":"))
        return time(h, m)

    def within(self, t: time) -> bool:
        s, e = self.start_time(), self.end_time()
        if s <= e:
            return s <= t <= e
        return t >= s or t <= e   # wraps midnight


@dataclass
class SessionConfig:
    market_close_block_min: int   = 15
    market_open_block_min: int    = 15
    trading_sessions: list[SessionWindow] = field(default_factory=list)


class SessionControl:
    """
    Evaluates whether the current UTC time falls in a blocked session window.
    """

    def __init__(self, config: SessionConfig) -> None:
        self._cfg = config

    def is_blocked(self, now_utc: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Returns (blocked: bool, reason: str).
        If no sessions are configured, only uses close/open block windows.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        t = now_utc.time()

        if not self._cfg.trading_sessions:
            # No sessions defined: no block
            return False, ""

        # Find if we're in any session
        for sess in self._cfg.trading_sessions:
            if sess.within(t):
                # Check proximity to session end (close block)
                from datetime import timedelta
                end_t = sess.end_time()
                end_dt = now_utc.replace(
                    hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0
                )
                if end_dt < now_utc:
                    end_dt = end_dt.replace(day=now_utc.day + 1)
                minutes_to_close = (end_dt - now_utc).total_seconds() / 60
                if minutes_to_close < self._cfg.market_close_block_min:
                    return (
                        True,
                        f"market_close_block: {minutes_to_close:.0f}m to session end"
                        f" (block={self._cfg.market_close_block_min}m)",
                    )

                # Check proximity to session start (open block)
                start_t = sess.start_time()
                start_dt = now_utc.replace(
                    hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0
                )
                minutes_from_open = (now_utc - start_dt).total_seconds() / 60
                if 0 <= minutes_from_open < self._cfg.market_open_block_min:
                    return (
                        True,
                        f"market_open_block: {minutes_from_open:.0f}m since session open"
                        f" (block={self._cfg.market_open_block_min}m)",
                    )

                return False, ""

        # Not in any defined session
        return True, "outside_trading_sessions"
