"""
engine_builders.py

TradingCore mixin for subsystem construction during initialization.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.src.adapters.telegram import TelegramConfig, TelegramGateway
from app.src.core.feature_logger import FeatureLogger
from app.src.core.micro_guard import MicroGuard, MicroGuardConfig
from app.src.core.models_atr import ATRConfig, ATRModel
from app.src.core.models_spread import SpreadConfig, SpreadMedianModel
from app.src.core.order_manager import OrderManager, PendingConfig
from app.src.core.persistence import JSONLLogger, TradeLedger
from app.src.core.position_manager import PositionConfig, PositionManager
from app.src.core.risk import BEConfig, ConfirmConfig, EntryConfig, TrailConfig
from app.src.core.session_control import SessionConfig, SessionControl, SessionWindow


class _BuildersHost(Protocol):
    _cfg: dict[str, Any]
    _adapter: Any
    _state: Any

    def _on_retcode_policy(self, action: str, retcode: int) -> None: ...
    def _on_position_event(self, event: str, data: dict) -> None: ...
    def _handle_telegram_command(self, cmd: str, arg: str) -> None: ...


class BuildersMixin:
    def _build_models(self: _BuildersHost) -> None:
        cfg = self._cfg

        self._spread_model = SpreadMedianModel(SpreadConfig(
            rolling_window_sec=cfg["spread"]["rolling_window_sec"],
            k_maxspread=cfg["spread"]["k_maxspread"],
            maxspread_min=cfg["spread"]["maxspread_min"],
            maxspread_cap=cfg["spread"]["maxspread_cap"],
            k_spike=cfg["spread"]["k_spike"],
        ))

        self._atr_model = ATRModel(ATRConfig(
            period=cfg["atr"]["period"],
            bars_fetch=cfg["atr"]["bars_fetch"],
            atr_min_points=cfg["atr"]["atr_min_points"],
            ratio_max=cfg["atr"]["ratio_max"],
        ))

        trail_atr_period = int(cfg["trailing"].get("trail_atr_period", 14))
        trail_atr_bars = int(cfg["trailing"].get("trail_atr_bars_fetch", 100))
        self._trail_atr_model = ATRModel(ATRConfig(
            period=trail_atr_period,
            bars_fetch=trail_atr_bars,
            atr_min_points=cfg["atr"].get("atr_min_points", 50.0),
            ratio_max=cfg["atr"].get("ratio_max", 0.4),
        ))

    def _build_trade_managers(self: _BuildersHost) -> None:
        cfg = self._cfg

        entry_cfg = EntryConfig(
            k_entry_atr=cfg["entry"]["k_entry_atr"],
            k_entry_spread=cfg["entry"]["k_entry_spread"],
            entry_offset_min_points=cfg["entry"]["entry_offset_min_points"],
            k_rearm_atr=cfg["rearm"]["k_rearm_atr"],
            k_rearm_spread=cfg["rearm"]["k_rearm_spread"],
            rearm_min_points=cfg["rearm"]["rearm_min_points"],
            k_sl_atr=cfg["sl"]["k_sl_atr"],
            k_sl_spread=cfg["sl"]["k_sl_spread"],
            sl_min_points=cfg["sl"]["sl_min_points"],
            sl_max_points=float(cfg["sl"].get("sl_max_points", 0.0)),
            mode=cfg["entry"].get("mode", "balanced"),
            offset_cap_atr=cfg["entry"].get("offset_cap_atr", 0.25),
            rearm_hysteresis_pts=cfg["entry"].get("rearm_hysteresis_pts", 0.0),
            min_order_age_ms=cfg["entry"].get("min_order_age_ms", 0.0),
            burst_min_spread_mult=cfg["entry"].get("burst_min_spread_mult", 0.8),
            burst_min_abs_pts=cfg["entry"].get("burst_min_abs_pts", 6.0),
            burst_max_wait_ms=cfg["entry"].get("burst_max_wait_ms", 5000.0),
            impulse_atr_mult=cfg["entry"].get("impulse_atr_mult", 0.35),
            impulse_dur_ms=cfg["entry"].get("impulse_dur_ms", 2000.0),
            countertrend_guard_window_ms=cfg["entry"].get("countertrend_guard_window_ms", 1800.0),
            countertrend_guard_atr_mult=cfg["entry"].get("countertrend_guard_atr_mult", 0.10),
            countertrend_guard_min_pts=cfg["entry"].get("countertrend_guard_min_pts", 24.0),
            noise_window_ms=cfg["entry"].get("noise_window_ms", 2000.0),
            noise_ratio_high=cfg["entry"].get("noise_ratio_high", 2.5),
            noise_ratio_mid=cfg["entry"].get("noise_ratio_mid", 1.8),
            noise_mult_high=cfg["entry"].get("noise_mult_high", 1.6),
            noise_mult_mid=cfg["entry"].get("noise_mult_mid", 1.3),
            only_buy=bool(cfg["entry"].get("only_buy", False)),
            offset_min_spread_mult=cfg["entry"].get("offset_min_spread_mult", 2.0),
            offset_max_spread_mult=cfg["entry"].get("offset_max_spread_mult", 6.0),
            idle_offset_spread_mult=cfg["entry"].get("idle_offset_spread_mult", 5.0),
            impulse_capture_delta_pts=cfg["entry"].get("impulse_capture_delta_pts", 8.0),
            impulse_capture_spread_mult=cfg["entry"].get("impulse_capture_spread_mult", 1.5),
            impulse_capture_floor_pts=cfg["entry"].get("impulse_capture_floor_pts", 10.0),
            impulse_capture_dur_ms=cfg["entry"].get("impulse_capture_dur_ms", 3000.0),
            entry_buffer_enabled=bool(cfg["entry"].get("entry_buffer_enabled", True)),
            entry_buffer_spread_mult=float(cfg["entry"].get("entry_buffer_spread_mult", 2.0)),
            entry_buffer_atr_mult=float(cfg["entry"].get("entry_buffer_atr_mult", 0.25)),
            fixed_min_buffer=float(cfg["entry"].get("fixed_min_buffer", 60.0)),
            min_total_offset_points=float(cfg["entry"].get("min_total_offset_points", 0.0)),
            orders_expand_points=float(cfg["entry"].get("orders_expand_points", 0.0)),
            flat_window_ms=float(cfg["entry"].get("flat_window_ms", 20000.0)),
            flat_range_pts=float(cfg["entry"].get("flat_range_pts", 25.0)),
            flat_offset_pts=float(cfg["entry"].get("flat_offset_pts", 40.0)),
            flat_freeze_enabled=bool(cfg["entry"].get("flat_freeze_enabled", True)),
            flat_freeze_ttl_ms=float(cfg["entry"].get("flat_freeze_ttl_ms", 30000.0)),
            offset_abs_max_points=float(cfg["entry"].get("offset_abs_max_points", 0.0)),
        )
        pending_cfg = PendingConfig(
            symbol=cfg["symbol"]["name"],
            magic=cfg["symbol"]["magic"],
            volume=cfg["risk"]["volume"],
            ttl_sec=cfg["pending"]["ttl_sec"],
            use_order_time_specified=cfg["pending"]["use_order_time_specified"],
            backoff_invalid_stops_ms=cfg["retcode"]["backoff_invalid_stops_ms"],
            backoff_requote_ms=cfg["retcode"]["backoff_requote_ms"],
            max_retries_requote=cfg["retcode"]["max_retries_requote"],
            op_deadline_ms=cfg["retcode"].get("op_deadline_ms", 3000.0),
        )
        self._order_mgr = OrderManager(
            self._adapter,
            self._state,
            entry_cfg,
            pending_cfg,
            retcode_policy_cb=self._on_retcode_policy,
        )

        confirm_cfg = ConfirmConfig(
            window_ms=cfg["confirm"]["window_ms"],
            window_ticks=cfg["confirm"]["window_ticks"],
            k_confirm_atr=cfg["confirm"]["k_confirm_atr"],
            k_confirm_spread=cfg["confirm"]["k_confirm_spread"],
            confirm_min_points=cfg["confirm"]["confirm_min_points"],
            cooldown_on_fail_sec=cfg["confirm"]["cooldown_on_fail_sec"],
        )
        be_cfg = BEConfig(
            be_activation_usd=float(cfg["breakeven"].get("be_activation_usd", 0.25)),
            be_stop_usd=float(cfg["breakeven"].get("be_stop_usd", 0.15)),
            min_hold_ms=float(cfg["breakeven"].get("min_hold_ms", 2000.0)),
        )
        trail_cfg = TrailConfig(
            trail_activation_points=float(cfg["trailing"].get("trail_activation_points", 50.0)),
            trail_stop_points=float(cfg["trailing"].get("trail_stop_points", 20.0)),
            trail_step_points=float(cfg["trailing"].get("trail_step_points", 20.0)),
            throttle_sec=float(cfg["trailing"].get("throttle_sec", 0.5)),
        )
        pos_cfg = PositionConfig(
            symbol=cfg["symbol"]["name"],
            magic=cfg["symbol"]["magic"],
            volume=cfg["risk"]["volume"],
            emergency_sl_points=cfg["risk"]["emergency_sl_points"],
            cancel_deadline_ms=cfg.get("position", {}).get("cancel_deadline_ms", 3000.0),
        )
        self._pos_mgr = PositionManager(
            self._adapter,
            self._state,
            pos_cfg,
            confirm_cfg,
            be_cfg,
            trail_cfg,
            event_cb=self._on_position_event,
        )

        self._micro_guard = MicroGuard(MicroGuardConfig(
            latency_max_ms=cfg["micro_guard"]["latency_max_ms"],
            flat_ticks_limit=cfg["micro_guard"]["flat_ticks_limit"],
            tick_stale_ms=cfg["micro_guard"].get("tick_stale_ms", 5000.0),
            ping_max_ms=cfg["micro_guard"].get("ping_max_ms", 1000),
        ))

    def _build_session_control(self: _BuildersHost) -> None:
        session_windows = [
            SessionWindow(start_hm=window["start"], end_hm=window["end"])
            for window in self._cfg["session"].get("trading_sessions", [])
        ]
        self._session = SessionControl(SessionConfig(
            market_close_block_min=self._cfg["session"]["market_close_block_min"],
            market_open_block_min=self._cfg["session"]["market_open_block_min"],
            trading_sessions=session_windows,
        ))

    def _build_persistence(self: _BuildersHost) -> None:
        cfg = self._cfg
        self._jsonl = JSONLLogger(
            cfg["logging"]["jsonl_path"],
            max_mb=cfg["logging"]["max_jsonl_mb"],
            max_archives=cfg["logging"].get("max_jsonl_archives", 2),
        )
        self._ledger = TradeLedger(cfg["logging"]["sqlite_path"])
        self._feature_logger = FeatureLogger(
            csv_path=cfg.get("logging", {}).get("ml_features_path", "logs/ml_features.csv")
        )

        log_name = cfg["logging"].get("level", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_name, logging.INFO),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

    def _build_telegram_gateway(self: _BuildersHost) -> None:
        tg_cfg = self._cfg["telegram"]
        tg_config = TelegramConfig(
            enabled=tg_cfg["enabled"],
            bot_token=tg_cfg.get("bot_token", ""),
            chat_id=tg_cfg.get("chat_id", ""),
            timeout_sec=tg_cfg.get("timeout_sec", 10),
        )
        self._tg = TelegramGateway(tg_config)
        self._tg.register_command_handler(self._handle_telegram_command)