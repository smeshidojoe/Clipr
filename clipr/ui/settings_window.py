"""Окно настроек: слева разделы (как «Системные настройки» macOS), справа
карточки со строками. Рамка окна своя: полоса сверху служит заголовком, а
системные тень, привязка к краям и изменение размера остаются (см. chrome).

Всё применяется сразу, без «Сохранить»: переключили — уже работает. Запись
на диск откладывается на полсекунды, чтобы ползунок не писал файл на каждом
шаге. Цвета и шрифты — от панели записи: тёмное стекло, оранжевый акцент.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QPointF, QRectF, Qt, QThread, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import autostart, tools
from ..config import scaled_size
from ..constants import APP_NAME, APP_VERSION, DEVELOPER_URL, RELEASES_URL
from ..i18n import LANGUAGES, tr
from ..winapi import system_animations_enabled
from . import motion, theme
from .chrome import WindowsChrome
from .controls import (
    Group, HotkeyEdit, ProgressLine, Row, Segmented, Switch, Tween, ValueSlider,
    crossfade, hbox, label, section, ui_font,
)
from .icons import app_pixmap, glyph
from .widgets import PressButton

SIDEBAR_W = 196
ROW_H = 34
# Разделы: ключ (он же ключ перевода nav.<ключ>) и пиктограмма.
NAV = [
    ("capture", "record"),
    ("gif", "film"),
    ("keys", "keyboard"),
    ("files", "folder"),
    ("system", "power"),
    ("about", "info"),
]

FPS_CHOICES = [("10", "10"), ("15", "15"), ("20", "20"), ("30", "30"), ("50", "50"),
               ("60", "60")]
DITHER_CHOICES = [("bayer:bayer_scale=3", "dither.bayer"), ("sierra2_4a", "dither.diffusion"),
                  ("none", "dither.none")]
COMPRESS_CHOICES = [("none", "compress.none"), ("light", "compress.light"),
                    ("strong", "compress.strong")]
# Настройка, имя для HotkeyManager, ключ подписи.
HOTKEYS = [
    ("hotkey_start_stop", "record", "keys.record"),
    ("hotkey_pause", "pause", "keys.pause"),
    ("hotkey_region", "region", "keys.region"),
    ("hotkey_toggle_panel", "panel", "keys.panel"),
]


def _translated(choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(key, tr(text)) for key, text in choices]


def dither_choices() -> list[tuple[str, str]]:
    return _translated(DITHER_CHOICES)


def compress_choices() -> list[tuple[str, str]]:
    return _translated(COMPRESS_CHOICES)


def nearest(choices: list[tuple[str, str]], value) -> str:
    """Значение из старых настроек может не совпасть ни с одним сегментом
    (fps 14, дизеринг floyd_steinberg) — берём ближайший, а не падаем."""
    keys = [k for k, _ in choices]
    if str(value) in keys:
        return str(value)
    try:
        number = float(value)
        return min(keys, key=lambda k: abs(float(k) - number))
    except (TypeError, ValueError):
        return "sierra2_4a" if "floyd" in str(value) else keys[0]


def _readable(path: str) -> str:
    """%TEMP% часто приходит короткой формой 8.3 (MIKETH~1) — показываем полную."""
    try:
        return os.path.realpath(path) if os.path.exists(path) else path
    except OSError:
        return path


def gif_dither(value: str) -> str:
    return nearest(DITHER_CHOICES, value)


class ToolDownload(QThread):
    """Скачивание бинарника в фоне — иначе окно замирает на всё время загрузки."""

    progress = Signal(float)
    done = Signal(str)
    failed = Signal(str, str)

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind

    def run(self) -> None:
        try:
            fetch = tools.download_ffmpeg if self.kind == "ffmpeg" else tools.download_gifsicle
            fetch(lambda fraction: self.progress.emit(float(fraction)))
            self.done.emit(self.kind)
        except Exception as exc:                # сеть, диск, битый архив
            self.failed.emit(self.kind, str(exc))


# --- заголовок и навигация --------------------------------------------------------------- #

class TitleBar(QWidget):
    """Полоса заголовка: слева продолжает цвет навигации, справа — цвет страницы."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.layout_.addStretch(1)
        self._mark = app_pixmap(44, False)
        self._mark.setDevicePixelRatio(2)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(0, 0, SIDEBAR_W, self.height(), QColor(theme.SIDEBAR))
        painter.fillRect(SIDEBAR_W, 0, self.width() - SIDEBAR_W, self.height(),
                         QColor(theme.WINDOW))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(18, (self.height() - 22) // 2, self._mark)
        painter.setFont(ui_font(14, QFont.DemiBold))
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(QRectF(48, 0, SIDEBAR_W - 56, self.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, tr("settings.title"))


class NavBar(QWidget):
    """Разделы: подсветка выбранного пункта переезжает к новому, а не
    перескакивает — видно, откуда и куда перешли."""

    selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(SIDEBAR_W)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.current = 0
        self._hover = -1
        self._keyboard = False          # кольцо фокуса — только при работе с клавиатуры
        self._pos = Tween(self, 0.0, motion.SEGMENT_MS, motion.EASE_OUT)
        self._icons = {key: (glyph(name, 18, theme.TEXT_DIM), glyph(name, 18, theme.ACCENT))
                       for key, name in NAV}

    def select(self, key: str, animate: bool = True) -> None:
        for index, (k, _) in enumerate(NAV):
            if k == key:
                self.current = index
                self._pos.to(float(index), animate and not motion.reduced_motion())

    def _row(self, index: int) -> QRectF:
        return QRectF(10, 8 + index * (ROW_H + 2), SIDEBAR_W - 20, ROW_H)

    def _index_at(self, position) -> int:
        return next((i for i in range(len(NAV)) if self._row(i).contains(position)), -1)

    def _choose(self, index: int) -> None:
        index = max(0, min(len(NAV) - 1, index))
        if index != self.current:
            self.current = index
            self._pos.to(float(index), not motion.reduced_motion())
            self.selected.emit(NAV[index][0])

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.setCursor(Qt.PointingHandCursor if index >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def focusInEvent(self, event) -> None:
        self._keyboard = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        self.update()

    def focusOutEvent(self, event) -> None:
        self._keyboard = False
        self.update()

    def mousePressEvent(self, event) -> None:
        # Выбор — на нажатие, а не на отпускание: навигация отвечает сразу.
        self._keyboard = False
        index = self._index_at(event.position())
        if index >= 0:
            self._choose(index)

    def keyPressEvent(self, event) -> None:
        self._keyboard = True
        if event.key() == Qt.Key_Up:
            self._choose(self.current - 1)
        elif event.key() == Qt.Key_Down:
            self._choose(self.current + 1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        selection = self._row(0).translated(0, self._pos.value * (ROW_H + 2))
        accent = QColor(theme.ACCENT)
        accent.setAlpha(34)
        painter.setBrush(accent)
        painter.drawRoundedRect(selection, 8, 8)
        for index, (key, _) in enumerate(NAV):
            title = tr(f"nav.{key}")
            row = self._row(index)
            active = index == self.current
            if index == self._hover and not active:
                painter.setBrush(QColor(255, 255, 255, 12))
                painter.drawRoundedRect(row, 8, 8)
            icon = self._icons[key][1 if active else 0]
            icon.paint(painter, int(row.left() + 10), int(row.center().y() - 9), 18, 18)
            painter.setFont(ui_font(13, QFont.DemiBold if active else QFont.Normal))
            painter.setPen(QColor(theme.ACCENT if active else theme.TEXT))
            painter.drawText(row.adjusted(38, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, title)
        if self.hasFocus() and self._keyboard:
            painter.setPen(QColor(255, 255, 255, 60))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self._row(self.current).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        # Внизу — версия: здесь её ищут глазами.
        painter.setPen(QColor(theme.TEXT_DIM))
        painter.setFont(ui_font(11))
        painter.drawText(QRectF(18, self.height() - 34, SIDEBAR_W - 30, 18),
                         Qt.AlignVCenter | Qt.AlignLeft, f"{APP_NAME} {APP_VERSION}")


def _page(title: str) -> tuple[QScrollArea, QVBoxLayout]:
    """Страница: прокрутка, заголовок, дальше — карточки."""
    body = QWidget()
    body.setObjectName("page")
    column = QVBoxLayout(body)
    column.setContentsMargins(28, 14, 28, 28)
    column.setSpacing(0)
    column.addWidget(label(title, "pageTitle"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    return scroll, column


class AboutHeader(QWidget):
    """Знак программы, имя, версия — карточка «Об этом Mac» в миниатюре."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(96)
        self._mark = app_pixmap(128, False)
        self._mark.setDevicePixelRatio(2)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(QPointF(4, 16), self._mark)
        painter.setPen(QColor(theme.TEXT))
        painter.setFont(ui_font(20, QFont.Bold))
        painter.drawText(QRectF(88, 22, 300, 28), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
        painter.setPen(QColor(theme.TEXT_DIM))
        painter.setFont(ui_font(13))
        painter.drawText(QRectF(88, 50, 400, 20), Qt.AlignLeft | Qt.AlignVCenter,
                         tr("about.tagline", version=APP_VERSION))


# --- окно ----------------------------------------------------------------------------------- #

class SettingsWindow(QWidget):
    """`ctl` — CliprApp: у него настройки, горячие клавиши и обновления."""

    def __init__(self, ctl) -> None:
        super().__init__(None, Qt.Window | Qt.WindowMinimizeButtonHint
                         | Qt.WindowCloseButtonHint)
        self.ctl = ctl
        self.settings = ctl.settings
        self.setObjectName("settings")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowTitle(tr("settings.window"))
        self.resize(780, 580)
        self.setMinimumSize(700, 460)
        self.downloads: dict[str, ToolDownload] = {}
        self._fade: QVariantAnimation | None = None
        self._closing = False

        # Запись на диск — одна на серию изменений (ползунок даёт десятки шагов).
        self._save_timer = QTimer(self, singleShot=True, interval=400,
                                  timeout=self.settings.save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.bar = TitleBar()
        root.addWidget(self.bar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = NavBar()
        body.addWidget(self.nav)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.chrome = WindowsChrome(self, self.bar, self.bar.layout_)

        builders = {
            "capture": self._capture_page, "gif": self._gif_page, "keys": self._keys_page,
            "files": self._files_page, "system": self._system_page, "about": self._about_page,
        }
        for key, _ in NAV:
            scroll, column = _page(tr(f"nav.{key}"))
            builders[key](column)
            column.addStretch(1)
            self.stack.addWidget(scroll)
        self.nav.selected.connect(self.show_page)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)
        # Сигналы приходят из рабочего потока обновлений. Только связанные
        # методы QObject Qt доставляет в поток интерфейса; лямбда исполнилась
        # бы прямо в рабочем потоке и трогала бы виджеты оттуда.
        ctl.updates.state.connect(self._on_update_state)
        ctl.updates.progress.connect(self.upd_bar.set_fraction)
        self.sync()

    # ---- общее ------------------------------------------------------------------------
    def _put(self, key: str, value) -> None:
        """Поменять настройку, применить сразу, записать на диск чуть позже."""
        if getattr(self.settings, key) == value:
            return
        setattr(self.settings, key, value)
        self._save_timer.start()
        self.ctl.setting_changed(key)
        self.sync()

    def show_page(self, key: str) -> None:
        keys = [k for k, _ in NAV]
        if key in keys:
            self.nav.select(key, animate=self.isVisible())
            crossfade(self.stack, keys.index(key))

    def present(self, page: str | None = None, animate: bool = True) -> None:
        if page:
            self.show_page(page)
            if not animate:
                self.nav.select(page, animate=False)
        appearing = not self.isVisible() or self._closing
        self._closing = False
        if self.isMinimized():
            self.showNormal()
        if appearing:
            self.sync()
            self._animate_opacity(1.0, motion.ENTER_MS if animate else 0)
        self.show()
        self.raise_()
        self.activateWindow()

    def current_page(self) -> str:
        return NAV[self.nav.current][0]

    def dispose(self) -> None:
        """Убрать окно насовсем (его заменило новое, например после смены языка).
        Идущая загрузка FFmpeg доживает своё: поток принадлежит окну, и удалять
        окно до конца потока нельзя."""
        self._save_timer.stop()
        self.settings.save()
        self.hide()
        running = [w for w in self.downloads.values() if w.isRunning()]
        if not running:
            self.deleteLater()
            return
        for worker in running:
            worker.finished.connect(
                lambda: all(not w.isRunning() for w in running) and self.deleteLater())

    def _animate_opacity(self, target: float, duration: int, then=None) -> None:
        """Окно проявляется и тает. Сдвигов нет: окно Windows не «выезжает»,
        оно появляется там, где его ждут. Выход короче входа — его не
        рассматривают, его просто ждут."""
        if self._fade is not None:
            self._fade.stop()
        if motion.reduced_motion() and target >= 1.0:
            duration = 0
        if target >= 1.0 and not self.isVisible():
            self.setWindowOpacity(0.0)
        start = self.windowOpacity()
        if duration <= 0 or abs(start - target) < 1e-3:
            self.setWindowOpacity(target)
            if then:
                then()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setDuration(duration)
        anim.setEasingCurve(motion.EASE_OUT)
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        # DeleteWhenStopped удалит C++-объект — ссылку отпускаем раньше.
        anim.finished.connect(lambda: setattr(self, "_fade", None))
        if then:
            anim.finished.connect(then)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._fade = anim

    def closeEvent(self, event) -> None:
        # Окно не уничтожаем: второе открытие мгновенное и с тем же разделом.
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self._save_timer.stop()
        self.settings.save()

        def finish() -> None:
            if self._closing:
                self.hide()
                self._closing = False
        self._animate_opacity(0.0, motion.EXIT_MS, finish)

    def nativeEvent(self, event_type, message):
        handled = self.chrome.native_event(event_type, message)
        return handled if handled is not None else super().nativeEvent(event_type, message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.chrome.on_show()

    # ---- страницы ---------------------------------------------------------------------
    def _capture_page(self, column: QVBoxLayout) -> None:
        s = self.settings
        group = Group()
        self.capture_fps = ValueSlider(10, 60, s.capture_fps,
                                       lambda v: tr("capture.fps_value", value=v), step=5)
        self.capture_fps.changed.connect(lambda v: self._put("capture_fps", v))
        group.add(Row(tr("capture.fps"), self.capture_fps, tr("capture.fps_note")))
        self.backend = Segmented([("auto", tr("capture.auto")), ("ddagrab", "Duplication"),
                                  ("gdigrab", "GDI")], s.capture_method)
        self.backend.changed.connect(lambda v: self._put("capture_method", v))
        group.add(Row(tr("capture.backend"), self.backend, tr("capture.backend_note")))
        self.draw_mouse = Switch(s.draw_mouse)
        self.draw_mouse.toggled.connect(lambda on: self._put("draw_mouse", on))
        group.add(Row(tr("capture.cursor"), self.draw_mouse))
        column.addWidget(section(tr("capture.section")))
        column.addWidget(group)

    def _gif_page(self, column: QVBoxLayout) -> None:
        s = self.settings
        group = Group()
        self.gif_scale = ValueSlider(10, 100, s.gif_scale, lambda v: f"{v} %", step=5)
        self.gif_scale.changed.connect(lambda v: self._put("gif_scale", v))
        self.scale_row = group.add(Row(tr("gif.size"), self.gif_scale))
        self.gif_fps = Segmented(FPS_CHOICES, nearest(FPS_CHOICES, s.gif_fps))
        self.gif_fps.changed.connect(lambda v: self._put("gif_fps", int(v)))
        group.add(Row(tr("gif.fps"), self.gif_fps, tr("gif.fps_note")))
        self.dither = Segmented(dither_choices(), gif_dither(s.gif_dither))
        self.dither.changed.connect(lambda v: self._put("gif_dither", v))
        group.add(Row(tr("gif.dither"), self.dither, tr("gif.dither_note")))
        column.addWidget(section(tr("gif.section_defaults")))
        column.addWidget(group)

        group = Group()
        self.compress = Segmented(compress_choices(), s.gif_compress)
        self.compress.changed.connect(self._on_compress)
        self.gifsicle_bar = ProgressLine()
        self.gifsicle_bar.setFixedWidth(90)
        self.gifsicle_bar.hide()
        self.compress_row = group.add(Row(tr("gif.compress"),
                                          hbox(self.gifsicle_bar, self.compress, spacing=12)))
        column.addWidget(section(tr("gif.section_compress")))
        column.addWidget(group)
        note = label(tr("gif.compress_explain"), "rowNote", wrap=True)
        note.setContentsMargins(16, 10, 16, 0)
        column.addWidget(note)

    def _keys_page(self, column: QVBoxLayout) -> None:
        group = Group()
        self.hotkey_edits: dict[str, HotkeyEdit] = {}
        self.hotkey_rows: dict[str, Row] = {}
        for key, _name, title in HOTKEYS:
            edit = HotkeyEdit(getattr(self.settings, key))
            edit.changed.connect(lambda value, k=key: self._on_hotkey(k, value))
            edit.capturing.connect(self.ctl.suspend_hotkeys)
            self.hotkey_edits[key] = edit
            self.hotkey_rows[key] = group.add(Row(tr(title), edit))
        column.addWidget(section(tr("keys.section")))
        column.addWidget(group)
        note = label(tr("keys.help"), "rowNote", wrap=True)
        note.setContentsMargins(16, 10, 16, 0)
        column.addWidget(note)

    def _files_page(self, column: QVBoxLayout) -> None:
        s = self.settings
        group = Group()
        change_out = PressButton(tr("files.change"))
        change_out.clicked.connect(lambda: self._browse("output_dir"))
        open_out = PressButton(tr("files.open"))
        open_out.clicked.connect(self.ctl.open_folder)
        self.out_row = group.add(Row(tr("files.output"), hbox(change_out, open_out)))
        change_tmp = PressButton(tr("files.change"))
        change_tmp.clicked.connect(lambda: self._browse("temp_dir"))
        self.tmp_row = group.add(Row(tr("files.temp"), change_tmp))
        self.keep_source = Switch(s.keep_source_video)
        self.keep_source.toggled.connect(lambda on: self._put("keep_source_video", on))
        group.add(Row(tr("files.keep"), self.keep_source, tr("files.keep_note")))
        column.addWidget(section(tr("files.section")))
        column.addWidget(group)

        group = Group()
        self.tool_rows: dict[str, Row] = {}
        self.tool_buttons: dict[str, PressButton] = {}
        self.tool_bars: dict[str, ProgressLine] = {}
        for kind, title in (("ffmpeg", "FFmpeg"), ("gifsicle", "gifsicle")):
            button = PressButton("")
            button.clicked.connect(lambda _=False, k=kind: self._fetch(k))
            bar = ProgressLine()
            bar.setFixedWidth(90)
            bar.hide()
            self.tool_buttons[kind] = button
            self.tool_bars[kind] = bar
            self.tool_rows[kind] = group.add(Row(title, hbox(bar, button, spacing=12)))
        folder = PressButton(tr("files.tools_folder"))
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(tools.TOOLS_DIR)))
        column.addWidget(section(tr("files.tools")))
        column.addWidget(group)
        holder = hbox(folder)
        holder.layout().addStretch(1)
        holder.layout().setContentsMargins(0, 10, 0, 0)
        column.addWidget(holder)

    def _system_page(self, column: QVBoxLayout) -> None:
        s = self.settings
        group = Group()
        self.autostart = Switch(s.autostart)
        self.autostart.toggled.connect(lambda on: self._put("autostart", on))
        self.autostart_row = group.add(Row(tr("system.autostart"), self.autostart))
        self.minimized = Switch(s.start_minimized)
        self.minimized.toggled.connect(lambda on: self._put("start_minimized", on))
        self.minimized_row = group.add(Row(tr("system.minimized"), self.minimized))
        column.addWidget(section(tr("system.section_start")))
        column.addWidget(group)

        group = Group()
        self.language = Segmented(list(LANGUAGES.items()), s.language)
        self.language.changed.connect(self._on_language)
        group.add(Row(tr("system.language"), self.language))
        column.addWidget(section(tr("system.section_language")))
        column.addWidget(group)

        group = Group()
        self.reduced = Switch(s.reduced_motion)
        self.reduced.toggled.connect(lambda on: self._put("reduced_motion", on))
        self.reduced_row = group.add(Row(tr("system.reduced"), self.reduced))
        column.addWidget(section(tr("system.section_motion")))
        column.addWidget(group)

    def _about_page(self, column: QVBoxLayout) -> None:
        s = self.settings
        column.addWidget(AboutHeader())
        group = Group()
        self.check_updates = Switch(s.check_updates)
        self.check_updates.toggled.connect(lambda on: self._put("check_updates", on))
        group.add(Row(tr("about.check"), self.check_updates, tr("about.check_note")))
        self.background = Switch(s.update_in_background)
        self.background.toggled.connect(lambda on: self._put("update_in_background", on))
        group.add(Row(tr("about.background"), self.background, tr("about.background_note")))
        self.upd_button = PressButton(tr("update.check_button"))
        self.upd_button.clicked.connect(self.ctl.update_action)
        self.upd_bar = ProgressLine()
        self.upd_bar.setFixedWidth(90)
        self.upd_row = group.add(Row(tr("about.update"), hbox(self.upd_bar, self.upd_button,
                                                        spacing=12)))
        column.addWidget(section(tr("about.section_updates")))
        column.addWidget(group)

        group = Group()
        releases = PressButton(tr("files.open"))
        releases.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        group.add(Row(tr("about.whats_new"), releases, tr("about.whats_new_note")))
        author = PressButton("SmeshidoJoe")
        author.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DEVELOPER_URL)))
        group.add(Row(tr("about.developer"), author))
        column.addWidget(section(tr("about.section_project")))
        column.addWidget(group)

    # ---- действия ----------------------------------------------------------------------
    def _browse(self, key: str) -> None:
        current = getattr(self.settings, key)
        chosen = QFileDialog.getExistingDirectory(self, tr("choose_folder"), current)
        if chosen:
            self._put(key, str(Path(chosen)))

    def _on_hotkey(self, key: str, value: str) -> None:
        self._put(key, value)
        self._sync_hotkeys()

    def _on_language(self, code: str) -> None:
        """Язык меняется на лету: программа пересобирает это окно на том же
        разделе, панель и меню трея — переподписывает."""
        self._put("language", code)

    def _on_compress(self, value: str) -> None:
        self._put("gif_compress", value)
        # gifsicle нужен только для сжатия — и качается при первом выборе.
        if value != "none" and not tools.have_gifsicle():
            self._fetch("gifsicle")

    def _fetch(self, kind: str) -> None:
        running = self.downloads.get(kind)
        if running is not None and running.isRunning():
            return
        worker = ToolDownload(kind, self)
        worker.progress.connect(lambda f, k=kind: self._on_tool_progress(k, f))
        worker.done.connect(self._on_tool_done)
        worker.failed.connect(self._on_tool_failed)
        self.downloads[kind] = worker
        self._tool_error = {}
        worker.start()
        self.sync()

    def _downloading(self, kind: str) -> bool:
        worker = self.downloads.get(kind)
        return worker is not None and worker.isRunning()

    def _on_tool_progress(self, kind: str, fraction: float) -> None:
        self.tool_bars[kind].set_fraction(fraction)
        if kind == "gifsicle":
            self.gifsicle_bar.set_fraction(fraction)

    def _on_tool_done(self, kind: str) -> None:
        self.downloads.pop(kind, None)
        self.sync()

    def _on_tool_failed(self, kind: str, message: str) -> None:
        self.downloads.pop(kind, None)
        self._tool_error = {kind: message}
        self.sync()

    # ---- согласование с настройками -------------------------------------------------------
    def sync(self) -> None:
        """Значения и подписи — из настроек: их могли поменять из трея, редактора
        или горячей клавишей, пока окно было скрыто."""
        s = self.settings
        self.capture_fps.set_value(s.capture_fps)
        self.backend.set_value(s.capture_method)
        self.draw_mouse.set_checked(s.draw_mouse)
        self.gif_scale.set_value(s.gif_scale)
        self.gif_fps.set_value(nearest(FPS_CHOICES, s.gif_fps))
        self.dither.set_value(gif_dither(s.gif_dither))
        self.compress.set_value(s.gif_compress)
        self.keep_source.set_checked(s.keep_source_video)
        # Автозапуск мог включить установщик — правда в реестре, не в JSON.
        self.autostart.set_checked(autostart.is_enabled() if autostart.supported()
                                   else s.autostart)
        self.minimized.set_checked(s.start_minimized)
        self.reduced.set_checked(s.reduced_motion)
        self.check_updates.set_checked(s.check_updates)
        self.background.set_checked(s.update_in_background)
        self.background.setEnabled(s.check_updates)

        width, height = self.ctl.region_size()
        out_w, out_h = scaled_size(width, height, s.gif_scale)
        self.scale_row.set_note(tr("gif.size_note", w=width, h=height, ow=out_w, oh=out_h))

        self.out_row.set_note(_readable(s.output_dir))
        self.tmp_row.set_note(tr("files.temp_note", path=_readable(s.temp_dir)))
        self._sync_tools()
        self._sync_hotkeys()

        self.language.set_value(s.language)
        panel_key = s.hotkey_toggle_panel or tr("system.tray_icon")
        self.minimized_row.set_note(tr("system.minimized_note", shortcut=panel_key))
        frozen = autostart.supported()
        self.autostart.setEnabled(frozen)
        self.autostart_row.set_note("" if frozen else tr("system.autostart_dev"))
        self.reduced_row.set_note(tr("system.reduced_note") if system_animations_enabled()
                                  else tr("system.reduced_os"))
        self._sync_update()

    def _sync_tools(self) -> None:
        errors = getattr(self, "_tool_error", {})
        for kind in ("ffmpeg", "gifsicle"):
            path = tools.ffmpeg_path() if kind == "ffmpeg" else tools.gifsicle_path()
            busy = self._downloading(kind)
            button, bar, row = self.tool_buttons[kind], self.tool_bars[kind], self.tool_rows[kind]
            bar.setVisible(busy)
            button.setVisible(not busy)
            if busy:
                row.set_note(tr("tool.downloading"))
            elif kind in errors:
                row.set_note(tr("tool.failed", error=errors[kind]), warn=True)
            elif path:
                where = tr("tool.own_copy") if path.startswith(tools.TOOLS_DIR) else path
                row.set_note(tr("tool.installed", where=where))
            else:
                row.set_note(tr(f"tool.missing_{kind}"), warn=kind == "ffmpeg")
            button.setText(tr("tool.update") if path else tr(f"tool.get_{kind}"))

        compress = self.settings.gif_compress
        busy = self._downloading("gifsicle")
        self.gifsicle_bar.setVisible(busy)
        if compress == "none":
            self.compress_row.set_note(tr("compress.off_note"))
        elif busy:
            self.compress_row.set_note(tr("compress.downloading"))
        elif not tools.have_gifsicle():
            self.compress_row.set_note(tr("compress.missing"), warn=True)
        else:
            self.compress_row.set_note(tr(f"compress.{compress}_note"))

    def _sync_hotkeys(self) -> None:
        """Сначала совпадения внутри Clipr, потом — занятые другими программами."""
        failed = self.ctl.failed_hotkeys()
        seen: dict[str, str] = {}
        for key, name, title in HOTKEYS:
            value = getattr(self.settings, key)
            edit, row = self.hotkey_edits[key], self.hotkey_rows[key]
            edit.set_value(value)
            if value and value in seen:
                edit.set_error(True)
                row.set_note(tr("keys.duplicate", title=seen[value]), warn=True)
            elif name in failed:
                edit.set_error(True)
                row.set_note(tr("keys.taken"), warn=True)
            else:
                edit.set_error(False)
                row.set_note("")
            if value:
                seen.setdefault(value, tr(title))

    def _on_update_state(self, _state: str, _version: str) -> None:
        self._sync_update()

    def _sync_update(self) -> None:
        state, version, fraction = self.ctl.update_status()
        text = tr("update." + (state or "idle"),
                  version=version if state else APP_VERSION)
        self.upd_row.set_note(text, warn=state in ("error", "failed"))
        self.upd_bar.setVisible(state == "downloading")
        if state == "downloading":
            self.upd_bar.set_fraction(fraction)
        self.upd_button.setText(tr({"available": "update.install_button",
                                    "ready": "update.restart_button"}
                                   .get(state, "update.check_button")))
        self.upd_button.setVisible(state != "downloading")
        self.upd_button.setEnabled(state not in ("checking", "dev"))


__all__ = ["FPS_CHOICES", "SettingsWindow", "compress_choices", "dither_choices",
           "gif_dither", "nearest"]

