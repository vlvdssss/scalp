"""
analytics.py – online performance metrics computed from trade ledger.

Metrics (P0-007 corrected definitions):
  confirm_success_rate  = trades with confirm_success==True / total
  be_trigger_rate       = trades with be_triggered==True / total
                          (NOT the same as confirm_success_rate)
  fake_breakout_rate    = trades with fake_breakout==True / total
  Profit Factor         = gross_profit / gross_loss
  Winrate               = wins / total
  E_R                   = mean(pnl_R)
  Spread_cost_ratio     = avg(spread_entry_points) / avg(|pnl_points|)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class PerformanceMetrics:
    total_trades: int           = 0
    wins: int                   = 0
    winrate: float              = 0.0
    avg_pnl_pts: float          = 0.0
    avg_R: float                = 0.0
    profit_factor: float        = 0.0
    # P0-007: corrected – confirm_success_rate and be_trigger_rate are independent metrics
    confirm_success_rate: float = 0.0   # fraction of trades where confirm succeeded
    be_trigger_rate: float      = 0.0   # fraction of trades where BE was triggered
    fake_breakout_rate: float   = 0.0   # fraction of trades that were fake breakouts
    spread_cost_ratio: float    = 0.0
    gross_profit: float         = 0.0
    gross_loss: float           = 0.0


def compute_metrics(rows: list[dict]) -> PerformanceMetrics:
    """
    Compute performance metrics from a list of trade record dicts.

    P0-007: `be_trigger_rate` uses the `be_triggered` field (bool),
    NOT `confirm_success`. They are independent metrics.
    """
    m = PerformanceMetrics()
    if not rows:
        return m

    m.total_trades = len(rows)
    pnl_pts_list: list[float] = []
    pnl_r_list:   list[float] = []
    spread_list:  list[float] = []
    confirm_success_count = 0
    be_trigger_count      = 0
    fake_count            = 0
    gross_profit  = 0.0
    gross_loss    = 0.0

    for r in rows:
        pts  = float(r.get("pnl_points", 0) or 0)
        pnl  = float(r.get("pnl_money", 0) or 0)
        R    = float(r.get("pnl_R", 0) or 0)
        spr  = float(r.get("spread_entry_points", 0) or 0)
        # P0-007: confirm_success and be_triggered are SEPARATE boolean columns
        cf   = int(r.get("confirm_success", 0) or 0)
        be   = int(r.get("be_triggered", 0) or 0)   # was: "confirm_success" – WRONG
        fk   = int(r.get("fake_breakout", 0) or 0)

        pnl_pts_list.append(pts)
        pnl_r_list.append(R)
        spread_list.append(spr)

        if pnl > 0:
            gross_profit += pnl
            m.wins += 1
        else:
            gross_loss += abs(pnl)

        if cf:
            confirm_success_count += 1
        if be:
            be_trigger_count += 1
        if fk:
            fake_count += 1

    m.gross_profit         = gross_profit
    m.gross_loss           = gross_loss
    m.profit_factor        = gross_profit / max(gross_loss, 1e-9)
    m.winrate              = m.wins / m.total_trades
    m.avg_pnl_pts          = sum(pnl_pts_list) / len(pnl_pts_list)
    m.avg_R                = sum(pnl_r_list) / len(pnl_r_list)
    m.confirm_success_rate = confirm_success_count / m.total_trades
    m.be_trigger_rate      = be_trigger_count / m.total_trades   # P0-007 fix
    m.fake_breakout_rate   = fake_count / m.total_trades

    avg_spr     = sum(spread_list) / max(len(spread_list), 1)
    avg_abs_pts = sum(abs(p) for p in pnl_pts_list) / max(len(pnl_pts_list), 1)
    m.spread_cost_ratio = avg_spr / max(avg_abs_pts, 1e-9)

    return m


def compute_stats(rows: list[dict]) -> dict:
    """Dict-returning wrapper around compute_metrics. Used by tests and UI."""
    return asdict(compute_metrics(rows))
