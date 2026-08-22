"""Пользовательские настройки: JSON в %APPDATA%/Clipr."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .constants import app_dir

CONFIG_PATH = Path(app_dir()) / "settings.json"


def _default_output_dir() -> str:
    return str(Path(os.path.expanduser("~")) / "Videos" / "Clipr")


def _default_temp_dir() -> str:
    """Промежуточные MP4 — в %TEMP%, чтобы папка с результатами оставалась чистой."""
    base = os.environ.get("TEMP") or os.path.expanduser("~")
    return str(Path(base) / "Clipr")


@dataclass
class Settings:
    # запись
    capture_fps: int = 30
    capture_backend: str = "gdigrab"  # gdigrab | ddagrab
    draw_mouse: bool = True
    output_dir: str = field(default_factory=_default_output_dir)   # готовые GIF
    temp_dir: str = field(default_factory=_default_temp_dir)       # сырые записи

    # экспорт GIF
    gif_fps: int = 15
    gif_width: int = 800             # 0 = ширина исходника
    gif_dither: str = "bayer:bayer_scale=3"
    gif_lossy: int = 80              # gifsicle --lossy, 0 = выключено
    gif_optimize: bool = True        # прогонять gifsicle, когда он есть

    # приложение
    hotkey_start_stop: str = "Ctrl+Alt+R"
    hotkey_pause: str = "Ctrl+Alt+P"
    hotkey_region: str = "Ctrl+Alt+A"
    hotkey_toggle_panel: str = "Ctrl+Alt+X"
    reduced_motion: bool = False
    keep_source_video: bool = True
    autostart: bool = False
    check_updates: bool = True

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw: dict[str, Any] = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), "utf-8")

    def ensure_output_dir(self) -> Path:
        return _ensure(self.output_dir, _default_output_dir())

    def ensure_temp_dir(self) -> Path:
        return _ensure(self.temp_dir, _default_temp_dir())


def _ensure(path: str, fallback: str) -> Path:
    """Создаёт папку; если путь недоступен (диск отключили) — откатывается."""
    for candidate in (path, fallback):
        try:
            resolved = Path(candidate).expanduser()
            resolved.mkdir(parents=True, exist_ok=True)
            return resolved
        except OSError:
            continue
    return Path(os.path.expanduser("~"))
