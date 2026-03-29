"""
PositionManager – manages open position lifecycle.

Phases:
  1. Confirm-after-Fill  (POSITION_CONFIRM)
  2. BE arm              (POSITION_ACTIVE before BE done)
  3. Trailing            (POSITION_ACTIVE after BE done)

P0-002: Confirm and TTL are clock-driven (monotonic time).
  * on_clock_confirm() runs EVERY cycle regardless of tick freshness.
  * on_tick_confirm_progress() runs only when a new tick arrives.
  This prevents confirm from "freezing" when tick.time_msc is stale.

P0-006: Cancel-opposite deadline.
  * After fill, opposite pending must be cancelled within CANCEL_DEADLINE_MS.
  * If deadline exceeded → emergency_close_position() + SAFE + cleanup_active.

P0-007: be_triggered is tracked as a separate field (not confirm_success).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.src.adapters.mt5_adapter import (
    MT5Adapter, PositionSnapshot, SymbolSnapshot,
    RC_DONE, RC_PLACED, RC_DONE_PARTIAL, RC_NO_CHANGES,
)
from app.src.core.state import StateStore, TradingState, Side, ConfirmContext
from app.src.core.risk import (
    BEConfig, TrailConfig, ConfirmConfig,
    calc_confirm_move, round_to_step,
)
from app.src.core.position_trailing_policy import (
    apply_risk_floor,
    build_trailing_candidate,
    compute_profit_points,
)
from app.src.core.position_confirm_policy import (
    build_confirm_fail_payload,
    build_confirm_progress_payload,
    build_confirm_success_payload,
    build_confirm_tick_update,
    compute_confirm_move_points,
    evaluate_confirm_window,
    resolve_clock_confirm_threshold,
)
from app.src.core.position_runtime_policy import (
    evaluate_hold_guard,
    evaluate_trailing_throttle,
)

log = logging.getLogger(__name__)

# P0-006: deadline for cancelling opposite pending after fill (ms)
CANCEL_DEADLINE_MS_DEFAULT = 20000.0


@dataclass
class PositionConfig:
    symbol: str   = "XAUUSD"
    magic: int    = 20260225
    volume: float = 0.01
    emergency_sl_points: float = 500.0
    cancel_deadline_ms: float  = CANCEL_DEADLINE_MS_DEFAULT


class PositionManager:
    """
    Handles all position-level actions: confirm, BE, trailing, close.
    All MT5 calls remain on TradingCore's thread via injected adapter.
    """

    def __init__(
        self,
        adapter: MT5Adapter,
        state: StateStore,
        pos_cfg: PositionConfig,
        confirm_cfg: ConfirmConfig,
        be_cfg: BEConfig,
        trail_cfg: TrailConfig,
        event_cb: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._a    = adapter
        self._st   = state
        self._pc   = pos_cfg
        self._cc   = confirm_cfg
        self._bc   = be_cfg
        self._tc   = trail_cfg
        self._emit = event_cb or (lambda ev, d: None)
        # Hold-guard: timestamp when position became active
        self._position_start_mono_ms: float = 0.0
        # Risk floor SL: trailing never moves SL below this (for BUY) / above this (for SELL)
        self._risk_floor_sl: Optional[float] = None

    # ── P0-002: Clock-driven confirm ─────────────────────────────────────────

    def set_be_activation_points(self, pts: float) -> None:
        """No-op: kept for call-site compatibility."""
        pass

    def set_risk_floor_sl(self, sl: Optional[float]) -> None:
        """Set/clear the risk-floor SL price. Trailing never moves SL past this.
        BUY: SL stays >= floor. SELL: SL stays <= floor. None=no floor."""
        self._risk_floor_sl = sl
        log.info("RISK_FLOOR_SL_SET: %s", sl)

    def set_position_start_ms(self, mono_ms: float) -> None:
        """Called by engine when position becomes ACTIVE."""
        self._position_start_mono_ms = mono_ms
        log.debug("POSITION_START_MS: %.0f", mono_ms)

    def set_trail_atr_pts(self, atr_pts: float) -> None:
        """No-op: kept for call-site compatibility."""
        pass

    def on_clock_confirm(self, mono_ms: float) -> "Optional[dict]":
        """
        P0-002: Called EVERY cycle (clock-driven), regardless of new tick.
        Checks elapsed time to enforce window_ms timeout.
        Does NOT require a new tick to fire.
        """
        ctx = self._st.confirm
        if ctx.finished:
            return

        elapsed_ms = mono_ms - ctx.start_monotonic_ms
        window = evaluate_confirm_window(
            elapsed_ms=elapsed_ms,
            ticks_seen=ctx.ticks_seen,
            window_ms=self._cc.window_ms,
            window_ticks=self._cc.window_ticks,
        )

        if window.timed_out:
            # Compute threshold snapshot for forensics
            threshold = resolve_clock_confirm_threshold(
                best_move_points=ctx.best_move_points,
                threshold_points_at_finish=ctx.threshold_points_at_finish,
                confirm_min_points=self._cc.confirm_min_points,
            )

            if ctx.best_move_points >= threshold and threshold > 0:
                # Success via time/tick window but threshold already met
                # (tick_event should have caught this – handle here as fallback)
                self._finalize_confirm_success(ctx, elapsed_ms, threshold)
                return {"timed_out": True, "success": True, "elapsed_ms": elapsed_ms, "window_ms": self._cc.window_ms}
            else:
                self._finalize_confirm_fail(ctx, elapsed_ms, threshold, window.fail_reason)
                return {"timed_out": True, "success": False, "elapsed_ms": elapsed_ms, "window_ms": self._cc.window_ms, "ticks_seen": ctx.ticks_seen}

        # Emit progress event for observability
        elif elapsed_ms > 0 and elapsed_ms % 250 < 50:  # roughly every 250ms
            self._emit(
                "CONFIRM_PROGRESS",
                build_confirm_progress_payload(
                    elapsed_ms=elapsed_ms,
                    ticks_seen=ctx.ticks_seen,
                    best_move_points=ctx.best_move_points,
                    window_ms=self._cc.window_ms,
                    window_ticks=self._cc.window_ticks,
                ),
            )

    def on_tick_confirm_progress(
        self,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_med_pts: float,
        si: SymbolSnapshot,
        mono_ms: float,
    ) -> None:
        """
        P0-002: Called only when a NEW tick arrives (is_new_tick == True).
        Updates tick count and best_move progress.
        May fire success if threshold crossed.
        """
        ctx = self._st.confirm
        if ctx.finished:
            return

        ctx.ticks_seen += 1
        threshold = calc_confirm_move(atr_pts, spread_med_pts, self._cc)
        ctx.threshold_points_at_finish = threshold  # keep updated for clock fallback
        elapsed_ms = mono_ms - ctx.start_monotonic_ms
        update = build_confirm_tick_update(
            side=self._st.position_side,
            entry_price=self._st.entry_price,
            bid=bid,
            ask=ask,
            point=si.point,
            previous_best_move_points=ctx.best_move_points,
            threshold_points=threshold,
            elapsed_ms=elapsed_ms,
            window_ms=self._cc.window_ms,
            ticks_seen=ctx.ticks_seen,
            window_ticks=self._cc.window_ticks,
        )
        ctx.best_move_points = update.best_move_points

        if update.success:
            # SUCCESS – threshold crossed on a tick
            self._finalize_confirm_success(ctx, elapsed_ms, threshold)
        elif update.fail_reason:
            # Window ended on this tick
            self._finalize_confirm_fail(ctx, elapsed_ms, threshold, update.fail_reason)

    # Keep backward-compat shim for existing callers (P0-002 migration helper)
    def tick_confirm(
        self,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_med_pts: float,
        si: SymbolSnapshot,
        mono_ms: float,
    ) -> None:
        """Deprecated shim: real callers should use on_clock_confirm + on_tick_confirm_progress."""
        self.on_clock_confirm(mono_ms)
        if not self._st.confirm.finished:
            self.on_tick_confirm_progress(bid, ask, atr_pts, spread_med_pts, si, mono_ms)

    def _finalize_confirm_success(
        self, ctx: ConfirmContext, elapsed_ms: float, threshold: float
    ) -> None:
        ctx.finished = True
        ctx.success  = True
        ctx.elapsed_ms_at_finish = elapsed_ms
        ctx.threshold_points_at_finish = threshold
        self._st.state = TradingState.POSITION_ACTIVE
        self._st.extreme_price = (
            self._get_live_best_price_for_extreme()
        )
        log.info(
            "CONFIRM_SUCCESS: best=%.1f pts threshold=%.1f pts ticks=%d elapsed=%.0f ms",
            ctx.best_move_points, threshold, ctx.ticks_seen, elapsed_ms,
        )
        self._emit(
            "CONFIRM_SUCCESS",
            build_confirm_success_payload(
                best_move_points=ctx.best_move_points,
                threshold=threshold,
                ticks_seen=ctx.ticks_seen,
                elapsed_ms=elapsed_ms,
            ),
        )

    def _finalize_confirm_fail(
        self, ctx: ConfirmContext, elapsed_ms: float, threshold: float, reason: str
    ) -> None:
        ctx.finished = True
        ctx.success  = False
        ctx.elapsed_ms_at_finish = elapsed_ms
        ctx.threshold_points_at_finish = threshold
        ctx.fail_reason = reason
        log.warning(
            "CONFIRM_FAIL_FAKE_BREAKOUT: best=%.1f pts threshold=%.1f pts "
            "ticks=%d elapsed=%.0f ms reason=%s",
            ctx.best_move_points, threshold, ctx.ticks_seen, elapsed_ms, reason,
        )
        self._emit(
            "FAKE_BREAKOUT",
            build_confirm_fail_payload(
                best_move_points=ctx.best_move_points,
                threshold=threshold,
                ticks_seen=ctx.ticks_seen,
                elapsed_ms=elapsed_ms,
                reason=reason,
            ),
        )
        # Close position
        self._exit_on_fake_breakout_by_side()
        if self._cc.cooldown_on_fail_sec > 0:
            self._st.set_cooldown(self._cc.cooldown_on_fail_sec, reason="CONFIRM_FAIL")

    def _get_live_best_price_for_extreme(self) -> Optional[float]:
        """Return cached price for extreme tracking; actual tick values set in engine."""
        return None  # engine sets extreme_price after calling this

    # ── P0-006: Cancel deadline clock check ──────────────────────────────────

    def check_cancel_deadline(
        self,
        mono_ms: float,
        si: SymbolSnapshot,
        bid: float,
        ask: float,
    ) -> bool:
        """
        P0-006: Called every cycle when position is open and cleanup not yet done.
        Returns True if emergency was triggered (caller should enter SAFE MODE).
        """
        if self._st.cancel_opposite_deadline_mono is None:
            return False
        if self._st.cleanup_active:
            return False  # already in cleanup loop

        if mono_ms < self._st.cancel_opposite_deadline_mono:
            return False  # deadline not reached yet

        # Deadline exceeded – but first verify there are ACTUAL pending orders left.
        # If cancel already succeeded (race between confirm and deadline timer),
        # clearing the deadline is sufficient – no emergency close needed.
        live_orders = self._a.get_orders(self._pc.symbol)
        my_pendings = [o for o in live_orders if getattr(o, 'magic', None) == self._pc.magic]
        if not my_pendings:
            log.info(
                "cancel_deadline exceeded but no pending orders remain – "
                "cancel already succeeded (deadline=%.0f ms). Clearing.",
                self._pc.cancel_deadline_ms,
            )
            self._emit("CANCEL_DEADLINE_AUTO_CLEARED", {
                "deadline_ms": self._pc.cancel_deadline_ms,
                "elapsed_ms": mono_ms - (self._st.cancel_opposite_deadline_mono - self._pc.cancel_deadline_ms),
            })
            self._st.cancel_opposite_deadline_mono = None
            return False

        # Pendings still exist – trigger emergency
        log.critical(
            "CRITICAL_OPPOSITE_CANCEL_FAILED: deadline=%.0f ms exceeded, "
            "%d pending(s) still present – triggering emergency close",
            self._pc.cancel_deadline_ms, len(my_pendings),
        )
        self._emit("CRITICAL_OPPOSITE_CANCEL_FAILED", {
            "deadline_ms": self._pc.cancel_deadline_ms,
            "pending_count": len(my_pendings),
            "freeze_level": si.trade_freeze_level,
            "stops_level": si.trade_stops_level,
        })

        # Emergency close
        success = self.emergency_close_position(bid, ask, si)
        self._emit("EMERGENCY_CLOSE_RESULT", {"success": success})

        # Mark cleanup active so engine enters SAFE and runs cleanup loop
        self._st.cleanup_active = True
        # Clear deadline so we don't re-trigger
        self._st.cancel_opposite_deadline_mono = None
        return True

    def run_pending_cleanup_step(self) -> int:
        """
        P0-006: Cancel any still-present pendings. Returns count remaining.
        Called each cycle while cleanup_active == True.
        """
        from app.src.adapters.mt5_adapter import TRADE_ACTION_REMOVE
        orders = self._a.get_orders(self._pc.symbol)
        my_orders = [o for o in orders if o.magic == self._pc.magic]
        remaining = len(my_orders)
        if remaining > 0:
            for o in my_orders:
                req = self._a.build_cancel_request(o.ticket)
                self._a.order_send(req)
        self._emit("PENDING_CLEANUP_STEP", {
            "pending_count_before": remaining,
            "pending_count_after": max(0, remaining - len(my_orders)),
        })
        return remaining

    # ── Active phase: BE + Trailing ──────────────────────────────────────────

    def tick_active(
        self,
        bid: float,
        ask: float,
        atr_pts: float,
        spread_med_pts: float,
        si: SymbolSnapshot,
        mono_ms: float,
        trail_atr_pts: float = 0.0,
    ) -> None:
        """Called each tick/clock while state == POSITION_ACTIVE."""
        if self._st.position_ticket is None:
            return

        if not self._st.be_done:
            self._check_be(bid, ask, si, mono_ms)

        self._update_trailing(bid, ask, si, mono_ms)

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_position_market(self, bid: float, ask: float, si: SymbolSnapshot,
                              comment: str = "manual_close") -> bool:
        pos = self._get_live_position()
        if pos is None:
            log.warning("close_position_market: no live position found")
            return False

        close_price = ask if pos.type == 1 else bid  # SELL→close@ask, BUY→close@bid
        req = self._a.build_market_close_request(
            symbol=self._pc.symbol,
            ticket=pos.ticket,
            volume=pos.volume,
            pos_type=pos.type,
            price=close_price,
            magic=self._pc.magic,
            comment=comment,
        )
        result = self._a.order_send(req)
        if result and result.retcode in (RC_DONE, RC_DONE_PARTIAL):
            log.info("Position closed: ticket=%s retcode=%s", pos.ticket, result.retcode)
            self._emit("POSITION_CLOSED", {"reason": comment, "retcode": result.retcode})
            return True
        log.error("close_position_market failed: retcode=%s", result.retcode if result else "None")
        return False

    def emergency_close_position(self, bid: float, ask: float, si: SymbolSnapshot) -> bool:
        """P0-006: Emergency market close. Logs EMERGENCY_CLOSE_SENT."""
        self._emit("EMERGENCY_CLOSE_SENT", {
            "bid": bid, "ask": ask,
            "position_ticket": self._st.position_ticket,
        })
        return self.close_position_market(bid, ask, si, comment="emergency_cancel_deadline")

    # ── Private ───────────────────────────────────────────────────────────────

    def _check_be(
        self,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
    ) -> None:
        """Simple USD-based breakeven: fire when profit_usd >= be_activation_usd,
        set SL to entry + be_stop_usd equivalent in points."""
        if self._st.entry_price is None:
            return

        # Min hold guard
        if self._bc.min_hold_ms > 0 and self._position_start_mono_ms > 0:
            if (mono_ms - self._position_start_mono_ms) < self._bc.min_hold_ms:
                return

        profit_pts = compute_profit_points(
            self._st.position_side,
            self._st.entry_price,
            bid,
            ask,
            si.point,
        )

        # Convert to USD
        vpp = si.value_per_point * self._pc.volume
        if vpp <= 0:
            return
        profit_usd = profit_pts * vpp

        if profit_usd < self._bc.be_activation_usd:
            return

        # Compute BE stop price (entry + be_stop_usd equivalent in points)
        be_stop_pts = self._bc.be_stop_usd / vpp
        if self._st.position_side == Side.BUY:
            sl_price = round_to_step(
                self._st.entry_price + be_stop_pts * si.point, si.point, si.digits
            )
            current_sl = self._st.current_sl or 0.0
            if sl_price <= current_sl:
                return  # already protected at this level or better
        else:
            sl_price = round_to_step(
                self._st.entry_price - be_stop_pts * si.point, si.point, si.digits
            )
            current_sl = self._st.current_sl if self._st.current_sl is not None else float("inf")
            if sl_price >= current_sl:
                return

        if self._violates_stops(sl_price, bid, ask, si):
            return

        if self._modify_sl(sl_price, si):
            self._st.be_done = True
            self._st.current_sl = sl_price
            log.info(
                "BE_SET: sl=%.5f profit_pts=%.1f profit_usd=%.2f be_stop_pts=%.1f",
                sl_price, profit_pts, profit_usd, be_stop_pts,
            )
            self._emit("BE_MOVED", {
                "sl": sl_price,
                "profit_pts": round(profit_pts, 1),
                "profit_usd": round(profit_usd, 2),
                "be_activation_usd": self._bc.be_activation_usd,
                "be_stop_usd": self._bc.be_stop_usd,
                "be_stop_pts": round(be_stop_pts, 1),
            })

    def _update_trailing(
        self,
        bid: float,
        ask: float,
        si: SymbolSnapshot,
        mono_ms: float,
    ) -> None:
        """Simple fixed-points trailing.
        Activates when profit >= trail_activation_points from entry.
        Moves SL (trail_stop_points behind extreme) when price advances trail_step_points.
        """
        if self._st.entry_price is None:
            return

        profit_pts = compute_profit_points(
            self._st.position_side,
            self._st.entry_price,
            bid,
            ask,
            si.point,
        )

        if profit_pts < self._tc.trail_activation_points:
            return

        # Throttle
        mono_s = mono_ms / 1000.0
        since_last = evaluate_trailing_throttle(
            mono_ms=mono_ms,
            last_trailing_update_mono=self._st.last_trailing_update_mono,
            throttle_sec=self._tc.throttle_sec,
        )
        if since_last is not None:
            return

        # Update extreme price and compute SL candidate
        candidate = build_trailing_candidate(
            side=self._st.position_side,
            current_extreme_price=self._st.extreme_price,
            current_sl=self._st.current_sl,
            bid=bid,
            ask=ask,
            trail_dist=self._tc.trail_stop_points,
            point=si.point,
            digits=si.digits,
        )
        self._st.extreme_price = candidate.extreme_price
        improvement = candidate.improvement

        if improvement < self._tc.trail_step_points * si.point:
            return

        sl_candidate = apply_risk_floor(
            side=self._st.position_side,
            sl_candidate=candidate.sl_candidate,
            risk_floor_sl=self._risk_floor_sl,
            point=si.point,
            digits=si.digits,
        )

        # Clamp SL to MT5 minimum stop distance instead of skipping entirely.
        # Using strict > (not >=) to avoid borderline rejections.
        _min_sl_gap = (si.trade_stops_level + 2) * si.point
        if self._st.position_side == Side.BUY:
            sl_candidate = min(sl_candidate, round_to_step(bid - _min_sl_gap, si.point, si.digits))
            if sl_candidate <= (self._st.current_sl or 0.0):
                return
        else:
            sl_candidate = max(sl_candidate, round_to_step(ask + _min_sl_gap, si.point, si.digits))
            if sl_candidate >= (self._st.current_sl if self._st.current_sl is not None else float("inf")):
                return

        if self._in_freeze_zone(sl_candidate, bid, ask, si):
            return

        if self._modify_sl(sl_candidate, si):
            self._st.current_sl = sl_candidate
            self._st.last_trailing_update_mono = mono_s
            log.info(
                "TRAIL_UPDATE: sl=%.5f extreme=%.5f profit_pts=%.1f gap_pts=%.1f step_pts=%.1f",
                sl_candidate, candidate.extreme_price, profit_pts,
                self._tc.trail_stop_points, self._tc.trail_step_points,
            )
            self._emit("TRAIL_UPDATE", {
                "sl": sl_candidate,
                "old_sl": candidate.current_sl,
                "extreme": candidate.extreme_price,
                "trail_stop_pts": self._tc.trail_stop_points,
                "trail_step_pts": self._tc.trail_step_points,
                "profit_pts": round(profit_pts, 1),
            })



    def _exit_on_fake_breakout_by_side(self) -> None:
        """Try to close near BE, fallback to market. No bid/ask needed (re-queries)."""
        pos = self._get_live_position()
        if pos is None:
            return
        orders = self._a.get_orders(self._pc.symbol)
        tick = None
        try:
            # Get fresh prices if available via adapter
            positions = self._a.get_positions(self._pc.symbol)
        except Exception:
            pass
        # Use a simple market close; engine will handle finalization
        si_dummy = None
        # We don't have si/bid/ask here; emit the event, engine handles close
        self._emit("FAKE_BREAKOUT_CLOSE_NEEDED", {
            "ticket": pos.ticket,
            "pos_type": pos.type,
        })

    def _exit_on_fake_breakout(
        self, bid: float, ask: float, si: SymbolSnapshot
    ) -> None:
        """Try to close near BE, fallback to market."""
        pos = self._get_live_position()
        if pos is None:
            return
        close_price = ask if pos.type == 1 else bid
        req = self._a.build_market_close_request(
            symbol=self._pc.symbol,
            ticket=pos.ticket,
            volume=pos.volume,
            pos_type=pos.type,
            price=close_price,
            magic=self._pc.magic,
            comment="fake_breakout_exit",
        )
        result = self._a.order_send(req)
        if result and result.retcode in (RC_DONE, RC_DONE_PARTIAL):
            log.info("Fake-breakout exit done: ticket=%s", pos.ticket)
        else:
            log.error("Fake-breakout exit FAILED: retcode=%s",
                      result.retcode if result else "None")

    def _modify_sl(self, sl: float, si: SymbolSnapshot) -> bool:
        if self._st.position_ticket is None:
            return False
        req = self._a.build_modify_sl_request(
            symbol=self._pc.symbol,
            ticket=self._st.position_ticket,
            sl=sl,
            is_position=True,
        )
        result = self._a.order_send(req)
        if result and result.retcode in (RC_DONE, RC_PLACED, RC_NO_CHANGES):
            return True
        log.warning("modify_sl failed: sl=%.5f retcode=%s",
                    sl, result.retcode if result else "None")
        return False

    def _get_live_position(self) -> Optional[PositionSnapshot]:
        positions = self._a.get_positions(self._pc.symbol)
        if self._st.position_ticket:
            for p in positions:
                if p.ticket == self._st.position_ticket and p.magic == self._pc.magic:
                    return p
        for p in positions:
            if p.magic == self._pc.magic:
                return p
        return None

    def _violates_stops(
        self, sl: float, bid: float, ask: float, si: SymbolSnapshot
    ) -> bool:
        min_dist = si.trade_stops_level * si.point
        ref = bid if self._st.position_side == Side.BUY else ask
        return abs(sl - ref) < min_dist

    def _in_freeze_zone(
        self, sl: float, bid: float, ask: float, si: SymbolSnapshot
    ) -> bool:
        freeze = si.trade_freeze_level * si.point
        if freeze <= 0:
            return False
        ref = bid if self._st.position_side == Side.BUY else ask
        return abs(sl - ref) < freeze

    @property
    def be_arm_points(self) -> float:
        return self._be_arm_points

    @property
    def be_buffer_points(self) -> float:
        return self._be_buffer_points

    @property
    def be_activation_points(self) -> float:
        return self._be_activation_points
