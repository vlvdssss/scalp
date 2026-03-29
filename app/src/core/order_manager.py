"""
OrderManager – maintains dual BUY STOP / SELL STOP pending orders.

Responsibilities:
  * Place dual pending when ARMED and no deny
  * Rearm (cancel+replace) when price drifts beyond REARM_THRESHOLD
  * Cancel all pending on DENY / SAFE / SESSION_BLOCK
  * TTL control (external watchdog approach for brokers that ignore ORDER_TIME_SPECIFIED)
  * Guard against placing double orders
  * Respect trade_stops_level and trade_freeze_level (INV-E)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from typing import Any, Callable, Optional

from app.src.adapters.mt5_adapter import (
    MT5Adapter, OrderSnapshot, SymbolSnapshot,
    RC_DONE, RC_PLACED, RC_DONE_PARTIAL, RC_INVALID_STOPS, RC_NO_CHANGES,
    RC_REQUOTE, RC_PRICE_CHANGED, RC_TIMEOUT,
    get_retcode_policy, RetcodeAction,
)
from app.src.core.state import StateStore, Side
from app.src.core.risk import (
    EntryConfig, calc_entry_offset, calc_rearm_threshold, calc_sl_distance,
    calc_entry_buffer, round_to_step,
)

log = logging.getLogger(__name__)


@dataclass
class PendingConfig:
    symbol: str           = "XAUUSD"
    magic: int            = 20260225
    volume: float         = 0.01
    ttl_sec: float        = 300.0
    use_order_time_specified: bool = False
    backoff_invalid_stops_ms: float = 2000.0
    backoff_requote_ms: float       = 500.0
    max_retries_requote: int        = 3
    # P1-2: order_send elapsed > op_deadline_ms triggers SAFE MODE
    op_deadline_ms: float           = 3000.0


class OrderManager:
    """
    Manages dual pending stop orders. All MT5 calls MUST be made from
    TradingCore's single thread before calling any method.
    """

    def __init__(
        self,
        adapter: MT5Adapter,
        state: StateStore,
        entry_cfg: EntryConfig,
        pending_cfg: PendingConfig,
        retcode_policy_cb: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        self._a   = adapter
        self._st  = state
        self._ec  = entry_cfg
        self._pc  = pending_cfg
        self._last_invalid_stops_ts: float = 0.0
        self._retcode_policy_cb = retcode_policy_cb
        # Aggressive mode state
        self._last_buy_place_ms:  float = 0.0
        self._last_sell_place_ms: float = 0.0
        self._tick_history: list[tuple[float, float]] = []  # (ms, mid_price)
        # Trailing Pending: capture mode state
        self._impulse_capture_until_ms: float = 0.0
        # Rearm flags: bypass burst check when replacing (not new entry)
        self._buy_rearm_pending:  bool = False
        self._sell_rearm_pending: bool = False
        # Flat detector + freeze state
        self._flat_frozen: bool           = False
        self._flat_freeze_until_ms: float = 0.0

    # ── Public API ─────────────────────────────────────────────────────────────

    def ensure_dual_pending(
        self,
        tick_bid: float,
        tick_ask: float,
        atr_pts: float,
        spread_med_pts: float,
        si: SymbolSnapshot,
        now_ms: float,
        blocked_side: Optional[Side] = None,
    ) -> None:
        """
        Called each cycle when state==ARMED and deny==False.
        Guarantees two pending orders exist with up-to-date prices.
        INV-B enforcement.
        blocked_side: if set (Side.BUY or Side.SELL), that side is suppressed/cancelled.
        """
        # ── Safety: skip if position exists (ISMarkets fix) ────────────────────
        try:
            positions = self._a.get_positions(self._pc.symbol)
            my_positions = [p for p in positions if getattr(p, 'magic', None) == self._pc.magic]
            if my_positions:
                log.debug("SKIP_PENDING: position exists ticket=%s", my_positions[0].ticket)
                return
        except Exception:
            pass  # If can't check, proceed with caution
        
        # ── Update tick history for burst / impulse-age filters ────────────────
        mid = (tick_bid + tick_ask) / 2.0
        self._tick_history.append((now_ms, mid))
        # Keep enough history for both 3s burst detection AND flat_window_ms
        _hist_cutoff = now_ms - max(3000.0, self._ec.flat_window_ms + 500.0)
        self._tick_history = [(t, p) for t, p in self._tick_history if t >= _hist_cutoff]

        offset   = calc_entry_offset(atr_pts, spread_med_pts, self._ec)
        rearm_th = calc_rearm_threshold(atr_pts, spread_med_pts, self._ec)
        sl_dist  = calc_sl_distance(atr_pts, spread_med_pts, self._ec)

        # ── Trailing Pending: idle vs capture mode ────────────────────────────────
        # Detect impulse: price movement in last 300ms
        cutoff_300 = now_ms - 300.0
        recent_300 = [p for t, p in self._tick_history if t >= cutoff_300]
        delta_300ms = (
            abs(recent_300[-1] - recent_300[0]) / si.point
            if len(recent_300) >= 2 else 0.0
        )

        _was_capture = now_ms < self._impulse_capture_until_ms
        if delta_300ms >= self._ec.impulse_capture_delta_pts and not _was_capture:
            self._impulse_capture_until_ms = now_ms + self._ec.impulse_capture_dur_ms
            log.info(
                "IMPULSE_CAPTURE_ENTER: delta_300ms=%.1fpts, active for %.0fms",
                delta_300ms, self._ec.impulse_capture_dur_ms,
            )
        _in_capture = now_ms < self._impulse_capture_until_ms

        if _in_capture:
            # narrow offset: snap close to price, catch the move
            offset = max(
                spread_med_pts * self._ec.impulse_capture_spread_mult,
                self._ec.impulse_capture_floor_pts,
            )
            _rearm_hysteresis = 8.0  # chase aggressively during impulse
            _min_order_age    = 500.0   # wait ≥500ms before replacing in capture mode
            log.debug(
                "IMPULSE_CAPTURE_ACTIVE: offset=%.1f delta=%.1f rem_ms=%.0f",
                offset, delta_300ms, self._impulse_capture_until_ms - now_ms,
            )
        else:
            # idle mode: wide offset + NoiseRatio multiplier
            idle_min = max(
                spread_med_pts * self._ec.idle_offset_spread_mult,
                self._ec.entry_offset_min_points,
            )
            base_offset = max(
                self._ec.k_entry_atr    * atr_pts,
                self._ec.k_entry_spread * spread_med_pts,
                idle_min,
            )
            # apply noise multiplier (choppy market → push even further)
            noise_ratio = self._calc_noise_ratio(now_ms, si.point)
            if noise_ratio >= self._ec.noise_ratio_high:
                noise_mult = self._ec.noise_mult_high
            elif noise_ratio >= self._ec.noise_ratio_mid:
                noise_mult = self._ec.noise_mult_mid
            else:
                noise_mult = 1.0
            cap_offset = self._ec.offset_cap_atr * atr_pts if atr_pts > 0 else idle_min
            max_offset  = max(spread_med_pts * self._ec.offset_max_spread_mult, cap_offset)
            if max_offset < idle_min:
                max_offset = idle_min
            offset_raw = base_offset * noise_mult
            offset = max(idle_min, min(offset_raw, max_offset))
            # Hard absolute ceiling: prevent spike-inflated ATR from pushing orders to cosmos
            if self._ec.offset_abs_max_points > 0:
                offset = min(offset, self._ec.offset_abs_max_points)
            _rearm_hysteresis = self._ec.rearm_hysteresis_pts
            _min_order_age    = self._ec.min_order_age_ms
            log.debug(
                "IDLE_OFFSET: idle_min=%.1f noise=%.2f mult=%.1f raw=%.1f final=%.1f",
                idle_min, noise_ratio, noise_mult, offset_raw, offset,
            )

        # ── Flat detector + freeze ──────────────────────────────────────────────────
        _flat_range, _is_flat = self._detect_flat(now_ms, si.point)

        # Unfreeze: TTL expired
        if self._flat_frozen and now_ms >= self._flat_freeze_until_ms:
            log.info("FLAT_FREEZE_TTL_EXPIRED: repositioning, range=%.1fpts", _flat_range)
            self._flat_frozen = False
        # Unfreeze: price escaped consolidation range
        if self._flat_frozen and not _is_flat:
            log.info("FLAT_UNFREEZE_BREAKOUT: range=%.1fpts > threshold=%.1fpts",
                     _flat_range, self._ec.flat_range_pts)
            self._flat_frozen = False

        _expand_override = self._ec.orders_expand_points  # default
        if _is_flat and self._ec.flat_freeze_enabled:
            _flat_off = max(self._ec.flat_offset_pts, spread_med_pts * 3.0)
            offset = _flat_off         # override: tighter placement in consolidation
            _expand_override = 0.0     # no extra expand in flat mode
            if self._flat_frozen:
                _rearm_hysteresis = max(
                    self._ec.rearm_hysteresis_pts,
                    rearm_th,
                    _flat_off * 0.6,
                )
                _min_order_age = max(
                    _min_order_age,
                    min(self._ec.flat_freeze_ttl_ms / 3.0, 1500.0),
                )
                log.debug(
                    "FLAT_FROZEN: range=%.1fpts offset=%.1fpts hysteresis=%.1f min_age=%.0fms",
                    _flat_range,
                    _flat_off,
                    _rearm_hysteresis,
                    _min_order_age,
                )
            else:
                # Enter freeze: reposition this once then hold for TTL
                self._flat_frozen = True
                self._flat_freeze_until_ms = now_ms + self._ec.flat_freeze_ttl_ms
                _rearm_hysteresis = 0.0   # force repositioning right now
                _min_order_age    = 0.0   # bypass age guard for this one move
                log.info("FLAT_DETECTED: range=%.1fpts <= %.1fpts → placing at %.1fpts, "
                         "freeze for %.0fms",
                         _flat_range, self._ec.flat_range_pts,
                         _flat_off, self._ec.flat_freeze_ttl_ms)

        # APT v2: entry_buffer pushes orders further from price to avoid micro-spikes.
        # Enforce one final absolute floor so candle/flat refreshes cannot squeeze orders too close.
        with_buffer_offset = offset + calc_entry_buffer(atr_pts, spread_med_pts, self._ec)
        # Fixed expand: BUY STOP moves up, SELL STOP moves down (wider placement)
        # _expand_override is 0 in flat mode (already at tight flat offset)
        _expand = _expand_override
        total_offset = with_buffer_offset + _expand
        if self._ec.min_total_offset_points > 0:
            total_offset = max(total_offset, self._ec.min_total_offset_points)

        # ── Counter-trend post-close: push opposite-direction order further ───
        _ct_buy_extra = _ct_sell_extra = 0.0
        _ct_extra = self._ec.counter_trend_extra_points
        _ct_win = self._ec.counter_trend_window_sec * 1000.0
        if _ct_extra > 0 and _ct_win > 0:
            _lc_side = self._st.last_closed_side
            _lc_ms   = self._st.last_closed_mono_ms
            if _lc_side is not None and (now_ms - _lc_ms) < _ct_win:
                if _lc_side.value == "BUY":
                    _ct_sell_extra = _ct_extra   # last was BUY → SELL is counter-trend
                else:
                    _ct_buy_extra  = _ct_extra   # last was SELL → BUY is counter-trend

        target_buy  = round_to_step(tick_ask + (total_offset + _ct_buy_extra)  * si.point, si.point, si.digits)
        target_sell = round_to_step(tick_bid - (total_offset + _ct_sell_extra) * si.point, si.point, si.digits)

        # Compute initial SL for pending orders using proper SL distance (not rearm_th)
        buy_sl  = round_to_step(target_buy  - sl_dist * si.point, si.point, si.digits)
        sell_sl = round_to_step(target_sell + sl_dist * si.point, si.point, si.digits)

        has_buy  = self._st.buy_stop_ticket  is not None
        has_sell = self._st.sell_stop_ticket is not None

        # ── Directional-cooldown: cancel + suppress blocked side ───────────────────
        _buy_blocked  = blocked_side is not None and blocked_side.value == "BUY"
        _sell_blocked = (blocked_side is not None and blocked_side.value == "SELL") or self._ec.only_buy
        if _buy_blocked and has_buy:
            log.info("DIR_COOLDOWN: cancelling BUY_STOP (blocked)")
            buy_ticket = self._st.buy_stop_ticket
            assert buy_ticket is not None
            self._cancel_order(buy_ticket, "BUY")
            self._st.buy_stop_ticket = None
            self._st.buy_stop_price  = None
            has_buy = False
        if _sell_blocked and has_sell:
            log.info("DIR_COOLDOWN: cancelling SELL_STOP (blocked)")
            sell_ticket = self._st.sell_stop_ticket
            assert sell_ticket is not None
            self._cancel_order(sell_ticket, "SELL")
            self._st.sell_stop_ticket = None
            self._st.sell_stop_price  = None
            has_sell = False
        # Pass per-side last_place_ms so each side has its own burst timeout countdown
        buy_burst_ok     = self._check_burst(
            direction=1,
            now_ms=now_ms,
            spread_med_pts=spread_med_pts,
            point=si.point,
            last_place_ms=self._last_buy_place_ms,
        )
        sell_burst_ok    = self._check_burst(
            direction=-1,
            now_ms=now_ms,
            spread_med_pts=spread_med_pts,
            point=si.point,
            last_place_ms=self._last_sell_place_ms,
        )
        buy_impulse_old  = self._check_impulse_old(direction=1,  atr_pts=atr_pts,
                                                   now_ms=now_ms, point=si.point)
        sell_impulse_old = self._check_impulse_old(direction=-1, atr_pts=atr_pts,
                                                   now_ms=now_ms, point=si.point)
        dominant_move_pts = self._recent_net_move(
            now_ms=now_ms,
            window_ms=self._ec.countertrend_guard_window_ms,
            point=si.point,
        )
        countertrend_threshold = max(
            self._ec.countertrend_guard_min_pts,
            atr_pts * self._ec.countertrend_guard_atr_mult,
        )
        buy_countertrend_guard = (
            dominant_move_pts <= -countertrend_threshold
            and not buy_burst_ok
            and not self._buy_rearm_pending
        )
        sell_countertrend_guard = (
            dominant_move_pts >= countertrend_threshold
            and not sell_burst_ok
            and not self._sell_rearm_pending
        )

        # ── TTL check ──────────────────────────────────────────────────────────
        if has_buy and self._is_ttl_expired(now_ms):
            log.info("Pending TTL expired – replacing dual pending")
            self.cancel_all(si)
            has_buy = has_sell = False

        # ── Rearm check ────────────────────────────────────────────────────────
        if has_buy and self._st.buy_stop_price is not None:
            drift = abs(target_buy - self._st.buy_stop_price) / si.point
            if drift < _rearm_hysteresis and _rearm_hysteresis > 0:
                log.debug("REARM_SKIPPED_HYSTERESIS BUY drift=%.1f < hyst=%.1f",
                          drift, _rearm_hysteresis)
            elif drift > rearm_th:
                move_buy_toward_price = target_buy < self._st.buy_stop_price
                if buy_countertrend_guard and move_buy_toward_price:
                    log.debug(
                        "COUNTERTREND_GUARD BUY: dominant_move=%.1fpts threshold=%.1fpts",
                        dominant_move_pts,
                        countertrend_threshold,
                    )
                else:
                    age_ms = now_ms - self._last_buy_place_ms
                    if _min_order_age > 0 and age_ms < _min_order_age:
                        log.debug("REARM_SKIPPED_MIN_AGE BUY age=%.0fms < %.0fms",
                                  age_ms, _min_order_age)
                    elif not self._in_freeze(self._st.buy_stop_price, si):
                        assert self._st.buy_stop_ticket is not None
                        modified = self._modify_pending_order(
                            ticket=self._st.buy_stop_ticket,
                            price=target_buy,
                            sl=buy_sl,
                            label="BUY",
                            now_ms=now_ms,
                        )
                        if modified:
                            self._st.buy_stop_price = target_buy
                        else:
                            log.info("BUY pending modify failed; falling back to cancel+replace")
                            self._cancel_order(self._st.buy_stop_ticket, "BUY")
                            self._st.buy_stop_ticket = None
                            self._st.buy_stop_price = None
                            has_buy = False
                            self._buy_rearm_pending = True

        if has_sell and self._st.sell_stop_price is not None:
            drift = abs(target_sell - self._st.sell_stop_price) / si.point
            if drift < _rearm_hysteresis and _rearm_hysteresis > 0:
                log.debug("REARM_SKIPPED_HYSTERESIS SELL drift=%.1f < hyst=%.1f",
                          drift, _rearm_hysteresis)
            elif drift > rearm_th:
                move_sell_toward_price = target_sell > self._st.sell_stop_price
                if sell_countertrend_guard and move_sell_toward_price:
                    log.debug(
                        "COUNTERTREND_GUARD SELL: dominant_move=%.1fpts threshold=%.1fpts",
                        dominant_move_pts,
                        countertrend_threshold,
                    )
                else:
                    age_ms = now_ms - self._last_sell_place_ms
                    if _min_order_age > 0 and age_ms < _min_order_age:
                        log.debug("REARM_SKIPPED_MIN_AGE SELL age=%.0fms < %.0fms",
                                  age_ms, _min_order_age)
                    elif not self._in_freeze(self._st.sell_stop_price, si):
                        assert self._st.sell_stop_ticket is not None
                        modified = self._modify_pending_order(
                            ticket=self._st.sell_stop_ticket,
                            price=target_sell,
                            sl=sell_sl,
                            label="SELL",
                            now_ms=now_ms,
                        )
                        if modified:
                            self._st.sell_stop_price = target_sell
                        else:
                            log.info("SELL pending modify failed; falling back to cancel+replace")
                            self._cancel_order(self._st.sell_stop_ticket, "SELL")
                            self._st.sell_stop_ticket = None
                            self._st.sell_stop_price = None
                            has_sell = False
                            self._sell_rearm_pending = True

        # ── Place missing orders ───────────────────────────────────────────────
        if not has_buy and not _buy_blocked:
            if target_buy <= tick_ask:
                log.warning(
                    "BUY STOP skipped: target=%.5f <= ask=%.5f (price passed entry)",
                    target_buy, tick_ask,
                )
                self._buy_rearm_pending = False
            elif buy_impulse_old and not self._buy_rearm_pending:
                log.info(
                    "DENY_OLD_IMPULSE_TICK BUY: cumulative up %.1f > atr*%.2f, "
                    "duration > %.0fms – impulse old",
                    self._ec.impulse_atr_mult * atr_pts, self._ec.impulse_atr_mult,
                    self._ec.impulse_dur_ms,
                )
            elif buy_countertrend_guard:
                log.debug(
                    "COUNTERTREND_GUARD BUY: dominant down move %.1fpts blocks BUY placement",
                    dominant_move_pts,
                )
            elif not buy_burst_ok and not self._buy_rearm_pending:
                burst_min = max(spread_med_pts * self._ec.burst_min_spread_mult,
                                self._ec.burst_min_abs_pts)
                log.debug("DENY_NO_BURST BUY: burst_min=%.1f pts", burst_min)
            elif self._violates_stops(target_buy, tick_ask, si, "BUY_STOP"):
                log.debug("BUY STOP price violates stops_level, skipping")
                self._buy_rearm_pending = False
            else:
                if self._buy_rearm_pending:
                    log.debug("REARM_REPLACE BUY: burst check bypassed")
                self._buy_rearm_pending = False
                self._place_buy_stop(target_buy, buy_sl, si, now_ms)

        if not has_sell and not _sell_blocked:
            if target_sell >= tick_bid:
                log.warning(
                    "SELL STOP skipped: target=%.5f >= bid=%.5f (price passed entry)",
                    target_sell, tick_bid,
                )
                self._sell_rearm_pending = False
            elif sell_impulse_old and not self._sell_rearm_pending:
                log.info(
                    "DENY_OLD_IMPULSE_TICK SELL: cumulative down %.1f > atr*%.2f, "
                    "duration > %.0fms – impulse old",
                    self._ec.impulse_atr_mult * atr_pts, self._ec.impulse_atr_mult,
                    self._ec.impulse_dur_ms,
                )
            elif sell_countertrend_guard:
                log.debug(
                    "COUNTERTREND_GUARD SELL: dominant up move %.1fpts blocks SELL placement",
                    dominant_move_pts,
                )
            elif not sell_burst_ok and not self._sell_rearm_pending:
                burst_min = max(spread_med_pts * self._ec.burst_min_spread_mult,
                                self._ec.burst_min_abs_pts)
                log.debug("DENY_NO_BURST SELL: burst_min=%.1f pts", burst_min)
            elif self._violates_stops(target_sell, tick_bid, si, "SELL_STOP"):
                log.debug("SELL STOP price violates stops_level, skipping")
                self._sell_rearm_pending = False
            else:
                if self._sell_rearm_pending:
                    log.debug("REARM_REPLACE SELL: burst check bypassed")
                self._sell_rearm_pending = False
                self._place_sell_stop(target_sell, sell_sl, si, now_ms)

    def purge_all_orders_urgent(self, reason: str = "urgent_purge") -> None:
        """Hard purge: cancel state-tracked orders AND do a terminal scan.
        Called after position close to kill zombie pending orders."""
        self._buy_rearm_pending  = False
        self._sell_rearm_pending = False
        self._flat_frozen = False
        self._flat_freeze_until_ms = 0.0
        # Cancel state-tracked orders
        if self._st.buy_stop_ticket is not None:
            buy_ticket = self._st.buy_stop_ticket
            self._cancel_order(buy_ticket, f"BUY_{reason}")
            self._st.buy_stop_ticket = None
            self._st.buy_stop_price  = None
        if self._st.sell_stop_ticket is not None:
            sell_ticket = self._st.sell_stop_ticket
            self._cancel_order(sell_ticket, f"SELL_{reason}")
            self._st.sell_stop_ticket = None
            self._st.sell_stop_price  = None
        # Terminal scan: kill any remaining orders from our magic
        self._cancel_all_live_pending(reason)

    def cancel_all(self, si: Optional[SymbolSnapshot] = None) -> None:
        """Cancel both pending orders unconditionally."""
        self._buy_rearm_pending  = False
        self._sell_rearm_pending = False
        self._flat_frozen = False  # clear freeze so orders can be repositioned fresh
        self._flat_freeze_until_ms = 0.0
        if self._st.buy_stop_ticket is not None:
            buy_ticket = self._st.buy_stop_ticket
            self._cancel_order(buy_ticket, "BUY")
            self._st.buy_stop_ticket  = None
            self._st.buy_stop_price   = None
        if self._st.sell_stop_ticket is not None:
            sell_ticket = self._st.sell_stop_ticket
            self._cancel_order(sell_ticket, "SELL")
            self._st.sell_stop_ticket = None
            self._st.sell_stop_price  = None

    def cancel_opposite(self, filled_side: Side) -> None:
        """After a fill, cancel the opposite-side pending. P0-4: logs attempt/result.
        On failure, falls back to scanning all live orders by magic and cancelling any found.
        """
        if filled_side == Side.BUY and self._st.sell_stop_ticket is not None:
            ticket = self._st.sell_stop_ticket
            log.info("OPPOSITE_CANCEL_ATTEMPT side=SELL ticket=%s", ticket)
            ok = self._cancel_order(ticket, "SELL")
            log.info("OPPOSITE_CANCEL_RESULT side=SELL ticket=%s success=%s", ticket, ok)
            self._st.sell_stop_ticket = None
            self._st.sell_stop_price  = None
            if not ok:
                self._cancel_all_live_pending("fallback_cancel_opposite_SELL")
        elif filled_side == Side.SELL and self._st.buy_stop_ticket is not None:
            ticket = self._st.buy_stop_ticket
            log.info("OPPOSITE_CANCEL_ATTEMPT side=BUY ticket=%s", ticket)
            ok = self._cancel_order(ticket, "BUY")
            log.info("OPPOSITE_CANCEL_RESULT side=BUY ticket=%s success=%s", ticket, ok)
            self._st.buy_stop_ticket = None
            self._st.buy_stop_price  = None
            if not ok:
                self._cancel_all_live_pending("fallback_cancel_opposite_BUY")

    def _cancel_all_live_pending(self, reason: str) -> None:
        """Scan terminal for all pending orders from our magic and cancel them."""
        try:
            live_orders = self._a.get_orders(self._pc.symbol)
        except Exception:
            live_orders = []
        for o in live_orders:
            if getattr(o, 'magic', None) == self._pc.magic:
                log.warning("FALLBACK_CANCEL ticket=%s reason=%s", o.ticket, reason)
                self._cancel_order(o.ticket, f"fallback_{o.ticket}")

    def reconcile_with_terminal(self, live_orders: list[OrderSnapshot]) -> None:
        """
        Sync state tickets vs actual terminal orders.
        Called on startup/recovery.
        """
        live_tickets = {o.ticket for o in live_orders}
        if self._st.buy_stop_ticket and self._st.buy_stop_ticket not in live_tickets:
            log.warning("buy_stop_ticket %s not in terminal – clearing", self._st.buy_stop_ticket)
            self._st.buy_stop_ticket = None
            self._st.buy_stop_price  = None
        if self._st.sell_stop_ticket and self._st.sell_stop_ticket not in live_tickets:
            log.warning("sell_stop_ticket %s not in terminal – clearing", self._st.sell_stop_ticket)
            self._st.sell_stop_ticket = None
            self._st.sell_stop_price  = None

    # ── Private ───────────────────────────────────────────────────────────────

    def _detect_flat(self, now_ms: float, point: float) -> tuple[float, bool]:
        """Return (range_pts, is_flat) based on flat_window_ms price history.
        is_flat=True when the price range over the window <= flat_range_pts.
        """
        cutoff = now_ms - self._ec.flat_window_ms
        prices = [p for t, p in self._tick_history if t >= cutoff]
        if len(prices) < 3:
            return 0.0, False
        range_pts = (max(prices) - min(prices)) / point
        return range_pts, range_pts <= self._ec.flat_range_pts

    # ── Private – timed order_send ─────────────────────────────────────────────

    def _order_send_timed(self, req: dict, context: str):
        """P1-2: Wrap order_send with elapsed-time deadline. Calls policy_cb on overrun."""
        t0 = time.monotonic()
        result = self._a.order_send(req)
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > self._pc.op_deadline_ms:
            log.error(
                "OP_DEADLINE_EXCEEDED context=%s elapsed_ms=%.0f deadline_ms=%.0f",
                context, elapsed_ms, self._pc.op_deadline_ms,
            )
            if self._retcode_policy_cb is not None:
                self._retcode_policy_cb("OP_DEADLINE_EXCEEDED", 0)
        return result

    def _place_buy_stop(
        self,
        price: float,
        sl: float,
        si: SymbolSnapshot,
        now_ms: float,
    ) -> None:
        expiration = None
        if self._pc.use_order_time_specified:
            expiration = int((now_ms + self._pc.ttl_sec * 1000) / 1000)

        req = self._a.build_buy_stop_request(
            symbol=self._pc.symbol,
            volume=self._pc.volume,
            price=price,
            sl=sl,
            magic=self._pc.magic,
            comment="scalp_buy_stop",
            expiration=expiration,
        )
        result = self._order_send_timed(req, "place_buy_stop")
        if result and result.retcode in (RC_DONE, RC_PLACED):
            self._st.buy_stop_ticket = result.order
            self._st.buy_stop_price  = price
            self._last_buy_place_ms  = now_ms
            if self._st.pending_placed_at_utc_ms is None:
                self._st.pending_placed_at_utc_ms = now_ms
            log.info("BUY STOP placed: ticket=%s price=%.5f sl=%.5f",
                     result.order, price, sl)
        elif result:
            self._handle_retcode_error(result.retcode, "place_buy_stop", req)

    def _place_sell_stop(
        self,
        price: float,
        sl: float,
        si: SymbolSnapshot,
        now_ms: float,
    ) -> None:
        expiration = None
        if self._pc.use_order_time_specified:
            expiration = int((now_ms + self._pc.ttl_sec * 1000) / 1000)

        req = self._a.build_sell_stop_request(
            symbol=self._pc.symbol,
            volume=self._pc.volume,
            price=price,
            sl=sl,
            magic=self._pc.magic,
            comment="scalp_sell_stop",
            expiration=expiration,
        )
        result = self._order_send_timed(req, "place_sell_stop")
        if result and result.retcode in (RC_DONE, RC_PLACED):
            self._st.sell_stop_ticket = result.order
            self._st.sell_stop_price  = price
            self._last_sell_place_ms  = now_ms
            if self._st.pending_placed_at_utc_ms is None:
                self._st.pending_placed_at_utc_ms = now_ms
            log.info("SELL STOP placed: ticket=%s price=%.5f sl=%.5f",
                     result.order, price, sl)
        elif result:
            self._handle_retcode_error(result.retcode, "place_sell_stop", req)

    def _cancel_order(self, ticket: int, label: str) -> bool:
        req = self._a.build_cancel_request(ticket)
        result = self._order_send_timed(req, f"cancel_{label}")
        if result and result.retcode in (RC_DONE, RC_PLACED, 10007):
            log.info("Pending %s ticket=%s cancelled", label, ticket)
            return True
        if result:
            log.warning("Cancel %s ticket=%s retcode=%s comment=%s",
                        label, ticket, result.retcode, result.comment)
        return False

    def _modify_pending_order(
        self,
        ticket: int,
        price: float,
        sl: float,
        label: str,
        now_ms: float,
    ) -> bool:
        expiration = None
        if self._pc.use_order_time_specified:
            expiration = int((now_ms + self._pc.ttl_sec * 1000) / 1000)

        req = self._a.build_modify_pending_request(
            ticket=ticket,
            price=price,
            sl=sl,
            expiration=expiration,
        )
        result = self._order_send_timed(req, f"modify_{label}")
        if result and result.retcode in (RC_DONE, RC_PLACED, RC_NO_CHANGES):
            self._st.pending_placed_at_utc_ms = now_ms
            if label == "BUY":
                self._last_buy_place_ms = now_ms
            else:
                self._last_sell_place_ms = now_ms
            log.info("Pending %s modified in place: ticket=%s price=%.5f sl=%.5f",
                     label, ticket, price, sl)
            return True
        if result:
            log.warning("Modify %s ticket=%s retcode=%s comment=%s",
                        label, ticket, result.retcode, result.comment)
        return False

    def _is_ttl_expired(self, now_ms: float) -> bool:
        if self._st.pending_placed_at_utc_ms is None:
            return False
        return now_ms > self._st.pending_placed_at_utc_ms + self._pc.ttl_sec * 1000

    def _in_freeze(self, order_price: float, si: SymbolSnapshot) -> bool:
        """Returns True if order_price is within trade_freeze_level of current price."""
        return False  # Richer check done at caller level

    def _calc_noise_ratio(self, now_ms: float, point: float) -> float:
        """Tick-based Noise Ratio over noise_window_ms.
        path_move / max(net_move, 1pt)
        ~1.0 = clean impulse, 2.5+ = choppy/saw market.
        """
        cutoff = now_ms - self._ec.noise_window_ms
        window = [(t, p) for t, p in self._tick_history if t >= cutoff]
        if len(window) < 3:
            return 1.0  # not enough data – assume clean
        prices = [p for _, p in window]
        net_move = abs(prices[-1] - prices[0]) / point
        path_move = sum(abs(prices[i + 1] - prices[i])
                        for i in range(len(prices) - 1)) / point
        return path_move / max(net_move, 1.0)

    def _check_burst(
        self,
        direction: int,
        now_ms: float,
        spread_med_pts: float,
        point: float,
        last_place_ms: float = 0.0,
    ) -> bool:
        """Burst filter: True if signed tick-delta in last 300ms supports the side.
        Also returns True (bypass) if no order has been placed for burst_max_wait_ms,
        so the bot is never starved of entries during calm markets.
        """
        # Bypass if this side has been unplaced for too long
        if self._ec.burst_max_wait_ms > 0:
            since = now_ms - last_place_ms  # large if never placed (last_place_ms==0)
            if since >= self._ec.burst_max_wait_ms:
                return True
        cutoff = now_ms - 300.0
        recent = [p for t, p in self._tick_history if t >= cutoff]
        if len(recent) < 2:
            return True  # not enough data – allow
        signed_delta_pts = (recent[-1] - recent[0]) / point
        burst_min = max(spread_med_pts * self._ec.burst_min_spread_mult,
                        self._ec.burst_min_abs_pts)
        if direction > 0:
            return signed_delta_pts >= burst_min
        return signed_delta_pts <= -burst_min

    def _recent_net_move(self, now_ms: float, window_ms: float, point: float) -> float:
        """Signed net move over a recent window in points."""
        if window_ms <= 0:
            return 0.0
        cutoff = now_ms - window_ms
        window = [p for t, p in self._tick_history if t >= cutoff]
        if len(window) < 2:
            return 0.0
        return (window[-1] - window[0]) / point

    def _check_impulse_old(
        self,
        direction: int,   # +1 = check upward impulse (block BUY), -1 = downward (block SELL)
        atr_pts: float,
        now_ms: float,
        point: float,
    ) -> bool:
        """Impulse-age guard (tick-based, no copy_rates).
        Returns True if a significant impulse in `direction` already ran 3000ms ago
        → caller should block placement in that direction.
        """
        if atr_pts <= 0 or len(self._tick_history) < 2:
            return False
        cutoff = now_ms - 3000.0
        window = [(t, p) for t, p in self._tick_history if t >= cutoff]
        if len(window) < 2:
            return False
        delta_pts = (window[-1][1] - window[0][1]) / point
        duration_ms = window[-1][0] - window[0][0]
        threshold = atr_pts * self._ec.impulse_atr_mult
        if direction > 0:   # upward impulse → block BUY
            old = delta_pts > threshold and duration_ms > self._ec.impulse_dur_ms
        else:               # downward impulse → block SELL
            old = delta_pts < -threshold and duration_ms > self._ec.impulse_dur_ms
        return old

    def _violates_stops(
        self,
        price: float,
        ref_price: float,
        si: SymbolSnapshot,
        order_type: str,
    ) -> bool:
        """Check trade_stops_level constraint: |price - ref| < stops_level*point"""
        min_dist = si.trade_stops_level * si.point
        dist = abs(price - ref_price)
        if dist < min_dist:
            log.debug(
                "%s: price=%.5f ref=%.5f dist=%.5f < min_dist=%.5f (stops_level=%d)",
                order_type, price, ref_price, dist, min_dist, si.trade_stops_level,
            )
            return True
        return False

    def _handle_retcode_error(self, retcode: int, context: str, req: dict) -> None:
        policy = get_retcode_policy(retcode)
        log.warning(
            "OrderManager %s retcode=%s name=%s action=%s",
            context, retcode, policy.name, policy.action.value,
        )
        if retcode == RC_INVALID_STOPS:
            self._last_invalid_stops_ts = time.monotonic()
        # P0-2: escalate HARD_BLOCK / DENY_WAIT to engine via callback
        if policy.action in (RetcodeAction.HARD_BLOCK, RetcodeAction.DENY_WAIT):
            if self._retcode_policy_cb is not None:
                self._retcode_policy_cb(policy.action.value, retcode)
            else:
                log.error(
                    "HARD_BLOCK/DENY_WAIT retcode=%s but no retcode_policy_cb set – "
                    "escalate manually!", retcode
                )
