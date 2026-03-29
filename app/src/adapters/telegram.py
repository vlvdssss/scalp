"""
TelegramGateway – sends notifications and handles commands via Telegram Bot API.

P0-003: All command handling MUST go through CoreCommandQueue.
The poll thread ONLY enqueues commands; the core trading thread drains the queue
and executes commands. No MT5 calls ever happen in this module.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    timeout_sec: int = 10
    # P1-1: ignore repeated identical commands within this window (ms)
    dedup_window_ms: float = 3000.0


@dataclass
class CoreCommand:
    """A command enqueued from Telegram/GUI for execution in the core thread."""
    cmd: str
    arg: str = ""
    chat_id: str = ""
    tg_update_id: int = 0
    source_thread_id: int = 0


class TelegramGateway:
    """
    Thread-safe Telegram notification gateway.

    P0-003 contract:
      - _poll_loop runs in a daemon thread; it ONLY puts CoreCommand objects
        into _command_queue and logs TG_COMMAND_RECEIVED / CORE_COMMAND_ENQUEUED.
      - The core trading thread calls drain_command_queue() each cycle to
        consume and execute commands, logging CORE_COMMAND_EXECUTED.
      - NO MT5 adapter calls are ever made from this class or its threads.
    """

    def __init__(self, config: TelegramConfig) -> None:
        self._cfg = config
        self._send_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=200)
        # P0-003: command queue drained by core thread
        self._command_queue: queue.Queue[CoreCommand] = queue.Queue(maxsize=100)
        self._command_handler: Optional[Callable[[str, str], None]] = None
        # P1-1: dedup — maps cmd key to last enqueue monotonic time (sec)
        self._last_cmd_ts: dict[str, float] = {}
        self._thread = threading.Thread(
            target=self._sender_loop, daemon=True, name="TelegramSender"
        )
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="TelegramPoller"
        )
        self._offset: int = 0
        self._running = False

    def start(self) -> None:
        if not self._cfg.enabled:
            return
        self._running = True
        self._thread.start()
        self._poll_thread.start()
        log.info("TelegramGateway started (chat_id=%s)", self._cfg.chat_id)

    def stop(self) -> None:
        self._running = False
        self._send_queue.put(None)  # sentinel

    def register_command_handler(self, handler: Callable[[str, str], None]) -> None:
        """handler(command: str, arg: str) – called from CORE thread only (P0-003)."""
        self._command_handler = handler

    def drain_command_queue(self) -> list[CoreCommand]:
        """
        P0-003: Called by TradingCore at the start of each cycle.
        Returns all pending commands to be executed in the core thread.
        """
        cmds: list[CoreCommand] = []
        while True:
            try:
                cmds.append(self._command_queue.get_nowait())
            except queue.Empty:
                break
        return cmds

    # ── Public notify API ─────────────────────────────────────────────────────

    def notify_armed(self, buy_price: float, sell_price: float) -> None:
        self._enqueue(f"🟡 ARMED\nBUY STOP @ {buy_price:.2f}\nSELL STOP @ {sell_price:.2f}")

    def notify_fill(self, side: str, price: float, volume: float) -> None:
        sym = "🔺" if side == "BUY" else "🔻"
        self._enqueue(f"{sym} FILL {side}\nPrice: {price:.2f}  Vol: {volume}")

    def notify_confirm_success(self, move_pts: float) -> None:
        self._enqueue(f"✅ CONFIRM OK  +{move_pts:.1f} pts")

    def notify_fake_breakout(self, move_pts: float, threshold: float) -> None:
        self._enqueue(
            f"⚠️ FAKE BREAKOUT\nMove: {move_pts:.1f} pts  Need: {threshold:.1f} pts → EXIT"
        )

    def notify_breakeven(self, sl: float) -> None:
        self._enqueue(f"🔒 BREAK-EVEN\nSL moved to {sl:.2f}")

    def notify_exit(self, reason: str, pnl_pts: float, pnl_usd: float) -> None:
        sym = "🟢" if pnl_pts >= 0 else "🔴"
        self._enqueue(
            f"{sym} EXIT [{reason}]\nP&L: {pnl_pts:+.1f} pts  ${pnl_usd:+.2f}"
        )

    def notify_safe_mode(self, reason: str) -> None:
        self._enqueue(f"🚨 SAFE MODE\n{reason}")

    def notify_disconnect(self, error_code: int, msg: str) -> None:
        self._enqueue(f"❌ DISCONNECTED [{error_code}] {msg}")

    def notify_reconnect(self) -> None:
        self._enqueue("✅ RECONNECTED")

    def send_status(self, text: str) -> None:
        self._enqueue(text)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _enqueue(self, text: str) -> None:
        if not self._cfg.enabled:
            return
        try:
            self._send_queue.put_nowait(text)
        except queue.Full:
            log.warning("Telegram send queue full, dropping message")

    def _send_message(self, text: str) -> bool:
        url = BASE_URL.format(token=self._cfg.bot_token, method="sendMessage")
        payload = {
            "chat_id": self._cfg.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=self._cfg.timeout_sec)
            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram sendMessage failed: %s", data)
                return False
            return True
        except Exception as exc:
            log.warning("Telegram HTTP error: %s", exc)
            return False

    def _sender_loop(self) -> None:
        while self._running:
            try:
                msg = self._send_queue.get(timeout=5)
                if msg is None:
                    break
                self._send_message(msg)
            except queue.Empty:
                continue
            except Exception as exc:
                log.error("TelegramGateway sender error: %s", exc)

    def _poll_loop(self) -> None:
        """
        Long-poll for commands every 3 seconds.

        P0-003: This thread MUST NOT call self._command_handler or any MT5 method.
        It ONLY puts CoreCommand objects into self._command_queue and writes logs.
        """
        url = BASE_URL.format(token=self._cfg.bot_token, method="getUpdates")
        while self._running:
            try:
                params = {"timeout": 2, "offset": self._offset, "allowed_updates": ["message"]}
                resp = requests.get(url, params=params, timeout=self._cfg.timeout_sec)
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        self._offset = max(self._offset, update["update_id"] + 1)
                        msg = update.get("message", {})
                        text = msg.get("text", "")
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        if text.startswith("/"):
                            parts = text.split(None, 1)
                            cmd = parts[0].lstrip("/").split("@")[0]
                            arg = parts[1] if len(parts) > 1 else ""
                            # P1-1: dedup — drop if same cmd received within dedup_window_ms
                            dedup_key = f"{cmd}:{arg}"
                            now_mono = time.monotonic()
                            last_ts = self._last_cmd_ts.get(dedup_key, 0.0)
                            dedup_window_sec = self._cfg.dedup_window_ms / 1000.0
                            if now_mono - last_ts < dedup_window_sec:
                                log.info(
                                    "COMMAND_IGNORED_DUPLICATE cmd=%s update_id=%d "
                                    "elapsed_ms=%.0f dedup_window_ms=%.0f",
                                    cmd, update["update_id"],
                                    (now_mono - last_ts) * 1000,
                                    self._cfg.dedup_window_ms,
                                )
                                continue
                            self._last_cmd_ts[dedup_key] = now_mono
                            # P0-003: ONLY enqueue; never call handler from this thread
                            core_cmd = CoreCommand(
                                cmd=cmd,
                                arg=arg,
                                chat_id=chat_id,
                                tg_update_id=update["update_id"],
                                source_thread_id=threading.get_ident(),
                            )
                            log.info(
                                "TG_COMMAND_RECEIVED cmd=%s update_id=%d thread_id=%d",
                                cmd, update["update_id"], core_cmd.source_thread_id,
                            )
                            try:
                                self._command_queue.put_nowait(core_cmd)
                                log.debug(
                                    "CORE_COMMAND_ENQUEUED cmd=%s queue_depth=%d",
                                    cmd, self._command_queue.qsize(),
                                )
                            except queue.Full:
                                log.warning("TG command queue full, dropping cmd=%s", cmd)
            except Exception as exc:
                log.debug("Telegram poll error: %s", exc)
            threading.Event().wait(3)
