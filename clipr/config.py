"""Пользовательские настройки: JSON в %APPDATA%/Clipr."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .constants import app_dir

CONFIG_PATH = Path(app_dir()) / "settings.json"

# Уровень сжатия -> gifsicle --lossy. 0 — gifsicle не запускаем вовсе.
COMPRESS_LOSSY = {"none": 0, "light": 30, "strong": 80}

# Задержка кадра в GIF — в сотых долях секунды, и браузеры растягивают задержку
# меньше 2/100 до 10/100. На 60 кадрах/с половина задержек — 1/100, и GIF шёл бы
# рывками в разы медленнее. 50 — предел, который GIF показывает честно.
GIF_MAX_FPS = 50

# Встроенные пресеты экспорта: ключ имени в i18n, размер %, кадры/с, сжатие.
BUILTIN_PRESETS = [
    ("preset.small", 50, 10, "strong"),
    ("preset.chat", 75, 15, "light"),
    ("preset.quality", 100, 30, "none"),
    ("preset.smooth", 100, 60, "none"),
]


def scaled_size(width: int, height: int, percent: int) -> tuple[int, int]:
    """Размер GIF после уменьшения на `percent` %, округлённый до чётного —
    так же, как его посчитает фильтр scale в FFmpeg (см. GifExporter)."""
    if percent >= 100:
        return width, height            # 100 % FFmpeg не масштабирует вовсе
    factor = max(10, percent) / 100.0
    return (max(2, int(width * factor) // 2 * 2),
            max(2, int(height * factor) // 2 * 2))


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
    # auto — Desktop Duplication, где возможно (курсор не мигает), иначе gdigrab.
    # Ключ новый: у старого capture_backend значением по умолчанию был gdigrab.
    capture_method: str = "auto"      # auto | gdigrab | ddagrab
    draw_mouse: bool = True
    output_dir: str = field(default_factory=_default_output_dir)   # готовые GIF
    temp_dir: str = field(default_factory=_default_temp_dir)       # сырые записи

    # экспорт GIF
    gif_fps: int = 15
    # Размер GIF — доля от выделенной области (или кадра после crop), а не
    # абсолютная ширина: область и так задаёт пропорции и масштаб.
    gif_scale: int = 100             # %, 10..100
    gif_dither: str = "bayer:bayer_scale=3"
    # Сжатие gifsicle с потерями. Без потерь (-O3) он почти ничего не даёт —
    # FFmpeg уже пишет только изменившиеся прямоугольники (замер: −2 %), весь
    # выигрыш даёт --lossy, а он же и портит картинку. Поэтому по умолчанию выкл.
    gif_compress: str = "none"       # none | light | strong
    # Свои пресеты экспорта: [{"name", "scale", "fps", "compress"}, ...]
    export_presets: list[dict] = field(default_factory=list)

    # приложение
    hotkey_start_stop: str = "Ctrl+Alt+R"
    hotkey_pause: str = "Ctrl+Alt+P"
    hotkey_region: str = "Ctrl+Alt+A"
    hotkey_toggle_panel: str = "Ctrl+Alt+X"
    language: str = "en"             # en | ru — английский по умолчанию
    reduced_motion: bool = False
    keep_source_video: bool = True
    autostart: bool = False
    start_minimized: bool = False    # стартовать в трее, без панели записи
    check_updates: bool = True
    update_in_background: bool = False   # найденное обновление качать сразу

    # окно редактора: где стояло и было ли развёрнуто — откроется так же
    editor_geometry: list[int] = field(default_factory=list)   # x, y, w, h
    editor_maximized: bool = False

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw: dict[str, Any] = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in raw.items() if k in known})
        settings.gif_scale = max(10, min(100, int(settings.gif_scale)))
        if settings.gif_compress not in COMPRESS_LOSSY:
            settings.gif_compress = "none"
        settings.export_presets = [
            {"name": p["name"].strip(), "scale": max(10, min(100, p["scale"])),
             "fps": p["fps"], "compress": p["compress"]}
            for p in settings.export_presets if isinstance(p, dict)
            and isinstance(p.get("name"), str) and p["name"].strip()
            and isinstance(p.get("scale"), int) and isinstance(p.get("fps"), int)
            and p.get("compress") in COMPRESS_LOSSY
        ] if isinstance(settings.export_presets, list) else []
        geometry = settings.editor_geometry
        if not (isinstance(geometry, list) and len(geometry) == 4
                and all(isinstance(v, int) for v in geometry)):
            settings.editor_geometry = []
        return settings

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
