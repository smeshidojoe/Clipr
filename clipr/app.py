"""Оболочка приложения: трей, хоткеи и машина состояний записи."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart, i18n, tools
from .config import Settings
from .constants import APP_NAME, APP_VERSION, INSTANCE_MUTEX
from .ffmpeg import Recorder, Region, cleanup_temp, ffmpeg_path, run_hidden
from .i18n import size_text, tr
from .ui import motion, theme
from .ui.editor import EditorWindow, unique_path
from .ui.icons import app_icon
from .ui.overlay import OverlayPanel
from .ui.region_frame import RegionFrame
from .ui.region_select import RegionSelector, physical_region
from .ui.settings_window import SettingsWindow
from .ui.toast import Toast
from .ui.widgets import rounded_menu
from .updates import UpdateService
from .winapi import HotkeyManager, exclude_from_capture, system_animations_enabled

UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000     # фоновая проверка раз в 6 часов
FIRST_CHECK_MS = 5000                       # первая — вскоре после старта
SHOW_SERVER = INSTANCE_MUTEX + "-show"      # второй запуск просит показать панель

HOTKEY_KEYS = (
    ("record", "hotkey_start_stop"),
    ("pause", "hotkey_pause"),
    ("region", "hotkey_region"),
    ("panel", "hotkey_toggle_panel"),
)


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
        i18n.set_language(self.settings.language)
        self._apply_motion_preference()
        cleanup_temp(self.settings.ensure_temp_dir())

        self.region: Region | None = None
        self.region_rect: QRect | None = None      # логические координаты для рамки
        self._selector: RegionSelector | None = None   # открытый выбор области
        self.editor: EditorWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self._failed_hotkeys: set[str] = set()
        self._hotkeys_suspended = False

        self.recorder = Recorder(self.settings)
        self.recorder.started.connect(self._on_started)
        self.recorder.paused.connect(lambda: self._sync_state("paused"))
        self.recorder.resumed.connect(lambda: self._sync_state("recording"))
        self.recorder.finished.connect(self._on_finished)
        self.recorder.failed.connect(self._on_failed)

        self.region_frame = RegionFrame(self)
        self.toast = Toast()

        self.panel = OverlayPanel()
        self.panel.select_region_requested.connect(self.choose_region)
        self.panel.reset_region_requested.connect(self.reset_region)
        self.panel.visibility_changed.connect(self._on_panel_visibility)
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

        self.updates = UpdateService(self)
        self.updates.state.connect(self._on_update_state)
        self._update_timer = QTimer(self, interval=UPDATE_INTERVAL_MS,
                                    timeout=self._auto_check)

        self._build_tray()
        self.panel.home()
        self._register_hotkeys()
        self._listen_for_second_launch()
        # Панель не должна попадать в собственные записи.
        exclude_from_capture(int(self.panel.winId()), True)

        # «Запускать свёрнутой»: только трей. --show (после установщика)
        # показывает панель всё равно — видно, что программа работает.
        if "--show" in argv or not self.settings.start_minimized:
            self.panel.show_animated()

        if not tools.have_ffmpeg():
            QTimer.singleShot(400, self._warn_no_ffmpeg)
        if self.settings.check_updates:
            QTimer.singleShot(FIRST_CHECK_MS, self._auto_check)
            self._update_timer.start()
        if self.updates.ready_version():
            self._on_update_state("ready", self.updates.ready_version())

    # ---- настройка -------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        rounded_menu(menu)
        self.action_panel = QAction(menu)
        self.action_panel.triggered.connect(self.panel.toggle)
        self.action_record = QAction(menu)
        self.action_record.triggered.connect(self.toggle_recording)
        self.action_region = QAction(menu)
        self.action_region.triggered.connect(self.choose_region)
        self.action_full_screen = QAction(menu)
        self.action_full_screen.triggered.connect(self.reset_region)
        self.action_folder = QAction(menu)
        self.action_folder.triggered.connect(self.open_folder)
        self.action_settings = QAction(menu)
        self.action_settings.triggered.connect(self.open_settings)
        self.action_update = QAction(menu)
        self.action_update.triggered.connect(self._update_from_tray)
        self.action_quit = QAction(menu)
        self.action_quit.triggered.connect(self.quit)

        menu.addAction(self.action_panel)
        menu.addAction(self.action_record)
        menu.addAction(self.action_region)
        menu.addAction(self.action_full_screen)
        # Пункт есть только когда выбрана своя область: сбрасывать нечего — нечего и показывать.
        menu.aboutToShow.connect(self._update_region_actions)
        menu.addSeparator()
        menu.addAction(self.action_folder)
        menu.addAction(self.action_settings)
        menu.addAction(self.action_update)
        menu.addSeparator()
        menu.addAction(self.action_quit)
        self._retranslate_tray()

        self.menu = menu                      # держим ссылку: иначе меню соберёт GC
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _retranslate_tray(self) -> None:
        self.action_panel.setText(tr("tray.show_panel"))
        self.action_record.setText(tr("tray.stop" if self.recorder.is_active else "tray.start"))
        self.action_region.setText(tr("tray.region"))
        self.action_full_screen.setText(tr("tray.full_screen"))
        self.action_folder.setText(tr("tray.folder"))
        self.action_settings.setText(tr("tray.settings"))
        ready = self.updates.ready_version()
        self.action_update.setText(tr("tray.restart", version=ready) if ready
                                   else tr("tray.update"))
        self.action_quit.setText(tr("tray.quit"))

    def _apply_language(self) -> None:
        """Язык меняется на лету: трей и панель переподписываются, окно
        настроек пересобирается на том же разделе. Открытый редактор
        подхватит язык при следующем открытии."""
        i18n.set_language(self.settings.language)
        self._retranslate_tray()
        self.panel.retranslate()
        old = self.settings_window
        if old is None:
            return
        page = old.current_page()
        geometry = old.geometry()
        self.settings_window = SettingsWindow(self)
        self.settings_window.setWindowIcon(app_icon())
        self.settings_window.setGeometry(geometry)
        self.settings_window.present(page, animate=False)
        # Новое окно уже на месте старого — старое убираем без мигания.
        old.dispose()

    def _listen_for_second_launch(self) -> None:
        """Второй запуск не открывает вторую копию (мьютекс), а просит эту
        показать панель: при «Запускать свёрнутой» иначе двойной клик по
        ярлыку не давал бы ничего видимого."""
        QLocalServer.removeServer(SHOW_SERVER)          # хвост от упавшего процесса
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_second_launch)
        self._server.listen(SHOW_SERVER)

    def _on_second_launch(self) -> None:
        while self._server.hasPendingConnections():
            self._server.nextPendingConnection().deleteLater()
        if not self.panel.isVisible():
            self.panel.show_animated()
        self.panel.raise_()

    def _apply_motion_preference(self) -> None:
        """Переключатель в настройках плюс системный флаг Windows.

        Если человек отключил анимацию в параметрах доступности, спрашивать его
        об этом ещё раз внутри программы незачем — уважаем сразу.
        """
        motion.set_reduced_motion(
            self.settings.reduced_motion or not system_animations_enabled())

    def _register_hotkeys(self) -> None:
        self.hotkeys.unregister_all()
        self._failed_hotkeys = set()
        if self._hotkeys_suspended:
            return
        for name, key in HOTKEY_KEYS:
            keys = getattr(self.settings, key)
            if keys and not self.hotkeys.register(name, keys):
                self._failed_hotkeys.add(name)

    def failed_hotkeys(self) -> set[str]:
        """Какие сочетания не удалось занять (их держит другая программа)."""
        return set(self._failed_hotkeys)

    def suspend_hotkeys(self, suspended: bool) -> None:
        """Пока в настройках записывают новое сочетание, глобальные хоткеи
        сняты — иначе нажатое сочетание запустило бы запись, а не попало в поле."""
        self._hotkeys_suspended = suspended
        if suspended:
            self.hotkeys.unregister_all()
        else:
            self._register_hotkeys()

    def setting_changed(self, key: str) -> None:
        """Окно настроек применяет изменения сразу — здесь их побочные эффекты."""
        if key.startswith("hotkey_"):
            self._register_hotkeys()
        elif key == "reduced_motion":
            self._apply_motion_preference()
        elif key == "autostart":
            autostart.set_enabled(self.settings.autostart)
        elif key == "check_updates":
            if self.settings.check_updates:
                self._update_timer.start()
                self._auto_check()
            else:
                self._update_timer.stop()
        elif key == "update_in_background":
            if self.settings.update_in_background and self.updates.latest_available():
                self.updates.download()
        elif key == "temp_dir":
            self.settings.ensure_temp_dir()
        elif key == "language":
            # После выхода из обработчика: окно, которое сейчас зовёт нас,
            # будет заменено новым.
            QTimer.singleShot(0, self._apply_language)

    def region_size(self) -> tuple[int, int]:
        """Размер области записи в физических пикселях — для подписи в
        настройках «область → GIF»."""
        region = self.region or self._full_screen_region()
        return region.width, region.height

    # ---- действия --------------------------------------------------------
    def choose_region(self) -> None:
        # Область выбирают только при открытой панели: спрятанная панель прячет
        # и рамку, и выбор вслепую ничего бы не показал. Хоткей выбора тогда
        # молчит — работает лишь хоткей, открывающий панель.
        if (self.recorder.is_active or not self.panel.is_shown()
                or self._selector is not None):
            return
        self.panel.hide()
        self.region_frame.hide()               # рамка не должна мешать выбору
        # Своя область открывается с ручками — её правят, а не рисуют заново.
        selector = RegionSelector(self.region_rect if self._has_custom_region() else None)
        self._selector = selector             # держим ссылку до закрытия окна

        def on_selected(rect: QRect) -> None:
            self.region_rect = rect
            # Чётные размеры — те, что реально запишутся (H.264 yuv420p).
            self.region = physical_region(rect).even()
            # Клик без протягивания — весь монитор. Подпись тогда «Весь экран»,
            # а не «2560×1440»: размер ничего не говорит, а режим — говорит.
            self.panel.set_region_label(
                "" if self._is_full_screen(rect)
                else f"{self.region.width}×{self.region.height}")
            if self.settings_window is not None:
                self.settings_window.sync()
            restore()

        def restore() -> None:
            self._selector = None
            # Панель пряталась только на время выбора — место не меняем.
            # Рамку области покажет она же (visibility_changed).
            self.panel.show_animated(home=False)

        selector.selected.connect(on_selected)
        selector.cancelled.connect(restore)
        selector.showFullScreen()
        selector.raise_()
        selector.activateWindow()

    def _show_region_frame(self) -> None:
        """Рамка вокруг выбранной области. Для «всего экрана» её не рисуем —
        полоски ушли бы за границу монитора, да и обводить его целиком незачем."""
        rect = self.region_rect
        if rect is None or self.region is None or self._is_full_screen(rect):
            self.region_frame.hide()
            return
        self.region_frame.show_region(
            self.region_rect, f"{self.region.width}×{self.region.height}")
        self.region_frame.set_recording(self.recorder.state == "recording")

    def reset_region(self) -> None:
        """Назад к записи всего экрана — к тому, что было до выбора области."""
        if self.recorder.is_active:
            return
        self.region = None
        self.region_rect = None
        self.region_frame.hide()
        self.panel.set_region_label("")
        if self.settings_window is not None:
            self.settings_window.sync()

    def _update_region_actions(self) -> None:
        """Пункты области в трее — по тем же правилам, что и хоткей: при
        спрятанной панели выбор недоступен (серый — чтобы было видно, что
        пункт есть, просто сейчас нельзя)."""
        available = self.panel.is_shown() and not self.recorder.is_active
        self.action_region.setEnabled(available)
        self.action_full_screen.setVisible(available and self._has_custom_region())

    def _has_custom_region(self) -> bool:
        return self.region_rect is not None and not self._is_full_screen(self.region_rect)

    def _on_panel_visibility(self, visible: bool) -> None:
        """Рамка области живёт вместе с панелью: спрятали панель — рамка не
        должна висеть на экране сама по себе."""
        if visible:
            self._show_region_frame()
        else:
            self.region_frame.hide()

    @staticmethod
    def _is_full_screen(rect: QRect) -> bool:
        return any(screen.geometry() == rect for screen in QGuiApplication.screens())

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

    def open_settings(self, page: str | None = None) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.setWindowIcon(app_icon())
        self.settings_window.present(page if isinstance(page, str) else None)

    def quit(self) -> None:
        self.region_frame.hide()
        self.recorder.cancel()
        self.hotkeys.unregister_all()
        if self.editor is not None:
            self.editor.close()                  # уберёт свою временную папку
        if self.settings_window is not None:
            self.settings_window.dispose()
        self.toast.hide()
        self.settings.save()
        self.tray.hide()
        # Не quit(): в Qt 6 он сперва закрывает все окна и ОТМЕНЯЕТСЯ, если
        # какое-то отказалось закрыться, — а окно настроек при закрытии не
        # уничтожается, а прячется. Окна мы уже убрали сами.
        self.qt.exit(0)

    # ---- обновления ------------------------------------------------------
    # Найденное обновление: уведомление в трее, клик — скачать. С «Скачивать в
    # фоне» качается само. Скачанное ждёт перезапуска: пункт в трее и кнопка в
    # настройках. Посреди записи не перезапускаемся никогда.

    def _auto_check(self) -> None:
        if self.settings.check_updates:
            self.updates.check(silent=True,
                               then_download=self.settings.update_in_background
                               and self.updates.supported())

    def update_status(self) -> tuple[str, str, float]:
        state, version = self.updates.last_state()
        if state == "available" and not self.updates.supported():
            state = "dev"
        return state, version, self.updates.fraction()

    def update_action(self) -> None:
        """Кнопка в настройках: проверить → скачать → перезапустить."""
        if self.updates.ready_version():
            self.restart_and_update()
        elif self.updates.latest_available() and self.updates.supported():
            self.updates.download()
        else:
            self.updates.check()

    def _update_from_tray(self) -> None:
        if self.updates.ready_version():
            self.restart_and_update()
            return
        self._tray_check = True
        self.updates.check()

    def _on_update_state(self, state: str, version: str) -> None:
        tray_check = getattr(self, "_tray_check", False)
        if state == "ready":
            self._tray_check = False
            self.action_update.setText(tr("tray.restart", version=version))
            self._notify(tr("notify.ready", version=version), "restart", 8000)
        elif state == "available":
            self._tray_check = False
            if not self.updates.supported():
                if tray_check:
                    self._notify(tr("notify.available_dev", version=version), "", 5000)
            elif not self.settings.update_in_background or tray_check:
                self._notify(tr("notify.available", version=version), "download", 8000)
        elif state in ("current", "error") and tray_check:
            self._tray_check = False
            self._notify(tr("notify.current", version=APP_VERSION) if state == "current"
                         else tr("notify.check_failed"), "", 4000)
        elif state == "failed":
            self._notify(tr("notify.download_failed"), "", 4000)

    def _notify(self, message: str, on_click: str, ms: int) -> None:
        """Своя плашка вместо системного уведомления: те доходят не всегда
        («Фокусировка внимания», выключенные уведомления)."""
        action = {"download": self.updates.download,
                  "restart": self.restart_and_update}.get(on_click)
        self.toast.show_message(message, timeout_ms=0 if on_click == "restart" else ms,
                                on_click=action, tag="update" if action else "")

    def _on_exported(self, path: str, size: int) -> None:
        """Результат уже в буфере обмена — сказать об этом там, где человек
        смотрит, и показать, что именно скопировано. Клик — файл в Проводнике."""
        from .ui.clipboard import reveal
        video = path.lower().endswith(".mp4")
        self.toast.show_message(
            tr("toast.video_copied" if video else "toast.copied"),
            tr("toast.copied_sub", size=size_text(size)),
            on_click=lambda: reveal(path), tag="copied",
            thumbnail=self._video_thumbnail(path) if video else path)

    def _video_thumbnail(self, path: str) -> str | None:
        """Первый кадр MP4 картинкой: Qt читает из видео кадр только плеером."""
        exe = ffmpeg_path()
        if not exe:
            return None
        thumb = self.settings.ensure_temp_dir() / "thumbnail.png"
        try:
            run_hidden([exe, "-hide_banner", "-loglevel", "error", "-i", path,
                        "-frames:v", "1", "-vf", "scale=320:-2", "-y", str(thumb)], timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return str(thumb) if thumb.exists() else None

    def restart_and_update(self) -> None:
        if not self.updates.ready_version():
            return
        if self.recorder.is_active:
            self._notify(tr("notify.stop_first"), "", 4000)
            return
        if self.updates.start_helper():
            self.hotkeys.unregister_all()
            self.region_frame.hide()
            self.settings.save()
            self.tray.hide()
            self.updates.exit_now()
            return
        self.action_update.setText(tr("tray.update"))
        self._notify(tr("notify.install_failed"), "", 5000)

    # ---- события рекордера ----------------------------------------------
    def _sync_state(self, state: str) -> None:
        self.panel.set_state(state)
        self.region_frame.set_recording(state == "recording")
        active = state in ("recording", "paused")
        self.tray.setIcon(app_icon(recording=state == "recording"))
        self.action_record.setText(tr("tray.stop" if active else "tray.start"))

    def _on_started(self) -> None:
        self._sync_state("recording")
        self.tick.start()
        if not self.panel.isVisible():
            self.panel.show_animated()

    def _on_finished(self, path: str) -> None:
        self.tick.stop()
        self._sync_state("idle")
        if not path:
            self._notify(tr("notify.nothing"), "", 3000)
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
        self.editor.exported.connect(self._on_exported)
        self.editor.present()

    def _on_editor_closed(self, video_path: str) -> None:
        self.editor = None
        source = Path(video_path)
        work_dir = source.parent
        if self.settings.keep_source_video:
            # Сырая запись лежит во временной папке — переносим её к результатам,
            # иначе уборка temp однажды её унесёт.
            try:
                folder = self.settings.ensure_output_dir()
                target = folder / f"{work_dir.name}.mp4"
                if target.exists():      # имя занял экспорт видео из редактора
                    target = unique_path(folder / f"{work_dir.name}_source.mp4")
                if source.exists():
                    shutil.move(str(source), str(target))
            except OSError:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)
        if self.settings_window is not None:
            self.settings_window.sync()          # редактор мог поменять значения GIF

    # ---- прочее ----------------------------------------------------------
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
            # Действие с клавиатуры не анимируем: переход превращает мгновенную
            # команду в ожидание.
            self.panel.toggle(instant=True)

    def _warn_no_ffmpeg(self) -> None:
        answer = QMessageBox.question(
            None, tr("ffmpeg.missing_title"), tr("ffmpeg.missing_text"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.open_settings("files")
            self.settings_window._fetch("ffmpeg")

    def run(self) -> int:
        return self.qt.exec()


def main() -> int:
    return CliprApp(sys.argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
