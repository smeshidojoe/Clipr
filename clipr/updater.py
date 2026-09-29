"""Самообновление через релизы GitHub (перенесено из Spotty).

Порядок:
  1. check()        — есть ли релиз новее текущего и zip-ассет к нему.
  2. download(url)  — качаем zip в <папка установки>/_update/update.zip, рядом
     version.txt с его версией.
  3. restart_to_update() — достаём новый exe из архива, запускаем его с флагом
     --apply-update и немедленно выходим. Новый exe дожидается, пока старый
     освободит файл, подменяет его собой и запускает. Иначе никак: работающий
     exe заблокирован и заменить себя сам не может. Если запустить новый exe
     не дали, ту же работу делает помощник на PowerShell.

Между 2 и 3 может пройти сколько угодно: при фоновой загрузке человек сам
выбирает, когда перезапуститься, и может до этого выйти из программы.
pending_version() на старте находит скачанный архив, и кнопка перезапуска
появляется снова, без повторной загрузки.

Только стандартная библиотека (плюс certifi, если есть): обновление не должно
зависеть от пакетов, которых может не оказаться в сборке.
"""

import os
import shutil
import subprocess
import sys
import zipfile

from .constants import APP_NAME, APP_VERSION, GITHUB_REPO, install_dir
from .tools import ssl_context

API_URL = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO

# Флаги отвязанного процесса: помощник должен пережить выход программы.
_DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

UPDATE_DIR = os.path.join(install_dir(), "_update")
UPDATE_ZIP = os.path.join(UPDATE_DIR, "update.zip")
VERSION_FILE = os.path.join(UPDATE_DIR, "version.txt")
NEW_EXE = os.path.join(UPDATE_DIR, "%s-new.exe" % APP_NAME)


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _parse(version):
    """'v1.2.3' -> (1, 2, 3); нечисловые куски считаем нулями."""
    out = []
    for part in (version or "").lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _is_newer(remote, current):
    return _parse(remote) > _parse(current)


# --- проверка и загрузка ----------------------------------------------------- #

def check(timeout=8):
    """{"status": "available" | "current" | "error", "version": ..., "url": ...}"""
    import json
    import urllib.request

    request = urllib.request.Request(
        API_URL, headers={"User-Agent": APP_NAME,
                          "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl_context()) as response:
            data = json.load(response)
    except Exception as error:
        return {"status": "error", "error": str(error)}

    tag = data.get("tag_name") or ""
    if not tag:
        return {"status": "error", "error": "no releases"}
    if not _is_newer(tag, APP_VERSION):
        return {"status": "current", "version": tag}

    url = None
    for asset in data.get("assets", []):
        if (asset.get("name") or "").endswith(".zip"):
            url = asset.get("browser_download_url")
            break
    return {"status": "available", "version": tag, "url": url,
            "notes": data.get("body", "")}


def download(url, version="", on_progress=None, timeout=60):
    """Качает zip обновления. on_progress(доля 0..1). Бросает при сбое."""
    import urllib.request
    if not url:
        raise RuntimeError("no archive URL")
    discard_pending()
    os.makedirs(UPDATE_DIR, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    part = UPDATE_ZIP + ".part"
    with urllib.request.urlopen(request, timeout=timeout,
                                context=ssl_context()) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(part, "wb") as out:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
    os.replace(part, UPDATE_ZIP)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version)
    return True


def has_pending():
    return os.path.isfile(UPDATE_ZIP)


def pending_version():
    """Версия, скачанная раньше и ещё не установленная, или "".

    Архив старее текущей версии или без подписи версии (программу обновили
    установщиком, пока он лежал) — удаляем: ставить из него нечего.
    """
    if not is_frozen() or not has_pending():
        return ""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        version = ""
    if version and _is_newer(version, APP_VERSION):
        return version
    discard_pending()
    return ""


def discard_pending():
    for path in (UPDATE_ZIP, VERSION_FILE):
        try:
            os.remove(path)
        except OSError:
            pass


# --- подмена работающего exe -------------------------------------------------- #

def _log(message):
    try:
        import datetime
        os.makedirs(UPDATE_DIR, exist_ok=True)
        with open(os.path.join(UPDATE_DIR, "helper.log"), "a",
                  encoding="utf-8") as f:
            f.write("[%s] %s\n" % (
                datetime.datetime.now().isoformat(timespec="seconds"), message))
    except Exception:
        pass


def _extract_new_exe():
    """Достаёт наш exe из архива в _update/Clipr-new.exe.

    Делаем это, пока программа ещё работает: файл новый, блокировок нет.
    """
    if not has_pending():
        return None
    try:
        wanted = os.path.basename(sys.executable).lower()
        with zipfile.ZipFile(UPDATE_ZIP, "r") as archive:
            names = archive.namelist()
            pick = next((n for n in names
                         if os.path.basename(n).lower() == wanted), None)
            if pick is None:
                pick = next((n for n in names if n.lower().endswith(".exe")), None)
            if pick is None:
                return None
            with archive.open(pick) as src, open(NEW_EXE, "wb") as out:
                shutil.copyfileobj(src, out)
        return NEW_EXE
    except Exception:
        _log("не смог достать exe из архива")
        return None


def _ps_lit(path):
    """Путь как литерал PowerShell: одинарные кавычки, внутренние — удвоением."""
    return "'%s'" % str(path).replace("'", "''")


def _restart_via_powershell(new_exe):
    """Запасной помощник: ждёт освобождения exe, копирует новый и запускает.

    Нужен, когда запустить новый exe напрямую не дали — например, политика
    запрещает запуск из папки _update. PowerShell есть в любой Windows.
    """
    log = os.path.join(UPDATE_DIR, "helper.log")
    # Пути подставляем литералами: в них бывают кириллица и пробелы. Цикл
    # сначала пробует удалить старый exe — это удаётся только когда процесс
    # действительно вышел, — и лишь потом копирует новый на его место.
    script = (
        "$ErrorActionPreference='SilentlyContinue';\n"
        "$old=%s; $new=%s; $log=%s;\n" % (_ps_lit(sys.executable),
                                          _ps_lit(new_exe), _ps_lit(log)) +
        "function L($m){ ('['+(Get-Date -Format o)+'] '+$m) |"
        " Out-File -LiteralPath $log -Append -Encoding utf8 }\n"
        "L('powershell helper started')\n"
        "$gone=$false\n"
        "for($i=0;$i -lt 200;$i++){\n"
        "  try{ Remove-Item -LiteralPath $old -Force -ErrorAction Stop;"
        " $gone=$true; break }\n"
        "  catch{ Start-Sleep -Milliseconds 400 }\n"
        "}\n"
        "L('old removed='+$gone)\n"
        "try{ Copy-Item -LiteralPath $new -Destination $old -Force"
        " -ErrorAction Stop }\n"
        "catch{ L('copy err: '+$_.Exception.Message) }\n"
        "if(-not (Test-Path -LiteralPath $old)){"
        " try{ Move-Item -LiteralPath $new -Destination $old -Force }catch{} }\n"
        "Start-Process -FilePath $old\n"
        "Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "L('done')\n"
    )
    try:
        import base64
        os.makedirs(UPDATE_DIR, exist_ok=True)
        # -EncodedCommand (base64 в UTF-16LE) снимает разом все вопросы с
        # кавычками и кодировкой командной строки.
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        # Полный путь к powershell.exe: на PATH не полагаемся, его правят.
        shell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "WindowsPowerShell", "v1.0",
                             "powershell.exe")
        if not os.path.isfile(shell):
            shell = "powershell"
        subprocess.Popen([shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                         creationflags=_DETACHED, close_fds=True)
        _log("запущен помощник PowerShell")
        return True
    except Exception as error:
        _log("не удалось запустить помощника PowerShell: %s" % error)
        return False


def restart_to_update():
    """Запускает подмену и требует немедленного выхода вызывающего.

    После True программа обязана тут же завершиться (os._exit), иначе её exe
    останется заблокированным и помощник будет ждать впустую.
    """
    if not is_frozen() or not has_pending():
        return False
    new_exe = _extract_new_exe()
    if not new_exe or not os.path.isfile(new_exe):
        return False
    discard_pending()             # exe уже извлечён, архив больше не нужен
    try:
        subprocess.Popen([new_exe, "--apply-update", sys.executable],
                         creationflags=_DETACHED, close_fds=True)
        _log("помощник запущен: %s" % new_exe)
        return True
    except Exception as error:
        _log("не удалось запустить помощника: %s — пробую PowerShell" % error)
    return _restart_via_powershell(new_exe)


def apply_self_update(target):
    """Выполняется НОВЫМ exe, запущенным с --apply-update.

    Ждёт, пока старый exe освободится, подменяет его собой и запускает.
    """
    import time
    source = sys.executable
    if not target or not os.path.isfile(source):
        return False
    ok = False
    for _ in range(200):                  # до ~80 секунд ждём выхода старого
        try:
            shutil.copyfile(source, target)
            ok = True
            break
        except (PermissionError, OSError):
            time.sleep(0.4)
    _log("подмена ok=%s -> %s" % (ok, target))
    # Запускаем цель в любом случае: если подмена не удалась, пользователь хотя
    # бы не останется без программы.
    try:
        subprocess.Popen([target], creationflags=_DETACHED, close_fds=True,
                         cwd=os.path.dirname(target) or None)
    except Exception as error:
        _log("не удалось перезапустить: %s" % error)
    return ok


def cleanup_applied():
    """Убирает Clipr-new.exe, если обновление уже применилось."""
    if not is_frozen() or not os.path.isfile(NEW_EXE):
        return
    try:
        if os.path.getsize(NEW_EXE) == os.path.getsize(sys.executable):
            os.remove(NEW_EXE)
    except OSError:
        pass
