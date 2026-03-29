"""
Unit tests for P0-007: Confirm-success ≠ BE-trigger metrics.

Validates that:
- confirm_success_rate is the fraction of fills where confirm succeeded
- be_trigger_rate is the fraction of trades where BE was actually triggered
- They are computed independently (a trade can have confirm_success=True, be_triggered=False)
"""
from __future__ import annotations

import pytest

from app.src.core.analytics import compute_stats


def _make_records(**kwargs) -> list[dict]:
    """Build a list of minimal trade record dicts."""
    return list(kwargs.get("records", []))


def _trade(
    confirm_success: bool = True,
    fake_breakout: bool = False,
    be_triggered: bool = False,
    pnl_points: float = 10.0,
) -> dict:
    return {
        "confirm_success": confirm_success,
        "fake_breakout": fake_breakout,
        "be_triggered": be_triggered,
        "pnl_points": pnl_points,
        "pnl_money": pnl_points * 0.1,
        "MFE_points": max(pnl_points, 0),
        "MAE_points": max(-pnl_points, 0),
        "spread_entry_points": 5.0,
        "spread_exit_points": 5.0,
    }


class TestMetricsConfirmVsBE:
    def test_confirm_rate_independent_of_be(self) -> None:
        """confirm_success_rate must NOT count be_triggered."""
        records = [
            _trade(confirm_success=True,  be_triggered=False),  # confirm only
            _trade(confirm_success=True,  be_triggered=True),   # both
            _trade(confirm_success=False, be_triggered=False),  # neither
            _trade(confirm_success=False, be_triggered=True),   # be only (impossible but test)
        ]
        stats = compute_stats(records)
        # confirm success: 2 / 4 = 0.5
        assert abs(stats["confirm_success_rate"] - 0.5) < 1e-9, (
            f"confirm_success_rate={stats['confirm_success_rate']!r}, expected 0.5"
        )

    def test_be_trigger_rate_independent_of_confirm(self) -> None:
        """be_trigger_rate must NOT count confirm_success."""
        records = [
            _trade(confirm_success=True,  be_triggered=True),   # both
            _trade(confirm_success=True,  be_triggered=False),  # confirm only
            _trade(confirm_success=False, be_triggered=False),  # neither
        ]
        stats = compute_stats(records)
        # be triggered: 1 / 3
        expected = 1.0 / 3.0
        assert abs(stats["be_trigger_rate"] - expected) < 1e-9, (
            f"be_trigger_rate={stats['be_trigger_rate']!r}, expected {expected}"
        )

    def test_pre_fix_regression_be_rate_was_confirm_rate(self) -> None:
        """
        Regression guard: before the fix, be_rate was computed from confirm_success.
        This test would FAIL on the old code where be_rate=confirm_success.
        """
        records = [
            _trade(confirm_success=True,  be_triggered=False),
            _trade(confirm_success=True,  be_triggered=False),
            _trade(confirm_success=False, be_triggered=True),
        ]
        stats = compute_stats(records)
        # confirm: 2/3, be: 1/3  → they MUST differ
        assert stats["confirm_success_rate"] != stats["be_trigger_rate"], (
            "confirm_success_rate and be_trigger_rate must be independent metrics"
        )

    def test_empty_records_returns_zeros(self) -> None:
        stats = compute_stats([])
        assert stats["confirm_success_rate"] == 0.0
        assert stats["be_trigger_rate"] == 0.0

    def test_fake_breakout_rate_from_fake_breakout_field(self) -> None:
        records = [
            _trade(confirm_success=False, fake_breakout=True),
            _trade(confirm_success=True,  fake_breakout=False),
            _trade(confirm_success=True,  fake_breakout=False),
        ]
        stats = compute_stats(records)
        assert abs(stats["fake_breakout_rate"] - (1.0 / 3.0)) < 1e-9

    def test_stats_key_names(self) -> None:
        """Ensure the result dict uses the canonical P0-007 field names."""
        stats = compute_stats([_trade()])
        assert "be_trigger_rate" in stats, "'be_trigger_rate' key missing (old name was 'be_rate')"
        assert "confirm_success_rate" in stats, "'confirm_success_rate' key missing"
        assert "fake_breakout_rate" in stats, "'fake_breakout_rate' key missing (old name was 'fake_rate')"
        # Old keys must NOT exist
        assert "be_rate" not in stats, "'be_rate' is obsolete; must be renamed to 'be_trigger_rate'"
        assert "fake_rate" not in stats, "'fake_rate' is obsolete; must be renamed to 'fake_breakout_rate'"
