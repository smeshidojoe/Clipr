"""Окно настроек."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QThread, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
)

from .. import autostart, tools
from ..constants import APP_NAME, APP_VERSION, DEVELOPER_URL
from . import motion
from .widgets import PressButton


class ToolDownload(QThread):
    """Скачивание бинарника в фоне — иначе окно замирает на всё время загрузки."""

    progress = Signal(int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind

    def run(self) -> None:
        try:
            fetch = tools.download_ffmpeg if self.kind == "ffmpeg" else tools.download_gifsicle
            fetch(lambda fraction: self.progress.emit(int(fraction * 100)))
            self.done.emit(self.kind)
        except Exception as exc:                # сеть, диск, битый архив
            self.failed.emit(str(exc))


def _path_row(edit: QLineEdit, on_browse) -> QHBoxLayout:
    row = QHBoxLayout()
    browse = PressButton("Обзор…")
    browse.clicked.connect(on_browse)
    row.addWidget(edit, 1)
    row.addWidget(browse)
    return row


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.download: ToolDownload | None = None
        self.setWindowTitle(f"{APP_NAME} — настройки")
        self.setMinimumWidth(520)
        self.setWindowOpacity(0.0)
        self._build()
        self._refresh_tools()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if motion.reduced_motion():
            self.setWindowOpacity(1.0)
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(1.0)
        anim.setDuration(motion.ENTER_MS)
        anim.setEasingCurve(motion.ENTER_CURVE)
        anim.valueChanged.connect(lambda value: self.setWindowOpacity(float(value)))
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._enter_anim = anim

    # ---- построение ------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        root.addWidget(self._capture_group())
        root.addWidget(self._gif_group())
        root.addWidget(self._hotkeys_group())
        root.addWidget(self._tools_group())

        misc = QHBoxLayout()
        self.check_reduced = QCheckBox("Меньше анимации")
        self.check_reduced.setChecked(self.settings.reduced_motion)
        self.check_autostart = QCheckBox("Запускать вместе с Windows")
        self.check_autostart.setChecked(self.settings.autostart or autostart.is_enabled())
        self.check_updates = QCheckBox("Проверять обновления")
        self.check_updates.setChecked(self.settings.check_updates)
        misc.addWidget(self.check_reduced)
        misc.addWidget(self.check_autostart)
        misc.addWidget(self.check_updates)
        misc.addStretch(1)
        root.addLayout(misc)

        about = QLabel(
            f"{APP_NAME} {APP_VERSION} · <a href='{DEVELOPER_URL}' "
            f"style='color:#F5623D;text-decoration:none'>SmeshidoJoe</a>"
        )
        about.setObjectName("caption")
        about.setOpenExternalLinks(True)
        root.addWidget(about)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PressButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = PressButton("Сохранить")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _capture_group(self) -> QGroupBox:
        box = QGroupBox("Запись")
        form = QFormLayout(box)
        form.setSpacing(9)

        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(5, 60)
        self.spin_fps.setValue(self.settings.capture_fps)
        form.addRow("Кадры/с", self.spin_fps)

        self.combo_backend = QComboBox()
        self.combo_backend.addItems(["gdigrab", "ddagrab"])
        self.combo_backend.setCurrentText(self.settings.capture_backend)
        self.combo_backend.setToolTip(
            "gdigrab работает везде. ddagrab снимает через GPU (Windows 8+, свежие "
            "сборки FFmpeg) — легче по нагрузке, но есть не в каждой сборке."
        )
        form.addRow("Захват", self.combo_backend)

        self.check_mouse = QCheckBox("Записывать курсор")
        self.check_mouse.setChecked(self.settings.draw_mouse)
        form.addRow("", self.check_mouse)

        self.edit_out = QLineEdit(self.settings.output_dir)
        form.addRow("Готовые GIF", _path_row(self.edit_out, lambda: self._browse(self.edit_out)))

        self.edit_temp = QLineEdit(self.settings.temp_dir)
        self.edit_temp.setToolTip("Промежуточные MP4 во время записи")
        form.addRow("Временные файлы", _path_row(self.edit_temp, lambda: self._browse(self.edit_temp)))

        self.check_keep = QCheckBox("Сохранять исходное видео рядом с GIF")
        self.check_keep.setChecked(self.settings.keep_source_video)
        form.addRow("", self.check_keep)
        return box

    def _gif_group(self) -> QGroupBox:
        box = QGroupBox("GIF по умолчанию")
        form = QFormLayout(box)
        form.setSpacing(9)

        self.spin_gif_fps = QSpinBox()
        self.spin_gif_fps.setRange(5, 30)
        self.spin_gif_fps.setValue(self.settings.gif_fps)
        form.addRow("Кадры/с", self.spin_gif_fps)

        self.spin_width = QSpinBox()
        self.spin_width.setRange(0, 3840)
        self.spin_width.setSingleStep(20)
        self.spin_width.setSpecialValueText("как в исходнике")
        self.spin_width.setValue(self.settings.gif_width)
        form.addRow("Ширина", self.spin_width)

        self.spin_lossy = QSpinBox()
        self.spin_lossy.setRange(0, 200)
        self.spin_lossy.setSpecialValueText("выкл")
        self.spin_lossy.setValue(self.settings.gif_lossy)
        self.spin_lossy.setToolTip("gifsicle --lossy: больше значение — сильнее сжатие")
        form.addRow("Потери", self.spin_lossy)
        return box

    def _hotkeys_group(self) -> QGroupBox:
        box = QGroupBox("Горячие клавиши (глобальные)")
        form = QFormLayout(box)
        form.setSpacing(9)
        self.key_record = QKeySequenceEdit(QKeySequence(self.settings.hotkey_start_stop))
        self.key_pause = QKeySequenceEdit(QKeySequence(self.settings.hotkey_pause))
        self.key_region = QKeySequenceEdit(QKeySequence(self.settings.hotkey_region))
        self.key_panel = QKeySequenceEdit(QKeySequence(self.settings.hotkey_toggle_panel))
        form.addRow("Старт / стоп", self.key_record)
        form.addRow("Пауза / продолжить", self.key_pause)
        form.addRow("Выбрать область", self.key_region)
        form.addRow("Показать / скрыть панель", self.key_panel)
        return box

    def _tools_group(self) -> QGroupBox:
        box = QGroupBox("Внешние программы")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.lbl_tools = QLabel()
        self.lbl_tools.setObjectName("caption")
        self.lbl_tools.setWordWrap(True)
        layout.addWidget(self.lbl_tools)

        row = QHBoxLayout()
        self.btn_get_ffmpeg = PressButton("Скачать FFmpeg")
        self.btn_get_ffmpeg.clicked.connect(lambda: self._fetch("ffmpeg"))
        self.btn_get_gifsicle = PressButton("Скачать gifsicle")
        self.btn_get_gifsicle.clicked.connect(lambda: self._fetch("gifsicle"))
        btn_open = PressButton("Папка программ")
        btn_open.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(tools.TOOLS_DIR)))
        row.addWidget(self.btn_get_ffmpeg)
        row.addWidget(self.btn_get_gifsicle)
        row.addWidget(btn_open)
        row.addStretch(1)
        layout.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        return box

    # ---- действия --------------------------------------------------------
    def _browse(self, edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Выберите папку", edit.text())
        if chosen:
            edit.setText(chosen)

    def _refresh_tools(self) -> None:
        status = tools.status_lines()
        self.lbl_tools.setText(
            "FFmpeg: {ffmpeg}\ngifsicle: {gifsicle}".format(**status)
            + "\ngifsicle необязателен: без него GIF собирается, но без финального сжатия."
        )
        self.btn_get_ffmpeg.setText(
            "Обновить FFmpeg" if tools.have_ffmpeg() else "Скачать FFmpeg (~80 МБ)")
        self.btn_get_gifsicle.setText(
            "Обновить gifsicle" if tools.have_gifsicle() else "Скачать gifsicle (~230 КБ)")

    def _fetch(self, kind: str) -> None:
        if self.download and self.download.isRunning():
            return
        self.progress.setValue(0)
        self.progress.show()
        self.btn_get_ffmpeg.setEnabled(False)
        self.btn_get_gifsicle.setEnabled(False)

        self.download = ToolDownload(kind, self)
        self.download.progress.connect(self.progress.setValue)
        self.download.done.connect(self._on_download_done)
        self.download.failed.connect(self._on_download_failed)
        self.download.start()

    def _on_download_done(self, kind: str) -> None:
        self._finish_download()
        self.lbl_tools.setText(f"{kind} установлен.")
        self._refresh_tools()

    def _on_download_failed(self, message: str) -> None:
        self._finish_download()
        self.lbl_tools.setText(f"Не удалось скачать: {message}")

    def _finish_download(self) -> None:
        self.progress.hide()
        self.btn_get_ffmpeg.setEnabled(True)
        self.btn_get_gifsicle.setEnabled(True)

    def _save(self) -> None:
        s = self.settings
        s.capture_fps = self.spin_fps.value()
        s.capture_backend = self.combo_backend.currentText()
        s.draw_mouse = self.check_mouse.isChecked()
        s.output_dir = str(Path(self.edit_out.text()).expanduser())
        s.temp_dir = str(Path(self.edit_temp.text()).expanduser())
        s.keep_source_video = self.check_keep.isChecked()
        s.gif_fps = self.spin_gif_fps.value()
        s.gif_width = self.spin_width.value()
        s.gif_lossy = self.spin_lossy.value()
        s.hotkey_start_stop = self.key_record.keySequence().toString(QKeySequence.PortableText)
        s.hotkey_pause = self.key_pause.keySequence().toString(QKeySequence.PortableText)
        s.hotkey_region = self.key_region.keySequence().toString(QKeySequence.PortableText)
        s.hotkey_toggle_panel = self.key_panel.keySequence().toString(QKeySequence.PortableText)
        s.reduced_motion = self.check_reduced.isChecked()
        s.autostart = self.check_autostart.isChecked()
        s.check_updates = self.check_updates.isChecked()
        s.save()
        self.applied.emit()
        self.accept()

    def closeEvent(self, event) -> None:
        if self.download and self.download.isRunning():
            self.download.wait(2000)
        super().closeEvent(event)
