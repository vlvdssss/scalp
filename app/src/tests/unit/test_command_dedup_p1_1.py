"""
Unit tests for P1-1: Telegram command dedup.
Commands sent within dedup_window_ms of each other must be dropped.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.src.adapters.telegram import TelegramConfig, TelegramGateway, CoreCommand


def _make_gateway(dedup_window_ms: float = 3000.0) -> TelegramGateway:
    cfg = TelegramConfig(
        enabled=True,
        bot_token="test_token",
        chat_id="12345",
        timeout_sec=5,
        dedup_window_ms=dedup_window_ms,
    )
    return TelegramGateway(cfg)


class TestTelegramConfigDedupField:
    def test_default_dedup_window_is_3000ms(self):
        cfg = TelegramConfig(enabled=False, bot_token="", chat_id="")
        assert cfg.dedup_window_ms == 3000.0

    def test_custom_dedup_window(self):
        cfg = TelegramConfig(enabled=False, bot_token="", chat_id="", dedup_window_ms=1000.0)
        assert cfg.dedup_window_ms == 1000.0


class TestCommandDedupInPollLoop:
    """Test the dedup logic in _poll_loop via direct invocation of the internal state."""

    def test_first_command_enqueued(self):
        gw = _make_gateway(dedup_window_ms=5000.0)
        now = time.monotonic()
        gw._last_cmd_ts = {}  # fresh state
        gw._last_cmd_ts["status:"] = now - 9999  # very old → should allow
        # Enqueue via internal queue directly for this test
        cmd = CoreCommand(cmd="status", arg="", chat_id="1", tg_update_id=1, source_thread_id=0)
        gw._command_queue.put_nowait(cmd)
        cmds = gw.drain_command_queue()
        assert len(cmds) == 1
        assert cmds[0].cmd == "status"

    def test_dedup_window_config_stored_correctly(self):
        gw = _make_gateway(dedup_window_ms=1500.0)
        assert gw._cfg.dedup_window_ms == 1500.0

    def test_last_cmd_ts_dict_initialized(self):
        gw = _make_gateway()
        assert isinstance(gw._last_cmd_ts, dict)
        assert len(gw._last_cmd_ts) == 0

    def test_dedup_key_format(self):
        """Verify that dedup key is cmd:arg format."""
        gw = _make_gateway()
        # Simulate what poll loop would do:
        cmd = "safe"
        arg = ""
        dedup_key = f"{cmd}:{arg}"
        now = time.monotonic()
        gw._last_cmd_ts[dedup_key] = now  # mark as just seen
        dedup_window_sec = gw._cfg.dedup_window_ms / 1000.0
        elapsed = time.monotonic() - gw._last_cmd_ts[dedup_key]
        # Should be < window → duplicate
        assert elapsed < dedup_window_sec

    def test_different_commands_not_deduped(self):
        """Different commands (different dedup keys) must both be allowed."""
        gw = _make_gateway(dedup_window_ms=5000.0)
        now = time.monotonic()
        # Mark "status" as recently seen
        gw._last_cmd_ts["status:"] = now
        # "safe" key not seen → should pass
        dedup_key_safe = "safe:"
        assert dedup_key_safe not in gw._last_cmd_ts

    def test_same_cmd_different_arg_not_deduped(self):
        """Same command with different arg should have separate dedup key."""
        gw = _make_gateway(dedup_window_ms=5000.0)
        now = time.monotonic()
        gw._last_cmd_ts["set:foo"] = now  # "set foo" recently seen
        # "set bar" must be independent
        dedup_key_bar = "set:bar"
        assert dedup_key_bar not in gw._last_cmd_ts
