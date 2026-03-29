"""
MT5Adapter – single port for all MetaTrader5 Python API calls.

All MT5 calls are intended to be made from a single thread (TradingCore).
This module never calls mt5 directly from the constructor; call initialize()
first and handle errors via last_error().
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, NamedTuple, Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Lazy import of MetaTrader5 so unit tests can run without it ───────────────
from typing import Any as _Any  # noqa: E402
_mt5: _Any
try:
    import MetaTrader5 as _mt5  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _mt5 = None

# ── Retcode constants (from ENUM_TRADE_RETCODE) ──────────────────────────────
RC_DONE          = 10009
RC_PLACED        = 10008
RC_DONE_PARTIAL  = 10010
RC_REQUOTE       = 10004
RC_PRICE_CHANGED = 10006
RC_INVALID_STOPS = 10016
RC_TRADE_DISABLED= 10017
RC_MARKET_CLOSED = 10018
RC_TIMEOUT       = 10007
RC_ERROR         = 10011
RC_REJECT        = 10006
RC_NO_CHANGES    = 10025

RC_CANCEL        = 10007   # same as TIMEOUT numerically in ENUM; also used as order cancel confirmation
RC_INVALID       = 10013
RC_INVALID_VOLUME= 10014
RC_INVALID_PRICE = 10015

SUCCESS_RETCODES = {RC_DONE, RC_PLACED, RC_DONE_PARTIAL}


# ── Retcode Policy (P0-004) ───────────────────────────────────────────────────

class RetcodeAction(str, Enum):
    SUCCESS          = "SUCCESS"
    SUCCESS_PARTIAL  = "SUCCESS_PARTIAL"   # DONE_PARTIAL – reconcile volumes
    RETRY_BACKOFF    = "RETRY_BACKOFF"     # transient – retry with backoff
    REBUILD_REQUEST  = "REBUILD_REQUEST"   # recalc stops/price/volume and retry
    STOP_TRADING     = "STOP_TRADING"      # fatal – enter SAFE MODE
    HARD_BLOCK       = "HARD_BLOCK"        # P0-2: permanent block – SAFE MODE, no retry
    DENY_WAIT        = "DENY_WAIT"         # P0-2: temporary block – DENY entry, cancel pending, no retry
    LOG_ONLY         = "LOG_ONLY"          # unexpected; log and treat as error


@dataclass
class RetcodePolicyEntry:
    action: RetcodeAction
    name: str
    backoff_ms: int = 0
    retry_limit: int = 0
    terminal_reason: str = ""   # reason string for SAFE MODE entry


# Authoritative retcode policy table (MT5 ENUM_TRADE_RETCODE 10004..10018)
RETCODE_POLICY: dict[int, RetcodePolicyEntry] = {
    10004: RetcodePolicyEntry(RetcodeAction.RETRY_BACKOFF,   "REQUOTE",          backoff_ms=500,  retry_limit=3),
    10005: RetcodePolicyEntry(RetcodeAction.LOG_ONLY,        "RESERVED_10005"),
    10006: RetcodePolicyEntry(RetcodeAction.STOP_TRADING,    "REJECT",           terminal_reason="RETCODE_REJECT"),
    10007: RetcodePolicyEntry(RetcodeAction.RETRY_BACKOFF,   "CANCEL",           backoff_ms=500,  retry_limit=3),
    10008: RetcodePolicyEntry(RetcodeAction.SUCCESS,         "PLACED"),
    10009: RetcodePolicyEntry(RetcodeAction.SUCCESS,         "DONE"),
    10010: RetcodePolicyEntry(RetcodeAction.SUCCESS_PARTIAL, "DONE_PARTIAL"),
    10011: RetcodePolicyEntry(RetcodeAction.RETRY_BACKOFF,   "ERROR",            backoff_ms=1000, retry_limit=2,
                              terminal_reason="RETCODE_ERROR_EXHAUSTED"),
    10012: RetcodePolicyEntry(RetcodeAction.RETRY_BACKOFF,   "TIMEOUT",          backoff_ms=1000, retry_limit=3,
                              terminal_reason="RETCODE_TIMEOUT_EXHAUSTED"),
    10013: RetcodePolicyEntry(RetcodeAction.REBUILD_REQUEST, "INVALID",          backoff_ms=500,  retry_limit=2),
    10014: RetcodePolicyEntry(RetcodeAction.REBUILD_REQUEST, "INVALID_VOLUME",   backoff_ms=500,  retry_limit=2),
    10015: RetcodePolicyEntry(RetcodeAction.REBUILD_REQUEST, "INVALID_PRICE",    backoff_ms=500,  retry_limit=2),
    10016: RetcodePolicyEntry(RetcodeAction.REBUILD_REQUEST, "INVALID_STOPS",    backoff_ms=2000, retry_limit=3),
    10017: RetcodePolicyEntry(RetcodeAction.HARD_BLOCK,     "TRADE_DISABLED",   terminal_reason="TRADE_DISABLED"),
    10018: RetcodePolicyEntry(RetcodeAction.DENY_WAIT,       "MARKET_CLOSED"),   # P0-2: temporary – deny entry, cancel pending
}

_UNKNOWN_POLICY = RetcodePolicyEntry(RetcodeAction.LOG_ONLY, "UNKNOWN")


def get_retcode_policy(retcode: int) -> RetcodePolicyEntry:
    """Return policy entry for retcode, falling back to LOG_ONLY."""
    return RETCODE_POLICY.get(retcode, _UNKNOWN_POLICY)


def get_retcode_name(retcode: int) -> str:
    return RETCODE_POLICY.get(retcode, _UNKNOWN_POLICY).name

# ── MT5 Order type constants (mirrors MQL5 ENUM_ORDER_TYPE) ──────────────────
ORDER_TYPE_BUY        = 0
ORDER_TYPE_SELL       = 1
ORDER_TYPE_BUY_LIMIT  = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP   = 4
ORDER_TYPE_SELL_STOP  = 5

# ── MT5 Trade action constants ────────────────────────────────────────────────
TRADE_ACTION_DEAL    = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_REMOVE  = 8
TRADE_ACTION_MODIFY  = 6
TRADE_ACTION_SLTP    = 6   # same value as MODIFY in practice

# ── ORDER_TIME type ───────────────────────────────────────────────────────────
ORDER_TIME_GTC       = 0
ORDER_TIME_DAY       = 1
ORDER_TIME_SPECIFIED = 2


@dataclass
class SymbolSnapshot:
    """Immutable snapshot of symbol_info relevant fields."""
    name: str
    point: float
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_stops_level: int
    trade_freeze_level: int
    digits: int
    spread: int
    trade_mode: int

    @property
    def value_per_point(self) -> float:
        """INV-FORMULA: value_per_point = tick_value * point / tick_size"""
        if self.tick_size == 0:
            return 0.0
        return self.tick_value * self.point / self.tick_size


@dataclass
class TickSnapshot:
    bid: float
    ask: float
    last: float
    time: int        # unix seconds
    time_msc: int    # unix milliseconds

    @property
    def spread_raw(self) -> float:
        return self.ask - self.bid


@dataclass
class TerminalSnapshot:
    connected: bool
    trade_allowed: bool
    tradeapi_disabled: bool
    ping_last: int   # ms

    def is_ready(self) -> bool:
        return self.connected and self.trade_allowed and not self.tradeapi_disabled


@dataclass
class AccountSnapshot:
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str
    leverage: int


@dataclass
class OrderSnapshot:
    ticket: int
    type: int
    symbol: str
    volume_current: float
    price_open: float
    sl: float
    tp: float
    time_setup: int    # unix seconds
    time_expiration: int
    magic: int
    comment: str


@dataclass
class PositionSnapshot:
    ticket: int
    type: int        # 0=BUY, 1=SELL
    symbol: str
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int
    comment: str
    time: int        # unix seconds


@dataclass
class TradeResult:
    retcode: int
    deal: int
    order: int
    volume: float
    price: float
    bid: float
    ask: float
    comment: str
    request_id: int
    retcode_external: int

    @property
    def success(self) -> bool:
        return self.retcode in SUCCESS_RETCODES


@dataclass
class PreflightResult:
    """Structured preflight result (P0-1).

    ok                  – True iff START may proceed.
    blocking_reasons    – reasons that MUST block START (trade_allowed, tradeapi_disabled, etc.)
    warnings            – soft issues (volume limits, etc.) that do NOT block START.
    terminal_info       – snapshot from terminal_info() at time of preflight.
    symbol_info         – snapshot from symbol_info() at time of preflight.
    """
    ok: bool
    blocking_reasons: list[str]
    warnings: list[str]
    terminal_info: Optional[TerminalSnapshot]
    symbol_info: Optional[SymbolSnapshot]

    @property
    def connected(self) -> bool:
        return self.terminal_info is not None and self.terminal_info.connected

    @property
    def trade_allowed(self) -> bool:
        return self.terminal_info is not None and self.terminal_info.trade_allowed

    @property
    def tradeapi_disabled(self) -> bool:
        return self.terminal_info is not None and self.terminal_info.tradeapi_disabled

    @property
    def ping_last(self) -> int:
        if self.terminal_info is None:
            return -1
        return self.terminal_info.ping_last


class MT5Adapter:
    """
    Thin, testable wrapper over the MetaTrader5 package.
    All methods return Python dataclasses; never expose raw MT5 objects.

    P0-003: thread ownership is enforced on all trading operations.
    Call set_core_thread() from TradingCore after initialization.
    """

    def __init__(self, mt5_module: Any = None) -> None:
        self._mt5 = mt5_module if mt5_module is not None else _mt5
        if self._mt5 is None:
            raise ImportError(
                "MetaTrader5 package not found. Install it: pip install MetaTrader5"
            )
        # P0-003: track which thread is allowed to make MT5 calls.
        # None = not yet registered (allows tests and initialization from any thread).
        self._core_thread_id: Optional[int] = None

    def set_core_thread(self) -> None:
        """Register current thread as the sole MT5-owning thread (P0-003).
        Call once from TradingCore at the start of _run_loop().
        """
        self._core_thread_id = threading.get_ident()
        log.info("MT5Adapter: core thread registered id=%d", self._core_thread_id)

    def _assert_core_thread(self, method: str = "") -> None:
        """Raise RuntimeError if called from wrong thread (P0-003)."""
        if self._core_thread_id is None:
            return  # not yet registered – pre-start context (preflight/tests)
        tid = threading.get_ident()
        if tid != self._core_thread_id:
            log.critical(
                "CRITICAL_MT5_CALL_FROM_WRONG_THREAD method=%s thread_id=%d core_thread_id=%d",
                method, tid, self._core_thread_id,
            )
            raise RuntimeError(
                f"MT5 call '{method}' from wrong thread {tid} "
                f"(core={self._core_thread_id})"
            )

    # ── Connection ────────────────────────────────────────────────────────────

    def initialize(
        self,
        path: str = "",
        login: int = 0,
        password: str = "",
        server: str = "",
        timeout_ms: int = 10000,
    ) -> bool:
        kwargs: dict[str, Any] = {"timeout": timeout_ms}
        if path:
            kwargs["path"] = path
        if login:
            kwargs["login"] = login
        if password:
            kwargs["password"] = password
        if server:
            kwargs["server"] = server
        result = self._mt5.initialize(**kwargs)
        if not result:
            err = self._mt5.last_error()
            log.error("MT5 initialize() failed: code=%s msg=%s", err[0], err[1])
        return bool(result)

    def shutdown(self) -> None:
        try:
            self._mt5.shutdown()
        except Exception as exc:
            log.warning("MT5 shutdown error: %s", exc)

    def last_error(self) -> tuple[int, str]:
        """Returns (error_code, error_description)."""
        err = self._mt5.last_error()
        if err is None:
            return (0, "")
        return (int(err[0]), str(err[1]))

    # ── Market data ───────────────────────────────────────────────────────────

    def get_tick(self, symbol: str = "XAUUSD") -> Optional[TickSnapshot]:
        t = self._mt5.symbol_info_tick(symbol)
        if t is None:
            code, msg = self.last_error()
            log.warning("symbol_info_tick(%s) returned None: %s %s", symbol, code, msg)
            return None
        return TickSnapshot(
            bid=float(t.bid),
            ask=float(t.ask),
            last=float(t.last),
            time=int(t.time),
            time_msc=int(t.time_msc),
        )

    def get_symbol_info(self, symbol: str = "XAUUSD") -> Optional[SymbolSnapshot]:
        si = self._mt5.symbol_info(symbol)
        if si is None:
            code, msg = self.last_error()
            log.warning("symbol_info(%s) returned None: %s %s", symbol, code, msg)
            return None
        return SymbolSnapshot(
            name=si.name,
            point=float(si.point),
            tick_size=float(si.trade_tick_size),
            tick_value=float(si.trade_tick_value),
            volume_min=float(si.volume_min),
            volume_max=float(si.volume_max),
            volume_step=float(si.volume_step),
            trade_stops_level=int(si.trade_stops_level),
            trade_freeze_level=int(si.trade_freeze_level),
            digits=int(si.digits),
            spread=int(si.spread),
            trade_mode=int(si.trade_mode),
        )

    def get_terminal_info(self) -> Optional[TerminalSnapshot]:
        ti = self._mt5.terminal_info()
        if ti is None:
            code, msg = self.last_error()
            log.warning("terminal_info() returned None: %s %s", code, msg)
            return None
        return TerminalSnapshot(
            connected=bool(ti.connected),
            trade_allowed=bool(ti.trade_allowed),
            tradeapi_disabled=bool(getattr(ti, "tradeapi_disabled", False)),
            ping_last=int(getattr(ti, "ping_last", -1)),
        )

    def get_account_info(self) -> Optional[AccountSnapshot]:
        ai = self._mt5.account_info()
        if ai is None:
            return None
        return AccountSnapshot(
            balance=float(ai.balance),
            equity=float(ai.equity),
            margin=float(ai.margin),
            margin_free=float(ai.margin_free),
            currency=str(ai.currency),
            leverage=int(ai.leverage),
        )

    def symbol_select(self, symbol: str = "XAUUSD", enable: bool = True) -> bool:
        return bool(self._mt5.symbol_select(symbol, enable))

    # ── Historical bars ───────────────────────────────────────────────────────

    # ── Timeframe helpers ─────────────────────────────────────────────────────
    _TF_MAP: dict[int, int] = {}  # populated lazily per mt5 module

    def _resolve_timeframe(self, timeframe: int) -> int:
        """Map minute-count shorthand (1, 5, 15, 30) to mt5 TIMEFRAME constant.
        For M1-M30 the values are identical, so this is mostly a safety net.
        """
        _attrs = {
            1: "TIMEFRAME_M1", 5: "TIMEFRAME_M5", 15: "TIMEFRAME_M15",
            30: "TIMEFRAME_M30", 60: "TIMEFRAME_H1",
        }
        name = _attrs.get(timeframe)
        if name:
            return int(getattr(self._mt5, name, timeframe))
        return timeframe

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> Optional[np.ndarray]:
        """Returns numpy structured array with OHLC data (UTC timestamps).
        timeframe: minute count (1=M1, 5=M5, 15=M15, etc.)
        """
        tf = self._resolve_timeframe(timeframe)
        rates = self._mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
        if rates is None or len(rates) == 0:
            code, msg = self.last_error()
            log.warning(
                "copy_rates_from_pos(%s, TF=%s, %s, %s) returned None/empty: %s %s",
                symbol, timeframe, start_pos, count, code, msg,
            )
            return None
        return rates

    # ── Orders & positions ────────────────────────────────────────────────────

    def get_orders(self, symbol: str = "XAUUSD") -> list[OrderSnapshot]:
        orders = self._mt5.orders_get(symbol=symbol)
        if orders is None:
            return []
        result = []
        for o in orders:
            result.append(OrderSnapshot(
                ticket=int(o.ticket),
                type=int(o.type),
                symbol=str(o.symbol),
                volume_current=float(o.volume_current),
                price_open=float(o.price_open),
                sl=float(o.sl),
                tp=float(o.tp),
                time_setup=int(o.time_setup),
                time_expiration=int(o.time_expiration),
                magic=int(o.magic),
                comment=str(o.comment),
            ))
        return result

    def get_positions(self, symbol: str = "XAUUSD") -> list[PositionSnapshot]:
        positions = self._mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        result = []
        for p in positions:
            result.append(PositionSnapshot(
                ticket=int(p.ticket),
                type=int(p.type),
                symbol=str(p.symbol),
                volume=float(p.volume),
                price_open=float(p.price_open),
                sl=float(p.sl),
                tp=float(p.tp),
                profit=float(p.profit),
                magic=int(p.magic),
                comment=str(p.comment),
                time=int(p.time),
            ))
        return result

    def get_closing_deal_price(self, position_ticket: int) -> Optional[float]:
        """Return the actual fill price of the closing deal for this position.

        Queries MT5 deal history filtered by position ticket and returns the
        price of the last OUT deal (entry==1). Falls back to None if history
        is not yet available (caller should use current bid/ask instead).
        """
        try:
            deals = self._mt5.history_deals_get(position=position_ticket)
        except Exception as exc:
            log.warning("history_deals_get failed for ticket=%s: %s", position_ticket, exc)
            return None
        if not deals:
            return None
        DEAL_ENTRY_OUT = 1
        closing = [d for d in deals if int(d.entry) == DEAL_ENTRY_OUT]
        if not closing:
            return None
        return float(closing[-1].price)

    # ── Trade operations ─────────────────────────────────────────────────────

    def order_check(self, request: dict) -> Optional[Any]:
        """Pre-validation of trade request. Returns raw check result."""
        self._assert_core_thread("order_check")
        return self._mt5.order_check(request)

    def order_send(self, request: dict) -> Optional["TradeResult"]:
        """Send trade request. Returns TradeResult or None on API error."""
        self._assert_core_thread("order_send")
        raw = self._mt5.order_send(request)
        if raw is None:
            code, msg = self.last_error()
            log.error("order_send() returned None: %s %s | request=%s", code, msg, request)
            return None
        result = TradeResult(
            retcode=int(raw.retcode),
            deal=int(raw.deal),
            order=int(raw.order),
            volume=float(raw.volume),
            price=float(raw.price),
            bid=float(raw.bid),
            ask=float(raw.ask),
            comment=str(raw.comment),
            request_id=int(raw.request_id),
            retcode_external=int(raw.retcode_external),
        )
        if not result.success:
            log.warning(
                "order_send failed: retcode=%s (%s) | request=%s",
                result.retcode, result.comment, request,
            )
        return result

    def build_buy_stop_request(
        self,
        symbol: str,
        volume: float,
        price: float,
        sl: float,
        magic: int,
        comment: str = "",
        expiration: Optional[int] = None,
    ) -> dict:
        req: dict = {
            "action": TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": ORDER_TYPE_BUY_STOP,
            "price": price,
            "sl": sl,
            "tp": 0.0,
            "magic": magic,
            "comment": comment,
            "type_filling": self._get_filling_mode(symbol),
        }
        if expiration is not None:
            req["type_time"] = ORDER_TIME_SPECIFIED
            req["expiration"] = expiration
        else:
            req["type_time"] = ORDER_TIME_GTC
        return req

    def build_sell_stop_request(
        self,
        symbol: str,
        volume: float,
        price: float,
        sl: float,
        magic: int,
        comment: str = "",
        expiration: Optional[int] = None,
    ) -> dict:
        req: dict = {
            "action": TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": ORDER_TYPE_SELL_STOP,
            "price": price,
            "sl": sl,
            "tp": 0.0,
            "magic": magic,
            "comment": comment,
            "type_filling": self._get_filling_mode(symbol),
        }
        if expiration is not None:
            req["type_time"] = ORDER_TIME_SPECIFIED
            req["expiration"] = expiration
        else:
            req["type_time"] = ORDER_TIME_GTC
        return req

    def build_cancel_request(self, ticket: int) -> dict:
        return {
            "action": TRADE_ACTION_REMOVE,
            "order": ticket,
        }

    def build_modify_pending_request(
        self,
        ticket: int,
        price: float,
        sl: float,
        expiration: Optional[int] = None,
    ) -> dict:
        req: dict = {
            "action": TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": price,
            "sl": sl,
            "tp": 0.0,
        }
        if expiration is not None:
            req["type_time"] = ORDER_TIME_SPECIFIED
            req["expiration"] = expiration
        else:
            req["type_time"] = ORDER_TIME_GTC
        return req

    def build_modify_sl_request(
        self,
        symbol: str,
        ticket: int,
        sl: float,
        is_position: bool = True,
    ) -> dict:
        if is_position:
            return {
                "action": TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": ticket,
                "sl": sl,
                "tp": 0.0,
            }
        return {
            "action": TRADE_ACTION_MODIFY,
            "order": ticket,
            "sl": sl,
            "tp": 0.0,
        }

    def build_market_close_request(
        self,
        symbol: str,
        ticket: int,
        volume: float,
        pos_type: int,  # 0=BUY → close with SELL, 1=SELL → close with BUY
        price: float,
        magic: int,
        comment: str = "",
    ) -> dict:
        close_type = ORDER_TYPE_SELL if pos_type == 0 else ORDER_TYPE_BUY
        return {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_filling": self._get_filling_mode(symbol),
            "type_time": ORDER_TIME_GTC,
        }

    def build_partial_close_request(
        self,
        symbol: str,
        ticket: int,
        close_volume: float,   # volume to close (must be <= position volume)
        pos_type: int,         # 0=BUY position → close with SELL, 1=SELL → close with BUY
        price: float,
        magic: int,
        comment: str = "apt_partial",
    ) -> dict:
        """APT v2: partial market close – closes close_volume lots of an open position.
        MT5 requires TRADE_ACTION_DEAL with the position ticket to partially close.
        """
        close_type = ORDER_TYPE_SELL if pos_type == 0 else ORDER_TYPE_BUY
        return {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": close_volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 30,
            "magic": magic,
            "comment": comment,
            "type_filling": self._get_filling_mode(symbol),
            "type_time": ORDER_TIME_GTC,
        }

    # ── Preflight ─────────────────────────────────────────────────────────────

    def run_preflight(
        self,
        symbol: str = "XAUUSD",
        volume: float = 0.01,
        path: str = "",
        login: int = 0,
        password: str = "",
        server: str = "",
        timeout_ms: int = 10000,
    ) -> "PreflightResult":
        """Structured preflight: returns PreflightResult with ok/blocking_reasons/warnings.

        P0-1: blocks START when connected==False, trade_allowed==False or
        tradeapi_disabled==True. Returns terminal_info and symbol_info snapshots.
        """
        blocking: list[str] = []
        warnings: list[str] = []
        terminal_info: Optional[TerminalSnapshot] = None
        symbol_info: Optional[SymbolSnapshot]   = None

        # 1. Initialize connection
        if not self.initialize(path=path, login=login, password=password,
                               server=server, timeout_ms=timeout_ms):
            code, msg = self.last_error()
            blocking.append(f"MT5 initialize() failed: [{code}] {msg}")
            return PreflightResult(
                ok=False,
                blocking_reasons=blocking,
                warnings=warnings,
                terminal_info=None,
                symbol_info=None,
            )

        # 2. terminal_info – hard-block on connectivity or API restriction issues
        ti = self.get_terminal_info()
        terminal_info = ti
        if ti is None:
            code, msg = self.last_error()
            blocking.append(f"terminal_info() returned None: [{code}] {msg}")
        else:
            if not ti.connected:
                blocking.append(
                    "terminal_info.connected == False — no active broker connection"
                )
            if not ti.trade_allowed:
                blocking.append(
                    "terminal_info.trade_allowed == False — trading disabled in terminal settings"
                )
            if ti.tradeapi_disabled:
                blocking.append(
                    "terminal_info.tradeapi_disabled == True — "
                    "(Options → Expert Advisors → 'Disable automated trading via Python API' is ticked)"
                )

        # 3. symbol_info
        if not self.symbol_select(symbol, True):
            warnings.append(f"symbol_select({symbol}) failed")
        si = self.get_symbol_info(symbol)
        symbol_info = si
        if si is None:
            blocking.append(
                f"symbol_info({symbol}) returned None — symbol not visible in Market Watch"
            )
        else:
            if volume < si.volume_min:
                warnings.append(
                    f"volume={volume} < volume_min={si.volume_min} for {symbol}"
                )
            if volume > si.volume_max:
                warnings.append(
                    f"volume={volume} > volume_max={si.volume_max} for {symbol}"
                )

        ok = len(blocking) == 0
        return PreflightResult(
            ok=ok,
            blocking_reasons=blocking,
            warnings=warnings,
            terminal_info=terminal_info,
            symbol_info=symbol_info,
        )

    def preflight(
        self,
        symbol: str = "XAUUSD",
        volume: float = 0.01,
        path: str = "",
        login: int = 0,
        password: str = "",
        server: str = "",
        timeout_ms: int = 10000,
    ) -> list[str]:
        """
        Run all preflight checks. Returns list of error strings.
        Empty list means all checks passed.
        """
        errors: list[str] = []

        # 1. initialize
        if not self.initialize(path=path, login=login, password=password,
                               server=server, timeout_ms=timeout_ms):
            code, msg = self.last_error()
            errors.append(f"MT5 initialize() failed: [{code}] {msg}")
            return errors   # cannot continue without connection

        # 2. terminal_info
        ti = self.get_terminal_info()
        if ti is None:
            code, msg = self.last_error()
            errors.append(f"terminal_info() returned None: [{code}] {msg}")
            return errors
        if not ti.connected:
            errors.append("terminal_info.connected == False (not connected to broker)")
        if not ti.trade_allowed:
            errors.append("terminal_info.trade_allowed == False (trading disabled in terminal)")
        if ti.tradeapi_disabled:
            errors.append(
                "terminal_info.tradeapi_disabled == True  "
                "(Options → Expert Advisors → 'Disable automated trading via Python API' is ticked)"
            )

        # 3. symbol_info
        if not self.symbol_select(symbol, True):
            errors.append(f"symbol_select({symbol}) failed")
        si = self.get_symbol_info(symbol)
        if si is None:
            errors.append(f"symbol_info({symbol}) returned None – symbol not visible in Market Watch")
        else:
            # 4. volume validation
            if volume < si.volume_min:
                errors.append(
                    f"volume={volume} < volume_min={si.volume_min} for {symbol}"
                )
            if volume > si.volume_max:
                errors.append(
                    f"volume={volume} > volume_max={si.volume_max} for {symbol}"
                )

        return errors

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_filling_mode(self, symbol: str) -> int:
        """Detect broker-supported filling mode (IOC / FOK / Return)."""
        try:
            si = self._mt5.symbol_info(symbol)
            if si is None:
                return 2  # FILL_RETURN as safe default
            # filling_mode is a bitmask: bit0=FOK, bit1=IOC, bit2=RETURN
            fm = getattr(si, "filling_mode", 0)
            if fm & 1:
                return 0  # FOK
            if fm & 2:
                return 1  # IOC
            return 2      # RETURN
        except Exception:
            return 2
