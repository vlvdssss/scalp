"""
engine_control_plane.py

TradingCore mixin for command handling, notifications, UI payloads, and event logging.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from app.src.adapters.mt5_adapter import AccountSnapshot, MT5Adapter, TerminalSnapshot, TickSnapshot
from app.src.adapters.telegram import CoreCommand, TelegramGateway
from app.src.core.models_atr import ATRResult
from app.src.core.models_spread import SpreadResult
from app.src.core.persistence import JSONLLogger, TradeLedger
from app.src.core.state import StateStore, SystemMode


class _ControlPlaneHost(Protocol):
    _adapter: MT5Adapter
    _state: StateStore
    _tg: TelegramGateway
    _ledger: TradeLedger
    _jsonl: JSONLLogger
    _ui_cb: Callable[[str, Any], None]
    _snapshot_path: Path
    _spec_version: str
    _spec_hash: str
    _run_id: str
    _trade_trail_triggered: bool
    _trade_trail_updates: int
    _trade_trail_max_pts: float
    _trade_be_triggered: bool
    _trade_be_time_utc: str
    _trade_be_arm_pts: float
    _trade_be_buffer_pts: float
    _trade_critical_flags: list[str]
    _session_start_balance: Optional[float]

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _handle_core_command(self, cmd: CoreCommand) -> None: ...
    def stop(self) -> None: ...
    def _enter_safe_mode(self, reason: str) -> None: ...


class ControlPlaneMixin:
    def _mark_trade_trailing(self: _ControlPlaneHost, data: dict[str, Any]) -> None:
        self._trade_trail_triggered = True
        self._trade_trail_updates += 1
        pts = float(
            data.get("profit_pts", data.get("profit_points", data.get("trail_max_pts_locked", 0.0))) or 0.0
        )
        if pts > self._trade_trail_max_pts:
            self._trade_trail_max_pts = pts

    def _mark_trade_breakeven(
        self: _ControlPlaneHost,
        data: dict[str, Any],
        *,
        notify: bool = True,
    ) -> None:
        if not self._trade_be_triggered:
            self._state.be_trigger_count = getattr(self._state, "be_trigger_count", 0) + 1
            self._state.be_storm.add(time.time() * 1000)
        self._trade_be_triggered = True
        self._trade_be_time_utc = datetime.now(timezone.utc).isoformat()
        self._trade_be_arm_pts = float(data.get("be_arm_points", self._trade_be_arm_pts) or self._trade_be_arm_pts)
        self._trade_be_buffer_pts = float(
            data.get("be_buffer_points", data.get("be_buf_pts", self._trade_be_buffer_pts))
            or self._trade_be_buffer_pts
        )
        if notify:
            self._tg.notify_breakeven(data.get("sl", 0.0))

    def _drain_command_queue(self: _ControlPlaneHost) -> None:
        """Drain Telegram CoreCommand objects on the core thread."""
        cmds = self._tg.drain_command_queue()
        for cmd in cmds:
            try:
                self._handle_core_command(cmd)
            except Exception as exc:
                self._log_event("CORE_COMMAND_ERROR", {
                    "cmd": cmd.cmd,
                    "error": str(exc),
                    "source_thread_id": cmd.source_thread_id,
                })

    def _handle_core_command(self: _ControlPlaneHost, cmd: CoreCommand) -> None:
        self._log_event("CORE_COMMAND_EXEC", {"cmd": cmd.cmd, "arg": cmd.arg})

        if cmd.cmd == "status":
            acc = self._adapter.get_account_info()
            ti = self._adapter.get_terminal_info()
            msg = (
                f"State: {self._state.state.value} | Mode: {self._state.mode.value}\n"
                f"Balance: {acc.balance if acc else 'N/A'} | "
                f"Equity: {acc.equity if acc else 'N/A'}\n"
                f"Connected: {ti.connected if ti else False}"
            )
            self._tg.send_status(msg)
        elif cmd.cmd == "stop":
            self._log_event("TG_STOP_COMMAND", {})
            self.stop()
        elif cmd.cmd == "safe":
            self._enter_safe_mode("telegram_command")
        elif cmd.cmd == "resume":
            if self._state.mode == SystemMode.SAFE:
                self._state.mode = SystemMode.NORMAL
                self._log_event("TG_RESUME", {})
                self._tg.send_status("Bot resumed from Telegram command.")
        else:
            self._tg.send_status(f"Unknown command: {cmd.cmd}")

    def _on_position_event(self: _ControlPlaneHost, event: str, data: dict) -> None:
        self._log_event(event, data)
        if event == "TRAIL_UPDATE":
            self._mark_trade_trailing(data)
        elif event == "APT_TRAIL_UPDATE":
            if bool(data.get("moved", False)):
                self._mark_trade_trailing(data)
        elif event == "APT_PARTIAL_CLOSE_DONE":
            self._mark_trade_trailing(data)
        elif event in ("BREAKEVEN", "BE_TRIGGERED", "BE_MOVED"):
            self._mark_trade_breakeven(data)
        elif event == "APT_RUNNER_BE_SET":
            self._mark_trade_breakeven(data)
            self._mark_trade_trailing(data)
        elif event == "CONFIRM_SUCCESS":
            self._tg.notify_confirm_success(data.get("best_move_points", 0.0))
        elif event in ("FAKE_BREAKOUT", "FAKE_BREAKOUT_CLOSE_NEEDED"):
            self._tg.notify_fake_breakout(
                data.get("best_move_points", 0.0),
                data.get("threshold", 0.0),
            )
        elif event == "EMERGENCY_CLOSE_SENT":
            if "api_restriction" not in self._trade_critical_flags:
                self._trade_critical_flags.append("emergency_close")
        self._ui_cb(event, data)

    def _handle_telegram_command(self: _ControlPlaneHost, cmd: str, arg: str) -> None:
        self._log_event("TELEGRAM_COMMAND", {"cmd": cmd, "arg": arg})
        if cmd == "status":
            ai = self._adapter.get_account_info()
            ti = self._adapter.get_terminal_info()
            msg = (
                f"*Status:* {self._state.mode.value}/{self._state.state.value}\n"
                f"Balance: {ai.balance if ai else '?'}  Equity: {ai.equity if ai else '?'}\n"
                f"Spread: {self._state.deny_reasons}"
            )
            self._tg.send_status(msg)
        elif cmd == "position":
            self._tg.send_status(
                f"Position: ticket={self._state.position_ticket} "
                f"side={self._state.position_side} "
                f"entry={self._state.entry_price}"
            )
        elif cmd == "today":
            stats = self._ledger.get_today_stats()
            self._tg.send_status(str(stats))
        elif cmd == "stats":
            stats = self._ledger.get_all_stats()
            self._tg.send_status(str(stats))
        elif cmd == "errors":
            code, msg = self._adapter.last_error()
            self._tg.send_status(f"Last error: [{code}] {msg}")

    def _log_event(self: _ControlPlaneHost, event: str, data: dict) -> None:
        data.update({
            "state": self._state.state.value,
            "mode": self._state.mode.value,
            "spec_version": self._spec_version,
            "spec_hash": self._spec_hash,
            "run_id": self._run_id,
        })
        self._jsonl.log(event, data)
        self._state.save_snapshot(self._snapshot_path)

    def _build_ui_payload(
        self: _ControlPlaneHost,
        tick: Optional[TickSnapshot],
        spread_res: SpreadResult,
        atr_res: ATRResult,
        ti: TerminalSnapshot | None,
        deny_reasons: list[str],
    ) -> dict:
        ai = self._adapter.get_account_info()

        # Latch starting balance on first successful account read.
        # session_pnl = balance change since bot started (includes commission/swap).
        if ai is not None and self._session_start_balance is None:
            self._session_start_balance = ai.balance
        session_pnl = (
            round(ai.balance - self._session_start_balance, 2)
            if ai is not None and self._session_start_balance is not None
            else 0.0
        )

        bid = tick.bid if tick is not None else getattr(self, "_last_known_bid", 0.0)
        ask = tick.ask if tick is not None else getattr(self, "_last_known_ask", 0.0)
        terminal_trade_allowed = ti.trade_allowed if ti else False
        api_disabled = ti.tradeapi_disabled if ti else False
        bot_trade_allowed = (
            terminal_trade_allowed
            and not api_disabled
            and self._state.mode == SystemMode.NORMAL
            and not deny_reasons
        )
        return {
            "bid": bid,
            "ask": ask,
            "spread_points": spread_res.spread_points,
            "spread_med": spread_res.spread_med_points,
            "max_spread": spread_res.max_spread_points,
            "atr_points": atr_res.atr_points,
            "mode": self._state.mode.value,
            "state": self._state.state.value,
            "deny_reasons": deny_reasons,
            "position_ticket": self._state.position_ticket,
            "position_side": self._state.position_side.value if self._state.position_side else None,
            "entry_price": self._state.entry_price,
            "current_sl": self._state.current_sl,
            "be_done": self._state.be_done,
            "confirm_ticks": self._state.confirm.ticks_seen,
            "confirm_best_pts": self._state.confirm.best_move_points,
            "connected": ti.connected if ti else False,
            "ping_ms": ti.ping_last if ti else -1,
            "trade_allowed": bot_trade_allowed,
            "terminal_trade_allowed": terminal_trade_allowed,
            "tradeapi_disabled": api_disabled,
            "balance": ai.balance if ai else 0.0,
            "equity": ai.equity if ai else 0.0,
            "margin_free": ai.margin_free if ai else 0.0,
            "session_pnl": session_pnl,
            **{f"daily_{k}": v for k, v in self._ledger.get_today_stats().items()},
        }