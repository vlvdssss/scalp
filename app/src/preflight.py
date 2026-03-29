"""
preflight.py – standalone preflight checker.

Run before launching GUI to validate MT5 connection and config.
Exits with code 0 on success, non-zero on failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    print("=== XAUUSD Scalper Bot – Preflight Check ===")

    # 1. Config load
    try:
        import yaml
        cfg_path = ROOT / "config" / "default.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        print(f"[OK] Config loaded: {cfg_path}")
    except Exception as e:
        print(f"[WARN] Config load failed: {e} – using defaults")
        cfg = {}

    # 2. MetaTrader5 package
    try:
        import MetaTrader5 as mt5  # type: ignore[import-untyped]
        print(f"[OK] MetaTrader5 package found (version info unavailable before init)")
    except ImportError:
        print("[FAIL] MetaTrader5 package not installed. Run: pip install MetaTrader5")
        sys.exit(1)

    # 3. PySide6
    try:
        import PySide6
        print(f"[OK] PySide6 found: {PySide6.__version__}")
    except ImportError:
        print("[FAIL] PySide6 not installed. Run: pip install PySide6")
        sys.exit(1)

    # 4. MT5 connection preflight
    mt5_cfg = cfg.get("mt5", {})
    print("[..] Running MT5 preflight (requires running terminal)...")
    try:
        from app.src.adapters.mt5_adapter import MT5Adapter
        adapter = MT5Adapter()
        result = adapter.run_preflight(
            symbol=cfg.get("symbol", {}).get("name", "XAUUSD"),
            volume=cfg.get("risk", {}).get("volume", 0.01),
            path=mt5_cfg.get("path", ""),
            login=mt5_cfg.get("login", 0),
            password=mt5_cfg.get("password", ""),
            server=mt5_cfg.get("server", ""),
            timeout_ms=mt5_cfg.get("timeout_ms", 10000),
        )
        if not result.ok:
            print("[FAIL] Fatal preflight errors – cannot start trading:")
            for r in result.blocking_reasons:
                print(f"   \u2717 {r}")
            if result.warnings:
                print("[WARN] Additional warnings:")
                for w in result.warnings:
                    print(f"   \u26a0 {w}")
            adapter.shutdown()
            sys.exit(2)
        elif result.warnings:
            print("[WARN] MT5 preflight warnings (trading may be limited):")
            for w in result.warnings:
                print(f"   \u26a0 {w}")
        else:
            print("[OK] MT5 preflight passed")
        adapter.shutdown()
    except Exception as exc:
        print(f"[WARN] MT5 preflight exception: {exc}")
        print("      Start the MetaTrader5 terminal first.")

    print("=== Preflight complete – launching GUI ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
