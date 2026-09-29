"""Готовый GIF — в буфер обмена файлом, а не путём.

Путь текстом мессенджер вставил бы строкой. Нужен тот же формат, что кладёт
Проводник по Ctrl+C: список файлов (CF_HDROP). Qt строит его сам из file://
URL в QMimeData. Рядом кладём «Preferred DropEffect» = копирование: без него
часть программ (тот же Проводник) трактует вставку как перемещение и может
увести файл из папки результатов.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

DROPEFFECT_COPY = 1
_DROP_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'


def copy_file(path: str | Path) -> None:
    """Ctrl+V в Telegram, Discord, Slack или Проводнике вставит сам файл."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(Path(path)))])
    mime.setData(_DROP_EFFECT, struct.pack("<I", DROPEFFECT_COPY))
    QApplication.clipboard().setMimeData(mime)


def reveal(path: str | Path) -> None:
    """Открыть папку и выделить в ней файл — как «Показать в папке»."""
    target = Path(path)
    if target.is_file():
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        subprocess.Popen(["explorer", str(target if target.is_dir() else target.parent)])
