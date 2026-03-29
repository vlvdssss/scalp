"""
Entry point for the Telegram service.

Starts:
  1. BotBridge (wraps TradingCore, controls start/stop/safe)
  2. PauseGuard (event listener, auto safe-mode on loss streak/window)
  3. FastAPI backend (uvicorn, serves REST API + /webapp Mini App)
  4. Telegram bot (python-telegram-bot 20.x, async polling)

Usage:
    python run_tg_service.py

Requires:
    .env or environment variables:
        BOT_TOKEN          — from @BotFather
        ALLOWED_USER_IDS   — comma-separated Telegram user IDs
        WEBAPP_URL         — public HTTPS URL to /webapp/index.html
        API_HOST           — default 0.0.0.0
        API_PORT           — default 8100
        API_KEY            — optional REST API key

NOTE: Do NOT run this simultaneously with run_gui.ps1 — both would
      try to own the MT5 connection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Workspace root on sys.path so `app.src.core` resolves ─────────────────────
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Load .env before anything else ────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed — rely on real env vars

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tg_service")


def _get_allowed_ids() -> set[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    if not ids:
        log.warning(
            "ALLOWED_USER_IDS is empty — bot is open to ANYONE. "
            "Set it in .env to restrict access."
        )
    return ids


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    # ── Env ───────────────────────────────────────────────────────────────────
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        log.error("BOT_TOKEN not set. Create a .env file or set the environment variable.")
        sys.exit(1)

    allowed_ids  = _get_allowed_ids()
    webapp_url   = os.getenv("WEBAPP_URL", "")
    api_url      = os.getenv("API_URL", "")   # tunnel URL (set by run_with_cloudflare.ps1)
    api_host     = os.getenv("API_HOST", "0.0.0.0")
    api_port     = int(os.getenv("API_PORT", "8100"))
    api_key      = os.getenv("API_KEY", "")

    # ── Bridge + guard ────────────────────────────────────────────────────────
    from tg_service.bridge import BotBridge
    from tg_service.pause_guard import PauseGuard

    bridge      = BotBridge()
    pause_guard = PauseGuard(bridge)
    bridge.add_event_listener(pause_guard.on_event)

    # ── Backend ───────────────────────────────────────────────────────────────
    from tg_service.backend import app as fastapi_app, set_bridge, init_api_key
    set_bridge(bridge, pause_guard)
    init_api_key(api_key)

    import uvicorn
    uv_config = uvicorn.Config(
        fastapi_app,
        host=api_host,
        port=api_port,
        log_level="warning",
    )
    uv_server = uvicorn.Server(uv_config)

    # ── Telegram bot ──────────────────────────────────────────────────────────
    from tg_service.bot import build_application, configure as bot_configure
    bot_configure(bridge, pause_guard, allowed_ids, webapp_url, api_url, api_key)
    tg_app = build_application(bot_token)

    log.info("Starting FastAPI on %s:%d", api_host, api_port)
    if webapp_url:
        log.info("Mini App URL: %s", webapp_url)
    else:
        log.warning("WEBAPP_URL not set — Mini App button will be hidden in bot")

    log.info("Starting Telegram bot (polling)...")

    # Run FastAPI server + PTB bot concurrently
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)

    log.info("Service ready. Press Ctrl+C to stop.")

    try:
        await uv_server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        if bridge.is_running():
            bridge.stop()
        log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
