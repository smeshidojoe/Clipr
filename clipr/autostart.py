r"""Автозапуск при старте Windows через HKCU\...\CurrentVersion\Run.

Без прав администратора. В dev-режиме реестр НЕ трогаем — иначе галочка
прописала бы в автозапуск python.exe.
"""

import os
import sys

from .constants import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def set_enabled(on):
    """Возвращает True при успехе (или в dev, где регистрировать нечего)."""
    if not _is_frozen():
        return True
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                                  f'"{os.path.abspath(sys.executable)}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def is_enabled():
    if not _is_frozen():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False
