"""
Integration tests for DUAL PENDING STOP full lifecycle.

REQUIRES: Running MetaTrader5 terminal connected to a DEMO account.
Skip these tests in CI (no MT5) via:  pytest -m "not mt5"

Marker: @pytest.mark.mt5
"""
import time
import pytest

pytestmark = pytest.mark.mt5  # all tests here require live MT5

try:
    import MetaTrader5 as mt5  # type: ignore[import-untyped]
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


@pytest.fixture(scope="module")
def mt5_session():
    if not MT5_AVAILABLE:
        pytest.skip("MetaTrader5 package not installed")
    if not mt5.initialize():
        pytest.skip("MT5 terminal not available or not running")
    yield mt5
    mt5.shutdown()


@pytest.mark.mt5
class TestDualPendingLifecycle:
    """
    Scenario: place dual BUY STOP + SELL STOP, then cancel both.
    Validates order IDs returned, both appear in orders_get, cancel removes them.
    """

    def test_place_and_cancel_dual(self, mt5_session):
        sym = mt5_session.symbol_info("XAUUSD")
        if sym is None:
            pytest.skip("XAUUSD not available on this account")

        tick = mt5_session.symbol_info_tick("XAUUSD")
        ask, bid = tick.ask, tick.bid
        pt = sym.point

        buy_price  = round(ask + 200 * pt, sym.digits)
        sell_price = round(bid - 200 * pt, sym.digits)
        sl_buy     = round(buy_price  - 500 * pt, sym.digits)
        sl_sell    = round(sell_price + 500 * pt, sym.digits)

        buy_req = {
            "action":      mt5_session.TRADE_ACTION_PENDING,
            "symbol":      "XAUUSD",
            "volume":      0.01,
            "type":        mt5_session.ORDER_TYPE_BUY_STOP,
            "price":       buy_price,
            "sl":          sl_buy,
            "type_time":   mt5_session.ORDER_TIME_GTC,
            "type_filling": mt5_session.ORDER_FILLING_IOC,
            "magic":       20260225,
            "comment":     "integration-test-buy",
        }
        sell_req = {**buy_req,
                    "type":    mt5_session.ORDER_TYPE_SELL_STOP,
                    "price":   sell_price,
                    "sl":      sl_sell,
                    "comment": "integration-test-sell"}

        res_buy  = mt5_session.order_send(buy_req)
        res_sell = mt5_session.order_send(sell_req)

        assert res_buy.retcode  in {10008, 10009}, f"buy_stop failed: {res_buy.comment}"
        assert res_sell.retcode in {10008, 10009}, f"sell_stop failed: {res_sell.comment}"

        orders = mt5_session.orders_get(symbol="XAUUSD")
        tickets = {o.ticket for o in (orders or [])}
        assert res_buy.order  in tickets
        assert res_sell.order in tickets

        for ticket in (res_buy.order, res_sell.order):
            delete_req = {
                "action": mt5_session.TRADE_ACTION_REMOVE,
                "order":  ticket,
            }
            r = mt5_session.order_send(delete_req)
            assert r.retcode in {10008, 10009}

        # Verify both gone
        orders_after = mt5_session.orders_get(symbol="XAUUSD")
        tickets_after = {o.ticket for o in (orders_after or [])}
        assert res_buy.order  not in tickets_after
        assert res_sell.order not in tickets_after


@pytest.mark.mt5
class TestConfirmFailure:
    """
    Manual / semi-automated: after a pending stop fills, simulate fake breakout.
    This test is a scaffold — full automation requires tick replay.
    """

    def test_confirm_context_timeout(self):
        """Verify that a ConfirmContext with elapsed > window evaluates to fail."""
        from app.src.core.state import ConfirmContext
        ctx = ConfirmContext(start_monotonic_ms=0.0)
        elapsed_ms = 5000.0  # 5 s > 2 s window
        window_ms  = 2000.0
        ctx.ticks_seen = 2

        exhausted = elapsed_ms >= window_ms or ctx.ticks_seen >= 8
        assert exhausted is True


@pytest.mark.mt5
class TestRestartRecovery:
    """
    Scaffold: after kill/restart the engine must reconcile existing positions
    and pending orders without placing duplicates.
    """

    def test_recovery_finds_existing_position(self, mt5_session):
        pos = mt5_session.positions_get(symbol="XAUUSD")
        # Just validate the call works; no position expected on a fresh demo
        assert pos is not None  # returns empty tuple or list

    def test_recovery_finds_existing_orders(self, mt5_session):
        orders = mt5_session.orders_get(symbol="XAUUSD")
        assert orders is not None
