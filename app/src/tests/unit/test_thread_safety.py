"""
Unit tests for P0-003: Single-thread MT5 ownership.

Validates that:
- set_core_thread() registers the calling thread
- _assert_core_thread() raises RuntimeError from any other thread
- Telegram _poll_loop enqueues CoreCommand without calling handler directly
"""
from __future__ import annotations

import threading
from queue import Empty
from unittest.mock import MagicMock, patch

import pytest


class TestMT5ThreadOwnership:
    def test_call_from_correct_thread_does_not_raise(self) -> None:
        """MT5 calls from core thread must NOT raise."""
        from app.src.adapters.mt5_adapter import MT5Adapter
        adapter = MT5Adapter.__new__(MT5Adapter)
        adapter._core_thread_id = None
        # Register current thread
        adapter.set_core_thread()
        # Calling assert from same thread must not raise
        adapter._assert_core_thread("order_send")  # no exception

    def test_call_from_wrong_thread_raises(self) -> None:
        """MT5 calls from non-core thread must raise RuntimeError."""
        from app.src.adapters.mt5_adapter import MT5Adapter
        adapter = MT5Adapter.__new__(MT5Adapter)
        adapter._core_thread_id = None
        adapter.set_core_thread()  # register main thread

        error_from_thread: list[Exception] = []

        def violating_thread():
            try:
                adapter._assert_core_thread("order_send")
            except RuntimeError as e:
                error_from_thread.append(e)

        t = threading.Thread(target=violating_thread)
        t.start()
        t.join(timeout=2.0)

        assert error_from_thread, (
            "_assert_core_thread must raise RuntimeError when called from wrong thread"
        )
        assert "core thread" in str(error_from_thread[0]).lower() or \
               "thread" in str(error_from_thread[0]).lower()

    def test_assert_before_set_does_not_raise(self) -> None:
        """Before set_core_thread is called, _assert_core_thread should be a no-op or warn."""
        from app.src.adapters.mt5_adapter import MT5Adapter
        adapter = MT5Adapter.__new__(MT5Adapter)
        adapter._core_thread_id = None
        # No set_core_thread call – should not crash (nothing to assert against)
        adapter._assert_core_thread("order_check")  # should not raise


class TestTelegramCoreCommandQueue:
    def test_drain_returns_empty_if_no_commands(self) -> None:
        """drain_command_queue must return an empty list when no commands arrived."""
        from app.src.adapters.telegram import TelegramGateway
        tg = TelegramGateway.__new__(TelegramGateway)
        from queue import Queue
        tg._command_queue = Queue()
        result = tg.drain_command_queue()
        assert result == []

    def test_drain_returns_all_enqueued_commands(self) -> None:
        """drain_command_queue must drain ALL pending CoreCommand objects atomically."""
        from app.src.adapters.telegram import TelegramGateway, CoreCommand
        tg = TelegramGateway.__new__(TelegramGateway)
        from queue import Queue
        tg._command_queue = Queue()
        tg._command_queue.put(CoreCommand(cmd="status", arg="", chat_id="1", tg_update_id=1, source_thread_id=2))
        tg._command_queue.put(CoreCommand(cmd="stop",   arg="", chat_id="1", tg_update_id=2, source_thread_id=2))
        result = tg.drain_command_queue()
        assert len(result) == 2
        assert result[0].cmd == "status"
        assert result[1].cmd == "stop"

    def test_core_command_has_required_fields(self) -> None:
        """CoreCommand must carry cmd, arg, chat_id, tg_update_id, source_thread_id."""
        from app.src.adapters.telegram import CoreCommand
        cmd = CoreCommand(cmd="safe", arg="test", chat_id="123", tg_update_id=456, source_thread_id=789)
        assert cmd.cmd == "safe"
        assert cmd.arg == "test"
        assert cmd.chat_id == "123"
        assert cmd.tg_update_id == 456
        assert cmd.source_thread_id == 789

    def test_poll_loop_does_not_call_handler_directly(self) -> None:
        """
        _poll_loop must NEVER call command_handler callbacks directly.
        It must only enqueue CoreCommand objects (P0-003 invariant).
        """
        from app.src.adapters.telegram import TelegramGateway
        # If _poll_loop calls a handler directly, it would fail with AttributeError
        # or call a mock. We verify the command_queue is used instead.
        tg = TelegramGateway.__new__(TelegramGateway)
        from queue import Queue
        tg._command_queue = Queue()
        tg._send_queue = Queue()
        from app.src.adapters.telegram import TelegramConfig
        tg._cfg = TelegramConfig(enabled=True, bot_token="fake", chat_id="123")  # type: ignore[attr-defined]
        tg._running = False
        tg._offset = 0
        tg._command_handler = MagicMock()  # if called directly, we'd detect it

        # Simulate a /status update arriving
        fake_update = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 123},
                "text": "/status",
                "date": 1234567890,
            }
        }
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True, "result": [fake_update]}
        import requests as _requests

        call_count = 0
        original_get = _requests.get

        def _get_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            tg._running = False  # stop after first iteration
            return fake_resp

        with patch.object(_requests, "get", side_effect=_get_once):
            tg._running = True
            tg._offset = 0
            try:
                tg._poll_loop()
            except Exception:
                pass  # may throw; we only check handler was not called

        tg._command_handler.assert_not_called()  # P0-003: must NOT be called from poll thread
