"""Внешние бинарники: ffmpeg, ffprobe, gifsicle.

Ищем сначала в своей папке (%APPDATA%/Clipr/tools), потом в PATH. Свою папку
держим в APPDATA, а не рядом с exe: так скачанные бинарники переживают
обновление и переустановку программы.

gifsicle нет ни в winget, ни в стандартных источниках Windows — только в
Chocolatey (нужны права администратора). Поэтому качаем готовую win64-сборку
сами, кнопкой из настроек.

Все запуски — без всплывающего окна консоли (CREATE_NO_WINDOW).
"""

import os
import shutil
import subprocess
import zipfile

from .constants import APP_NAME, app_dir

TOOLS_DIR = os.path.join(app_dir(), "tools")

FFMPEG_ZIP = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")
GIFSICLE_ZIP = "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip"

CREATE_NO_WINDOW = 0x08000000

_SSL_CTX = None


def ssl_context():
    """Системное хранилище ПЛЮС certifi.

    На машинах с устаревшими корневыми сертификатами Windows проверка цепочки
    падает с «certificate has expired» — сайт gifsicle как раз такой случай.
    """
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass                      # certifi нет — остаёмся на системном хранилище
        try:
            ctx.load_default_certs()
        except Exception:
            pass
        _SSL_CTX = ctx
    return _SSL_CTX


def run_hidden(args, timeout=None):
    """Запуск консольной утилиты без мелькающего окна терминала."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=CREATE_NO_WINDOW)


# ------------------------------------------------------------------ #
def _find(name):
    """Путь к бинарнику: сначала наша папка tools, потом PATH."""
    local = os.path.join(TOOLS_DIR, name + ".exe")
    if os.path.isfile(local):
        return local
    return shutil.which(name)


def ffmpeg_path():
    return _find("ffmpeg")


def ffprobe_path():
    return _find("ffprobe")


def gifsicle_path():
    return _find("gifsicle")


def have_ffmpeg():
    return bool(ffmpeg_path())


def have_gifsicle():
    return bool(gifsicle_path())


# ------------------------------------------------------------------ #
def _download(url, dest, progress=None, timeout=60):
    """Качает файл в dest (через .part, чтобы не оставить огрызок)."""
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, dest)
    return dest


def _extract_exes(zip_path, wanted):
    """Достаёт нужные .exe из архива в TOOLS_DIR, игнорируя структуру папок."""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    found = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = os.path.basename(name).lower()
            if base in wanted:
                target = os.path.join(TOOLS_DIR, base)
                with zf.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                found.append(target)
    return found


def download_ffmpeg(progress=None):
    """Ставит ffmpeg.exe и ffprobe.exe в TOOLS_DIR (сборка BtbN, ~80 МБ)."""
    zip_path = os.path.join(TOOLS_DIR, "ffmpeg.zip")
    _download(FFMPEG_ZIP, zip_path, progress, timeout=120)
    try:
        found = _extract_exes(zip_path, {"ffmpeg.exe", "ffprobe.exe"})
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    if len(found) < 2:
        raise RuntimeError("ffmpeg.exe/ffprobe.exe not found in the archive")
    return found


def download_gifsicle(progress=None):
    """Ставит gifsicle.exe в TOOLS_DIR (~230 КБ)."""
    zip_path = os.path.join(TOOLS_DIR, "gifsicle.zip")
    _download(GIFSICLE_ZIP, zip_path, progress, timeout=60)
    try:
        found = _extract_exes(zip_path, {"gifsicle.exe"})
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    if not found:
        raise RuntimeError("gifsicle.exe not found in the archive")
    return found[0]
