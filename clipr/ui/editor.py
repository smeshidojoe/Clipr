"""Post-processing window: preview, trim, crop, export to GIF."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QKeySequence, QMouseEvent, QPainter,
    QPen, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..ffmpeg import GifExporter, Region, probe
from . import motion, theme
from .icons import glyph
from .widgets import PressButton

HANDLE = 9.0  # px grab radius for crop handles
MIN_CROP = 24.0


class PreviewView(QGraphicsView):
    """Video preview with a rubber-band crop rectangle drawn over it.

    The crop is stored in *video pixel* coordinates so it survives resizing the
    window, and is mapped to the view only for drawing and hit-testing.
    """

    crop_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setStyleSheet("background: #101014; border-radius: 12px;")
        self.setMouseTracking(True)

        self.video_item = QGraphicsVideoItem()
        self.scene().addItem(self.video_item)

        self._source_size = QSizeF(0, 0)
        self._crop: QRectF | None = None  # video pixels
        self._drag_mode: str | None = None
        self._drag_start = QPointF()
        self._crop_at_press: QRectF | None = None

    # ---- geometry --------------------------------------------------------
    def set_source_size(self, width: int, height: int) -> None:
        self._source_size = QSizeF(width, height)
        self._crop = None
        self.relayout()

    def relayout(self) -> None:
        if self._source_size.isEmpty():
            return
        area = self.viewport().rect()
        scale = min(
            area.width() / self._source_size.width(),
            area.height() / self._source_size.height(),
        )
        size = QSizeF(self._source_size.width() * scale, self._source_size.height() * scale)
        self.video_item.setSize(size)
        self.video_item.setPos(
            (area.width() - size.width()) / 2.0,
            (area.height() - size.height()) / 2.0,
        )
        self.setSceneRect(0, 0, area.width(), area.height())
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout()

    def _video_rect(self) -> QRectF:
        return QRectF(self.video_item.pos(), self.video_item.size())

    def _scale(self) -> float:
        if self._source_size.isEmpty() or self.video_item.size().isEmpty():
            return 1.0
        return self.video_item.size().width() / self._source_size.width()

    def _to_view(self, rect: QRectF) -> QRectF:
        scale = self._scale()
        origin = self.video_item.pos()
        return QRectF(
            origin.x() + rect.x() * scale,
            origin.y() + rect.y() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    def _to_video(self, point: QPointF) -> QPointF:
        scale = self._scale() or 1.0
        origin = self.video_item.pos()
        return QPointF((point.x() - origin.x()) / scale, (point.y() - origin.y()) / scale)

    def _clamp_to_source(self, rect: QRectF) -> QRectF:
        bounds = QRectF(0, 0, self._source_size.width(), self._source_size.height())
        return rect.intersected(bounds)

    # ---- crop API --------------------------------------------------------
    def crop_region(self) -> Region | None:
        if self._crop is None:
            return None
        rect = self._clamp_to_source(self._crop.normalized())
        if rect.width() < MIN_CROP or rect.height() < MIN_CROP:
            return None
        x, y = int(rect.x()), int(rect.y())
        width, height = int(rect.width()), int(rect.height())
        return Region(x - x % 2, y - y % 2, width - width % 2, height - height % 2)

    def clear_crop(self) -> None:
        self._crop = None
        self.viewport().update()
        self.crop_changed.emit()

    # ---- interaction -----------------------------------------------------
    def _hit(self, pos: QPointF) -> str | None:
        if self._crop is None:
            return None
        rect = self._to_view(self._crop.normalized())
        corners = {
            "tl": rect.topLeft(), "tr": rect.topRight(),
            "bl": rect.bottomLeft(), "br": rect.bottomRight(),
        }
        for name, point in corners.items():
            if (point - pos).manhattanLength() <= HANDLE * 2:
                return name
        return "move" if rect.contains(pos) else None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._source_size.isEmpty():
            return
        pos = QPointF(event.position())
        hit = self._hit(pos)
        self._drag_start = self._to_video(pos)
        self._crop_at_press = QRectF(self._crop) if self._crop else None
        if hit is None:
            self._drag_mode = "new"
            self._crop = QRectF(self._drag_start, self._drag_start)
        else:
            self._drag_mode = hit
        self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = QPointF(event.position())
        if self._drag_mode is None:
            hit = self._hit(pos)
            cursors = {
                "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
                "move": Qt.SizeAllCursor,
            }
            self.setCursor(cursors.get(hit, Qt.CrossCursor))
            return

        current = self._to_video(pos)
        if self._drag_mode == "new":
            self._crop = QRectF(self._drag_start, current).normalized()
        elif self._drag_mode == "move" and self._crop_at_press:
            delta = current - self._drag_start
            moved = self._crop_at_press.translated(delta)
            moved.moveLeft(min(max(moved.x(), 0), self._source_size.width() - moved.width()))
            moved.moveTop(min(max(moved.y(), 0), self._source_size.height() - moved.height()))
            self._crop = moved
        elif self._crop_at_press:
            rect = QRectF(self._crop_at_press)
            if self._drag_mode == "tl":
                rect.setTopLeft(current)
            elif self._drag_mode == "tr":
                rect.setTopRight(current)
            elif self._drag_mode == "bl":
                rect.setBottomLeft(current)
            elif self._drag_mode == "br":
                rect.setBottomRight(current)
            self._crop = rect
        self._crop = self._clamp_to_source(self._crop.normalized())
        self.viewport().update()
        self.crop_changed.emit()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self._drag_mode == "new" and self._crop is not None:
            rect = self._crop.normalized()
            if rect.width() < MIN_CROP or rect.height() < MIN_CROP:
                self._crop = None  # a click clears the crop
        self._drag_mode = None
        self.viewport().update()
        self.crop_changed.emit()

    # ---- painting --------------------------------------------------------
    def drawForeground(self, painter: QPainter, _rect: QRectF) -> None:
        if self._source_size.isEmpty():
            return
        video = self._video_rect()
        painter.setRenderHint(QPainter.Antialiasing)

        if self._crop is None:
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(video.adjusted(0.5, 0.5, -0.5, -0.5))
            return

        crop = self._to_view(self._crop.normalized())
        shade = QColor(0, 0, 0, 130)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        painter.drawRect(QRectF(video.left(), video.top(), video.width(), crop.top() - video.top()))
        painter.drawRect(QRectF(video.left(), crop.bottom(), video.width(), video.bottom() - crop.bottom()))
        painter.drawRect(QRectF(video.left(), crop.top(), crop.left() - video.left(), crop.height()))
        painter.drawRect(QRectF(crop.right(), crop.top(), video.right() - crop.right(), crop.height()))

        accent = QColor(theme.ACCENT)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(accent, 1.5))
        painter.drawRect(crop)

        # Thirds guides — free structure, and they make the crop feel deliberate.
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
        for i in (1, 2):
            x = crop.left() + crop.width() * i / 3.0
            y = crop.top() + crop.height() * i / 3.0
            painter.drawLine(QPointF(x, crop.top()), QPointF(x, crop.bottom()))
            painter.drawLine(QPointF(crop.left(), y), QPointF(crop.right(), y))

        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        for point in (crop.topLeft(), crop.topRight(), crop.bottomLeft(), crop.bottomRight()):
            painter.drawEllipse(point, 4, 4)


class Timeline(QWidget):
    """Trim range plus playhead, all dragged 1:1."""

    trim_changed = Signal(float, float)
    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setMouseTracking(True)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead = 0.0
        self._drag: str | None = None
        self._hot: str | None = None      # ручка под курсором

    def set_duration(self, seconds: float) -> None:
        self._duration = max(seconds, 0.001)
        self._start = 0.0
        self._end = self._duration
        self._playhead = 0.0
        self.update()
        self.trim_changed.emit(self._start, self._end)

    def set_playhead(self, seconds: float) -> None:
        self._playhead = seconds
        self.update()

    @property
    def trim(self) -> tuple[float, float]:
        return self._start, self._end

    def _track(self):
        # Тонкая дорожка вместо толстой полосы: таймлайн — это шкала, а не
        # главный объект окна; заливка во всю высоту перетягивала на себя
        # внимание с предпросмотра.
        centre = self.rect().center().y()
        return QRect(12, centre - 4, max(self.width() - 24, 1), 8)

    def _to_x(self, seconds: float) -> float:
        track = self._track()
        return track.left() + track.width() * (seconds / self._duration)

    def _to_time(self, x: float) -> float:
        track = self._track()
        ratio = (x - track.left()) / max(track.width(), 1)
        return min(max(ratio, 0.0), 1.0) * self._duration

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._duration <= 0:
            return
        x = event.position().x()
        near_start = abs(x - self._to_x(self._start)) < 12
        near_end = abs(x - self._to_x(self._end)) < 12
        if near_start and (not near_end or x < self._to_x(self._end)):
            self._drag = "start"
        elif near_end:
            self._drag = "end"
        else:
            self._drag = "playhead"
            self.seek_requested.emit(self._to_time(x))
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is None:
            x = event.position().x()
            to_start = abs(x - self._to_x(self._start))
            to_end = abs(x - self._to_x(self._end))
            hot = None
            if min(to_start, to_end) < 12:
                hot = "start" if to_start <= to_end else "end"
            if hot != self._hot:
                self._hot = hot           # подсветка ручки: без неё непонятно,
                self.update()             # что маркер вообще берётся мышью
            self.setCursor(Qt.SizeHorCursor if hot else Qt.PointingHandCursor)
            return
        value = self._to_time(event.position().x())
        if self._drag == "start":
            self._start = min(value, self._end - 0.05)
            self.seek_requested.emit(self._start)
        elif self._drag == "end":
            self._end = max(value, self._start + 0.05)
            self.seek_requested.emit(self._end)
        else:
            self.seek_requested.emit(value)
        self.update()
        self.trim_changed.emit(self._start, self._end)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag = None
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hot = None
        self.update()

    def paintEvent(self, _event) -> None:
        if self._duration <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        radius = track.height() / 2.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 20))
        painter.drawRoundedRect(QRectF(track), radius, radius)

        selected = QRectF(track)
        selected.setLeft(self._to_x(self._start))
        selected.setRight(self._to_x(self._end))
        accent = QColor(theme.ACCENT)
        accent.setAlpha(190)
        painter.setBrush(accent)
        painter.drawRoundedRect(selected, radius, radius)

        for name, seconds in (("start", self._start), ("end", self._end)):
            active = self._drag == name or (self._drag is None and self._hot == name)
            x = self._to_x(seconds)
            # Схваченная ручка заметно толстеет и белеет: отдача на нажатие,
            # а не только смена курсора.
            half = 5.0 if active else 3.5
            grow = 9.0 if active else 6.0
            painter.setBrush(QColor("#FFFFFF") if self._drag == name else QColor(theme.ACCENT))
            painter.drawRoundedRect(
                QRectF(x - half, track.top() - grow, half * 2, track.height() + grow * 2),
                half, half)

        head = self._to_x(self._playhead)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.drawLine(QPointF(head, track.top() - 3), QPointF(head, track.bottom() + 3))


def _format_time(seconds: float) -> str:
    minutes, rest = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


class EditorWindow(QWidget):
    """Trim, crop and export a finished recording."""

    closed = Signal(str)   # путь к исходному видео — вызывающий решает, что с ним делать

    def __init__(self, video_path: str, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("window")          # фон окна задаёт таблица стилей
        self.setWindowTitle("Clipr — редактирование записи")
        self.setMinimumSize(880, 620)
        self.settings = settings
        self.video_path = Path(video_path)
        self.exporter: GifExporter | None = None
        self._source = (0, 0)
        self._appeared = False

        self.setWindowOpacity(0.0)
        self._build()
        self._load()

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.preview = PreviewView()
        root.addWidget(self.preview, 1)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.preview.video_item)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_player_error)

        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.trim_changed.connect(self._on_trim)
        root.addWidget(self.timeline)

        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.btn_play = PressButton(radius=9)
        self.btn_play.setObjectName("icon")
        self.btn_play.setIcon(glyph("pause"))
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.setToolTip("Пауза (Пробел)")
        self.btn_play.clicked.connect(self._toggle_play)
        transport.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setObjectName("caption")
        digits = self.lbl_time.font()
        digits.setFeature(QFont.Tag("tnum"), 1)   # цифры не пляшут по ширине
        self.lbl_time.setFont(digits)
        transport.addWidget(self.lbl_time)
        transport.addStretch(1)

        self.lbl_crop = QLabel("Кадрирование: весь кадр")
        self.lbl_crop.setObjectName("caption")
        transport.addWidget(self.lbl_crop)

        self.btn_reset_crop = PressButton("Сбросить кадр")
        self.btn_reset_crop.setToolTip("Убрать кадрирование (R)")
        self.btn_reset_crop.clicked.connect(self.preview.clear_crop)
        transport.addWidget(self.btn_reset_crop)
        root.addLayout(transport)
        self.preview.crop_changed.connect(self._on_crop)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        controls.addWidget(QLabel("Кадры/с"))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(5, 30)
        self.spin_fps.setValue(self.settings.gif_fps)
        controls.addWidget(self.spin_fps)

        controls.addWidget(QLabel("Ширина"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(0, 3840)
        self.spin_width.setSingleStep(20)
        self.spin_width.setSpecialValueText("как есть")
        self.spin_width.setValue(self.settings.gif_width)
        controls.addWidget(self.spin_width)

        controls.addWidget(QLabel("Дизеринг"))
        self.combo_dither = QComboBox()
        self.combo_dither.addItems([
            "bayer:bayer_scale=3",
            "bayer:bayer_scale=5",
            "sierra2_4a",
            "floyd_steinberg",
            "none",
        ])
        self.combo_dither.setCurrentText(self.settings.gif_dither)
        controls.addWidget(self.combo_dither)

        self.check_optimize = QCheckBox("gifsicle")
        self.check_optimize.setChecked(self.settings.gif_optimize)
        self.check_optimize.setToolTip("Дополнительное сжатие gifsicle, если он установлен")
        controls.addWidget(self.check_optimize)

        controls.addStretch(1)

        # Что именно получится — до нажатия, а не после. Контекст рядом с
        # действием избавляет от «экспортировал и посмотрел, что вышло».
        self.lbl_result = QLabel("")
        self.lbl_result.setObjectName("caption")
        controls.addWidget(self.lbl_result)

        self.btn_folder = PressButton("Открыть папку")
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.settings.ensure_output_dir())))
        )
        controls.addWidget(self.btn_folder)

        self.btn_export = PressButton("Экспорт GIF")
        self.btn_export.setObjectName("primary")
        self.btn_export.setToolTip("Собрать GIF (Enter)")
        self.btn_export.clicked.connect(self._export)
        controls.addWidget(self.btn_export)
        root.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        # Место под полосу зарезервировано всегда: иначе её появление сдвигает
        # всё окно на шесть пикселей вверх.
        self.progress.setVisible(False)
        progress_row = QWidget()
        progress_row.setFixedHeight(6)
        progress_layout = QVBoxLayout(progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.addWidget(self.progress)
        root.addWidget(progress_row)

        self.status = QLabel("")
        self.status.setObjectName("caption")
        root.addWidget(self.status)

        for widget in (self.spin_fps, self.spin_width):
            widget.valueChanged.connect(self._update_result)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """Окно, в котором сидят дольше всего, обязано слушаться клавиатуры."""
        for keys, slot in (
            (QKeySequence(Qt.Key_Space), self._toggle_play),
            (QKeySequence(Qt.Key_Left), lambda: self._nudge(-1)),
            (QKeySequence(Qt.Key_Right), lambda: self._nudge(+1)),
            (QKeySequence(Qt.Key_Return), self._export),
            (QKeySequence(Qt.Key_Enter), self._export),
            (QKeySequence(Qt.Key_R), self.preview.clear_crop),
            (QKeySequence(Qt.Key_Escape), self.close),
        ):
            QShortcut(keys, self, activated=slot)

    def _nudge(self, direction: int) -> None:
        """Шаг по кадру — так ищут точную точку реза."""
        step = 1000 // max(self.spin_fps.value(), 1)
        self.player.pause()
        self.btn_play.setIcon(glyph("play"))
        self._seek(max(self.player.position() + direction * step, 0) / 1000.0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_appeared", False) or motion.reduced_motion():
            return
        self._appeared = True
        # Окно проявляется: до этого оно возникало поверх экрана рывком, сразу
        # после остановки записи — самый резкий момент во всём сценарии.
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(motion.ENTER_MS)
        anim.setEasingCurve(motion.ENTER_CURVE)
        anim.valueChanged.connect(lambda value: self.setWindowOpacity(float(value)))
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._enter_anim = anim

    # ---- loading ---------------------------------------------------------
    def _load(self) -> None:
        info = probe(self.video_path)
        width, height = info.get("width", 0), info.get("height", 0)
        duration = info.get("duration", 0.0)
        self._source = (width, height)
        if width and height:
            self.preview.set_source_size(width, height)
        self.timeline.set_duration(duration)
        self.player.setSource(QUrl.fromLocalFile(str(self.video_path)))
        QTimer.singleShot(60, self.player.play)
        self.status.setText(
            f"{self.video_path.name}  ·  {width}×{height}  ·  {_format_time(duration)}"
        )
        self._update_result()

    # ---- playback --------------------------------------------------------
    def _toggle_play(self) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.pause() if playing else self.player.play()
        self.btn_play.setIcon(glyph("play" if playing else "pause"))
        self.btn_play.setToolTip(("Играть" if playing else "Пауза") + " (Пробел)")

    def _seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))
        self.timeline.set_playhead(seconds)

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000.0
        start, end = self.timeline.trim
        if seconds > end + 0.05:
            self.player.setPosition(int(start * 1000))
            return
        self.timeline.set_playhead(seconds)
        self.lbl_time.setText(f"{_format_time(seconds)} / выбрано {_format_time(end - start)}")

    def _on_trim(self, start: float, end: float) -> None:
        self.lbl_time.setText(f"{_format_time(start)} → {_format_time(end)}")
        self._update_result()

    def _on_crop(self) -> None:
        region = self.preview.crop_region()
        self.lbl_crop.setText(
            "Кадрирование: весь кадр" if region is None
            else f"Кадр: {region.width}×{region.height} @ {region.x},{region.y}"
        )
        self._update_result()

    def _update_result(self) -> None:
        """Размер и длительность будущего GIF — до экспорта, а не после."""
        crop = self.preview.crop_region()
        width = crop.width if crop else self._source[0]
        height = crop.height if crop else self._source[1]
        if not width or not height:
            self.lbl_result.setText("")
            return
        target_width = self.spin_width.value()
        if target_width and target_width < width:
            height = round(height * target_width / width)
            width = target_width
        start, end = self.timeline.trim
        self.lbl_result.setText(f"итог: {width}×{height} · {end - start:.1f} с")

    def _on_player_error(self, _error, message: str) -> None:
        if message:
            self.status.setText(f"Ошибка предпросмотра: {message}")

    # ---- export ----------------------------------------------------------
    def _export(self) -> None:
        if self.exporter and self.exporter.isRunning():
            return
        self.settings.gif_fps = self.spin_fps.value()
        self.settings.gif_width = self.spin_width.value()
        self.settings.gif_dither = self.combo_dither.currentText()
        self.settings.gif_optimize = self.check_optimize.isChecked()
        self.settings.save()

        start, end = self.timeline.trim
        # Сырая запись лежит во временной папке — готовый GIF кладём к результатам.
        target = _unique(self.settings.ensure_output_dir() / f"{self.video_path.parent.name}.gif")

        self.btn_export.setEnabled(False)
        self.btn_export.setText("Собираем…")
        self.progress.setValue(0)
        self.progress.setVisible(True)

        self.exporter = GifExporter(
            self.video_path, target, self.settings,
            start=start, end=end, crop=self.preview.crop_region(), parent=self,
        )
        self.exporter.progress.connect(self._on_progress)
        self.exporter.done.connect(self._on_done)
        self.exporter.failed.connect(self._on_failed)
        self.exporter.start()

    def _on_progress(self, value: int, label: str) -> None:
        self.progress.setValue(value)
        self.status.setText(label)

    def _on_done(self, path: str, size: int) -> None:
        self.btn_export.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(f"Сохранено: {Path(path).name}  ·  {size / 1_048_576:.2f} МБ")
        self._flash_done()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _flash_done(self) -> None:
        """Единственный момент, где уместна «радость»: экспорт случается редко и
        завершает работу. Кнопка коротко подтверждает результат и возвращается
        к обычному виду — без конфетти."""
        self.btn_export.setIcon(glyph("check", colour="#FFFFFF"))
        self.btn_export.setIconSize(QSize(16, 16))
        self.btn_export.setText("Готово")
        delay = 200 if motion.reduced_motion() else motion.FLASH_MS * 4

        def restore() -> None:
            self.btn_export.setIcon(QIcon())
            self.btn_export.setText("Экспорт GIF")

        QTimer.singleShot(delay, restore)

    def _on_failed(self, message: str) -> None:
        self.btn_export.setEnabled(True)
        self.btn_export.setText("Экспорт GIF")
        self.progress.setVisible(False)
        self.status.setText("Экспорт не удался")
        QMessageBox.warning(self, "Экспорт не удался", message[:1500])

    # ---- lifecycle -------------------------------------------------------
    def closeEvent(self, event) -> None:
        self.player.stop()
        if self.exporter and self.exporter.isRunning():
            self.exporter.wait(3000)
        self.closed.emit(str(self.video_path))
        super().closeEvent(event)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path
