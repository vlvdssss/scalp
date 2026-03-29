"""
FastAPI backend — REST API over BotBridge.

Exposes:
  GET  /health
  GET  /status             full state + pause_guard status
  GET  /metrics            numeric metrics only
  GET  /position           open position fields
  GET  /settings           all configurable params (flat dict)
  POST /start              start TradingCore
  POST /stop               stop TradingCore
  POST /safe               enter safe mode
  POST /update-settings    update config/default.yaml
  Static /webapp           Mini App SPA files

Security: optional X-API-Key header (set via API_KEY env var).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger(__name__)

app = FastAPI(title="Scalper Telegram API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Serve Mini App from same process
_WEBAPP_DIR = Path(__file__).parent / "webapp"
if _WEBAPP_DIR.exists():
    app.mount("/webapp", StaticFiles(directory=str(_WEBAPP_DIR), html=True), name="webapp")


# ── Bridge / guard singletons (injected by run_tg_service.py) ─────────────────

_bridge = None
_pause_guard = None


def set_bridge(bridge, pause_guard=None) -> None:
    global _bridge, _pause_guard
    _bridge      = bridge
    _pause_guard = pause_guard


def get_bridge():
    return _bridge


# ── API key guard ──────────────────────────────────────────────────────────────

_API_KEY: str = ""


def init_api_key(key: str) -> None:
    global _API_KEY
    _API_KEY = key


def _require_key(request: Request) -> None:
    if not _API_KEY:
        return
    key = request.headers.get("X-API-Key") or request.query_params.get("key", "")
    if key != _API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── GET ────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/status")
async def get_status(request: Request):
    _require_key(request)
    if _bridge is None:
        return {"bot_running": False, "state": "IDLE", "connected": False}
    state = _bridge.get_state()
    if _pause_guard is not None:
        state["pause_guard"] = _pause_guard.get_status()
    return state


@app.get("/metrics")
async def get_metrics(request: Request):
    _require_key(request)
    if _bridge is None:
        return {}
    s = _bridge.get_state()
    return {
        "balance":           s.get("balance", 0),
        "equity":            s.get("equity", 0),
        "session_pnl":       s.get("session_pnl", 0),
        "daily_total_trades":s.get("daily_total_trades", 0),
        "daily_winrate":     s.get("daily_winrate", 0),
        "spread_points":     s.get("spread_points", 0),
        "bid":               s.get("bid", 0),
        "ask":               s.get("ask", 0),
        "connected":         s.get("connected", False),
        "bot_running":       s.get("bot_running", False),
    }


@app.get("/position")
async def get_position(request: Request):
    _require_key(request)
    if _bridge is None:
        return {}
    s = _bridge.get_state()
    return {
        "ticket":      s.get("position_ticket"),
        "side":        s.get("position_side"),
        "entry_price": s.get("entry_price"),
        "current_sl":  s.get("current_sl"),
        "be_done":     s.get("be_done", False),
        "unrealised":  s.get("position_pnl", s.get("unrealised_pnl")),
    }


@app.get("/settings")
async def get_settings(request: Request):
    _require_key(request)
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not ready")
    return _bridge.get_settings()


# ── POST ───────────────────────────────────────────────────────────────────────

@app.post("/start")
async def post_start(request: Request):
    _require_key(request)
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not ready")
    try:
        result = _bridge.start()
        return {"ok": True, "result": result}
    except Exception as exc:
        log.error("/start error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/stop")
async def post_stop(request: Request):
    _require_key(request)
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not ready")
    try:
        result = _bridge.stop()
        return {"ok": True, "result": result}
    except Exception as exc:
        log.error("/stop error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/safe")
async def post_safe(request: Request):
    _require_key(request)
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not ready")
    ok = _bridge.safe_mode()
    return {"ok": ok}


class SettingsBody(BaseModel):
    settings: dict


@app.post("/update-settings")
async def post_update_settings(body: SettingsBody, request: Request):
    _require_key(request)
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not ready")
    try:
        _bridge.update_settings(body.settings)
        return {"ok": True}
    except Exception as exc:
        log.error("/update-settings error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
