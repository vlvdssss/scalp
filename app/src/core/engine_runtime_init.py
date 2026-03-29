"""
engine_runtime_init.py

TradingCore mixin for non-component runtime field initialization.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from threading import Event, Thread
from typing import Any, Optional, Protocol

from app.src.adapters.mt5_adapter import SymbolSnapshot

from app.src.core.state import Side


class _RuntimeInitHost(Protocol):
    _cfg: dict[str, Any]

    @staticmethod
    def _compute_spec_hash(cfg: dict) -> str: ...


class RuntimeInitMixin:
    def _init_runtime_infrastructure(self: _RuntimeInitHost) -> None:
        self._stop_event = Event()
        self._thread: Optional[Thread] = None

        self._si: SymbolSnapshot | None = None
        self._si_refresh_ts = 0.0
        self._SI_REFRESH_SEC = 60.0
        self._snapshot_path = Path("logs/state_snapshot.json")

        self._cycle_interval_s = self._cfg["timing"]["cycle_interval_ms"] / 1000.0
        self._cycle_budget_s = self._cfg["timing"]["cycle_budget_ms"] / 1000.0
        self._max_skips = self._cfg["timing"]["max_skips_before_safe"]

        self._af = self._cfg["anti_flat"]
        self._reconnect_cfg = self._cfg["connectivity"]
        self._reconnect_attempts = 0
        self._double_trigger_ms = self._cfg["double_trigger"]["guard_ms"]

        # Track account balance at session start so dashboard can show
        # true realized P&L (includes commission + swap, unlike DB calc).
        self._session_start_balance: Optional[float] = None

    def _init_market_cache_state(self: _RuntimeInitHost) -> None:
        self._trail_atr_pts = 0.0
        self._trail_atr_last_fetch_mono = 0.0
        self._trail_atr_fetch_interval_s = 60.0
        self._micro_guard_pause_until_mono = 0.0
        self._micro_guard_stable_since_mono = 0.0
        self._micro_guard_pause_on_trigger_ms = float(
            self._cfg.get("micro_guard", {}).get("pause_on_trigger_ms", 5000.0)
        )
        self._micro_guard_recovery_stability_ms = float(
            self._cfg.get("micro_guard", {}).get("recovery_stability_ms", 4000.0)
        )

        self._last_atr_pts = 0.0
        self._last_spread_med_pts = 0.0
        self._last_candle_hi = 0.0
        self._last_candle_lo = 0.0
        self._last_is_flat = False

        self._position_last_seen_mono = None
        self._GHOST_WD_MS = 30_000.0

        self._tick_active_last_clock_ms = 0.0
        self._TICK_ACTIVE_CLOCK_MS = 1_000.0

    def _init_trade_runtime_state(self: _RuntimeInitHost) -> None:
        self._trade_entry_spread_pts = 0.0
        self._trade_entry_price_for_record = 0.0
        self._trade_mae = 0.0
        self._trade_mfe = 0.0

        self._trade_be_triggered = False
        self._trade_be_time_utc = ""
        self._trade_be_arm_pts = 0.0
        self._trade_be_buffer_pts = 0.0

        self._trade_trail_triggered = False
        self._trade_trail_updates = 0
        self._trade_trail_max_pts = 0.0
        self._trade_critical_flags: list[str] = []

        early_exit_cfg = self._cfg.get("early_exit", {})
        self._early_exit_enabled = bool(early_exit_cfg.get("enabled", False))
        self._early_exit_window_ms = float(early_exit_cfg.get("window_ms", 7000.0))
        self._early_exit_mfe_spread_mult = float(early_exit_cfg.get("mfe_spread_mult", 1.2))
        self._early_exit_mfe_min = float(early_exit_cfg.get("mfe_min_pts", 12.0))
        self._active_since_mono_ms = 0.0
        self._early_exit_triggered = False

        self._cooldown_after_close_sec = float(self._cfg.get("cooldown_after_close_sec", 0.0))
        self._cooldown_after_win_sec = float(
            self._cfg.get("cooldown_after_win_sec", self._cfg.get("cooldown_after_close_sec", 0.0))
        )
        self._cooldown_after_loss_sec = float(
            self._cfg.get("cooldown_after_loss_sec", self._cfg.get("cooldown_after_close_sec", 0.0))
        )
        self._deny_only_on_loss = bool(self._cfg.get("deny_only_on_loss", True))

        directional_cfg = self._cfg.get("directional_cooldown", {})
        self._dir_cooldown_sec = float(directional_cfg.get("sec", 20.0))
        self._dir_cooldown_burst_atr_mult = float(directional_cfg.get("burst_atr_mult", 0.30))
        self._dir_cooldown_until_ms = 0.0
        self._dir_cooldown_block_side: Optional[Side] = None
        self._dir_cooldown_entry_mid = 0.0

        continuation_cfg = self._cfg.get("profit_continuation", {})
        continuation_enabled = bool(continuation_cfg.get("enabled", True))
        self._profit_continuation_window_sec = float(
            continuation_cfg.get("window_sec", 4.0)
        ) if continuation_enabled else 0.0
        self._profit_continuation_require_managed_exit = bool(
            continuation_cfg.get("require_managed_exit", True)
        )
        self._profit_continuation_until_ms = 0.0
        self._profit_continuation_side: Optional[Side] = None

        rate_limit_cfg = self._cfg.get("rate_limit", {})
        self._max_trades_per_min = int(rate_limit_cfg.get("max_trades_per_minute", 0))
        self._rate_limit_window_sec = float(rate_limit_cfg.get("window_sec", 60.0))
        self._closed_trade_times: list[float] = []

    def _init_run_identity(self: _RuntimeInitHost) -> None:
        self._run_id = str(uuid.uuid4())
        self._spec_version = self._cfg.get("spec_version", "0.0.0")
        self._spec_hash = self._compute_spec_hash(self._cfg)