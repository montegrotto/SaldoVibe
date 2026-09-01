from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "SaldoVibe"


def get_project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def get_runtime_data_dir(project_root: Path | None = None) -> Path:
    override = os.getenv("SALDOVIBE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return (Path.home() / "Library" / "Application Support" / APP_NAME).resolve()
        if os.name == "nt":
            appdata = os.getenv("APPDATA")
            if appdata:
                return (Path(appdata) / APP_NAME).resolve()
        return (Path.home() / f".{APP_NAME.lower()}").resolve()

    return (project_root or get_project_root()).resolve()


def ensure_runtime_directories(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "media").mkdir(parents=True, exist_ok=True)
    (data_dir / "staticfiles").mkdir(parents=True, exist_ok=True)
    (data_dir / "export_bundles").mkdir(parents=True, exist_ok=True)
