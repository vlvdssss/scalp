"""
Telegram bot — python-telegram-bot 20.x (async).

Commands:
  /start    — welcome menu with inline keyboard
  /status   — current state summary
  /stop_bot — stop TradingCore
  /safe     — enter safe mode

Security: only users in ALLOWED_USER_IDS (from .env) can interact.
Inline keyboard with START / STOP / SAFE / STATUS / 📊 Panel buttons.
Proactive notifications: bridge events → notify_queue → periodic job sends messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

log = logging.getLogger(__name__)

# Injected from run_tg_service.py
_bridge       = None
_pause_guard  = None
_allowed_ids: set[int] = set()
_webapp_url   = ""   # GitHub Pages (or local) URL to index.html
_api_url      = ""   # Cloudflare tunnel URL for REST API calls
_api_key      = ""   # REST API key appended as ?key= param


def configure(bridge, pause_guard, allowed_ids: set[int],
              webapp_url: str, api_url: str = "", api_key: str = "") -> None:
    global _bridge, _pause_guard, _allowed_ids, _webapp_url, _api_url, _api_key
    _bridge      = bridge
    _pause_guard = pause_guard
    _allowed_ids = allowed_ids
    _webapp_url  = webapp_url
    _api_url     = api_url
    _api_key     = api_key


def _get_webapp_url() -> str:
    """Build Mini App URL with ?api= and ?key= params for GitHub Pages hosting."""
    if not _webapp_url:
        return ""
    from urllib.parse import quote
    params = []
    if _api_url:
        params.append(f"api={quote(_api_url, safe='')}")
    if _api_key:
        params.append(f"key={quote(_api_key, safe='')}")
    if params:
        sep = "&" if "?" in _webapp_url else "?"
        return _webapp_url + sep + "&".join(params)
    return _webapp_url


# ── Security ───────────────────────────────────────────────────────────────────

def _allowed(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if not _allowed_ids:
        return True  # no whitelist → open (dev mode only)
    return user.id in _allowed_ids


def _deny(ctx: ContextTypes.DEFAULT_TYPE):
    return ctx.bot.send_message(
        chat_id=ctx._chat_id if hasattr(ctx, "_chat_id") else 0,
        text="⛔ Not authorised.",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("▶ START",  callback_data="cb_start"),
            InlineKeyboardButton("■ STOP",   callback_data="cb_stop"),
            InlineKeyboardButton("🚨 SAFE",  callback_data="cb_safe"),
        ],
        [
            InlineKeyboardButton("📋 STATUS", callback_data="cb_status"),
        ],
    ]
    if _webapp_url:
        buttons.append([
            InlineKeyboardButton(
                "📊 Открыть панель",
                web_app=WebAppInfo(url=_get_webapp_url()),
            )
        ])
    return InlineKeyboardMarkup(buttons)


def _fmt_state() -> str:
    if _bridge is None:
        return "Bridge not initialised."

    s = _bridge.get_state()
    running = s.get("bot_running", False)
    state   = s.get("state", "?")
    mode    = s.get("mode", "?")
    conn    = "✅" if s.get("connected") else "❌"
    bal     = s.get("balance", 0) or 0
    eq      = s.get("equity", 0) or 0
    pnl     = s.get("session_pnl", 0) or 0
    trades  = s.get("daily_total_trades", 0) or 0
    wr      = s.get("daily_winrate", 0) or 0
    spread  = s.get("spread_points", 0) or 0

    ticket  = s.get("position_ticket")
    pos_text = "—"
    if ticket:
        side   = s.get("position_side", "?")
        entry  = s.get("entry_price", 0) or 0
        sl     = s.get("current_sl", 0) or 0
        be     = "✅" if s.get("be_done") else "—"
        pos_text = f"{side} @{entry:.2f}  SL {sl:.2f}  BE {be}"

    pg_text = ""
    if _pause_guard:
        pg = _pause_guard.get_status()
        if pg["paused"]:
            mins = pg["remaining_s"] // 60
            pg_text = f"\n🔴 PauseGuard: paused {mins}m ({pg['pause_reason']})"

    pnl_sign = "+" if pnl >= 0 else ""
    pnl_icon = "🟢" if pnl >= 0 else "🔴"

    lines = [
        f"*XAUUSD Scalper*",
        f"MT5 {conn}  |  Engine: {'✅ ON' if running else '⬜ OFF'}",
        f"State: `{state}`  Mode: `{mode}`",
        "",
        f"💰 Balance:  `{bal:.2f}$`",
        f"💼 Equity:   `{eq:.2f}$`",
        f"{pnl_icon} Session:  `{pnl_sign}{pnl:.2f}$`",
        f"📊 Trades:   `{trades}`  WR `{wr:.0f}%`",
        f"📈 Spread:   `{spread:.1f} pts`",
        "",
        f"📌 Position: {pos_text}",
        pg_text,
    ]
    return "\n".join(l for l in lines if l is not None)


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return
    await update.message.reply_text(
        _fmt_state(),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_make_keyboard(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return
    await update.message.reply_text(
        _fmt_state(),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_make_keyboard(),
    )


async def cmd_stop_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return
    if _bridge is None:
        await update.message.reply_text("⚠ Bridge not ready.")
        return
    result = _bridge.stop()
    await update.message.reply_text(f"🛑 stop → `{result}`", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_safe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return
    if _bridge is None:
        await update.message.reply_text("⚠ Bridge not ready.")
        return
    ok = _bridge.safe_mode()
    await update.message.reply_text("🚨 Safe mode requested." if ok else "⚠ Not running.")


# ── Callback query handlers ────────────────────────────────────────────────────

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if user is None or (_allowed_ids and user.id not in _allowed_ids):
        await query.edit_message_text("⛔ Not authorised.")
        return

    data = query.data
    if _bridge is None:
        await query.edit_message_text("⚠ Bridge not ready.")
        return

    if data == "cb_start":
        try:
            result = _bridge.start()
            text = f"▶ start → `{result}`"
        except Exception as exc:
            text = f"❌ {exc}"

    elif data == "cb_stop":
        try:
            result = _bridge.stop()
            text = f"■ stop → `{result}`"
        except Exception as exc:
            text = f"❌ {exc}"

    elif data == "cb_safe":
        ok   = _bridge.safe_mode()
        text = "🚨 Safe mode requested." if ok else "⚠ Not running."

    elif data == "cb_status":
        text = _fmt_state()
    else:
        text = "?"

    try:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_make_keyboard(),
        )
    except Exception:
        # Message unchanged — just ignore
        pass


# ── Notification job ───────────────────────────────────────────────────────────

async def _notification_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if _bridge is None or not _allowed_ids:
        return
    while True:
        try:
            msg = _bridge.notify_queue.get_nowait()
        except Exception:
            break
        for uid in _allowed_ids:
            try:
                await ctx.bot.send_message(chat_id=uid, text=msg)
            except Forbidden:
                log.warning("Bot forbidden for user %d", uid)
            except Exception as exc:
                log.error("Notification send error: %s", exc)


# ── Application builder ────────────────────────────────────────────────────────

def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start",    cmd_start))
    application.add_handler(CommandHandler("status",   cmd_status))
    application.add_handler(CommandHandler("stop_bot", cmd_stop_bot))
    application.add_handler(CommandHandler("safe",     cmd_safe))
    application.add_handler(CallbackQueryHandler(cb_handler))

    # Poll notify_queue every 3 seconds
    application.job_queue.run_repeating(
        _notification_job,
        interval=3,
        first=5,
    )

    return application
