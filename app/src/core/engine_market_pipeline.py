"""
engine_market_pipeline.py

TradingCore mixin for pre-state-machine market loading and analysis stages.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.src.adapters.mt5_adapter import OrderSnapshot, PositionSnapshot, SymbolSnapshot, TerminalSnapshot, TickSnapshot
from app.src.core.models_atr import ATRResult
from app.src.core.models_spread import SpreadResult
from app.src.core.state import SystemMode


@dataclass
class MarketContext:
    ti: TerminalSnapshot
    latency_ms: float
    si: SymbolSnapshot
    tick: TickSnapshot | None
    is_new_tick: bool
    bid: float
    ask: float


@dataclass
class MarketAnalysis:
    spread_points: float
    spread_res: SpreadResult
    atr_res: ATRResult
    live_positions: list[PositionSnapshot]
    live_orders: list[OrderSnapshot]
    micro_guard_blocked: bool = False


class _MarketPipelineHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: Any
    _state: Any
    _micro_guard: Any
    _spread_model: Any
    _atr_model: Any
    _order_mgr: Any
    _si: SymbolSnapshot | None
    _si_refresh_ts: float
    _SI_REFRESH_SEC: float
    _last_atr_pts: float
    _last_spread_med_pts: float
    _last_candle_hi: float
    _last_candle_lo: float
    _last_is_flat: bool
    _micro_guard_pause_until_mono: float
    _micro_guard_stable_since_mono: float
    _micro_guard_pause_on_trigger_ms: float
    _micro_guard_recovery_stability_ms: float

    def _handle_disconnect(self, ti: TerminalSnapshot | None) -> None: ...
    def _enter_safe_mode(self, reason: str) -> None: ...
    def _log_event(self, event: str, data: dict[str, Any]) -> None: ...
    def _clock_event(self, mono_ms: float, bid: float, ask: float, si: SymbolSnapshot) -> None: ...


class MarketPipelineMixin:
    def _load_market_context(
        self: _MarketPipelineHost,
        sym: str,
        mono_ms: float,
    ) -> MarketContext | None:
        t0 = time.monotonic()
        ti = self._adapter.get_terminal_info()
        latency_ms = (time.monotonic() - t0) * 1000

        if ti is None or not ti.connected:
            self._handle_disconnect(ti)
            return None
        if not ti.trade_allowed:
            self._enter_safe_mode("terminal_trade_not_allowed")
            return None
        if ti.tradeapi_disabled:
            self._enter_safe_mode("tradeapi_disabled")
            return None

        if self._si is None or (time.monotonic() - self._si_refresh_ts) > self._SI_REFRESH_SEC:
            self._si = self._adapter.get_symbol_info(sym)
            self._si_refresh_ts = time.monotonic()
            if self._si is None:
                return None

        tick = self._adapter.get_tick(sym)
        is_new_tick = False
        if tick is not None:
            is_new_tick = tick.time_msc != self._state.last_tick_time_msc
            if is_new_tick:
                self._state.last_tick_time_msc = tick.time_msc
                self._micro_guard.on_new_tick(mono_ms)
                self._last_known_bid = tick.bid
                self._last_known_ask = tick.ask
        else:
            self._log_event("TICK_NONE", {"latency_ms": latency_ms})

        bid = getattr(self, "_last_known_bid", 0.0)
        ask = getattr(self, "_last_known_ask", 0.0)
        self._clock_event(mono_ms, bid, ask, self._si)

        return MarketContext(
            ti=ti,
            latency_ms=latency_ms,
            si=self._si,
            tick=tick,
            is_new_tick=is_new_tick,
            bid=bid,
            ask=ask,
        )

    def _analyze_market(
        self: _MarketPipelineHost,
        sym: str,
        mono_ms: float,
        market: MarketContext,
    ) -> MarketAnalysis | None:
        if not market.is_new_tick:
            return None

        spread_points = (market.ask - market.bid) / market.si.point if market.si.point else 0.0
        spread_res = self._spread_model.update(
            spread_points,
            float(self._state.last_tick_time_msc),
        )

        rates = self._adapter.copy_rates_from_pos(sym, 1, 0, self._cfg["atr"]["bars_fetch"])
        atr_res = self._atr_model.compute_from_bars(
            rates,
            market.si.point,
            spread_res.spread_med_points,
        )

        self._last_atr_pts = atr_res.atr_points
        self._last_spread_med_pts = spread_res.spread_med_points
        if rates is not None and len(rates) > 0:
            self._last_candle_hi = float(rates[-1]["high"])
            self._last_candle_lo = float(rates[-1]["low"])
        self._last_is_flat = getattr(self._order_mgr, "_flat_frozen", False)

        mg = self._micro_guard.check(
            market.bid,
            market.ask,
            market.latency_ms,
            market.ti.ping_last,
            mono_ms=mono_ms,
            is_new_tick=market.is_new_tick,
        )
        micro_guard_blocked = False
        if mg.safe_trigger and self._state.mode == SystemMode.NORMAL:
            self._micro_guard_pause_until_mono = max(
                self._micro_guard_pause_until_mono,
                mono_ms + self._micro_guard_pause_on_trigger_ms,
            )
            self._micro_guard_stable_since_mono = 0.0
            micro_guard_blocked = True
            self._log_event("MICRO_GUARD_TRIGGER", {
                "reasons": mg.reasons,
                "tick_stale_ms": mg.tick_stale_ms,
                "ipc_duration_ms": mg.ipc_duration_ms,
                "ping_last_ms": mg.ping_last_ms,
                "pause_until_mono_ms": self._micro_guard_pause_until_mono,
                "pause_ms": self._micro_guard_pause_on_trigger_ms,
            })
        elif mono_ms < self._micro_guard_pause_until_mono:
            self._micro_guard_stable_since_mono = 0.0
            micro_guard_blocked = True
        elif self._micro_guard_pause_until_mono > 0.0:
            if self._micro_guard_recovery_stability_ms <= 0.0:
                self._log_event("MICRO_GUARD_RECOVERED", {})
                self._micro_guard_pause_until_mono = 0.0
                self._micro_guard_stable_since_mono = 0.0
            else:
                if self._micro_guard_stable_since_mono <= 0.0:
                    self._micro_guard_stable_since_mono = mono_ms
                    self._log_event("MICRO_GUARD_STABILITY_WAIT", {
                        "stable_required_ms": self._micro_guard_recovery_stability_ms,
                    })
                stable_elapsed_ms = mono_ms - self._micro_guard_stable_since_mono
                if stable_elapsed_ms < self._micro_guard_recovery_stability_ms:
                    micro_guard_blocked = True
                else:
                    self._log_event("MICRO_GUARD_RECOVERED", {
                        "stable_elapsed_ms": stable_elapsed_ms,
                    })
                    self._micro_guard_pause_until_mono = 0.0
                    self._micro_guard_stable_since_mono = 0.0

        return MarketAnalysis(
            spread_points=spread_points,
            spread_res=spread_res,
            atr_res=atr_res,
            live_positions=self._adapter.get_positions(sym),
            live_orders=self._adapter.get_orders(sym),
            micro_guard_blocked=micro_guard_blocked,
        )