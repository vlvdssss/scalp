"""
Unit tests for P0-2 retcode policy: TRADE_DISABLED (10017) → HARD_BLOCK,
MARKET_CLOSED (10018) → DENY_WAIT.
"""
from __future__ import annotations

from app.src.adapters.mt5_adapter import (
    RetcodeAction,
    get_retcode_policy,
    RC_TRADE_DISABLED,
    RC_MARKET_CLOSED,
)


class TestRetcodePolicyHardBlock:
    def test_trade_disabled_10017_is_hard_block(self):
        policy = get_retcode_policy(RC_TRADE_DISABLED)
        assert policy.action == RetcodeAction.HARD_BLOCK
        assert policy.name == "TRADE_DISABLED"

    def test_hard_block_has_terminal_reason(self):
        policy = get_retcode_policy(RC_TRADE_DISABLED)
        assert policy.terminal_reason != ""

    def test_hard_block_retcode_value_is_10017(self):
        assert RC_TRADE_DISABLED == 10017


class TestRetcodePolicyDenyWait:
    def test_market_closed_10018_is_deny_wait(self):
        policy = get_retcode_policy(RC_MARKET_CLOSED)
        assert policy.action == RetcodeAction.DENY_WAIT
        assert policy.name == "MARKET_CLOSED"

    def test_deny_wait_retcode_value_is_10018(self):
        assert RC_MARKET_CLOSED == 10018

    def test_deny_wait_no_retry(self):
        """DENY_WAIT should have retry_limit == 0 – no retry on market closed."""
        policy = get_retcode_policy(RC_MARKET_CLOSED)
        assert policy.retry_limit == 0


class TestRetcodePolicyOthers:
    """Smoke tests – ensure known safe retcodes still map correctly."""

    def test_done_10009_is_success(self):
        policy = get_retcode_policy(10009)
        assert policy.action == RetcodeAction.SUCCESS

    def test_requote_10004_is_retry_backoff(self):
        policy = get_retcode_policy(10004)
        assert policy.action == RetcodeAction.RETRY_BACKOFF

    def test_invalid_stops_10016_is_rebuild_request(self):
        policy = get_retcode_policy(10016)
        assert policy.action == RetcodeAction.REBUILD_REQUEST

    def test_unknown_retcode_is_log_only(self):
        policy = get_retcode_policy(99999)
        assert policy.action == RetcodeAction.LOG_ONLY


class TestOrderManagerHardBlockCallback:
    """P0-2: OrderManager calls retcode_policy_cb on HARD_BLOCK/DENY_WAIT."""

    def _make_order_manager(self, callback):
        from unittest.mock import MagicMock
        from app.src.adapters.mt5_adapter import TradeResult
        from app.src.core.order_manager import OrderManager, PendingConfig
        from app.src.core.state import StateStore
        from app.src.core.risk import EntryConfig

        entry_cfg = EntryConfig(
            k_entry_atr=0.3, k_entry_spread=1.2, entry_offset_min_points=30.0,
            k_rearm_atr=0.15, k_rearm_spread=0.6, rearm_min_points=15.0,
        )
        pending_cfg = PendingConfig(
            symbol="XAUUSD",
            op_deadline_ms=5000.0,  # generous deadline so it doesn't fire in test
        )
        adapter = MagicMock()
        state = StateStore()
        mgr = OrderManager(adapter, state, entry_cfg, pending_cfg, retcode_policy_cb=callback)
        return mgr, adapter

    def test_hard_block_callback_called_on_10017(self):
        called_with = []
        mgr, adapter = self._make_order_manager(
            lambda action, rc: called_with.append((action, rc))
        )
        mgr._handle_retcode_error(RC_TRADE_DISABLED, "test_ctx", {})
        assert len(called_with) == 1
        assert called_with[0] == (RetcodeAction.HARD_BLOCK.value, RC_TRADE_DISABLED)

    def test_deny_wait_callback_called_on_10018(self):
        called_with = []
        mgr, adapter = self._make_order_manager(
            lambda action, rc: called_with.append((action, rc))
        )
        mgr._handle_retcode_error(RC_MARKET_CLOSED, "test_ctx", {})
        assert len(called_with) == 1
        assert called_with[0] == (RetcodeAction.DENY_WAIT.value, RC_MARKET_CLOSED)

    def test_requote_does_not_trigger_callback(self):
        called_with = []
        mgr, adapter = self._make_order_manager(
            lambda action, rc: called_with.append((action, rc))
        )
        mgr._handle_retcode_error(10004, "test_ctx", {})
        assert called_with == []
