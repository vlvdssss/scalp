"""
engine_deny_policy.py

TradingCore mixin for deny computation and directional cooldown handling.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from app.src.core.models_atr import ATRResult
from app.src.core.models_spread import SpreadResult
from app.src.core.session_control import SessionControl
from app.src.core.state import StateStore
from app.src.core.state import Side

log = logging.getLogger(__name__)


class _DenyPolicyHost(Protocol):
    _cfg: dict[str, Any]
    _state: StateStore
    _session: SessionControl
    _max_trades_per_min: int
    _rate_limit_window_sec: float
    _closed_trade_times: list[float]
    _profit_continuation_until_ms: float
    _profit_continuation_side: Side | None
    _dir_cooldown_until_ms: float
    _dir_cooldown_block_side: Side | None
    _dir_cooldown_entry_mid: float
    _dir_cooldown_burst_atr_mult: float

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...


class DenyPolicyMixin:
    def _compute_deny(
        self: _DenyPolicyHost,
        spread_res: SpreadResult,
        atr_res: ATRResult,
        now_ms: float,
    ) -> list[str]:
        reasons: list[str] = []

        if spread_res.deny_spread:
            reasons.append(
                f"spread_gate: {spread_res.spread_points:.1f} > {spread_res.max_spread_points:.1f}"
            )

        if atr_res.deny_atr_min:
            reasons.append(
                f"atr_min: {atr_res.atr_points:.1f} < {self._cfg['atr']['atr_min_points']}"
            )
        if atr_res.deny_ratio and spread_res.spread_med_points > 0 and atr_res.atr_points > 0:
            ratio = spread_res.spread_med_points / atr_res.atr_points
            reasons.append(f"ratio_max: {ratio:.3f} > {self._cfg['atr']['ratio_max']}")

        sess_blocked, sess_reason = self._session.is_blocked()
        if sess_blocked:
            reasons.append(f"session_block: {sess_reason}")

        if self._state.is_in_cooldown(now_ms):
            remaining_s = (self._state.cooldown_until_ms - now_ms) / 1000
            reasons.append(f"cooldown: {remaining_s:.0f}s remaining")

        if self._max_trades_per_min > 0:
            now_mono = time.monotonic()
            window_sec = max(float(self._rate_limit_window_sec), 1.0)
            recent = [t for t in self._closed_trade_times if now_mono - t <= window_sec]
            if len(recent) >= self._max_trades_per_min:
                reasons.append(
                    f"rate_limit: {len(recent)}/{self._max_trades_per_min} trades in last {window_sec:.0f}s"
                )
                log.info("DENY_RATE_LIMIT_TRADES_PER_MIN: %d trades in last %.0fs", len(recent), window_sec)

        return reasons

    def _resolve_directional_block(
        self: _DenyPolicyHost,
        bid: float,
        ask: float,
        atr_points: float,
        mono_ms: float,
        point: float,
    ) -> Side | None:
        if self._profit_continuation_side is not None:
            if mono_ms >= self._profit_continuation_until_ms:
                log.info(
                    "PROFIT_CONTINUATION expired: %s continuation closed",
                    self._profit_continuation_side.value,
                )
                self._profit_continuation_side = None
            else:
                return Side.SELL if self._profit_continuation_side == Side.BUY else Side.BUY

        blocked: Side | None = None
        if self._dir_cooldown_block_side is None:
            return None

        if mono_ms >= self._dir_cooldown_until_ms:
            log.info("DIR_COOLDOWN expired: %s unblocked", self._dir_cooldown_block_side.value)
            self._dir_cooldown_block_side = None
            return None

        mid = (bid + ask) / 2.0
        move = (mid - self._dir_cooldown_entry_mid) / point
        burst_thr = self._dir_cooldown_burst_atr_mult * atr_points
        burst_hit = (
            (self._dir_cooldown_block_side == Side.BUY and move <= -burst_thr) or
            (self._dir_cooldown_block_side == Side.SELL and move >= burst_thr)
        )
        if burst_hit:
            log.info(
                "DIR_COOLDOWN_CLEARED_BURST: %s freed, move=%.1f pts (reversal)",
                self._dir_cooldown_block_side.value,
                move,
            )
            self._log_event("DIR_COOLDOWN_CLEARED_BURST", {
                "side": self._dir_cooldown_block_side.value,
                "move_pts": move,
                "burst_thr_pts": burst_thr,
            })
            self._dir_cooldown_block_side = None
            return None

        blocked = self._dir_cooldown_block_side
        return blocked