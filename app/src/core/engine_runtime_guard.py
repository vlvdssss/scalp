"""
engine_runtime_guard.py

TradingCore mixin for connectivity, recovery, and safe-mode runtime handling.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable, Protocol

from app.src.adapters.mt5_adapter import MT5Adapter, OrderSnapshot, PositionSnapshot, SymbolSnapshot, TerminalSnapshot
from app.src.adapters.telegram import TelegramGateway
from app.src.core.state import Side, StateStore, SystemMode, TradingState

log = logging.getLogger(__name__)


class _RuntimeGuardHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: MT5Adapter
    _state: StateStore
    _order_mgr: Any
    _tg: TelegramGateway
    _ui_cb: Callable[[str, Any], None]
    _snapshot_path: Path
    _si: SymbolSnapshot | None
    _stop_event: Event
    _safe_mode_enabled: bool
    _reconnect_cfg: dict[str, Any]
    _reconnect_attempts: int
    _position_last_seen_mono: float | None
    _active_since_mono_ms: float

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _connect_and_preflight(self) -> bool: ...


class RuntimeGuardMixin:
    def _alignment_procedure(
        self: _RuntimeGuardHost,
        positions: list[PositionSnapshot],
        orders: list[OrderSnapshot],
        bid: float,
        ask: float,
        si: SymbolSnapshot,
    ) -> None:
        log.critical("INV-A alignment: closing all %d positions", len(positions))
        for p in positions:
            close_price = ask if p.type == 1 else bid
            req = self._adapter.build_market_close_request(
                symbol=self._cfg["symbol"]["name"],
                ticket=p.ticket,
                volume=p.volume,
                pos_type=p.type,
                price=close_price,
                magic=self._cfg["symbol"]["magic"],
                comment="inv_a_alignment",
            )
            self._adapter.order_send(req)
        for o in orders:
            self._adapter.order_send(self._adapter.build_cancel_request(o.ticket))
        self._state.reset_position()
        self._state.reset_pending()
        self._position_last_seen_mono = None
        self._state.state = TradingState.ARMED
        log.warning("INV-A alignment complete – state reset to ARMED")

    def _connect_and_preflight(self: _RuntimeGuardHost) -> bool:
        """Run structured preflight and emit auditable result events."""
        mt5_cfg = self._cfg["mt5"]

        result = self._adapter.run_preflight(
            symbol=self._cfg["symbol"]["name"],
            volume=self._cfg["risk"]["volume"],
            path=mt5_cfg.get("path", ""),
            login=mt5_cfg.get("login", 0),
            password=mt5_cfg.get("password", ""),
            server=mt5_cfg.get("server", ""),
            timeout_ms=mt5_cfg.get("timeout_ms", 10000),
        )

        ti = result.terminal_info
        self._log_event("PREFLIGHT_RESULT", {
            "ok": result.ok,
            "blocking_reasons": result.blocking_reasons,
            "warnings": result.warnings,
            "connected": ti.connected if ti else False,
            "trade_allowed": ti.trade_allowed if ti else False,
            "tradeapi_disabled": ti.tradeapi_disabled if ti else False,
            "ping_last": ti.ping_last if ti else -1,
        })

        if not result.ok:
            for reason in result.blocking_reasons:
                log.error("PREFLIGHT BLOCKED: %s", reason)
                self._ui_cb("preflight_blocked", {"reason": reason})
            return False

        if result.warnings:
            for warning in result.warnings:
                log.warning("PREFLIGHT WARN: %s", warning)
                self._ui_cb("preflight_warning", {"warning": warning})

        log.info("Preflight OK")
        self._log_event("PREFLIGHT_OK", {})
        return True

    def _handle_disconnect(self: _RuntimeGuardHost, ti: TerminalSnapshot | None) -> None:
        code, msg = self._adapter.last_error()
        log.error("Disconnected: %s %s", code, msg)
        self._log_event("DISCONNECTED", {"error_code": code, "msg": msg})
        self._tg.notify_disconnect(code, msg)
        self._ui_cb("disconnected", {"code": code, "msg": msg})
        self._enter_safe_mode(f"disconnect: [{code}] {msg}")
        self._reconnect_with_backoff()

    def _reconnect_with_backoff(self: _RuntimeGuardHost) -> None:
        init_s = self._reconnect_cfg["reconnect_backoff_initial_sec"]
        max_s = self._reconnect_cfg["reconnect_backoff_max_sec"]
        limit = self._reconnect_cfg["reconnect_max_attempts"]
        delay = init_s
        for attempt in range(1, limit + 1):
            if self._stop_event.is_set():
                return
            log.info("Reconnect attempt %d/%d in %.0f s...", attempt, limit, delay)
            self._stop_event.wait(delay)
            if self._connect_and_preflight():
                self._reconnect_attempts = 0
                self._state.mode = SystemMode.NORMAL
                self._state.state = TradingState.ARMED
                self._tg.notify_reconnect()
                self._log_event("RECONNECTED", {"attempt": attempt})
                return
            delay = min(delay * 2, max_s)
        log.critical("Max reconnect attempts reached. Staying in SAFE MODE.")
        self._log_event("RECONNECT_EXHAUSTED", {})

    def _recover_on_start(self: _RuntimeGuardHost) -> None:
        """Read snapshot + terminal state and reconcile safely on startup."""
        sym = self._cfg["symbol"]["name"]
        magic = self._cfg["symbol"]["magic"]
        snap = StateStore.load_snapshot(self._snapshot_path)

        live_pos = self._adapter.get_positions(sym)
        live_orders = self._adapter.get_orders(sym)

        my_positions = [p for p in live_pos if p.magic == magic]
        my_orders = [o for o in live_orders if o.magic == magic]

        if my_positions:
            pos = my_positions[0]
            self._state.position_ticket = pos.ticket
            self._state.position_side = Side.BUY if pos.type == 0 else Side.SELL
            self._state.entry_price = pos.price_open
            self._state.position_volume = pos.volume
            self._state.current_sl = pos.sl

            if snap and snap.extreme_price:
                self._state.extreme_price = snap.extreme_price
                self._state.be_done = snap.be_done
            else:
                tick = self._adapter.get_tick(sym)
                if tick:
                    self._state.extreme_price = tick.bid if pos.type == 0 else tick.ask
                self._state.be_done = False

            for o in my_orders:
                self._adapter.order_send(self._adapter.build_cancel_request(o.ticket))
            self._state.reset_pending()

            if self._si and pos.sl != 0:
                if pos.type == 0 and pos.sl > pos.price_open:
                    self._state.be_done = True
                elif pos.type == 1 and pos.sl < pos.price_open:
                    self._state.be_done = True

            warmup_s = self._cfg["recovery"]["warmup_sec"]
            self._state.recovery_warmup_until_mono = time.monotonic() + warmup_s

            self._state.state = TradingState.POSITION_ACTIVE
            self._active_since_mono_ms = time.monotonic() * 1000.0
            log.info(
                "RECOVERY: restored position ticket=%s side=%s entry=%.5f",
                pos.ticket,
                self._state.position_side.value,
                pos.price_open,
            )
            self._log_event("RECOVERY_POSITION_RESTORED", {
                "ticket": pos.ticket,
                "side": self._state.position_side.value,
                "entry_price": pos.price_open,
            })
        else:
            for o in my_orders:
                self._adapter.order_send(self._adapter.build_cancel_request(o.ticket))
            self._state.reset_pending()
            self._state.state = TradingState.ARMED
            log.info("RECOVERY: no position found, starting ARMED")

    def _enter_safe_mode(self: _RuntimeGuardHost, reason: str) -> None:
        if self._state.mode == SystemMode.SAFE:
            return

        if not self._safe_mode_enabled:
            log.warning("SAFE_MODE_BYPASSED (safe_mode_enabled=false): %s", reason)
            self._order_mgr.cancel_all()
            self._state.reset_pending()
            if self._state.position_ticket is None:
                self._state.state = TradingState.IDLE
            self._log_event("SAFE_MODE_BYPASSED", {"reason": reason})
            self._ui_cb("safe_mode_bypassed", {"reason": reason})
            return

        log.warning("ENTERING SAFE MODE: %s", reason)
        self._state.set_safe_mode()
        self._order_mgr.cancel_all()
        self._state.reset_pending()
        self._log_event("SAFE_MODE", {"reason": reason})
        self._tg.notify_safe_mode(reason)
        self._ui_cb("safe_mode", {"reason": reason})
        self._state.save_snapshot(self._snapshot_path)