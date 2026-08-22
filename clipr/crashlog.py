"""Отчёты о падениях: %APPDATA%/Clipr/crash-reports/crash-<дата>.log

В собранном exe stdout никуда не ведёт — необработанное исключение исчезает
бесследно, окно просто закрывается. Ставим три перехватчика:

  * sys.excepthook       — главный (GUI) поток;
  * threading.excepthook — обычные потоки;
  * QThread.run          — воркеры (PySide проглатывает исключение внутри run(),
                           поток молча умирает и экспорт «зависает» навсегда).
"""

import os
import sys
import threading
import time
import traceback

from .constants import APP_NAME, APP_VERSION, app_dir

CRASH_DIR = os.path.join(app_dir(), "crash-reports")
CRASH_TTL = 14 * 24 * 3600

_installed = False

# Управляющие исключения — не падения программы. KeyboardInterrupt важен
# особенно: при запуске из консоли Ctrl+C иначе писал бы полный отчёт.
_NOT_A_CRASH = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _is_crash(exc_type):
    return not (isinstance(exc_type, type) and issubclass(exc_type, _NOT_A_CRASH))


def _safe(fn, default=""):
    try:
        return fn()
    except Exception:
        return default


def _environment():
    lines = ["%s %s" % (APP_NAME, APP_VERSION),
             "Python: %s" % sys.version.split()[0],
             "Frozen: %s" % bool(getattr(sys, "frozen", False)),
             "Platform: %s" % _safe(lambda: __import__("platform").platform()),
             "Qt: %s" % _safe(
                 lambda: __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion())]

    def _tools_line():
        from . import tools
        return "ffmpeg: %s | gifsicle: %s" % (tools.ffmpeg_path(), tools.gifsicle_path())

    lines.append(_safe(_tools_line))
    return [line for line in lines if line]


def save(exc_type, exc, tb, context=""):
    """Пишет отчёт о падении. Возвращает путь к файлу (или '')."""
    try:
        os.makedirs(CRASH_DIR, exist_ok=True)
        # Каскад падений укладывается в одну секунду — добавляем миллисекунды,
        # иначе второй отчёт затрёт первый.
        stamp = "%s-%03d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time() * 1000) % 1000)
        path = os.path.join(CRASH_DIR, "crash-%s.log" % stamp)
        body = ["=" * 60] + _environment()
        if context:
            body.append("Context: %s" % context)
        body.append("Thread: %s" % threading.current_thread().name)
        body += ["=" * 60, "", "".join(traceback.format_exception(exc_type, exc, tb))]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        return path
    except Exception:
        return ""                    # отчёт о падении не имеет права падать сам


def install():
    """Ставит перехватчики. Вызывать один раз, до создания QApplication."""
    global _installed
    if _installed:
        return
    _installed = True

    def _hook(exc_type, exc, tb):
        if _is_crash(exc_type):
            save(exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        if _is_crash(args.exc_type):
            save(args.exc_type, args.exc_value, args.exc_traceback,
                 context="thread=%s" % getattr(args.thread, "name", "?"))

    threading.excepthook = _thread_hook
    _patch_qthread()


def _patch_qthread():
    """Оборачивает run() у наших QThread — иначе исключение в воркере теряется."""
    try:
        from PySide6.QtCore import QThread
        from .ffmpeg import GifExporter
    except Exception:
        return
    for cls in (GifExporter,):
        if getattr(cls, "_clipr_crash_wrapped", False) or "run" not in cls.__dict__:
            continue
        original = cls.run

        def run(self, _original=original, _cls=cls):
            try:
                _original(self)
            except Exception:
                save(*sys.exc_info(), context="worker=%s" % _cls.__name__)
                raise

        run.__name__ = "run"
        cls.run = run
        cls._clipr_crash_wrapped = True
        del QThread                  # ссылка нужна была только для импорта-проверки


def cleanup(ttl=CRASH_TTL):
    """Удаляет отчёты старше ttl секунд. Возвращает, сколько удалено."""
    removed = 0
    try:
        now = time.time()
        for name in os.listdir(CRASH_DIR):
            if not name.endswith(".log"):
                continue
            path = os.path.join(CRASH_DIR, name)
            try:
                if now - os.path.getmtime(path) > ttl:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def latest():
    """Путь к самому свежему отчёту (или '')."""
    try:
        files = [os.path.join(CRASH_DIR, n) for n in os.listdir(CRASH_DIR)
                 if n.endswith(".log")]
        return max(files, key=os.path.getmtime) if files else ""
    except OSError:
        return ""
