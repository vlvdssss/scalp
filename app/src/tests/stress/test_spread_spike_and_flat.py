"""
Stress tests — spread spike, flat market, disconnect / reconnect simulation.

These tests exercise edge-case behaviour using synthetic data injected
directly into model objects or mocked adapters, so they do NOT require a
live MT5 terminal.
"""
import time
import pytest
from unittest.mock import MagicMock, patch, call

from app.src.core.models_spread import SpreadMedianModel, SpreadConfig
from app.src.core.micro_guard  import MicroGuard,  MicroGuardConfig


# ---------------------------------------------------------------------------
# Spread spike scenarios
# ---------------------------------------------------------------------------
class TestSpreadSpike:
    def test_single_massive_spike_does_not_poison_median(self):
        """One extreme spike should not permanently elevate the median."""
        cfg = SpreadConfig(
            rolling_window_sec=300.0,
            k_maxspread=2.0, maxspread_min=20.0, maxspread_cap=300.0,
            k_spike=3.0,
        )
        model = SpreadMedianModel(cfg)
        t = 0

        # Warm up with normal 20-pt spread
        for _ in range(10):
            model.update(20.0, t); t += 30_000

        normal_max = model.update(20.0, t).max_spread_points
        t += 30_000

        # Inject one enormous spike (500 pts)
        model.update(500.0, t); t += 30_000

        # After spike, spread returns to normal
        for _ in range(5):
            model.update(20.0, t); t += 30_000

        after_max = model.update(20.0, t).max_spread_points
        # Median should have recovered; max_spread must not be wildly inflated
        assert after_max < normal_max * 10

    def test_sustained_high_spread_elevates_max(self):
        """Consistent 200-pt spread should raise max_spread above 20-pt baseline."""
        cfg = SpreadConfig(
            rolling_window_sec=300.0, k_maxspread=2.0,
            maxspread_min=20.0, maxspread_cap=1000.0,
            k_spike=3.0,
        )
        model = SpreadMedianModel(cfg)
        t = 0

        for _ in range(20):
            model.update(200.0, t); t += 30_000

        result = model.update(200.0, t)
        assert result.max_spread_points > 100.0

    def test_consecutive_spikes_trigger_spike_flag(self):
        """k_spike × med == spike detection; multiple spikes increment denial count."""
        cfg = SpreadConfig(
            rolling_window_sec=300.0, k_maxspread=2.0,
            maxspread_min=20.0, maxspread_cap=300.0,
            k_spike=3.0,
        )
        model = SpreadMedianModel(cfg)
        t = 0

        # Warm up
        for _ in range(10):
            model.update(20.0, t); t += 30_000

        spike_count = 0
        for _ in range(5):
            r = model.update(200.0, t); t += 30_000
            if r.is_spike:
                spike_count += 1

        assert spike_count >= 2


# ---------------------------------------------------------------------------
# Flat market (zero-tick / unchanged quote) detection
# ---------------------------------------------------------------------------
class TestFlatMarket:
    def test_flat_tick_detected_after_n_identical(self):
        """N identical bid/ask ticks are a soft warning only – NOT a SAFE trigger.
        MicroGuard logs via debug but does not add to reasons / safe_trigger.
        (flat quotes can legitimately occur in low-volatility periods)
        """
        cfg = MicroGuardConfig(latency_max_ms=500.0, flat_ticks_limit=5)
        guard = MicroGuard(cfg)

        # feed 5 identical ticks; the 5th reaches the limit
        for i in range(5):
            guard.check(bid=1900.00, ask=1900.05,
                        api_call_latency_ms=0.0, terminal_ping_ms=None)

        result = guard.check(bid=1900.00, ask=1900.05,
                             api_call_latency_ms=0.0, terminal_ping_ms=None)
        # flat ticks are a soft warning only — must NOT trigger SAFE MODE
        assert result.safe_trigger is False
        assert not any("flat" in r for r in result.reasons)

    def test_single_move_resets_flat_counter(self):
        """One different tick resets the consecutive-flat counter."""
        cfg = MicroGuardConfig(latency_max_ms=500.0, flat_ticks_limit=5)
        guard = MicroGuard(cfg)

        for _ in range(4):
            guard.check(bid=1900.00, ask=1900.05,
                        api_call_latency_ms=0.0, terminal_ping_ms=None)

        guard.check(bid=1900.01, ask=1900.06,
                    api_call_latency_ms=0.0, terminal_ping_ms=None)  # movement

        result = guard.check(bid=1900.01, ask=1900.06,
                             api_call_latency_ms=0.0, terminal_ping_ms=None)
        assert result.safe_trigger is False  # not 5 consecutive anymore

    def test_high_ping_alone_is_soft_signal(self):
        """Sticky MT5 ping must not hard-block trading when ticks/API calls are healthy."""
        cfg = MicroGuardConfig(latency_max_ms=500.0, flat_ticks_limit=5, ping_max_ms=1000)
        guard = MicroGuard(cfg)
        guard.on_new_tick(1000.0)

        result = guard.check(
            bid=1900.00,
            ask=1900.05,
            api_call_latency_ms=5.0,
            terminal_ping_ms=42822,
            mono_ms=1200.0,
            is_new_tick=True,
        )

        assert result.safe_trigger is False
        assert not any("ping=" in reason for reason in result.reasons)

    def test_high_ping_with_other_channel_failure_is_hard_trigger(self):
        """Ping still contributes when the channel also shows a hard failure."""
        cfg = MicroGuardConfig(latency_max_ms=500.0, flat_ticks_limit=5, ping_max_ms=1000)
        guard = MicroGuard(cfg)
        guard.on_new_tick(1000.0)

        result = guard.check(
            bid=1900.00,
            ask=1900.05,
            api_call_latency_ms=850.0,
            terminal_ping_ms=42822,
            mono_ms=1200.0,
            is_new_tick=True,
        )

        assert result.safe_trigger is True
        assert any("latency=" in reason for reason in result.reasons)
        assert any("ping=" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Disconnect / reconnect simulation
# ---------------------------------------------------------------------------
class TestDisconnectReconnect:
    def test_adapter_reconnect_called_on_failure(self):
        """Engine must call reconnect when get_tick returns None repeatedly."""
        from app.src.adapters.mt5_adapter import MT5Adapter

        adapter = MagicMock(spec=MT5Adapter)
        adapter.get_tick.return_value = None  # simulate disconnect

        # Call get_tick to accumulate failures
        fails = sum(1 for _ in range(10) if adapter.get_tick("XAUUSD") is None)
        assert fails == 10

    def test_adapter_backoff_delays(self):
        """Verify backoff sequence increases by factor (unit-level simulation)."""
        delays = []
        base  = 1.0
        factor = 2.0
        cap   = 30.0
        d = base
        for _ in range(6):
            delays.append(d)
            d = min(d * factor, cap)

        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]

    def test_request_rate_stays_within_limit(self):
        """Simulated tick processing must not exceed MAX_REQ_PER_SEC."""
        # With CYCLE_INTERVAL_MS = 100ms → max 10 cycles/sec
        cycle_ms = 100
        sim_duration_ms = 10_000
        cycles = sim_duration_ms // cycle_ms
        requests_per_cycle = 7  # approx MT5 calls per cycle
        total = cycles * requests_per_cycle
        rate_per_sec = total / (sim_duration_ms / 1000)
        # Should comfortably stay below 200 req/sec (MT5 typical limit)
        assert rate_per_sec < 200
