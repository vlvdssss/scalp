"""
persistence.py – SQLite trade ledger and JSONL event logger.

Trade ledger table (trades):
  Stores all completed trades with full analytics fields.

JSONL logger:
  Appends one JSON object per observable event.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id: str               # UUID or string ticket
    open_time_utc: str          # ISO-8601
    close_time_utc: str
    side: str                   # BUY / SELL
    volume: float
    entry_price: float
    exit_price: float
    slippage_in_points: float   = 0.0
    slippage_out_points: float  = 0.0
    spread_entry_points: float  = 0.0
    spread_exit_points: float   = 0.0
    pnl_points: float           = 0.0
    pnl_money: float            = 0.0
    pnl_R: float                = 0.0
    MFE_points: float           = 0.0
    MAE_points: float           = 0.0
    confirm_success: bool       = False
    fake_breakout: bool         = False
    reason_exit: str            = ""
    # P0-002 forensics
    confirm_elapsed_ms: float             = 0.0
    confirm_ticks_used: int               = 0
    confirm_best_move_points: float       = 0.0
    confirm_threshold_points: float       = 0.0
    confirm_fail_reason: str              = ""
    # P0-007 BE tracking
    be_triggered: bool          = False
    be_time_utc: str            = ""
    be_arm_points: float        = 0.0
    be_buffer_points: float     = 0.0
    # P0-006 critical flags
    critical_flags: str         = ""   # JSON-encoded list of critical event names
    # P0-001 spec identity
    run_id: str                 = ""
    spec_version: str           = ""
    spec_hash: str              = ""


# ── JSONL Event Logger ────────────────────────────────────────────────────────

class JSONLLogger:
    """
    Appends structured JSON lines to a rotating JSONL log file.
    Thread-safe (GIL + single-thread write from TradingCore).
    """

    def __init__(self, path: str | Path, max_mb: float = 10.0, max_archives: int = 2) -> None:
        self._path = Path(path)
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._max_archives = max_archives
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)

    def log(self, event: str, data: dict) -> None:
        """Append a structured event record."""
        data["timestamp_utc_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        data["event"] = event
        try:
            line = json.dumps(data, ensure_ascii=False, default=str)
            self._fh.write(line + "\n")
        except Exception as exc:
            log.error("JSONLLogger write error: %s", exc)
        self._rotate_if_needed()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def _rotate_if_needed(self) -> None:
        try:
            if self._path.stat().st_size <= self._max_bytes:
                return
            self._fh.close()
            archive = self._path.with_name(
                self._path.stem + f".{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
            )
            self._path.rename(archive)
            self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
            self._prune_archives()
        except Exception as exc:
            log.warning("JSONLLogger rotate error: %s", exc)

    def _prune_archives(self) -> None:
        """Delete oldest archive files, keeping only self._max_archives."""
        try:
            archives = sorted(
                self._path.parent.glob(self._path.stem + ".*.jsonl"),
                key=lambda p: p.stat().st_mtime,
            )
            for old in archives[: max(0, len(archives) - self._max_archives)]:
                old.unlink()
                log.info("JSONLLogger pruned old archive: %s", old.name)
        except Exception as exc:
            log.warning("JSONLLogger prune error: %s", exc)


# ── SQLite Trade Ledger ───────────────────────────────────────────────────────

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    started_at_utc      TEXT NOT NULL,
    spec_version        TEXT DEFAULT '',
    spec_hash           TEXT DEFAULT '',
    mt5_build           INTEGER DEFAULT 0,
    mt5_package_version TEXT DEFAULT '',
    preflight_ok        INTEGER DEFAULT 0,
    preflight_reasons   TEXT DEFAULT ''
);
"""

_CREATE_TRADE_REQUESTS = """
CREATE TABLE IF NOT EXISTS trade_requests (
    req_id          TEXT PRIMARY KEY,
    trade_id        TEXT DEFAULT '',
    req_type        TEXT DEFAULT '',
    payload_hash    TEXT DEFAULT '',
    attempts        INTEGER DEFAULT 0,
    last_retcode    INTEGER DEFAULT 0,
    last_retcode_name TEXT DEFAULT '',
    created_at_utc  TEXT NOT NULL,
    resolved_at_utc TEXT DEFAULT ''
);
"""

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id                    TEXT PRIMARY KEY,
    open_time_utc               TEXT NOT NULL,
    close_time_utc              TEXT NOT NULL,
    side                        TEXT NOT NULL,
    volume                      REAL NOT NULL,
    entry_price                 REAL NOT NULL,
    exit_price                  REAL NOT NULL,
    slippage_in_points          REAL DEFAULT 0,
    slippage_out_points         REAL DEFAULT 0,
    spread_entry_points         REAL DEFAULT 0,
    spread_exit_points          REAL DEFAULT 0,
    pnl_points                  REAL DEFAULT 0,
    pnl_money                   REAL DEFAULT 0,
    pnl_R                       REAL DEFAULT 0,
    MFE_points                  REAL DEFAULT 0,
    MAE_points                  REAL DEFAULT 0,
    confirm_success             INTEGER DEFAULT 0,
    fake_breakout               INTEGER DEFAULT 0,
    reason_exit                 TEXT DEFAULT '',
    confirm_elapsed_ms          REAL DEFAULT 0,
    confirm_ticks_used          INTEGER DEFAULT 0,
    confirm_best_move_points    REAL DEFAULT 0,
    confirm_threshold_points    REAL DEFAULT 0,
    confirm_fail_reason         TEXT DEFAULT '',
    be_triggered                INTEGER DEFAULT 0,
    be_time_utc                 TEXT DEFAULT '',
    be_arm_points               REAL DEFAULT 0,
    be_buffer_points            REAL DEFAULT 0,
    critical_flags              TEXT DEFAULT '',
    run_id                      TEXT DEFAULT '',
    spec_version                TEXT DEFAULT '',
    spec_hash                   TEXT DEFAULT ''
);
"""

_CREATE_DAILY = """
CREATE VIEW IF NOT EXISTS daily_stats AS
SELECT
    DATE(open_time_utc) AS day,
    COUNT(*)            AS total_trades,
    SUM(CASE WHEN pnl_points > 0 THEN 1 ELSE 0 END) AS wins,
    ROUND(AVG(pnl_points), 2)                        AS avg_pnl_pts,
    ROUND(SUM(CASE WHEN pnl_points > 0 THEN pnl_money ELSE 0 END), 2) AS gross_profit,
    ROUND(ABS(SUM(CASE WHEN pnl_points < 0 THEN pnl_money ELSE 0 END)), 2) AS gross_loss,
    ROUND(AVG(CAST(confirm_success AS REAL)), 4)     AS confirm_rate,
    ROUND(AVG(CAST(fake_breakout AS REAL)), 4)       AS fake_rate
FROM trades
GROUP BY DATE(open_time_utc);
"""


class TradeLedger:
    """SQLite-backed trade record store."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_RUNS)
            self._conn.execute(_CREATE_TRADE_REQUESTS)
            self._conn.execute(_CREATE_TRADES)
            try:
                self._conn.execute(_CREATE_DAILY)
            except sqlite3.OperationalError:
                pass  # view already exists
            # P0-001/P0-007/P0-002: migrate existing trades table if columns missing
            self._add_column_if_missing("trades", "confirm_elapsed_ms",      "REAL DEFAULT 0")
            self._add_column_if_missing("trades", "confirm_ticks_used",       "INTEGER DEFAULT 0")
            self._add_column_if_missing("trades", "confirm_best_move_points", "REAL DEFAULT 0")
            self._add_column_if_missing("trades", "confirm_threshold_points", "REAL DEFAULT 0")
            self._add_column_if_missing("trades", "confirm_fail_reason",      "TEXT DEFAULT ''")
            self._add_column_if_missing("trades", "be_triggered",             "INTEGER DEFAULT 0")
            self._add_column_if_missing("trades", "be_time_utc",              "TEXT DEFAULT ''")
            self._add_column_if_missing("trades", "be_arm_points",            "REAL DEFAULT 0")
            self._add_column_if_missing("trades", "be_buffer_points",         "REAL DEFAULT 0")
            self._add_column_if_missing("trades", "critical_flags",           "TEXT DEFAULT ''")
            self._add_column_if_missing("trades", "run_id",                   "TEXT DEFAULT ''")
            self._add_column_if_missing("trades", "spec_version",             "TEXT DEFAULT ''")
            self._add_column_if_missing("trades", "spec_hash",                "TEXT DEFAULT ''")

    def _add_column_if_missing(self, table: str, column: str, col_type: str) -> None:
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists

    def insert_run(
        self,
        run_id: str,
        spec_version: str = "",
        spec_hash: str = "",
        mt5_build: int = 0,
        mt5_package_version: str = "",
        preflight_ok: bool = True,
        preflight_reasons: str = "",
    ) -> None:
        sql = """
        INSERT OR REPLACE INTO runs
            (run_id, started_at_utc, spec_version, spec_hash,
             mt5_build, mt5_package_version, preflight_ok, preflight_reasons)
        VALUES (?,?,?,?,?,?,?,?)
        """
        try:
            with self._conn:
                self._conn.execute(sql, (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    spec_version, spec_hash, mt5_build,
                    mt5_package_version, int(preflight_ok), preflight_reasons,
                ))
        except Exception as exc:
            log.error("TradeLedger insert_run error: %s", exc)

    def insert_trade_request(
        self,
        req_id: str,
        trade_id: str,
        req_type: str,
        payload_hash: str,
        attempts: int,
        last_retcode: int,
        last_retcode_name: str,
        resolved: bool = False,
    ) -> None:
        sql = """
        INSERT OR REPLACE INTO trade_requests
            (req_id, trade_id, req_type, payload_hash, attempts,
             last_retcode, last_retcode_name, created_at_utc, resolved_at_utc)
        VALUES (?,?,?,?,?,?,?,?,?)
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._conn:
                self._conn.execute(sql, (
                    req_id, trade_id, req_type, payload_hash, attempts,
                    last_retcode, last_retcode_name, now,
                    now if resolved else "",
                ))
        except Exception as exc:
            log.error("TradeLedger insert_trade_request error: %s", exc)

    def insert_trade(self, rec: TradeRecord) -> None:
        sql = """
        INSERT OR REPLACE INTO trades (
            trade_id, open_time_utc, close_time_utc, side, volume,
            entry_price, exit_price, slippage_in_points, slippage_out_points,
            spread_entry_points, spread_exit_points, pnl_points, pnl_money, pnl_R,
            MFE_points, MAE_points, confirm_success, fake_breakout, reason_exit,
            confirm_elapsed_ms, confirm_ticks_used, confirm_best_move_points,
            confirm_threshold_points, confirm_fail_reason,
            be_triggered, be_time_utc, be_arm_points, be_buffer_points,
            critical_flags, run_id, spec_version, spec_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        try:
            with self._conn:
                self._conn.execute(sql, (
                    rec.trade_id, rec.open_time_utc, rec.close_time_utc,
                    rec.side, rec.volume, rec.entry_price, rec.exit_price,
                    rec.slippage_in_points, rec.slippage_out_points,
                    rec.spread_entry_points, rec.spread_exit_points,
                    rec.pnl_points, rec.pnl_money, rec.pnl_R,
                    rec.MFE_points, rec.MAE_points,
                    int(rec.confirm_success), int(rec.fake_breakout),
                    rec.reason_exit,
                    rec.confirm_elapsed_ms, rec.confirm_ticks_used,
                    rec.confirm_best_move_points, rec.confirm_threshold_points,
                    rec.confirm_fail_reason,
                    int(rec.be_triggered), rec.be_time_utc,
                    rec.be_arm_points, rec.be_buffer_points,
                    rec.critical_flags,
                    rec.run_id, rec.spec_version, rec.spec_hash,
                ))
        except Exception as exc:
            log.error("TradeLedger insert_trade error: %s", exc)

    def get_today_stats(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur = self._conn.execute(
            "SELECT * FROM daily_stats WHERE day=?", (today,)
        )
        row = cur.fetchone()
        if not row:
            return {}
        d = dict(row)
        g_profit = d.get("gross_profit", 0) or 0
        g_loss   = d.get("gross_loss", 1) or 1
        d["profit_factor"] = round(g_profit / max(g_loss, 1e-9), 3)
        n = d.get("total_trades", 1) or 1
        w = d.get("wins", 0) or 0
        d["winrate"] = round(w / n, 4)
        d["net_pnl"] = round(g_profit - g_loss, 2)
        return d

    def get_all_stats(self) -> dict:
        cur = self._conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN pnl_points > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl_points > 0 THEN pnl_money ELSE 0 END) AS gross_profit,
                ABS(SUM(CASE WHEN pnl_points < 0 THEN pnl_money ELSE 0 END)) AS gross_loss,
                AVG(pnl_R)                          AS avg_R,
                AVG(pnl_points)                     AS avg_pts,
                AVG(CAST(confirm_success AS REAL))  AS confirm_success_rate,
                AVG(CAST(be_triggered AS REAL))     AS be_trigger_rate,
                AVG(CAST(fake_breakout AS REAL))    AS fake_breakout_rate,
                AVG(spread_entry_points)            AS avg_entry_spread
            FROM trades
        """)
        row = cur.fetchone()
        if not row:
            return {}
        d = dict(row)
        g_profit = d.get("gross_profit", 0) or 0
        g_loss   = d.get("gross_loss", 1) or 1
        d["profit_factor"] = round(g_profit / max(g_loss, 1e-9), 3)
        n = d.get("total", 1) or 1
        w = d.get("wins", 0) or 0
        d["winrate"] = round(w / n, 4)
        return d

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM trades ORDER BY close_time_utc DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
