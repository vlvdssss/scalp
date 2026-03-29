"""
Unit tests for P0-004: Retcode policy matrix.

Validates that all 10004–10018 retcodes are covered,
that actions are correct for known-critical retcodes,
and that unknown retcodes have a safe fallback.
"""
from __future__ import annotations

import pytest

from app.src.adapters.mt5_adapter import (
    MT5Adapter,
    RetcodeAction,
    RETCODE_POLICY,
    get_retcode_policy,
    get_retcode_name,
)


class TestRetcodePolicyMatrix:
    # Retcodes that MUST be present
    REQUIRED_RETCODES = list(range(10004, 10019))

    def test_all_required_retcodes_covered(self) -> None:
        for rc in self.REQUIRED_RETCODES:
            assert rc in RETCODE_POLICY, (
                f"Retcode {rc} ({get_retcode_name(rc)}) missing from RETCODE_POLICY"
            )

    def test_rc_done_is_success(self) -> None:
        policy = RETCODE_POLICY.get(10009)
        assert policy is not None
        assert policy.action == RetcodeAction.SUCCESS, (
            "10009 TRADE_RETCODE_DONE must map to SUCCESS"
        )

    def test_rc_reject_is_stop_trading(self) -> None:
        """10006 TRADE_RETCODE_REJECT: should STOP_TRADING (never retry blindly)."""
        policy = RETCODE_POLICY.get(10006)
        assert policy is not None
        assert policy.action == RetcodeAction.STOP_TRADING, (
            "10006 TRADE_RETCODE_REJECT must trigger STOP_TRADING"
        )

    def test_rc_requote_is_retry(self) -> None:
        """10004 TRADE_RETCODE_REQUOTE should be retryable."""
        policy = RETCODE_POLICY.get(10004)
        assert policy is not None
        assert policy.action in (RetcodeAction.RETRY_BACKOFF, RetcodeAction.REBUILD_REQUEST)

    def test_rc_invalid_stops_is_rebuild(self) -> None:
        """10016 TRADE_RETCODE_INVALID_STOPS: must rebuild request (not blind retry)."""
        policy = RETCODE_POLICY.get(10016)
        assert policy is not None
        assert policy.action in (RetcodeAction.REBUILD_REQUEST, RetcodeAction.STOP_TRADING), (
            "Invalid-stops retcode must trigger a rebuild or stop"
        )

    def test_get_retcode_policy_unknown_returns_fallback(self) -> None:
        """Unknown retcodes must not raise; must return a safe non-None policy."""
        policy = get_retcode_policy(99999)
        assert policy is not None, "Unknown retcode must return a fallback policy"

    def test_get_retcode_name_returns_string(self) -> None:
        name = get_retcode_name(10009)
        assert isinstance(name, str)
        assert len(name) > 0

    def test_get_retcode_name_unknown_does_not_raise(self) -> None:
        name = get_retcode_name(99999)
        assert isinstance(name, str)

    def test_no_retcode_has_none_action(self) -> None:
        """Every policy entry must have a valid RetcodeAction."""
        for rc, entry in RETCODE_POLICY.items():
            assert entry.action is not None, f"Retcode {rc} has None action"
            assert isinstance(entry.action, RetcodeAction), (
                f"Retcode {rc} action is not RetcodeAction enum"
            )

    def test_retry_retcodes_have_backoff_ms(self) -> None:
        """Retryable retcodes must have positive backoff_ms."""
        for rc, entry in RETCODE_POLICY.items():
            if entry.action in (RetcodeAction.RETRY_BACKOFF,):
                assert entry.backoff_ms > 0, (
                    f"Retcode {rc} is RETRY_BACKOFF but has backoff_ms={entry.backoff_ms}"
                )

    def test_retry_retcodes_have_retry_limit(self) -> None:
        """Retryable retcodes must specify a finite retry_limit."""
        for rc, entry in RETCODE_POLICY.items():
            if entry.action == RetcodeAction.RETRY_BACKOFF:
                assert entry.retry_limit is not None and entry.retry_limit > 0, (
                    f"Retcode {rc} is RETRY_BACKOFF but has no retry_limit"
                )
