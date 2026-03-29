"""
engine_trade_lifecycle.py

TradingCore mixin for the position lifecycle.

This module intentionally keeps the existing TradingCore behavior unchanged,
but moves the most stateful position methods out of engine.py so the engine
can remain focused on orchestration.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import (
    MT5Adapter,
    PositionSnapshot,
    SymbolSnapshot,
    RC_DONE,
    RC_NO_CHANGES,
    RC_PLACED,
)
from app.src.core.feature_logger import FeatureLogger
from app.src.core.persistence import TradeRecord
from app.src.core.risk import calc_dollar_sl_points, calc_pnl_points, calc_value_per_point
from app.src.core.state import Side, StateStore, TradingState

log = logging.getLogger(__name__)


class _TradeLifecycleHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: MT5Adapter
    _state: StateStore
    _order_mgr: Any
    _pos_mgr: Any
    _feature_logger: FeatureLogger
    _ledger: Any
    _micro_guard: Any
    _tg: Any
    _run_id: str
    _spec_version: str
    _spec_hash: str
    _fake_breakout_enabled: bool
    _position_last_seen_mono: float | None
    _trade_entry_spread_pts: float
    _trade_entry_price_for_record: float
    _trade_mae: float
    _trade_mfe: float
    _active_since_mono_ms: float
    _early_exit_triggered: bool
    _last_atr_pts: float
    _last_spread_med_pts: float
    _last_candle_hi: float
    _last_candle_lo: float
    _last_is_flat: bool
    _last_known_bid: float
    _last_known_ask: float
    _trade_be_triggered: bool
    _trade_be_time_utc: str
    _trade_be_arm_pts: float
    _trade_be_buffer_pts: float
    _trade_trail_triggered: bool
    _trade_trail_updates: int
    _trade_trail_max_pts: float
    _trade_critical_flags: list[str]
    _dir_cooldown_sec: float
    _dir_cooldown_until_ms: float
    _dir_cooldown_block_side: Side | None
    _dir_cooldown_entry_mid: float
    _cooldown_after_win_sec: float
    _cooldown_after_loss_sec: float
    _deny_only_on_loss: bool
    _profit_continuation_window_sec: float
    _profit_continuation_require_managed_exit: bool
    _profit_continuation_until_ms: float
    _profit_continuation_side: Side | None
    _rate_limit_window_sec: float
    _closed_trade_times: list[float]

    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _set_initial_sl(self, pos: PositionSnapshot, si: SymbolSnapshot, spread_pts: float) -> None: ...


class TradeLifecycleMixin:
    """TradingCore mixin for fill, initial SL, close finalization, and MFE/MAE."""

    def _handle_fill(
        self: _TradeLifecycleHost,
        pos: PositionSnapshot,
        bid: float,
        ask: float,
        spread_pts: float,
        si: SymbolSnapshot,
        now_ms: float,
        mono_ms: float,
    ) -> None:
        filled_side = Side.BUY if pos.type == 0 else Side.SELL
        log.info("FILL detected: ticket=%s side=%s price=%.5f",
                 pos.ticket, filled_side.value, pos.price_open)

        self._state.position_ticket = pos.ticket
        self._state.position_side = filled_side
        self._state.entry_price = pos.price_open
        self._state.position_volume = pos.volume
        self._state.first_fill_utc_ms = now_ms
        self._position_last_seen_mono = mono_ms
        self._state.be_done = False
        self._state.extreme_price = bid if filled_side == Side.BUY else ask
        self._state.confirm = self._state.confirm.__class__(
            start_monotonic_ms=mono_ms,
            ticks_seen=0,
            best_move_points=0.0,
        )

        self._trade_entry_spread_pts = spread_pts
        self._trade_entry_price_for_record = pos.price_open
        self._trade_mae = 0.0
        self._trade_mfe = 0.0
        self._active_since_mono_ms = 0.0
        self._early_exit_triggered = False

        self._order_mgr.cancel_opposite(filled_side)
        self._state.reset_pending()
        pos_cfg = self._cfg.get("symbol") if hasattr(self, "_cfg") else None
        cancel_deadline_ms = (
            pos_cfg.cancel_deadline_ms
            if pos_cfg and hasattr(pos_cfg, "cancel_deadline_ms")
            else float(self._cfg.get("position", {}).get("cancel_deadline_ms", 3000))
        )
        self._state.cancel_opposite_started_mono = mono_ms
        self._state.cancel_opposite_deadline_mono = mono_ms + cancel_deadline_ms
        log.debug("cancel_opposite_deadline set: +%.0f ms", cancel_deadline_ms)

        self._set_initial_sl(pos, si, spread_pts)

        if self._fake_breakout_enabled:
            self._state.state = TradingState.POSITION_CONFIRM
        else:
            log.info("fake_breakout_enabled=false – skipping confirm, going straight to ACTIVE")
            self._state.state = TradingState.POSITION_ACTIVE
            self._active_since_mono_ms = mono_ms
            self._pos_mgr.set_position_start_ms(mono_ms)

        self._log_event("FILL", {
            "ticket": pos.ticket,
            "side": filled_side.value,
            "entry_price": pos.price_open,
            "volume": pos.volume,
            "spread_pts": spread_pts,
        })
        self._feature_logger.on_fill(
            side=filled_side.value,
            entry_price=pos.price_open,
            bid=bid,
            ask=ask,
            atr_pts=self._last_atr_pts,
            spread_pts=spread_pts,
            spread_med_pts=self._last_spread_med_pts,
            candle_hi=self._last_candle_hi,
            candle_lo=self._last_candle_lo,
            point=si.point,
            is_flat=self._last_is_flat,
            now_utc_ms=now_ms,
        )
        self._tg.notify_fill(filled_side.value, pos.price_open, pos.volume)

    def _set_initial_sl(
        self: _TradeLifecycleHost,
        pos: PositionSnapshot,
        si: SymbolSnapshot,
        spread_pts: float,
    ) -> None:
        target_risk_usd = self._cfg["risk"].get("target_risk_usd", 1.0)
        safety_buf = self._cfg["risk"].get("sl_safety_buffer_points", 10.0)
        volume = self._cfg["risk"]["volume"]

        sl_pts = calc_dollar_sl_points(
            target_risk_usd=target_risk_usd,
            value_per_point_per_lot=si.value_per_point,
            volume=volume,
            trade_stops_level=si.trade_stops_level,
            safety_buffer_points=safety_buf,
        )

        sl_min_floor = int(self._cfg.get("sl", {}).get("sl_min_points", 80.0))
        sl_max_cap = int(self._cfg.get("sl", {}).get("sl_max_points", 100.0))

        if sl_pts > sl_max_cap:
            log.warning(
                "INITIAL_SL_CAP: dollar_sl=%d pts > sl_max_points=%d – using cap",
                sl_pts, sl_max_cap,
            )
            sl_pts = sl_max_cap
        elif sl_pts < sl_min_floor:
            log.warning(
                "INITIAL_SL_FLOOR: dollar_sl=%d pts < sl_min_points=%d – using floor",
                sl_pts, sl_min_floor,
            )
            sl_pts = sl_min_floor

        value_per_point = si.value_per_point * volume
        if pos.type == 0:
            sl = round(pos.price_open - sl_pts * si.point, si.digits)
        else:
            sl = round(pos.price_open + sl_pts * si.point, si.digits)

        req = self._adapter.build_modify_sl_request(
            symbol=self._cfg["symbol"]["name"],
            ticket=pos.ticket,
            sl=sl,
            is_position=True,
        )
        result = self._adapter.order_send(req)
        if result and result.retcode in (RC_DONE, RC_PLACED, RC_NO_CHANGES):
            self._state.current_sl = sl
            self._state.initial_sl_points = float(sl_pts)
            self._pos_mgr.set_risk_floor_sl(sl)
            event_name = "INITIAL_SL_SET" if result.retcode != RC_NO_CHANGES else "INITIAL_SL_ALREADY_SET"
            log.info(
                "%s: target_risk_usd=%.2f initial_sl_points=%d "
                "value_per_point=%.5f trade_stops_level=%d sl_price=%.5f retcode=%s",
                event_name,
                target_risk_usd,
                sl_pts,
                value_per_point,
                si.trade_stops_level,
                sl,
                result.retcode,
            )
            self._log_event(event_name, {
                "target_risk_usd": target_risk_usd,
                "initial_sl_points": sl_pts,
                "value_per_point": value_per_point,
                "trade_stops_level": si.trade_stops_level,
                "sl_price": sl,
                "retcode": result.retcode,
            })
        else:
            log.error(
                "CRITICAL: Initial SL set FAILED retcode=%s - EMERGENCY CLOSING POSITION %s",
                result.retcode if result else "None", pos.ticket,
            )
            self._log_event("CRITICAL_SL_SET_FAILED", {
                "ticket": pos.ticket,
                "retcode": result.retcode if result else "None",
                "intended_sl_pts": sl_pts,
            })
            bid = getattr(self, "_last_known_bid", 0.0)
            ask = getattr(self, "_last_known_ask", 0.0)
            if bid > 0 and ask > 0:
                close_price = ask if pos.type == 1 else bid
                close_req = self._adapter.build_market_close_request(
                    symbol=self._cfg["symbol"]["name"],
                    ticket=pos.ticket,
                    volume=pos.volume,
                    pos_type=pos.type,
                    price=close_price,
                    magic=self._cfg["symbol"]["magic"],
                    comment="emergency_sl_set_failed",
                )
                close_result = self._adapter.order_send(close_req)
                if close_result and close_result.retcode in (RC_DONE, RC_PLACED):
                    log.info("Emergency close successful after SL failure")
                    return
                log.error("Emergency close also failed! Position %s running without SL!", pos.ticket)
            slack = self._cfg["risk"].get("emergency_sl_points", 150.0)
            sl_pts = int(slack)
            self._state.initial_sl_points = float(sl_pts)

        # BE activation is USD-based — no dynamic calculation needed here

    def _finalize_closed_trade(
        self: _TradeLifecycleHost,
        bid: float,
        ask: float,
        spread_pts: float,
        si: SymbolSnapshot,
        reason: str,
    ) -> None:
        if self._state.position_ticket is None:
            return

        side = self._state.position_side
        entry = self._state.entry_price or 0.0

        # Prefer actual MT5 fill price over current bid/ask (avoids slippage error)
        actual_fill = self._adapter.get_closing_deal_price(self._state.position_ticket)
        if actual_fill is not None and actual_fill > 0:
            exit_price = actual_fill
        else:
            exit_price = bid if side == Side.BUY else ask

        pnl_pts = calc_pnl_points(entry, exit_price, si.point, side.value if side else "BUY")
        vppt = calc_value_per_point(si.tick_value, si.point, si.tick_size)
        vol = self._state.position_volume
        pnl_usd = pnl_pts * vppt * vol

        close_utc = datetime.now(timezone.utc)
        if self._state.first_fill_utc_ms is not None:
            open_utc = datetime.fromtimestamp(self._state.first_fill_utc_ms / 1000.0, tz=timezone.utc).isoformat()
        else:
            open_utc = close_utc.isoformat()

        rec = TradeRecord(
            trade_id=str(uuid.uuid4()),
            open_time_utc=open_utc,
            close_time_utc=close_utc.isoformat(),
            side=side.value if side else "",
            volume=vol,
            entry_price=entry,
            exit_price=exit_price,
            spread_entry_points=self._trade_entry_spread_pts,
            spread_exit_points=spread_pts,
            pnl_points=pnl_pts,
            pnl_money=pnl_usd,
            pnl_R=round(pnl_pts / self._state.initial_sl_points, 3) if self._state.initial_sl_points else 0.0,
            MFE_points=self._trade_mfe,
            MAE_points=self._trade_mae,
            confirm_success=self._state.confirm.success,
            fake_breakout=not self._state.confirm.success and reason == "fake_breakout",
            reason_exit=reason,
            confirm_elapsed_ms=float(getattr(self._state.confirm, "elapsed_ms_at_finish", 0.0) or 0.0),
            confirm_ticks_used=int(getattr(self._state.confirm, "ticks_seen", 0) or 0),
            confirm_best_move_points=float(getattr(self._state.confirm, "best_move_points", 0.0) or 0.0),
            confirm_threshold_points=float(getattr(self._state.confirm, "threshold_points_at_finish", 0.0) or 0.0),
            confirm_fail_reason=str(getattr(self._state.confirm, "fail_reason", "") or ""),
            be_triggered=self._trade_be_triggered,
            be_time_utc=self._trade_be_time_utc or "",
            be_arm_points=self._trade_be_arm_pts,
            be_buffer_points=self._trade_be_buffer_pts,
            critical_flags=",".join(self._trade_critical_flags) if self._trade_critical_flags else "",
            run_id=self._run_id,
            spec_version=self._spec_version,
            spec_hash=self._spec_hash,
        )
        self._ledger.insert_trade(rec)
        self._log_event("TRADE_CLOSED", {
            "reason": reason,
            "side": side.value if side else "",
            "entry": entry,
            "exit": exit_price,
            "pnl_pts": pnl_pts,
            "pnl_usd": pnl_usd,
        })
        self._feature_logger.on_close(
            pnl_usd=pnl_usd,
            pnl_pts=pnl_pts,
            mae_pts=self._trade_mae,
            mfe_pts=self._trade_mfe,
            exit_reason=reason,
            be_triggered=self._trade_be_triggered,
            trail_triggered=self._trade_trail_triggered,
            trail_updates=self._trade_trail_updates,
            trail_max_pts_locked=self._trade_trail_max_pts,
        )
        self._tg.notify_exit(reason, pnl_pts, pnl_usd)

        self._state.last_closed_side = side
        self._state.last_closed_mono_ms = time.monotonic() * 1000.0
        self._state.reset_position()
        self._state.state = TradingState.ARMED
        self._micro_guard.reset()
        self._profit_continuation_until_ms = 0.0
        self._profit_continuation_side = None

        managed_profit_exit = (
            side is not None
            and pnl_usd > 0
            and (
                not self._profit_continuation_require_managed_exit
                or self._trade_trail_triggered
                or self._trade_be_triggered
            )
        )

        if managed_profit_exit and self._profit_continuation_window_sec > 0 and side is not None:
            now_dc = time.monotonic() * 1000.0
            self._dir_cooldown_until_ms = 0.0
            self._dir_cooldown_block_side = None
            self._profit_continuation_until_ms = (
                now_dc + self._profit_continuation_window_sec * 1000.0
            )
            self._profit_continuation_side = side
            blocked_side = Side.SELL if side == Side.BUY else Side.BUY
            log.info(
                "PROFIT_CONTINUATION_SET: allowing=%s blocking=%s for %.0fs (reason=%s pnl=%.1f pts)",
                side.value,
                blocked_side.value,
                self._profit_continuation_window_sec,
                reason,
                pnl_pts,
            )
            self._log_event("PROFIT_CONTINUATION_SET", {
                "allow_side": side.value,
                "block_side": blocked_side.value,
                "pnl_pts": pnl_pts,
                "duration_sec": self._profit_continuation_window_sec,
                "reason": reason,
            })
        elif side is not None and pnl_usd < 0 and self._dir_cooldown_sec > 0:
            block_side = side
            now_dc = time.monotonic() * 1000.0
            self._dir_cooldown_until_ms = now_dc + self._dir_cooldown_sec * 1000.0
            self._dir_cooldown_block_side = block_side
            self._dir_cooldown_entry_mid = (bid + ask) / 2.0
            log.info(
                "DIR_COOLDOWN_SET: blocking=%s for %.0fs (closed %s pnl=%.1f pts)",
                block_side.value, self._dir_cooldown_sec, side.value, pnl_pts,
            )
            self._log_event("DIR_COOLDOWN_SET", {
                "block_side": block_side.value,
                "closed_side": side.value if side else "",
                "pnl_pts": pnl_pts,
                "duration_sec": self._dir_cooldown_sec,
            })

        if self._deny_only_on_loss:
            cooldown_dur = self._cooldown_after_loss_sec if pnl_usd < 0 else 0.0
        else:
            cooldown_dur = (
                self._cooldown_after_win_sec if pnl_usd >= 0
                else self._cooldown_after_loss_sec
            )
        if cooldown_dur > 0 and not self._state.is_in_cooldown(time.monotonic() * 1000.0):
            now_ms = time.monotonic() * 1000.0
            self._state.set_cooldown(
                cooldown_dur,
                now_ms,
                reason="POST_CLOSE_WIN" if pnl_usd >= 0 else "POST_CLOSE_LOSS",
            )
            log.info(
                "POST_CLOSE_COOLDOWN %.0fs reason=%s pnl_usd=%.2f %s",
                cooldown_dur, reason, pnl_usd,
                "WIN" if pnl_usd >= 0 else "LOSS",
            )
        self._pos_mgr.set_risk_floor_sl(None)
        now_mono = time.monotonic()
        if pnl_usd < 0 or not self._deny_only_on_loss:
            self._closed_trade_times.append(now_mono)
        window_sec = max(float(self._rate_limit_window_sec), 1.0)
        self._closed_trade_times = [t for t in self._closed_trade_times if now_mono - t <= window_sec]
        self._trade_be_triggered = False
        self._trade_be_time_utc = ""
        self._trade_be_arm_pts = 0.0
        self._trade_be_buffer_pts = 0.0
        self._trade_trail_triggered = False
        self._trade_trail_updates = 0
        self._trade_trail_max_pts = 0.0
        self._trade_critical_flags = []
        self._trade_entry_spread_pts = 0.0
        self._trade_mfe = 0.0
        self._trade_mae = 0.0

    def _track_mfe_mae(self: _TradeLifecycleHost, bid: float, ask: float, si: SymbolSnapshot) -> None:
        if self._state.entry_price is None or self._state.position_side is None:
            return
        entry = self._state.entry_price
        if self._state.position_side == Side.BUY:
            move = (bid - entry) / si.point
        else:
            move = (entry - ask) / si.point
        if move > self._trade_mfe:
            self._trade_mfe = move
        if move < -self._trade_mae:
            self._trade_mae = -move