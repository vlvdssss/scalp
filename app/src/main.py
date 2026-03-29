"""
main.py – Application entry point.

Loads config, creates QApplication + MainWindow, starts Qt event loop.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_config(path: Path) -> dict:
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        print("[WARNING] PyYAML not installed, using empty config")
        return {}
    except FileNotFoundError:
        print(f"[WARNING] Config not found at {path}, using defaults")
        return {}


def main() -> None:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt

    # Load config
    cfg_path = ROOT / "config" / "default.yaml"
    cfg = load_config(cfg_path)

    # Ensure log directories exist
    for key in ("jsonl_path", "sqlite_path"):
        p = cfg.get("logging", {}).get(key)
        if p:
            Path(p).parent.mkdir(parents=True, exist_ok=True)

    # Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("XAUUSD Scalper Bot")
    app.setOrganizationName("ScalperTeam")

    # Dark style
    app.setStyleSheet("""
        QMainWindow, QWidget { background: #2b2b2b; color: #d4d4d4; }
        QGroupBox { border: 1px solid #555; margin-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QPushButton { background: #3c3f41; border: 1px solid #555;
                      padding: 4px 10px; border-radius: 3px; }
        QPushButton:hover { background: #4c5052; }
        QTabWidget::pane { border: 1px solid #555; }
        QTableView { gridline-color: #444; selection-background-color: #2d5986; }
        QHeaderView::section { background: #3c3f41; border: 1px solid #555; }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background: #3c3f41; border: 1px solid #555; padding: 2px; }
    """)

    from app.src.ui.main_window import MainWindow
    window = MainWindow(cfg)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
