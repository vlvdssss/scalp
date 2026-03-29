"""
Unit tests for P1-2: Operation deadline in OrderManager.
order_send calls taking longer than op_deadline_ms must trigger retcode_policy_cb.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.src.core.order_manager import OrderManager, PendingConfig
from app.src.core.state import StateStore
from app.src.core.risk import EntryConfig
from app.src.adapters.mt5_adapter import TradeResult, RC_DONE


def _make_manager(deadline_ms: float, callback):
    entry_cfg = EntryConfig(
        k_entry_atr=0.3, k_entry_spread=1.2, entry_offset_min_points=30.0,
        k_rearm_atr=0.15, k_rearm_spread=0.6, rearm_min_points=15.0,
    )
    pending_cfg = PendingConfig(
        symbol="XAUUSD",
        op_deadline_ms=deadline_ms,
    )
    adapter = MagicMock()
    state = StateStore()
    mgr = OrderManager(adapter, state, entry_cfg, pending_cfg, retcode_policy_cb=callback)
    return mgr, adapter


class TestOpDeadlineExceeded:
    def test_deadline_callback_when_order_send_slow(self):
        """If order_send takes > op_deadline_ms, callback must be called."""
        called_with = []

        def slow_order_send(req):
            time.sleep(0.05)  # 50ms
            r = MagicMock()
            r.retcode = RC_DONE
            r.order = 1
            return r

        mgr, adapter = _make_manager(
            deadline_ms=0.1,  # 0.1ms – very tight, will fire
            callback=lambda action, rc: called_with.append((action, rc)),
        )
        adapter.order_send.side_effect = slow_order_send

        result = mgr._order_send_timed({"type": "test"}, "test_ctx")

        assert len(called_with) == 1
        assert called_with[0][0] == "OP_DEADLINE_EXCEEDED"

    def test_no_callback_when_order_send_fast(self):
        """If order_send completes within deadline, callback must NOT be called."""
        called_with = []

        def fast_order_send(req):
            r = MagicMock()
            r.retcode = RC_DONE
            r.order = 1
            return r

        mgr, adapter = _make_manager(
            deadline_ms=5000.0,  # 5 seconds – very generous
            callback=lambda action, rc: called_with.append((action, rc)),
        )
        adapter.order_send.side_effect = fast_order_send

        result = mgr._order_send_timed({"type": "test"}, "test_ctx")

        assert called_with == []

    def test_op_deadline_default_is_3000ms(self):
        """Default op_deadline_ms should be 3000 ms."""
        cfg = PendingConfig()
        assert cfg.op_deadline_ms == 3000.0

    def test_result_still_returned_on_deadline_exceeded(self):
        """Even when deadline exceeded, the result from order_send is still returned."""
        called = []

        def slow_order_send(req):
            time.sleep(0.05)
            r = MagicMock()
            r.retcode = RC_DONE
            r.order = 42
            return r

        mgr, adapter = _make_manager(
            deadline_ms=0.1,
            callback=lambda a, rc: called.append(a),
        )
        adapter.order_send.side_effect = slow_order_send
        result = mgr._order_send_timed({}, "ctx")
        # Result is still returned
        assert result is not None
        assert result.order == 42
        # Callback was also called
        assert "OP_DEADLINE_EXCEEDED" in called
