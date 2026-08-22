"""Самообновление через релизы GitHub.

Поток:
  1. check_update()       — есть ли релиз новее текущего (с zip-ассетом);
  2. download_update(url) — качаем zip в <папка установки>/_update/update.zip;
  3. restart_to_update()  — достаём новый exe и запускаем его с --apply-update:
     он дожидается выхода старого процесса, подменяет его собой и стартует.
     Так можно заменить и сам exe, заблокированный во время работы;
  4. apply_pending_update() — страховка при старте, если помощник не отработал.

Только stdlib (urllib) — ничего лишнего в сборке.
"""

import os
import shutil
import subprocess
import sys
import zipfile

from .constants import APP_NAME, APP_VERSION, GITHUB_REPO, install_dir
from . import tools

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Отвязанный процесс: переживает выход приложения.
_DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

UPDATE_DIR = os.path.join(install_dir(), "_update")
UPDATE_ZIP = os.path.join(UPDATE_DIR, "update.zip")
NEW_EXE = os.path.join(UPDATE_DIR, f"{APP_NAME}-new.exe")


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _parse(version):
    """'v1.2.3' -> (1, 2, 3); нечисловые части -> 0."""
    out = []
    for part in (version or "").lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _is_newer(remote, current):
    return _parse(remote) > _parse(current)


# ------------------------------------------------------------------ #
def check_update(timeout=8):
    """{"status": "available"|"current"|"error", "version", "download_url", "notes", "error"}"""
    import json
    import urllib.request
    req = urllib.request.Request(
        API_URL, headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=tools.ssl_context()) as resp:
            data = json.load(resp)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    tag = data.get("tag_name") or ""
    if not tag:
        return {"status": "error", "error": "нет релизов"}
    if not _is_newer(tag, APP_VERSION):
        return {"status": "current", "version": tag}

    zip_url = None
    for asset in data.get("assets", []):
        if (asset.get("name") or "").endswith(".zip"):
            zip_url = asset.get("browser_download_url")
            break
    return {"status": "available", "version": tag,
            "download_url": zip_url, "notes": data.get("body", "")}


def download_update(url, on_progress=None, timeout=120):
    if not url:
        raise RuntimeError("нет ссылки на загрузку")
    return tools._download(url, UPDATE_ZIP, on_progress, timeout)


def has_pending_update():
    return os.path.isfile(UPDATE_ZIP)


def apply_pending_update(target=None):
    """Страховка при старте: распаковать оставшийся zip поверх папки установки.
    Заблокированный exe так не заменить — это делает помощник."""
    if not has_pending_update():
        return False
    try:
        with zipfile.ZipFile(UPDATE_ZIP) as zf:
            zf.extractall(target or install_dir())
        os.remove(UPDATE_ZIP)
        return True
    except Exception:
        return False


def _log(message):
    try:
        import datetime
        os.makedirs(UPDATE_DIR, exist_ok=True)
        with open(os.path.join(UPDATE_DIR, "helper.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n"
                    % (datetime.datetime.now().isoformat(timespec="seconds"), message))
    except Exception:
        pass


def _extract_new_exe():
    """Достаёт наш exe из скачанного zip в _update/Clipr-new.exe, пока приложение
    ещё работает: файл новый, блокировок нет."""
    if not has_pending_update():
        return None
    try:
        want = os.path.basename(sys.executable).lower()
        with zipfile.ZipFile(UPDATE_ZIP) as zf:
            names = zf.namelist()
            pick = next((n for n in names if os.path.basename(n).lower() == want), None)
            if pick is None:
                pick = next((n for n in names if n.lower().endswith(".exe")), None)
            if pick is None:
                return None
            os.makedirs(UPDATE_DIR, exist_ok=True)
            with zf.open(pick) as src, open(NEW_EXE, "wb") as out:
                shutil.copyfileobj(src, out)
        return NEW_EXE
    except Exception:
        return None


def apply_self_update(target):
    """Выполняется НОВЫМ exe, запущенным с --apply-update: ждёт, пока старый
    target освободится, подменяет его собой и запускает."""
    import time
    src = sys.executable
    if not target or not os.path.isfile(src):
        return False
    ok = False
    for _ in range(200):                       # до ~80 c ждём выхода старого процесса
        try:
            shutil.copyfile(src, target)
            ok = True
            break
        except (PermissionError, OSError):
            time.sleep(0.4)
    _log("self-apply copy ok=%s -> %s" % (ok, target))
    try:
        subprocess.Popen([target], creationflags=_DETACHED, close_fds=True,
                         cwd=os.path.dirname(target) or None)
    except Exception as exc:
        _log("relaunch err: %s" % exc)
    return ok


def cleanup_applied():
    """Убирает Clipr-new.exe, если он уже совпадает с текущим exe."""
    if not is_frozen() or not os.path.isfile(NEW_EXE):
        return
    try:
        if os.path.getsize(NEW_EXE) == os.path.getsize(sys.executable):
            os.remove(NEW_EXE)
    except OSError:
        pass


def restart_to_update():
    """Готовит новый exe и запускает подмену. Вызывающий обязан немедленно выйти
    (os._exit), чтобы освободить свой exe-файл. Только для собранного exe."""
    if not is_frozen() or not has_pending_update():
        return False
    new_exe = _extract_new_exe()
    if not new_exe or not os.path.isfile(new_exe):
        _log("restart_to_update: новый exe не извлечён")
        return False
    try:
        os.remove(UPDATE_ZIP)                  # exe уже извлечён, zip не нужен
    except OSError:
        pass
    try:
        subprocess.Popen([new_exe, "--apply-update", sys.executable],
                         creationflags=_DETACHED, close_fds=True)
        _log("запущен помощник самоподмены: %s" % new_exe)
        return True
    except Exception as exc:
        _log("запуск помощника не удался: %s" % exc)
        return False
