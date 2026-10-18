"""Единственный источник правды по имени, версии и путям приложения.

APP_VERSION отсюда читают Clipr.spec (регуляркой, для ресурса версии exe) и
updater (для сравнения с тегом релиза на GitHub). Поднять версию = поправить
одну строку здесь.
"""

import os
import sys

APP_NAME = "Clipr"
APP_VERSION = "0.2.5"

GITHUB_REPO = "smeshidojoe/Clipr"
DEVELOPER_URL = "https://github.com/smeshidojoe"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

# Идентификатор для панели задач/уведомлений Windows и для именованного мьютекса
# однократного запуска.
APP_ID = "SmeshidoJoe.Clipr"
INSTANCE_MUTEX = "Clipr-Single-Instance-Mutex"

# В сборке PyInstaller ресурсы лежат во временной папке _MEIPASS; в разработке —
# в корне проекта.
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ASSETS_DIR = os.path.join(BASE_DIR, "assets")


def app_dir():
    """%APPDATA%/Clipr — настройки, логи, скачанные бинарники."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def install_dir():
    """Папка установки (где лежит exe). В разработке — корень проекта."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
