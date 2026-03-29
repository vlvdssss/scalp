from types import SimpleNamespace

from app.src.core.engine_market_pipeline import MarketContext, MarketPipelineMixin


class _Host(MarketPipelineMixin):
    def __init__(self) -> None:
        self._cfg = {"atr": {"bars_fetch": 5}}
        self._state = SimpleNamespace(mode="NORMAL", last_tick_time_msc=0)
        self._micro_guard = SimpleNamespace(check=self._check_micro_guard)
        self._spread_model = SimpleNamespace(
            update=lambda spread_points, tick_time: SimpleNamespace(
                spread_med_points=spread_points,
                spread_points=spread_points,
            )
        )
        self._atr_model = SimpleNamespace(
            compute_from_bars=lambda rates, point, spread_med_points: SimpleNamespace(
                atr_points=120.0,
            )
        )
        self._adapter = SimpleNamespace(
            copy_rates_from_pos=lambda *args, **kwargs: [{"high": 1.0, "low": 0.5}],
            get_positions=lambda sym: [],
            get_orders=lambda sym: [],
        )
        self._order_mgr = SimpleNamespace(_flat_frozen=False)
        self._last_atr_pts = 0.0
        self._last_spread_med_pts = 0.0
        self._last_candle_hi = 0.0
        self._last_candle_lo = 0.0
        self._last_is_flat = False
        self._micro_guard_pause_until_mono = 0.0
        self._micro_guard_stable_since_mono = 0.0
        self._micro_guard_pause_on_trigger_ms = 5000.0
        self._micro_guard_recovery_stability_ms = 4000.0
        self.logged_events: list[tuple[str, dict]] = []
        self._safe_trigger = False

    def _check_micro_guard(self, *args, **kwargs):
        return SimpleNamespace(
            safe_trigger=self._safe_trigger,
            reasons=["ping high"] if self._safe_trigger else [],
            tick_stale_ms=0.0,
            ipc_duration_ms=1.0,
            ping_last_ms=2500 if self._safe_trigger else 25,
        )

    def _handle_disconnect(self, ti):
        raise AssertionError("disconnect not expected")

    def _enter_safe_mode(self, reason: str) -> None:
        raise AssertionError(f"safe mode not expected: {reason}")

    def _log_event(self, event: str, data: dict) -> None:
        self.logged_events.append((event, data))

    def _clock_event(self, mono_ms: float, bid: float, ask: float, si) -> None:
        return None


def _market_context() -> MarketContext:
    return MarketContext(
        ti=SimpleNamespace(ping_last=25),
        latency_ms=1.0,
        si=SimpleNamespace(point=0.01),
        tick=SimpleNamespace(time_msc=1),
        is_new_tick=True,
        bid=2500.0,
        ask=2500.5,
    )


def test_micro_guard_pause_blocks_new_pending_until_recovery_window_passes() -> None:
    host = _Host()
    host._safe_trigger = True

    first = host._analyze_market("XAUUSD", 1000.0, _market_context())

    assert first is not None
    assert first.micro_guard_blocked is True
    assert host._micro_guard_pause_until_mono == 6000.0
    assert host.logged_events[0][0] == "MICRO_GUARD_TRIGGER"

    host._safe_trigger = False
    second = host._analyze_market("XAUUSD", 4000.0, _market_context())

    assert second is not None
    assert second.micro_guard_blocked is True
    assert host.logged_events[-1][0] == "MICRO_GUARD_TRIGGER"

    third = host._analyze_market("XAUUSD", 6001.0, _market_context())

    assert third is not None
    assert third.micro_guard_blocked is True
    assert host.logged_events[-1][0] == "MICRO_GUARD_STABILITY_WAIT"

    fourth = host._analyze_market("XAUUSD", 10002.0, _market_context())

    assert fourth is not None
    assert fourth.micro_guard_blocked is False
    assert host.logged_events[-1][0] == "MICRO_GUARD_RECOVERED"


def test_micro_guard_new_trigger_resets_recovery_stability_timer() -> None:
    host = _Host()
    host._safe_trigger = True

    first = host._analyze_market("XAUUSD", 1000.0, _market_context())

    assert first is not None
    assert first.micro_guard_blocked is True

    host._safe_trigger = False
    second = host._analyze_market("XAUUSD", 6001.0, _market_context())

    assert second is not None
    assert second.micro_guard_blocked is True
    assert host._micro_guard_stable_since_mono == 6001.0

    host._safe_trigger = True
    third = host._analyze_market("XAUUSD", 7000.0, _market_context())

    assert third is not None
    assert third.micro_guard_blocked is True
    assert host._micro_guard_pause_until_mono == 12000.0
    assert host._micro_guard_stable_since_mono == 0.0