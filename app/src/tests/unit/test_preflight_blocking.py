"""
Unit tests for PreflightResult and run_preflight() (P0-1).

Tests:
  - PreflightResult.ok is False when connected == False
  - PreflightResult.ok is False when trade_allowed == False
  - PreflightResult.ok is False when tradeapi_disabled == True
  - PreflightResult.ok is True when all terminal flags are healthy
  - PreflightResult properties (connected, trade_allowed, tradeapi_disabled, ping_last)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.src.adapters.mt5_adapter import (
    MT5Adapter,
    PreflightResult,
    TerminalSnapshot,
    SymbolSnapshot,
)


def _make_terminal(
    *,
    connected: bool = True,
    trade_allowed: bool = True,
    tradeapi_disabled: bool = False,
    ping_last: int = 10,
) -> TerminalSnapshot:
    return TerminalSnapshot(
        connected=connected,
        trade_allowed=trade_allowed,
        tradeapi_disabled=tradeapi_disabled,
        ping_last=ping_last,
    )


def _make_symbol(name: str = "XAUUSD") -> SymbolSnapshot:
    return SymbolSnapshot(
        name=name,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_stops_level=0,
        trade_freeze_level=0,
        digits=2,
        spread=30,
        trade_mode=4,
    )


def _make_adapter_with_ti(ti: TerminalSnapshot):
    """Build MT5Adapter with a mocked MT5 module that returns given TerminalSnapshot."""
    mt5_mock = MagicMock()
    mt5_mock.initialize.return_value = True
    # terminal_info raw mock
    raw_ti = MagicMock()
    raw_ti.connected = ti.connected
    raw_ti.trade_allowed = ti.trade_allowed
    raw_ti.tradeapi_disabled = ti.tradeapi_disabled
    raw_ti.ping_last = ti.ping_last
    mt5_mock.terminal_info.return_value = raw_ti
    # symbol_info raw mock
    raw_si = MagicMock()
    raw_si.name = "XAUUSD"
    raw_si.point = 0.01
    raw_si.tick_size = 0.01
    raw_si.tick_value = 0.01
    raw_si.volume_min = 0.01
    raw_si.volume_max = 100.0
    raw_si.volume_step = 0.01
    raw_si.trade_stops_level = 0
    raw_si.trade_freeze_level = 0
    raw_si.digits = 2
    raw_si.spread = 30
    raw_si.trade_mode = 4
    mt5_mock.symbol_select.return_value = True
    mt5_mock.symbol_info.return_value = raw_si
    mt5_mock.last_error.return_value = (0, "")
    return MT5Adapter(mt5_module=mt5_mock)


class TestPreflightResultProperties:
    def test_connected_when_terminal_connected(self):
        ti = _make_terminal(connected=True)
        r = PreflightResult(ok=True, blocking_reasons=[], warnings=[], terminal_info=ti, symbol_info=None)
        assert r.connected is True

    def test_not_connected_when_terminal_disconnected(self):
        ti = _make_terminal(connected=False)
        r = PreflightResult(ok=False, blocking_reasons=["disconnected"], warnings=[], terminal_info=ti, symbol_info=None)
        assert r.connected is False

    def test_trade_allowed_true(self):
        ti = _make_terminal(trade_allowed=True)
        r = PreflightResult(ok=True, blocking_reasons=[], warnings=[], terminal_info=ti, symbol_info=None)
        assert r.trade_allowed is True

    def test_tradeapi_disabled_false(self):
        ti = _make_terminal(tradeapi_disabled=False)
        r = PreflightResult(ok=True, blocking_reasons=[], warnings=[], terminal_info=ti, symbol_info=None)
        assert r.tradeapi_disabled is False

    def test_ping_last_returns_value(self):
        ti = _make_terminal(ping_last=42)
        r = PreflightResult(ok=True, blocking_reasons=[], warnings=[], terminal_info=ti, symbol_info=None)
        assert r.ping_last == 42

    def test_ping_last_minus_one_when_no_terminal_info(self):
        r = PreflightResult(ok=False, blocking_reasons=[], warnings=[], terminal_info=None, symbol_info=None)
        assert r.ping_last == -1


class TestRunPreflightHardBlocks:
    """P0-1: run_preflight() must return ok=False for each blocking condition."""

    def test_blocks_when_not_connected(self):
        adapter = _make_adapter_with_ti(_make_terminal(connected=False))
        result = adapter.run_preflight()
        assert result.ok is False
        assert any("connected" in r.lower() for r in result.blocking_reasons)

    def test_blocks_when_trade_not_allowed(self):
        adapter = _make_adapter_with_ti(_make_terminal(trade_allowed=False))
        result = adapter.run_preflight()
        assert result.ok is False
        assert any("trade_allowed" in r.lower() for r in result.blocking_reasons)

    def test_blocks_when_tradeapi_disabled(self):
        adapter = _make_adapter_with_ti(_make_terminal(tradeapi_disabled=True))
        result = adapter.run_preflight()
        assert result.ok is False
        assert any("tradeapi_disabled" in r.lower() for r in result.blocking_reasons)

    def test_ok_when_all_healthy(self):
        adapter = _make_adapter_with_ti(_make_terminal())
        result = adapter.run_preflight()
        assert result.ok is True
        assert result.blocking_reasons == []

    def test_symbol_info_present_on_success(self):
        adapter = _make_adapter_with_ti(_make_terminal())
        result = adapter.run_preflight(symbol="XAUUSD", volume=0.01)
        assert result.symbol_info is not None
        assert result.symbol_info.name == "XAUUSD"

    def test_volume_warning_on_small_volume(self):
        """Soft warning (not block) when volume < volume_min."""
        adapter = _make_adapter_with_ti(_make_terminal())
        # volume_min = 0.01 in mock; pass 0.001 → warning
        result = adapter.run_preflight(symbol="XAUUSD", volume=0.001)
        assert result.ok is True  # warnings don't block
        assert any("volume" in w.lower() for w in result.warnings)

    def test_blocks_on_initialize_failure(self):
        mt5_mock = MagicMock()
        mt5_mock.initialize.return_value = False
        mt5_mock.last_error.return_value = (-2, "init failed")
        adapter = MT5Adapter(mt5_module=mt5_mock)
        result = adapter.run_preflight()
        assert result.ok is False
        assert any("initialize" in r.lower() for r in result.blocking_reasons)
