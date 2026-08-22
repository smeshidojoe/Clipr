"""Оболочка приложения: трей, хоткеи и машина состояний записи."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart, tools, updater
from .config import Settings
from .constants import APP_NAME, APP_VERSION, RELEASES_URL
from .ffmpeg import Recorder, Region, cleanup_temp
from .ui import motion, theme
from .ui.editor import EditorWindow
from .ui.icons import app_icon
from .ui.overlay import OverlayPanel
from .ui.region_frame import RegionFrame
from .ui.region_select import RegionSelector, physical_region
from .ui.settings_dialog import SettingsDialog
from .winapi import HotkeyManager, exclude_from_capture


class UpdateCheck(QThread):
    """Фоновая проверка релизов — сеть не должна морозить интерфейс."""

    result = Signal(dict)

    def run(self) -> None:
        self.result.emit(updater.check_update())


class CliprApp(QObject):
    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.qt = QApplication.instance() or QApplication(argv)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setApplicationDisplayName(APP_NAME)
        self.qt.setApplicationVersion(APP_VERSION)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyleSheet(theme.QSS)
        self.qt.setWindowIcon(app_icon())

        self.settings = Settings.load()
        motion.set_reduced_motion(self.settings.reduced_motion)
        cleanup_temp(self.settings.ensure_temp_dir())

        self.region: Region | None = None
        self.region_rect: QRect | None = None      # логические координаты для рамки
        self.editor: EditorWindow | None = None
        self.update_check: UpdateCheck | None = None

        self.recorder = Recorder(self.settings)
        self.recorder.started.connect(self._on_started)
        self.recorder.paused.connect(lambda: self._sync_state("paused"))
        self.recorder.resumed.connect(lambda: self._sync_state("recording"))
        self.recorder.finished.connect(self._on_finished)
        self.recorder.failed.connect(self._on_failed)

        self.region_frame = RegionFrame(self)

        self.panel = OverlayPanel()
        self.panel.select_region_requested.connect(self.choose_region)
        self.panel.start_requested.connect(self.start_recording)
        self.panel.pause_requested.connect(self.recorder.toggle_pause)
        self.panel.stop_requested.connect(self.recorder.stop)
        self.panel.open_folder_requested.connect(self.open_folder)
        self.panel.settings_requested.connect(self.open_settings)

        self.tick = QTimer(self)
        self.tick.setInterval(100)
        self.tick.timeout.connect(lambda: self.panel.set_timecode(self.recorder.elapsed()))

        self.hotkeys = HotkeyManager(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.qt.installNativeEventFilter(self.hotkeys)

        self._build_tray()
        self._place_panel()
        self._apply_settings()
        self.panel.show_animated()
        # Панель не должна попадать в собственные записи.
        exclude_from_capture(int(self.panel.winId()), True)

        if not tools.have_ffmpeg():
            QTimer.singleShot(400, self._warn_no_ffmpeg)
        elif self.settings.check_updates:
            QTimer.singleShot(3000, self.check_updates)

    # ---- настройка -------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        self.action_panel = QAction("Показать панель", menu)
        self.action_panel.triggered.connect(self.panel.toggle)
        self.action_record = QAction("Начать запись", menu)
        self.action_record.triggered.connect(self.toggle_recording)
        action_region = QAction("Выбрать область…", menu)
        action_region.triggered.connect(self.choose_region)
        action_folder = QAction("Открыть папку", menu)
        action_folder.triggered.connect(self.open_folder)
        action_settings = QAction("Настройки", menu)
        action_settings.triggered.connect(self.open_settings)
        action_update = QAction("Проверить обновления", menu)
        action_update.triggered.connect(lambda: self.check_updates(quiet=False))
        action_quit = QAction("Выход", menu)
        action_quit.triggered.connect(self.quit)

        menu.addAction(self.action_panel)
        menu.addAction(self.action_record)
        menu.addAction(action_region)
        menu.addSeparator()
        menu.addAction(action_folder)
        menu.addAction(action_settings)
        menu.addAction(action_update)
        menu.addSeparator()
        menu.addAction(action_quit)

        self.menu = menu                      # держим ссылку: иначе меню соберёт GC
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_message_clicked)
        self.tray.show()

    def _place_panel(self) -> None:
        """Панель всегда стартует внизу по центру основного экрана.

        Положение намеренно не сохраняем: панель можно двигать, но каждый запуск
        начинается с предсказуемого места.
        """
        self.panel.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.panel.move(
            area.center().x() - self.panel.width() // 2,
            area.bottom() - self.panel.height() - 48,
        )

    def _apply_settings(self) -> None:
        motion.set_reduced_motion(self.settings.reduced_motion)
        autostart.set_enabled(self.settings.autostart)
        self.hotkeys.unregister_all()
        failed = [
            label
            for label, name, keys in (
                ("старт/стоп", "record", self.settings.hotkey_start_stop),
                ("пауза", "pause", self.settings.hotkey_pause),
                ("область", "region", self.settings.hotkey_region),
                ("панель", "panel", self.settings.hotkey_toggle_panel),
            )
            if keys and not self.hotkeys.register(name, keys)
        ]
        if failed:
            self.tray.showMessage(
                APP_NAME,
                "Эти хоткеи уже заняты другой программой: " + ", ".join(failed),
                app_icon(), 4000,
            )

    # ---- действия --------------------------------------------------------
    def choose_region(self) -> None:
        if self.recorder.is_active:
            return
        was_visible = self.panel.isVisible()
        self.panel.hide()
        self.region_frame.hide()               # рамка не должна мешать выбору
        selector = RegionSelector()
        self._selector = selector             # держим ссылку до закрытия окна

        def on_selected(rect: QRect) -> None:
            self.region_rect = rect
            self.region = physical_region(rect)
            self.panel.set_region_label(f"{self.region.width}×{self.region.height}")
            restore()

        def restore() -> None:
            self._selector = None
            self._show_region_frame()
            if was_visible:
                self.panel.show_animated()

        selector.selected.connect(on_selected)
        selector.cancelled.connect(restore)
        selector.showFullScreen()
        selector.raise_()
        selector.activateWindow()

    def _show_region_frame(self) -> None:
        """Рамка вокруг выбранной области. Для «всего экрана» её не рисуем —
        полоски ушли бы за границу монитора, да и обводить его целиком незачем."""
        rect = self.region_rect
        if rect is None or self.region is None or any(
                screen.geometry() == rect for screen in QGuiApplication.screens()):
            self.region_frame.hide()
            return
        self.region_frame.show_region(
            self.region_rect, f"{self.region.width}×{self.region.height}")
        self.region_frame.set_recording(self.recorder.state == "recording")

    def _full_screen_region(self) -> Region:
        screen = (QGuiApplication.screenAt(self.panel.geometry().center())
                  or QGuiApplication.primaryScreen())
        return physical_region(screen.geometry())

    def start_recording(self) -> None:
        if self.recorder.is_active:
            return
        self.recorder.start(self.region or self._full_screen_region())

    def toggle_recording(self) -> None:
        self.recorder.stop() if self.recorder.is_active else self.start_recording()

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.ensure_output_dir())))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, None)
        dialog.setWindowIcon(app_icon())
        dialog.applied.connect(self._apply_settings)
        dialog.exec()

    def quit(self) -> None:
        self.region_frame.hide()
        self.recorder.cancel()
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.qt.quit()

    # ---- обновления ------------------------------------------------------
    def check_updates(self, quiet: bool = True) -> None:
        if self.update_check and self.update_check.isRunning():
            return
        self.update_check = UpdateCheck(self)
        self.update_check.result.connect(lambda data: self._on_update_result(data, quiet))
        self.update_check.start()

    def _on_update_result(self, data: dict, quiet: bool) -> None:
        status = data.get("status")
        if status == "available":
            # Страницу релиза не открываем сами — только по клику на уведомлении.
            self._update_url = RELEASES_URL
            self.tray.showMessage(
                APP_NAME,
                f"Доступна версия {data.get('version')} — нажмите, чтобы открыть релиз.",
                app_icon(), 6000,
            )
        elif not quiet:
            message = (f"Установлена последняя версия ({APP_VERSION})."
                       if status == "current"
                       else f"Не удалось проверить: {data.get('error', '')}")
            self.tray.showMessage(APP_NAME, message, app_icon(), 4000)

    # ---- события рекордера ----------------------------------------------
    def _sync_state(self, state: str) -> None:
        self.panel.set_state(state)
        self.region_frame.set_recording(state == "recording")
        active = state in ("recording", "paused")
        self.tray.setIcon(app_icon(recording=state == "recording"))
        self.action_record.setText("Остановить запись" if active else "Начать запись")

    def _on_started(self) -> None:
        self._sync_state("recording")
        self.tick.start()
        if not self.panel.isVisible():
            self.panel.show_animated()

    def _on_finished(self, path: str) -> None:
        self.tick.stop()
        self._sync_state("idle")
        if not path:
            self.tray.showMessage(APP_NAME, "Ничего не записалось.", app_icon(), 3000)
            return
        self._open_editor(path)

    def _on_failed(self, message: str) -> None:
        self.tick.stop()
        self._sync_state("idle")
        QMessageBox.warning(None, APP_NAME, message[:1500])

    def _open_editor(self, path: str) -> None:
        if self.editor is not None:
            self.editor.close()
        self.editor = EditorWindow(path, self.settings)
        self.editor.setWindowIcon(app_icon())
        self.editor.setAttribute(Qt.WA_DeleteOnClose)
        self.editor.closed.connect(self._on_editor_closed)
        self.editor.show()
        self.editor.raise_()
        self.editor.activateWindow()

    def _on_editor_closed(self, video_path: str) -> None:
        self.editor = None
        source = Path(video_path)
        work_dir = source.parent
        if self.settings.keep_source_video:
            # Сырая запись лежит во временной папке — переносим её к результатам,
            # иначе уборка temp однажды её унесёт.
            try:
                target = self.settings.ensure_output_dir() / f"{work_dir.name}.mp4"
                if source.exists() and not target.exists():
                    shutil.move(str(source), str(target))
            except OSError:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)

    # ---- прочее ----------------------------------------------------------
    def _on_message_clicked(self) -> None:
        url, self._update_url = getattr(self, "_update_url", ""), ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.panel.toggle()

    def _on_hotkey(self, name: str) -> None:
        if name == "record":
            self.toggle_recording()
        elif name == "pause":
            self.recorder.toggle_pause()
        elif name == "region":
            self.choose_region()
        elif name == "panel":
            self.panel.toggle()

    def _warn_no_ffmpeg(self) -> None:
        answer = QMessageBox.question(
            None, "FFmpeg не найден",
            f"{APP_NAME} не может записывать и собирать GIF без FFmpeg.\n\n"
            "Скачать его сейчас (около 80 МБ, в папку программы в AppData)?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.open_settings()

    def run(self) -> int:
        return self.qt.exec()


def main() -> int:
    return CliprApp(sys.argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
