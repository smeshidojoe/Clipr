"""Проверка и загрузка обновлений — обёртка над updater (из Spotty).

Сеть и распаковка идут в отдельном потоке: сходить к GitHub и скачать десяток
мегабайт на потоке интерфейса значит подвесить панель записи.

Загрузка и установка разделены. Скачанное обновление ждёт (state «ready»), а
когда перезапуститься, решает человек: посреди записи программу не
перезапускают.
"""

import os
import threading

from PySide6.QtCore import QObject, Signal

from . import updater


class UpdateService(QObject):
    """`state(ключ, версия)` — что происходит: checking, current, available,
    downloading, ready, error (не удалось проверить), failed (не удалось
    скачать). Настройки и трей показывают это словами.

    Сигналы испускаются из рабочего потока; Qt сам доставит их в поток
    интерфейса (соединения с QObject там живут в очереди).
    """

    state = Signal(str, str)
    progress = Signal(float)        # доля скачанного 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False              # идёт проверка или загрузка
        self._want_download = False     # найдётся — сразу качать
        self._latest = {}
        self._downloading = ""          # какая версия сейчас качается
        self._fraction = 0.0
        # Скачанное в прошлый раз и не установленное ждёт своего перезапуска.
        self._ready = updater.pending_version()
        self._last = ("ready", self._ready) if self._ready else ("", "")
        self.state.connect(self._remember)
        self.progress.connect(lambda f: setattr(self, "_fraction", f))

    def _remember(self, state, version):
        self._last = (state, version)

    def supported(self):
        """В режиме разработки подменять нечего — обновляемся только собранными."""
        return updater.is_frozen()

    def last_state(self):
        return self._last

    def fraction(self):
        return self._fraction

    def latest_available(self):
        """Найденная, но ещё не скачанная версия, или пустая строка."""
        if self._ready:
            return ""
        return self._latest.get("version", "") \
            if self._latest.get("status") == "available" else ""

    def downloading(self):
        return self._downloading

    def ready_version(self):
        """Скачанная версия, которой осталось только перезапуститься."""
        return self._ready

    # --- проверка ------------------------------------------------------------ #

    def check(self, silent=False, then_download=False):
        if then_download:
            self._want_download = True
        if self._busy:
            return
        if self._ready:
            # Новее уже скачано — сеть не нужна, сразу говорим, что готово.
            self._want_download = False
            self.state.emit("ready", self._ready)
            return
        self._busy = True
        if not silent:
            self.state.emit("checking", "")
        threading.Thread(target=self._check_worker, args=(silent,),
                         name="clipr-update", daemon=True).start()

    def _check_worker(self, silent):
        try:
            result = updater.check()
        except Exception as error:
            result = {"status": "error", "error": str(error)}

        self._latest = result
        status = result.get("status")
        if status == "available":
            self.state.emit("available", result.get("version", ""))
            if self._want_download and result.get("url"):
                self._download_worker(result)
                return
        elif status == "current":
            if not silent:
                self.state.emit("current", result.get("version", ""))
        elif not silent:
            self.state.emit("error", result.get("error", ""))
        self._want_download = False
        self._busy = False

    # --- загрузка ------------------------------------------------------------ #

    def download(self):
        """Скачать найденное обновление; по готовности — state("ready")."""
        if self._ready:
            self.state.emit("ready", self._ready)
            return
        if self._busy:
            # Идёт проверка — она и скачает, когда найдёт; идёт загрузка — ждём.
            self._want_download = True
            return
        if self._latest.get("status") != "available":
            self.check(then_download=True)
            return
        self._busy = True
        threading.Thread(target=self._download_worker, args=(self._latest,),
                         name="clipr-update", daemon=True).start()

    def _download_worker(self, result):
        self._want_download = False
        version = result.get("version", "")
        self._downloading = version
        self._fraction = 0.0
        self.state.emit("downloading", version)
        self.progress.emit(0.0)
        last = [-1]

        def report(fraction):
            # Кусок в 64 КБ — сотни сигналов на архив. Полосе и подписи
            # хватит одного на процент.
            percent = int(fraction * 100)
            if percent != last[0]:
                last[0] = percent
                self.progress.emit(fraction)

        try:
            updater.download(result.get("url"), version, on_progress=report)
        except Exception:
            self._downloading = ""
            self._busy = False
            self.state.emit("failed", version)
            return
        self._downloading = ""
        self._ready = version
        self._busy = False
        self.state.emit("ready", version)

    # --- применение ----------------------------------------------------------- #

    def start_helper(self):
        """Запускает помощника, который подменит exe. True — пошло, и вызывающий
        обязан сразу выйти (exit_now). False — архив негоден: его больше нет,
        скачивать придётся заново."""
        if updater.restart_to_update():
            return True
        self._ready = ""
        return False

    @staticmethod
    def exit_now():
        """Обрывает процесс, освобождая свой exe.

        Обычный выход тут не годится: пока процесс жив, его файл заблокирован,
        и помощник будет ждать впустую.
        """
        os._exit(0)
