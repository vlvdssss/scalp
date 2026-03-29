"""
TradingCore – single-thread real-time trading engine.

* All MetaTrader5 API calls happen exclusively in this thread (P0-003).
* Runs a bounded-time polling loop driven by symbol_info_tick().
* P0-002: Clock-driven confirm/TTL; tick dedup only skips HEAVY processing,
  never skips confirm/TTL expiry or P0-006 cancel-deadline check.
* P0-003: CoreCommandQueue – TG thread only enqueues, core thread only executes.
* Implements the full state machine: IDLE → ARMED → POSITION_CONFIRM →
  POSITION_ACTIVE, with SAFE/COOLDOWN/RECOVERY modes.
* INV-A through INV-G are enforced here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from app.src.adapters.mt5_adapter import (
    MT5Adapter,
    SymbolSnapshot,
)
from app.src.core.state import StateStore, SystemMode, TradingState, Side
from app.src.core.engine_builders import BuildersMixin
from app.src.core.engine_clockwork import ClockworkMixin
from app.src.core.engine_cycle_orchestration import CycleOrchestrationMixin
from app.src.core.engine_control_plane import ControlPlaneMixin
from app.src.core.engine_deny_policy import DenyPolicyMixin
from app.src.core.engine_exit_policy import ExitPolicyMixin
from app.src.core.engine_market_pipeline import MarketPipelineMixin
from app.src.core.engine_runtime_init import RuntimeInitMixin
from app.src.core.engine_runtime_guard import RuntimeGuardMixin
from app.src.core.engine_safety_hooks import SafetyHooksMixin
from app.src.core.engine_state_machine import StateMachineMixin
from app.src.core.engine_trade_lifecycle import TradeLifecycleMixin

log = logging.getLogger(__name__)


class TradingCore(
    BuildersMixin,
    TradeLifecycleMixin,
    DenyPolicyMixin,
    ClockworkMixin,
    MarketPipelineMixin,
    RuntimeInitMixin,
    CycleOrchestrationMixin,
    RuntimeGuardMixin,
    ControlPlaneMixin,
    SafetyHooksMixin,
    ExitPolicyMixin,
    StateMachineMixin,
):
    """
    Bounded-time tick-driven trading engine.
    Runs in a dedicated worker thread; communicates with GUI via callbacks.
    """

    def __init__(self, cfg: dict, ui_callback: Optional[Callable[[str, Any], None]] = None) -> None:
        self._cfg = cfg
        self._ui_cb = ui_callback or (lambda ev, d: None)
        self._safe_mode_enabled: bool = bool(cfg.get("safe_mode_enabled", True))
        self._fake_breakout_enabled: bool = bool(
            cfg.get("confirm", {}).get("fake_breakout_enabled", True)
        )

        # ── Build components ──────────────────────────────────────────────────
        mt5_mod = self._load_mt5_module()
        self._adapter = MT5Adapter(mt5_mod)
        self._state   = StateStore()
        self._build_models()
        self._build_trade_managers()
        self._build_session_control()
        self._build_persistence()
        self._build_telegram_gateway()
        self._init_runtime_infrastructure()
        self._init_market_cache_state()
        self._init_trade_runtime_state()
        self._init_run_identity()

    # ── P0-001: spec_hash computation ─────────────────────────────────────────

    @staticmethod
    def _compute_spec_hash(cfg: dict) -> str:
        """SHA-256 of canonical JSON of config dict (P0-001)."""
        try:
            blob = json.dumps(cfg, sort_keys=True, ensure_ascii=True, default=str)
            return hashlib.sha256(blob.encode()).hexdigest()[:16]
        except Exception:
            return "unknown"

    # ── Public control API ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._state.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="TradingCore"
        )
        self._state.running = True
        self._state.spec_version = self._spec_version
        self._state.spec_hash    = self._spec_hash
        self._thread.start()
        self._tg.start()
        log.info("TradingCore started run_id=%s spec_version=%s spec_hash=%s",
                 self._run_id, self._spec_version, self._spec_hash)

    def stop(self) -> None:
        self._state.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._tg.stop()
        self._adapter.shutdown()
        self._jsonl.close()
        self._ledger.close()
        self._feature_logger.close()
        log.info("TradingCore stopped")

    def request_safe_mode(self, reason: str = "manual") -> None:
        self._enter_safe_mode(reason)

    def request_cancel_pendings(self) -> None:
        self._order_mgr.cancel_all()
        self._state.reset_pending()
        self._log_event("MANUAL_CANCEL_PENDINGS", {})

    def request_close_position(self) -> None:
        if self._state.position_ticket and self._si:
            tick = self._adapter.get_tick(self._cfg["symbol"]["name"])
            if tick:
                self._pos_mgr.close_position_market(tick.bid, tick.ask, self._si, "manual_close")
        self._log_event("MANUAL_CLOSE_POSITION", {})

    def request_close_all(self) -> None:
        self.request_cancel_pendings()
        self.request_close_position()

    def get_state_snapshot(self) -> dict:
        return self._state.to_dict()

    def is_running(self) -> bool:
        return self._state.running

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Bounded-time tick-driven polling loop."""
        # P0-003: register this thread as the sole MT5-owning thread
        self._adapter.set_core_thread()

        # Preflight and recovery
        if not self._connect_and_preflight():
            return
        self._recover_on_start()

        # P0-001: log SPEC_LOADED
        self._log_event("SPEC_LOADED", {
            "spec_version": self._spec_version,
            "spec_hash": self._spec_hash,
            "run_id": self._run_id,
            "python_version": __import__("sys").version,
        })

        # P0-001: record run in ledger
        self._ledger.insert_run(
            run_id=self._run_id,
            spec_version=self._spec_version,
            spec_hash=self._spec_hash,
        )

        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            try:
                self._cycle()
            except Exception as exc:
                log.exception("CRITICAL cycle exception: %s", exc)
                self._log_event("CYCLE_EXCEPTION", {"error": str(exc)})
                self._enter_safe_mode(f"cycle_exception: {exc}")

            elapsed = time.monotonic() - loop_start
            if elapsed > self._cycle_budget_s:
                self._state.budget_overrun_count += 1
                log.warning("CYCLE BUDGET OVERRUN: %.0f ms (limit %.0f ms)",
                            elapsed * 1000, self._cycle_budget_s * 1000)
                if self._state.budget_overrun_count >= self._max_skips:
                    self._enter_safe_mode("budget_overrun_threshold_reached")
                    self._state.budget_overrun_count = 0  # reset after trigger to prevent log spam
            else:
                self._state.budget_overrun_count = 0

            sleep_s = max(0.0, self._cycle_interval_s - elapsed)
            self._stop_event.wait(sleep_s)

        log.info("TradingCore loop exited")

    # ── Single cycle ──────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        sym = self._cfg["symbol"]["name"]
        now_ms = time.time() * 1000
        mono_ms = time.monotonic() * 1000

        # ── P0-003: Drain CoreCommandQueue ────────────────────────────────────
        try:
            self._drain_command_queue()
        except Exception as exc:
            log.error("drain_command_queue error: %s", exc)

        market = self._load_market_context(sym, mono_ms)
        if market is None:
            return

        analysis = self._analyze_market(sym, mono_ms, market)
        if analysis is None:
            return

        si = market.si
        tick = market.tick
        bid = market.bid
        ask = market.ask
        ti = market.ti
        spread_points = analysis.spread_points
        spread_res = analysis.spread_res
        atr_res = analysis.atr_res
        live_positions = analysis.live_positions
        live_orders = analysis.live_orders

        reconciled = self._reconcile_terminal_state(live_positions, live_orders, bid, ask, si)
        if reconciled is None:
            return
        my_positions, my_orders = reconciled

        if self._process_fill_and_double_trigger(
            my_positions=my_positions,
            my_orders=my_orders,
            bid=bid,
            ask=ask,
            si=si,
            now_ms=now_ms,
            mono_ms=mono_ms,
            spread_points=spread_points,
        ):
            return

        # ── Compute deny conditions ────────────────────────────────────────────
        deny_reasons = self._compute_deny(spread_res, atr_res, now_ms)
        self._state.deny_reasons = deny_reasons
        deny = len(deny_reasons) > 0

        if self._state.mode == SystemMode.SAFE:
            self._ui_cb("state_update", self._build_ui_payload(
                tick, spread_res, atr_res, ti, deny_reasons
            ))
            return

        self._run_state_machine(
            my_positions=my_positions,
            bid=bid,
            ask=ask,
            si=si,
            now_ms=now_ms,
            mono_ms=mono_ms,
            spread_points=spread_points,
            spread_res=spread_res,
            atr_res=atr_res,
            deny=deny,
            deny_reasons=deny_reasons,
            micro_guard_blocked=analysis.micro_guard_blocked,
        )

        # ── API restriction check (after state machine so normal close doesn't ──
        # ── trigger false positive: position_ticket is cleared by finalize) ─────
        self._check_api_restriction_symptoms(live_positions, live_orders)

        # ── BE storm check ─────────────────────────────────────────────────────
        self._check_be_storm(now_ms)

        # BE-storm may have just changed cooldown state after the state machine
        # ran, so rebuild deny reasons for a coherent UI snapshot.
        deny_reasons = self._compute_deny(spread_res, atr_res, now_ms)
        self._state.deny_reasons = deny_reasons

        # ── UI update ─────────────────────────────────────────────────────────
        self._ui_cb("state_update", self._build_ui_payload(
            tick, spread_res, atr_res, ti, deny_reasons
        ))

    # ── Module loader (allows injection in tests) ─────────────────────────────

    @staticmethod
    def _load_mt5_module() -> Any:
        try:
            import MetaTrader5 as mt5  # type: ignore
            return mt5
        except ImportError:
            raise ImportError(
                "MetaTrader5 package not installed. Run: pip install MetaTrader5"
            )
