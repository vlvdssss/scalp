"""
MicroGuard – detects microstructure hazards that should trigger SAFE MODE.

P1-009 improvements:
    * Latency: uses IPC call duration; terminal_info.ping_last is advisory unless
        another hard degradation signal confirms the channel is unhealthy
    * Staleness: SAFE when tick.time_msc hasn't advanced for T_STALE_MS
        (clock-driven, not bid/ask equality)
    * "flat bid/ask" guard repurposed to positional progress (MFE tracking
        is in engine; this module only handles tick staleness and channel quality)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class MicroGuardConfig:
    latency_max_ms: float    = 500.0
    flat_ticks_limit: int    = 3      # legacy: consecutive same-bid/ask ticks (soft)
    tick_stale_ms: float     = 5000.0 # P1-009: SAFE if ticks freeze this long (ms)
    ping_max_ms: int         = 1000   # P1-009: terminal ping threshold (ms)


@dataclass
class MicroGuardResult:
    safe_trigger: bool
    reasons: list[str]
    tick_stale_ms: float = 0.0
    ipc_duration_ms: float = 0.0
    ping_last_ms: int = 0


class MicroGuard:
    """
    Maintains a small sliding window of recent ticks to detect microstructure
    hazards: tick staleness (P1-009 primary) and latency anomalies.

    P1-009: flat bid/ask detection moved to a SOFT warning only; true SAFE
    trigger for stale data is based on monotonic time since last NEW tick.
    Terminal ping is treated as corroborating evidence, not a standalone hard
    blocker, because some MT5 environments report sticky/high ping values even
    while ticks and API calls remain healthy.
    """

    def __init__(self, config: MicroGuardConfig) -> None:
        self._cfg = config
        self._prev_bid: Optional[float] = None
        self._prev_ask: Optional[float] = None
        self._flat_ticks = 0
        # P1-009: monotonic time of last new tick (time_msc changed)
        self._last_new_tick_mono_ms: Optional[float] = None

    def on_new_tick(self, mono_ms: float) -> None:
        """Call this when a genuinely new tick (time_msc changed) is received."""
        self._last_new_tick_mono_ms = mono_ms

    def check(
        self,
        bid: float,
        ask: float,
        api_call_latency_ms: float,
        terminal_ping_ms: Optional[int],
        mono_ms: Optional[float] = None,
        is_new_tick: bool = True,
    ) -> MicroGuardResult:
        reasons: list[str] = []
        tick_stale = 0.0
        high_latency = api_call_latency_ms > self._cfg.latency_max_ms
        high_ping = terminal_ping_ms is not None and terminal_ping_ms > self._cfg.ping_max_ms
        stale_tick = False

        # ── IPC latency check ──────────────────────────────────────────────────
        if high_latency:
            reasons.append(
                f"latency={api_call_latency_ms:.0f}ms > max={self._cfg.latency_max_ms:.0f}ms"
            )
        ping = terminal_ping_ms or 0

        # ── Tick staleness (P1-009: primary stale detector) ───────────────────
        if mono_ms is not None and self._last_new_tick_mono_ms is not None:
            tick_stale = mono_ms - self._last_new_tick_mono_ms
            if tick_stale > self._cfg.tick_stale_ms:
                stale_tick = True
                reasons.append(
                    f"tick_stale={tick_stale:.0f}ms > limit={self._cfg.tick_stale_ms:.0f}ms"
                )

        # ── Terminal ping (advisory unless corroborated) ──────────────────────
        if high_ping and (high_latency or stale_tick):
            reasons.append(
                f"ping={terminal_ping_ms}ms > max={self._cfg.ping_max_ms}ms"
            )

        # ── Legacy flat-bid/ask detector (SOFT warning only, not hard SAFE) ───
        if is_new_tick:
            if (
                self._prev_bid is not None
                and bid == self._prev_bid
                and ask == self._prev_ask
            ):
                self._flat_ticks += 1
            else:
                self._flat_ticks = 0
            self._prev_bid = bid
            self._prev_ask = ask

            # Soft warning only – flat bid/ask can legitimately happen in low-vol
            if self._flat_ticks >= self._cfg.flat_ticks_limit:
                log.debug(
                    "MicroGuard: flat bid/ask for %d ticks (soft warning, not SAFE trigger)",
                    self._flat_ticks,
                )
                # NOT added to reasons – only tick_stale / latency trigger hard SAFE

        return MicroGuardResult(
            safe_trigger=len(reasons) > 0,
            reasons=reasons,
            tick_stale_ms=tick_stale,
            ipc_duration_ms=api_call_latency_ms,
            ping_last_ms=ping,
        )

    def reset(self) -> None:
        self._flat_ticks = 0
        self._prev_bid   = None
        self._prev_ask   = None
        self._last_new_tick_mono_ms = None
