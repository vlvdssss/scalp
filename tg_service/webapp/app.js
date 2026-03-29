/* jshint esversion: 11 */
"use strict";

// ── Telegram WebApp SDK ────────────────────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.BackButton.hide();
}

// ── API base: read ?api= param (GitHub Pages) or fall back to same origin ────────
const _params = new URLSearchParams(window.location.search);
const API     = (_params.get("api") || "").replace(/\/$/, "") || window.location.origin;
const API_KEY = _params.get("key") || "";

// ── DOM refs ───────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Fetch helper ───────────────────────────────────────────────────────────────
async function api(path, method = "GET", body = null) {
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path + (API_KEY && method === "GET" ? `?key=${API_KEY}` : ""), opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// ── State rendering ────────────────────────────────────────────────────────────
function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}
function fmtPnl(v) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "$";
}

function applyPnlClass(el, v) {
  const n = Number(v);
  el.classList.remove("pos", "neg");
  if (!isNaN(n)) el.classList.add(n >= 0 ? "pos" : "neg");
}

function renderState(s) {
  // Badges
  const stateBadge = $("badge-state");
  const mt5Badge   = $("badge-mt5");

  const state   = (s.state || "IDLE").toUpperCase();
  const mode    = (s.mode  || "NORMAL").toUpperCase();
  const running = !!s.bot_running;
  const conn    = !!s.connected;

  stateBadge.textContent = state;
  stateBadge.className = "badge";
  if (!running)           stateBadge.classList.add("off");
  else if (mode === "SAFE_MODE") stateBadge.classList.add("safe");
  else if (running)       stateBadge.classList.add("on");

  mt5Badge.textContent = conn ? "MT5 ✓" : "MT5 ✗";
  mt5Badge.className = "badge badge-mt5 " + (conn ? "connected" : "disconnected");

  // Metrics
  $("m-bid").textContent    = fmtNum(s.bid, 2);
  $("m-ask").textContent    = fmtNum(s.ask, 2);
  $("m-spread").textContent = s.spread_points !== undefined ? fmtNum(s.spread_points, 1) + " pt" : "—";
  $("m-balance").textContent= fmtNum(s.balance, 2) + "$";
  $("m-equity").textContent = fmtNum(s.equity, 2)  + "$";
  const pnlEl = $("m-pnl");
  pnlEl.textContent = fmtPnl(s.session_pnl);
  applyPnlClass(pnlEl, s.session_pnl);

  const wr = s.daily_winrate != null ? Math.round(s.daily_winrate) + "%" : "—";
  $("m-trades").textContent = (s.daily_total_trades ?? "—");
  $("m-wr").textContent     = wr;

  // Position
  const ticket = s.position_ticket;
  const posCard  = $("pos-card");
  const posEmpty = $("pos-empty");

  if (ticket) {
    posCard.style.display  = "block";
    posEmpty.style.display = "none";

    const side = (s.position_side || "?").toUpperCase();
    const sideEl = $("pos-side");
    sideEl.textContent = side;
    sideEl.className = `pos-side ${side}`;

    $("pos-entry").textContent = fmtNum(s.entry_price, 2);
    $("pos-sl").textContent    = fmtNum(s.current_sl,  2);
    $("pos-be").textContent    = s.be_done ? "✅ Done" : "—";

    const uprEl = $("pos-pnl");
    const upr   = s.unrealised ?? s.position_pnl;
    uprEl.textContent = fmtPnl(upr);
    applyPnlClass(uprEl, upr);
  } else {
    posCard.style.display  = "none";
    posEmpty.style.display = "block";
  }

  // Pause guard
  const banner   = $("pause-banner");
  const pauseGuard = s.pause_guard;
  if (pauseGuard?.paused) {
    const mins = Math.ceil(pauseGuard.remaining_s / 60);
    $("pause-text").textContent =
      `PauseGuard active — ${mins}m remaining (${pauseGuard.pause_reason})`;
    banner.style.display = "block";
  } else {
    banner.style.display = "none";
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
}

// ── Refresh ────────────────────────────────────────────────────────────────────
async function refresh() {
  try {
    const status = await api("/status");
    renderState(status);
  } catch (e) {
    toast("⚠ " + e.message);
  }
}

// ── Button handlers ────────────────────────────────────────────────────────────
async function handleStart() {
  try {
    const r = await api("/start", "POST");
    toast("▶ " + (r.result || r.ok));
    await refresh();
  } catch (e) { toast("❌ " + e.message); }
}

async function handleStop() {
  try {
    const r = await api("/stop", "POST");
    toast("■ " + (r.result || r.ok));
    await refresh();
  } catch (e) { toast("❌ " + e.message); }
}

async function handleSafe() {
  try {
    const r = await api("/safe", "POST");
    toast(r.ok ? "🚨 Safe mode requested" : "⚠ Not running");
    await refresh();
  } catch (e) { toast("❌ " + e.message); }
}

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-start").addEventListener("click",   handleStart);
  $("btn-stop").addEventListener("click",    handleStop);
  $("btn-safe").addEventListener("click",    handleSafe);

  // Auto-refresh every 4 seconds
  refresh();
  setInterval(refresh, 4000);
});

