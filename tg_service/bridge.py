"""
BotBridge — thread-safe adapter between FastAPI/TelegramBot and TradingCore.

Rules:
- DOES NOT modify any trading logic
- DOES NOT import engine internals at module level (deferred to start())
- Only calls the public TradingCore API: start/stop/request_safe_mode/get_state_snapshot
- Exposes a notification queue (threading.Queue) for the Telegram bot
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

log = logging.getLogger(__name__)

_CONFIG_PATH = Path("config/default.yaml")


# ── Config helpers ────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_cfg(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ── Event → notification text ─────────────────────────────────────────────────

_NOTIF_MAP: dict[str, Callable[[dict], str]] = {
    "FILL": lambda d: (
        f"{'🔺' if d.get('side') == 'BUY' else '🔻'} FILL {d.get('side', '?')}\n"
        f"Price: {d.get('price', '?'):.2f}  Vol: {d.get('volume', '?')}"
    ),
    "TRADE_CLOSED": lambda d: (
        f"{'🟢' if float(d.get('pnl_usd', d.get('profit', 0)) or 0) >= 0 else '🔴'} "
        f"CLOSED [{d.get('reason', '?')}]\n"
        f"P&L: {float(d.get('pnl_usd', d.get('profit', 0)) or 0):+.2f}$"
    ),
    "SAFE_MODE": lambda d: f"🚨 SAFE MODE\n{d.get('reason', '')}",
    "DISCONNECTED": lambda d: f"❌ DISCONNECTED [{d.get('error_code', '')}] {d.get('msg', '')}",
    "RECONNECTED": lambda _: "✅ RECONNECTED",
    "PREFLIGHT_FAILED": lambda d: f"⛔ PREFLIGHT FAILED\n{d.get('reason', str(d))}",
    "CYCLE_EXCEPTION": lambda d: f"⚠️ CYCLE ERROR\n{d.get('error', str(d))}",
    "BREAKEVEN": lambda d: f"🔒 BREAKEVEN — SL → {d.get('sl', '?'):.2f}",
}


def _event_to_notification(event: str, data: Any) -> Optional[str]:
    fn = _NOTIF_MAP.get(event)
    if fn is None:
        return None
    try:
        return fn(data if isinstance(data, dict) else {})
    except Exception:
        return f"[{event}]"


# ── Settings extraction / application ─────────────────────────────────────────

def extract_settings(cfg: dict) -> dict:
    tr   = cfg.get("trailing", {})
    be   = cfg.get("breakeven", {})
    en   = cfg.get("entry", {})
    risk = cfg.get("risk", {})
    pg   = cfg.get("pause_guard", {})
    streak = pg.get("streak", {})
    window = pg.get("window", {})
    return {
        # Trailing
        "trail_activation_points":    float(tr.get("trail_activation_points", 55.0)),
        "trail_stop_points":          float(tr.get("trail_stop_points", 20.0)),
        "trail_step_points":          float(tr.get("trail_step_points", 10.0)),
        # Breakeven
        "be_activation_usd":          float(be.get("be_activation_usd", 0.5)),
        "be_stop_usd":                float(be.get("be_stop_usd", 0.35)),
        # Orders
        "min_total_offset_points":    float(en.get("min_total_offset_points", 38.0)),
        "offset_abs_max_points":      float(en.get("offset_abs_max_points", 40.0)),
        "rearm_hysteresis_pts":       float(en.get("rearm_hysteresis_pts", 25.0)),
        "min_order_age_ms":           float(en.get("min_order_age_ms", 1500.0)),
        "impulse_capture_floor_pts":  float(en.get("impulse_capture_floor_pts", 20.0)),
        "impulse_capture_spread_mult":float(en.get("impulse_capture_spread_mult", 0.5)),
        # Risk
        "volume":                     float(risk.get("volume", 0.01)),
        "target_risk_usd":            float(risk.get("target_risk_usd", 0.7)),
        "emergency_sl_points":        float(risk.get("emergency_sl_points", 150.0)),
        # Pause guard — streak
        "pause_streak_enabled":       bool(streak.get("enabled", False)),
        "pause_streak_limit":         int(streak.get("loss_streak_limit", 3)),
        "pause_streak_minutes":       int(streak.get("pause_minutes", 30)),
        # Pause guard — window
        "pause_window_enabled":       bool(window.get("enabled", False)),
        "pause_window_minutes":       int(window.get("window_minutes", 60)),
        "pause_window_loss_usd":      float(window.get("loss_amount", 3.0)),
        "pause_window_pause_minutes": int(window.get("pause_minutes", 60)),
    }


def _cast(d: dict, key: str, cast_fn):
    if key in d:
        try:
            return cast_fn(d[key])
        except (ValueError, TypeError):
            pass
    return None


def apply_settings(cfg: dict, updates: dict) -> None:
    tr   = cfg.setdefault("trailing", {})
    be   = cfg.setdefault("breakeven", {})
    en   = cfg.setdefault("entry", {})
    risk = cfg.setdefault("risk", {})
    pg   = cfg.setdefault("pause_guard", {})
    streak = pg.setdefault("streak", {})
    window = pg.setdefault("window", {})

    for key, target, cast_fn in [
        ("trail_activation_points",    tr,   float),
        ("trail_stop_points",          tr,   float),
        ("trail_step_points",          tr,   float),
        ("be_activation_usd",          be,   float),
        ("be_stop_usd",                be,   float),
        ("min_total_offset_points",    en,   float),
        ("offset_abs_max_points",      en,   float),
        ("rearm_hysteresis_pts",       en,   float),
        ("min_order_age_ms",           en,   float),
        ("impulse_capture_floor_pts",  en,   float),
        ("impulse_capture_spread_mult",en,   float),
        ("volume",                     risk, float),
        ("target_risk_usd",            risk, float),
        ("emergency_sl_points",        risk, float),
    ]:
        v = _cast(updates, key, cast_fn)
        if v is not None:
            target[key] = v

    # Pause guard
    if "pause_streak_enabled"  in updates: streak["enabled"]          = bool(updates["pause_streak_enabled"])
    if "pause_streak_limit"    in updates: streak["loss_streak_limit"] = int(updates["pause_streak_limit"])
    if "pause_streak_minutes"  in updates: streak["pause_minutes"]     = int(updates["pause_streak_minutes"])
    if "pause_window_enabled"  in updates: window["enabled"]           = bool(updates["pause_window_enabled"])
    if "pause_window_minutes"  in updates: window["window_minutes"]    = int(updates["pause_window_minutes"])
    if "pause_window_loss_usd" in updates: window["loss_amount"]       = float(updates["pause_window_loss_usd"])
    if "pause_window_pause_minutes" in updates: window["pause_minutes"]= int(updates["pause_window_pause_minutes"])


# ── BotBridge ─────────────────────────────────────────────────────────────────

class BotBridge:
    """
    Thread-safe bridge between FastAPI/TelegramBot and TradingCore.
    TradingCore is imported lazily (only when start() is called).
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._core   = None          # TradingCore instance
        self._running = False
        self._state: dict = {"state": "IDLE", "mode": "NORMAL", "connected": False}
        # Listeners registered by pause_guard / bot
        self._event_listeners: list[Callable[[str, Any], None]] = []
        # Thread-safe notification queue for Telegram bot
        self.notify_queue: queue.Queue[str] = queue.Queue(maxsize=200)

    # ── Listener registration ──────────────────────────────────────────────────

    def add_event_listener(self, fn: Callable[[str, Any], None]) -> None:
        self._event_listeners.append(fn)

    # ── Internal event dispatch ───────────────────────────────────────────────

    def _on_event(self, event: str, data: Any) -> None:
        # Update cached state
        if event == "state_update" and isinstance(data, dict):
            with self._lock:
                self._state = dict(data)

        # Dispatch to listeners (e.g. PauseGuard)
        for fn in self._event_listeners:
            try:
                fn(event, data)
            except Exception as exc:
                log.error("BotBridge listener error: %s", exc)

        # Enqueue Telegram notification
        msg = _event_to_notification(event, data)
        if msg:
            try:
                self.notify_queue.put_nowait(msg)
            except queue.Full:
                pass

    # ── Public control API ────────────────────────────────────────────────────

    def start(self) -> str:
        """Returns 'already_running' | 'started' | raises on error."""
        with self._lock:
            if self._running and self._core is not None:
                return "already_running"
            try:
                cfg = load_cfg()
                from app.src.core.engine import TradingCore  # deferred import
                self._core = TradingCore(cfg, ui_callback=self._on_event)
                self._core.start()
                self._running = True
                log.info("BotBridge: TradingCore started")
                return "started"
            except Exception:
                self._core = None
                self._running = False
                raise

    def stop(self) -> str:
        """Returns 'not_running' | 'stopped' | raises on error."""
        with self._lock:
            if not self._running or self._core is None:
                return "not_running"
            try:
                self._core.stop()
                self._core = None
                self._running = False
                self._state = {"state": "IDLE", "mode": "NORMAL", "connected": False}
                log.info("BotBridge: TradingCore stopped")
                return "stopped"
            except Exception:
                raise

    def safe_mode(self) -> bool:
        with self._lock:
            if self._core is None:
                return False
            try:
                self._core.request_safe_mode("telegram_api")
                return True
            except Exception as exc:
                log.error("BotBridge safe_mode error: %s", exc)
                return False

    def is_running(self) -> bool:
        return self._running

    # ── State & settings ──────────────────────────────────────────────────────

    def get_state(self) -> dict:
        with self._lock:
            s = dict(self._state)
        s["bot_running"] = self._running
        return s

    def get_settings(self) -> dict:
        return extract_settings(load_cfg())

    def update_settings(self, updates: dict) -> None:
        cfg = load_cfg()
        apply_settings(cfg, updates)
        save_cfg(cfg)
        log.info("BotBridge: settings updated — restart bot to apply")
