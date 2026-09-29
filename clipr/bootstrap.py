"""Запуск приложения: всё, что должно случиться ДО создания окон — применение
обновления, защита от второго запуска, перехват падений и обработка Ctrl+C.

Живёт в пакете, чтобы `python main.py` и `python -m clipr` шли одним путём.
"""

import ctypes
import signal
import sys

from .constants import APP_ID, INSTANCE_MUTEX

_INSTANCE_MUTEX = None


def _is_only_instance() -> bool:
    """True — мы единственный экземпляр. Именованный мьютекс живёт до конца
    процесса; второй запуск иначе повесил бы в трей вторую иконку и перехватил
    бы те же горячие клавиши."""
    global _INSTANCE_MUTEX
    try:
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX)
        return kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS
    except Exception:
        return True                                    # при ошибке не блокируем запуск


def _ask_first_to_show() -> None:
    """Уже запущенная копия сидит в трее — просим её показать панель. Иначе
    двойной клик по ярлыку при «Запускать свёрнутой» не давал бы ничего."""
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalSocket

        app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        socket = QLocalSocket(app)
        socket.connectToServer(INSTANCE_MUTEX + "-show")
        if socket.waitForConnected(700):
            socket.write(b"show")
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
    except Exception:
        pass


def _set_app_identity(app) -> None:
    """Имя для панели задач и уведомлений — иначе Windows группирует нас под
    «Python»."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _install_sigint(app, quit_app) -> None:
    """Ctrl+C в консоли должен закрывать программу.

    Qt держит цикл событий в C++, и питоновский обработчик сигнала не получает
    управления, пока не выполнится питоновский код — поэтому будим интерпретатор
    периодическим таймером.
    """
    from PySide6.QtCore import QTimer

    def handler(_signum, _frame):
        print("\nClipr: Ctrl+C received, quitting.")
        quit_app()

    signal.signal(signal.SIGINT, handler)
    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError):
        pass

    waker = QTimer()
    waker.setInterval(200)
    waker.timeout.connect(lambda: None)      # даём интерпретатору шанс отработать сигнал
    waker.start()
    app._sigint_timer = waker                # держим ссылку, иначе таймер соберёт GC


def main() -> int:
    # Самоприменение обновления: новый exe запущен с --apply-update <старый exe>.
    # Обрабатываем ДО мьютекса — это не «второй экземпляр».
    if "--apply-update" in sys.argv:
        from . import updater
        index = sys.argv.index("--apply-update")
        updater.apply_self_update(sys.argv[index + 1] if index + 1 < len(sys.argv) else "")
        return 0

    if not _is_only_instance():
        _ask_first_to_show()
        return 0

    from . import crashlog, updater
    try:
        updater.cleanup_applied()            # убрать Clipr-new.exe после подмены
    except Exception:
        pass

    crashlog.install()                       # ставим ДО QApplication
    crashlog.cleanup()

    from .app import CliprApp
    app = CliprApp(sys.argv)
    _set_app_identity(app.qt)
    _install_sigint(app.qt, app.quit)
    return app.run()
