"""Post-processing window: preview, trim, crop, export to GIF or MP4."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QMouseEvent,
    QPainter, QPainterPath, QPen, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..config import BUILTIN_PRESETS, GIF_MAX_FPS, scaled_size
from ..ffmpeg import GifExporter, Region, VideoExporter, probe
from ..i18n import size_text, tr
from . import motion, theme
from .chrome import CaptionBar, WindowsChrome
from .clipboard import copy_file, reveal
from .controls import Segmented, Tween, ValueSlider
from .handles import EDGE_CURSORS, draw_edge_grips, drag_sides, edge_hit
from .icons import glyph, glyph_pixmap
from .settings_window import FPS_CHOICES, compress_choices, nearest
from .widgets import PressButton, rounded_menu

HANDLE = 9.0  # px grab radius for crop handles and edges
MIN_CROP = 24.0
MAX_ZOOM = 8.0
ZOOM_STEP = 1.25   # на одно деление колеса


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

        # Масштаб поверх «вписать в окно»: 1 — исходный вид, меньше нельзя.
        # Точка видео под курсором остаётся под курсором, пока масштаб плывёт.
        self._zoom = Tween(self, 1.0, motion.HOVER_MS, on_change=self._zoom_step)
        self._zoom_target = 1.0
        self._anchor: tuple[QPointF, QPointF] | None = None  # (точка окна, точка видео)
        self._focus: QPointF | None = None    # точка видео в центре окна
        self._pan_from: QPointF | None = None

    # ---- geometry --------------------------------------------------------
    def set_source_size(self, width: int, height: int) -> None:
        self._source_size = QSizeF(width, height)
        self._crop = None
        self._anchor = None
        self._zoom_target = 1.0
        self._zoom.to(1.0, False)

    def _fit_scale(self) -> float:
        area = self.viewport().rect()
        return min(area.width() / self._source_size.width(),
                   area.height() / self._source_size.height())

    def relayout(self, anchor: tuple[QPointF, QPointF] | None = None) -> None:
        if self._source_size.isEmpty():
            return
        area = self.viewport().rect()
        zoom = self._zoom.value
        scale = self._fit_scale() * zoom
        size = QSizeF(self._source_size.width() * scale, self._source_size.height() * scale)
        if anchor is not None:
            view, video = anchor
            pos = QPointF(view.x() - video.x() * scale, view.y() - video.y() * scale)
        elif zoom > 1.0 and self._focus is not None:
            # Окно изменило размер — та же точка видео остаётся в центре.
            pos = QPointF(area.width() / 2.0 - self._focus.x() * scale,
                          area.height() / 2.0 - self._focus.y() * scale)
        else:
            pos = QPointF()
        self.video_item.setSize(size)
        self._place(pos)
        self.setSceneRect(0, 0, area.width(), area.height())

    def _place(self, pos: QPointF) -> None:
        """Видео не отходит от краёв окна: пока оно меньше окна — по центру,
        больше — край кадра не отрывается от края окна."""
        area = self.viewport().rect()
        size = self.video_item.size()

        def axis(value: float, length: float, room: float) -> float:
            if length <= room:
                return (room - length) / 2.0
            return min(0.0, max(room - length, value))

        self.video_item.setPos(axis(pos.x(), size.width(), area.width()),
                               axis(pos.y(), size.height(), area.height()))
        self._focus = self._to_video(QPointF(area.width() / 2.0, area.height() / 2.0))
        self.viewport().update()

    def _zoom_step(self) -> None:
        self.relayout(self._anchor)

    def wheelEvent(self, event) -> None:
        if self._source_size.isEmpty():
            return
        event.accept()
        steps = event.angleDelta().y() / 120.0
        target = min(max(self._zoom_target * ZOOM_STEP ** steps, 1.0), MAX_ZOOM)
        if target < 1.01:
            target = 1.0               # дальше исходного вида не отдаляем
        if target == self._zoom_target:
            return
        self._zoom_target = target
        point = QPointF(event.position())
        self._anchor = (point, self._to_video(point))
        self._zoom.to(target, not motion.reduced_motion())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._anchor = None
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
        """Какие стороны рамки под курсором: "tl" — угол, "l" — край,
        "move" — внутри. Края берутся по всей длине, а не только за точку:
        подвинуть одну сторону — частая правка, и целиться в неё не нужно."""
        if self._crop is None:
            return None
        return edge_hit(self._to_view(self._crop.normalized()), pos, HANDLE)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MiddleButton and self._zoom_target > 1.0:
            self._pan_from = QPointF(event.position())
            self.setCursor(Qt.ClosedHandCursor)
            return
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
        if self._pan_from is not None:
            self._anchor = None
            self._place(self.video_item.pos() + (pos - self._pan_from))
            self._pan_from = pos
            return
        if self._drag_mode is None:
            hit = self._hit(pos)
            self.setCursor(EDGE_CURSORS.get(hit, Qt.CrossCursor))
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
            self._crop = drag_sides(self._crop_at_press, self._drag_mode, current)
        self._crop = self._clamp_to_source(self._crop.normalized())
        self.viewport().update()
        self.crop_changed.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MiddleButton:
            self._pan_from = None
            self.setCursor(Qt.CrossCursor)
            return
        if self._drag_mode is None:
            return
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
        draw_edge_grips(painter, crop)


class Timeline(QWidget):
    """Trim range plus playhead, all dragged 1:1."""

    trim_changed = Signal(float, float)
    seek_requested = Signal(float)
    scrub_started = Signal()     # пока тянут ползунок или ручку, видео стоит
    scrub_finished = Signal()    # и показывает кадр под ними

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
        if self._duration <= 0 or event.button() != Qt.LeftButton:
            return
        self.scrub_started.emit()
        x = event.position().x()
        near_start = abs(x - self._to_x(self._start)) < 12
        near_end = abs(x - self._to_x(self._end)) < 12
        if near_start and (not near_end or x < self._to_x(self._end)):
            self._drag = "start"
            self.seek_requested.emit(self._start)
        elif near_end:
            self._drag = "end"
            self.seek_requested.emit(self._end)
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
        if self._drag is None:
            return
        self._drag = None
        self.update()
        self.scrub_finished.emit()

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


def _caption(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName("caption")
    return widget


def _format_time(seconds: float) -> str:
    minutes, rest = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


class MenuButton(PressButton):
    """Кнопка, открывающая список: стрелка вниз справа от подписи."""

    ARROW = 14

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width() + self.ARROW, hint.height())

    def _draw_content(self, painter: QPainter, colour: QColor) -> None:
        painter.save()
        painter.translate(-self.ARROW / 2.0, 0)
        super()._draw_content(painter, colour)
        painter.restore()
        x, y = self.width() - 16.0, self.height() / 2.0
        pen = QPen(colour, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(x - 3.5, y - 1.8), QPointF(x, y + 1.8),
                              QPointF(x + 3.5, y - 1.8)])


class NamePopup(QWidget):
    """Поле для названия пресета — всплывает под кнопкой, Enter сохраняет,
    Esc и щелчок мимо закрывают. Отдельное окно с системной рамкой ради
    одной строки было бы тяжелее самого действия."""

    accepted = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("editor.preset_name"))
        self.edit.setMaxLength(32)
        self.edit.setFixedWidth(200)
        self.edit.returnPressed.connect(self._accept)
        self.edit.textChanged.connect(lambda text: self.button.setEnabled(bool(text.strip())))
        row.addWidget(self.edit)
        self.button = PressButton(tr("editor.preset_add"))
        self.button.setObjectName("primary")
        self.button.setEnabled(False)
        self.button.clicked.connect(self._accept)
        row.addWidget(self.button)

    def _accept(self) -> None:
        name = self.edit.text().strip()
        if name:
            self.accepted.emit(name)
            self.close()

    def popup(self, below: QWidget) -> None:
        self.adjustSize()
        self.move(below.mapToGlobal(QPoint(0, below.height() + 6)))
        self.show()
        self.edit.setFocus()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.fillPath(path, QColor(theme.SURFACE_RAISED))
        painter.setPen(QPen(QColor(theme.BORDER_BRIGHT), 1))
        painter.drawPath(path)


def builtin_presets() -> list[dict]:
    return [{"name": tr(key), "scale": scale, "fps": fps, "compress": compress}
            for key, scale, fps, compress in BUILTIN_PRESETS]


class EditorWindow(QWidget):
    """Trim, crop and export a finished recording."""

    closed = Signal(str)   # путь к исходному видео — вызывающий решает, что с ним делать
    exported = Signal(str, int)   # готовый GIF или MP4 (уже в буфере обмена) и его размер

    def __init__(self, video_path: str, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("window")          # фон окна задаёт таблица стилей
        self.setWindowTitle(tr("editor.title"))
        # Ширину не задаём: её минимум — строка параметров экспорта целиком.
        self.setMinimumHeight(620)
        self.settings = settings
        self.video_path = Path(video_path)
        self.exporter: GifExporter | VideoExporter | None = None
        self._source = (0, 0)
        self._source_fps = 0.0
        self._appeared = False
        self._last_export: Path | None = None
        self._busy_button: PressButton | None = None
        self._normal: QRect | None = None
        self._resume_after_scrub = False
        self._scrubbing = False

        self.setWindowOpacity(0.0)
        self._build()
        self._restore_placement()
        self._load()

    def _restore_placement(self) -> None:
        """Размер и место — как в прошлый раз.

        Рамку меняем до показа: пока окно думает, что у него системная
        рамка, заданный размер относится к клиентской области, и после смены
        рамки окно вырастает на её толщину — с каждым открытием всё больше.
        """
        self.winId()
        self.chrome.on_show()
        saved = self.settings.editor_geometry
        if not saved:
            self.resize(1000, 700)
            return
        rect = QRect(*saved)
        rect.setSize(rect.size().expandedTo(self.minimumSizeHint().expandedTo(self.minimumSize())))
        # Монитор могли отключить — тогда окно встанет по умолчанию.
        visible = any(
            (part := screen.availableGeometry().intersected(rect)).width() >= 200
            and part.height() >= 120 for screen in QGuiApplication.screens())
        if visible:
            self.setGeometry(rect)
            self._normal = rect           # окно может открыться развёрнутым
        else:
            self.resize(rect.size().boundedTo(QSize(1000, 700)))

    def present(self) -> None:
        if self.settings.editor_maximized:
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _track_normal(self) -> None:
        """Обычное положение запоминаем сами: normalGeometry() развёрнутого
        окна со своей рамкой врёт на толщину рамки, и окно сжималось бы с
        каждым «развернул — закрыл»."""
        if self.windowState() == Qt.WindowNoState and self.isVisible():
            self._normal = QRect(self.geometry())

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._track_normal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._track_normal()

    def _remember_placement(self) -> None:
        self.settings.editor_maximized = bool(self.windowState() & Qt.WindowMaximized)
        rect = self._normal if self._normal is not None else self.geometry()
        if rect.isValid():
            self.settings.editor_geometry = [rect.x(), rect.y(), rect.width(), rect.height()]
        self.settings.save()

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        # Рамка своя, как у настроек: полоса сверху — заголовок, системные
        # тень, привязка к краям и изменение размера остаются (см. chrome).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.bar = CaptionBar(tr("editor.heading"))
        outer.addWidget(self.bar)
        self.chrome = WindowsChrome(self, self.bar, self.bar.layout_,
                                    ("min", "max", "close"))
        root = QVBoxLayout()
        root.setContentsMargins(16, 4, 16, 16)
        root.setSpacing(12)
        outer.addLayout(root, 1)

        self.preview = PreviewView()
        self.preview.setToolTip(tr("editor.zoom_tip"))
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
        self.timeline.scrub_started.connect(self._scrub_started)
        self.timeline.scrub_finished.connect(self._scrub_finished)
        root.addWidget(self.timeline)

        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.btn_play = PressButton(radius=9)
        self.btn_play.setObjectName("icon")
        self.btn_play.setIcon(glyph("pause"))
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.setToolTip(tr("editor.pause"))
        self.btn_play.clicked.connect(self._toggle_play)
        transport.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setObjectName("caption")
        digits = self.lbl_time.font()
        digits.setFeature(QFont.Tag("tnum"), 1)   # цифры не пляшут по ширине
        self.lbl_time.setFont(digits)
        transport.addWidget(self.lbl_time)
        transport.addStretch(1)

        self.lbl_crop = QLabel(tr("editor.crop_none"))
        self.lbl_crop.setObjectName("caption")
        transport.addWidget(self.lbl_crop)

        self.btn_reset_crop = PressButton(tr("editor.reset_crop"))
        self.btn_reset_crop.setToolTip(tr("editor.reset_crop_tip"))
        self.btn_reset_crop.clicked.connect(self.preview.clear_crop)
        transport.addWidget(self.btn_reset_crop)
        root.addLayout(transport)
        self.preview.crop_changed.connect(self._on_crop)

        # Параметры GIF — строка над действиями. Те же элементы, что в
        # настройках: одно и то же значение не должно выглядеть по-разному.
        # Здесь только то, что меняют от ролика к ролику; сглаживание цветов
        # живёт в настройках.
        params = QHBoxLayout()
        params.setSpacing(10)
        self.btn_preset = MenuButton()
        self.btn_preset.setToolTip(tr("editor.preset_tip"))
        self.btn_preset.clicked.connect(self._show_presets)
        params.addWidget(self.btn_preset)
        params.addSpacing(8)
        params.addWidget(_caption(tr("editor.size")))
        self.scale = ValueSlider(10, 100, self.settings.gif_scale,
                                 lambda v: f"{v} %", step=5, width=130)
        params.addWidget(self.scale)
        params.addSpacing(8)
        params.addWidget(_caption(tr("editor.fps")))
        self.fps = Segmented(FPS_CHOICES, nearest(FPS_CHOICES, self.settings.gif_fps))
        params.addWidget(self.fps)
        params.addSpacing(8)
        params.addWidget(_caption(tr("editor.compress")))
        self.compress = Segmented(compress_choices(), self.settings.gif_compress)
        self.compress.setToolTip(tr("editor.compress_tip"))
        params.addWidget(self.compress)
        params.addStretch(1)
        root.addLayout(params)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.status = QLabel("")
        self.status.setObjectName("caption")
        controls.addWidget(self.status, 1)

        # Что именно получится — до нажатия, а не после. Контекст рядом с
        # действием избавляет от «экспортировал и посмотрел, что вышло».
        self.lbl_result = QLabel("")
        self.lbl_result.setObjectName("caption")
        digits = self.lbl_result.font()
        digits.setFeature(QFont.Tag("tnum"), 1)
        self.lbl_result.setFont(digits)
        controls.addWidget(self.lbl_result)

        self.btn_folder = PressButton(tr("editor.open_folder"))
        self.btn_folder.clicked.connect(self._open_folder)
        controls.addWidget(self.btn_folder)

        self.btn_video = PressButton(tr("editor.export_video"))
        self.btn_video.setToolTip(tr("editor.export_video_tip"))
        self.btn_video.clicked.connect(self._export_video)
        controls.addWidget(self.btn_video)

        self.btn_export = PressButton(tr("editor.export"))
        self.btn_export.setObjectName("primary")
        self.btn_export.setToolTip(tr("editor.export_tip"))
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

        for control in (self.scale, self.fps, self.compress):
            control.changed.connect(self._update_result)
            control.changed.connect(self._update_preset_label)
        self._update_preset_label()
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """Окно, в котором сидят дольше всего, обязано слушаться клавиатуры."""
        for keys, slot in (
            (QKeySequence(Qt.Key_Space), self._toggle_play),
            (QKeySequence(Qt.Key_Left), lambda: self._nudge(-1)),
            (QKeySequence(Qt.Key_Right), lambda: self._nudge(+1)),
            (QKeySequence(Qt.Key_Return), self._export),
            (QKeySequence(Qt.Key_Enter), self._export),
            (QKeySequence(Qt.CTRL | Qt.Key_Return), self._export_video),
            (QKeySequence(Qt.CTRL | Qt.Key_Enter), self._export_video),
            (QKeySequence(Qt.Key_R), self.preview.clear_crop),
            (QKeySequence(Qt.Key_Escape), self.close),
        ):
            QShortcut(keys, self, activated=slot)

    # ---- presets ---------------------------------------------------------
    def _current_values(self) -> tuple[int, int, str]:
        return self.scale.value(), int(self.fps.value()), self.compress.value()

    def _matching_preset(self) -> dict | None:
        current = self._current_values()
        for preset in self.settings.export_presets + builtin_presets():
            if (preset["scale"], preset["fps"], preset["compress"]) == current:
                return preset
        return None

    def _update_preset_label(self) -> None:
        preset = self._matching_preset()
        name = preset["name"] if preset else tr("editor.preset_custom")
        self.btn_preset.setText(f"{tr('editor.preset')}: {name}")
        self.btn_preset.updateGeometry()

    def _apply_preset(self, preset: dict) -> None:
        self.scale.set_value(preset["scale"])
        self.fps.set_value(nearest(FPS_CHOICES, preset["fps"]))
        self.compress.set_value(preset["compress"])
        self._update_result()
        self._update_preset_label()

    def _show_presets(self) -> None:
        menu = QMenu(self)
        rounded_menu(menu)
        current = self._matching_preset()
        custom = self.settings.export_presets

        check, blank = glyph("check", 16), QIcon(glyph_pixmap("check", 16, "#00000000"))

        def add(preset: dict) -> None:
            # Пустой значок у остальных держит подписи на одной линии.
            menu.addAction(check if preset is current else blank, preset["name"],
                           lambda p=preset: self._apply_preset(p))

        for preset in builtin_presets():
            add(preset)
        if custom:
            menu.addSeparator()
            for preset in custom:
                add(preset)
        menu.addSeparator()
        menu.addAction(tr("editor.preset_save"), self._ask_preset_name)
        if custom:
            remove = menu.addMenu(tr("editor.preset_delete"))
            rounded_menu(remove)
            for preset in custom:
                remove.addAction(preset["name"], lambda p=preset: self._delete_preset(p))
        menu.exec(self.btn_preset.mapToGlobal(QPoint(0, self.btn_preset.height() + 4)))

    def _ask_preset_name(self) -> None:
        popup = NamePopup(self)
        popup.accepted.connect(self._save_preset)
        popup.popup(self.btn_preset)

    def _save_preset(self, name: str) -> None:
        scale, fps, compress = self._current_values()
        presets = [p for p in self.settings.export_presets if p["name"] != name]
        presets.append({"name": name, "scale": scale, "fps": fps, "compress": compress})
        self.settings.export_presets = presets
        self.settings.save()
        self._update_preset_label()

    def _delete_preset(self, preset: dict) -> None:
        self.settings.export_presets = [p for p in self.settings.export_presets
                                        if p is not preset]
        self.settings.save()
        self._update_preset_label()

    def _effective_fps(self) -> int:
        """Выше частоты записи подниматься незачем — кадры просто повторятся."""
        fps = int(self.fps.value())
        if self._source_fps >= 1:
            fps = min(fps, round(self._source_fps))
        return fps

    def _nudge(self, direction: int) -> None:
        """Шаг по кадру — так ищут точную точку реза."""
        rate = self._source_fps if self._source_fps >= 1 else self._effective_fps()
        step = round(1000 / rate)
        self._set_playing(False)
        self._seek(max(self.player.position() + direction * step, 0) / 1000.0)

    def nativeEvent(self, event_type, message):
        handled = self.chrome.native_event(event_type, message)
        return handled if handled is not None else super().nativeEvent(event_type, message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.chrome.on_show()
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
        self._source_fps = info.get("fps", 0.0)
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
    def _set_playing(self, playing: bool) -> None:
        self.player.play() if playing else self.player.pause()
        self.btn_play.setIcon(glyph("pause" if playing else "play"))
        self.btn_play.setToolTip(tr("editor.pause" if playing else "editor.play"))

    def _toggle_play(self) -> None:
        self._set_playing(self.player.playbackState() != QMediaPlayer.PlayingState)

    def _scrub_started(self) -> None:
        """Пока ползунок или ручку обрезки держат, видео стоит на кадре под
        ними — иначе воспроизведение уводило бы кадр, пока его ищут."""
        self._scrubbing = True
        self._resume_after_scrub = self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.pause()

    def _scrub_finished(self) -> None:
        self._scrubbing = False
        if self._resume_after_scrub:
            self.player.play()

    def _seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))
        self.timeline.set_playhead(seconds)

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000.0
        start, end = self.timeline.trim
        if seconds > end + 0.05 and not self._scrubbing:
            self.player.setPosition(int(start * 1000))
            return
        if not self._scrubbing:      # во время перетаскивания шкала ведёт сама
            self.timeline.set_playhead(seconds)
        self.lbl_time.setText(tr("editor.selected", position=_format_time(seconds),
                                 length=_format_time(end - start)))

    def _on_trim(self, start: float, end: float) -> None:
        self.lbl_time.setText(f"{_format_time(start)} → {_format_time(end)}")
        self._update_result()

    def _on_crop(self) -> None:
        region = self.preview.crop_region()
        self.lbl_crop.setText(
            tr("editor.crop_none") if region is None
            else tr("editor.crop", w=region.width, h=region.height, x=region.x, y=region.y)
        )
        self._update_result()

    def _update_result(self) -> None:
        """Размер, длительность и частота результата — до экспорта, а не после."""
        crop = self.preview.crop_region()
        width = crop.width if crop else self._source[0]
        height = crop.height if crop else self._source[1]
        if not width or not height:
            self.lbl_result.setText("")
            return
        width, height = scaled_size(width, height, self.scale.value())
        start, end = self.timeline.trim
        video_fps = self._effective_fps()
        gif_fps = min(video_fps, GIF_MAX_FPS)
        fps = str(video_fps) if gif_fps == video_fps \
            else tr("editor.fps_split", gif=gif_fps, video=video_fps)
        self.lbl_result.setToolTip(
            "" if gif_fps == video_fps else tr("editor.fps_gif_cap", video=video_fps))
        self.lbl_result.setText(tr("editor.result", w=width, h=height,
                                   seconds=end - start, fps=fps))

    def _on_player_error(self, _error, message: str) -> None:
        if message:
            self.status.setText(tr("editor.preview_error", message=message))

    # ---- export ----------------------------------------------------------
    def _export(self) -> None:
        self._start_export("gif")

    def _export_video(self) -> None:
        self._start_export("video")

    def _start_export(self, kind: str) -> None:
        if self.exporter and self.exporter.isRunning():
            return
        # Выбранное здесь становится значением по умолчанию для следующего раза.
        self.settings.gif_fps = int(self.fps.value())
        self.settings.gif_scale = self.scale.value()
        self.settings.gif_compress = self.compress.value()
        self.settings.save()

        start, end = self.timeline.trim
        crop = self.preview.crop_region()
        fps = self._effective_fps()
        # Сырая запись лежит во временной папке — результат кладём к остальным.
        name = self.video_path.parent.name
        folder = self.settings.ensure_output_dir()
        if kind == "gif":
            self._busy_button = self.btn_export
            self.exporter = GifExporter(
                self.video_path, unique_path(folder / f"{name}.gif"), self.settings,
                start=start, end=end, crop=crop, fps=fps, parent=self,
            )
        else:
            self._busy_button = self.btn_video
            self.exporter = VideoExporter(
                self.video_path, unique_path(folder / f"{name}.mp4"),
                start=start, end=end, crop=crop, fps=fps, scale=self.scale.value(),
                parent=self,
            )

        for button in (self.btn_export, self.btn_video):
            button.setEnabled(False)
        self._busy_button.setText(tr("editor.exporting"))
        self.progress.setValue(0)
        self.progress.setVisible(True)

        self.exporter.progress.connect(self._on_progress)
        self.exporter.done.connect(self._on_done)
        self.exporter.failed.connect(self._on_failed)
        self.exporter.start()

    def _restore_buttons(self) -> None:
        for button, key in ((self.btn_export, "editor.export"),
                            (self.btn_video, "editor.export_video")):
            button.setEnabled(True)
            button.setIcon(QIcon())
            button.setText(tr(key))

    def _on_progress(self, value: int, label: str) -> None:
        self.progress.setValue(value)
        self.status.setText(label)

    def _on_done(self, path: str, size: int) -> None:
        self._restore_buttons()
        self.progress.setVisible(False)
        # Вместо того чтобы открывать папку — результат сразу в буфер обмена
        # файлом: дальше его обычно не смотрят в Проводнике, а вставляют в
        # мессенджер.
        self._last_export = Path(path)
        copy_file(path)
        self.status.setText(tr("editor.saved", name=Path(path).name, size=size_text(size)))
        self._flash_done()
        self.exported.emit(path, size)

    def _open_folder(self) -> None:
        """После экспорта — папка с выделенным файлом, до — просто папка результатов."""
        if self._last_export is not None and self._last_export.exists():
            reveal(self._last_export)
        else:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.settings.ensure_output_dir())))

    def _flash_done(self) -> None:
        """Единственный момент, где уместна «радость»: экспорт случается редко и
        завершает работу. Кнопка коротко подтверждает результат и возвращается
        к обычному виду — без конфетти."""
        button = self._busy_button or self.btn_export
        colour = "#FFFFFF" if button is self.btn_export else theme.TEXT
        button.setIcon(glyph("check", colour=colour))
        button.setIconSize(QSize(16, 16))
        button.setText(tr("editor.copied"))
        delay = 200 if motion.reduced_motion() else motion.FLASH_MS * 4

        def restore() -> None:
            if not (self.exporter and self.exporter.isRunning()):   # новый экспорт не трогаем
                self._restore_buttons()

        QTimer.singleShot(delay, restore)

    def _on_failed(self, message: str) -> None:
        self._restore_buttons()
        self.progress.setVisible(False)
        self.status.setText(tr("editor.failed"))
        QMessageBox.warning(self, tr("editor.failed"), message[:1500])

    # ---- lifecycle -------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._remember_placement()
        self.player.stop()
        if self.exporter and self.exporter.isRunning():
            self.exporter.wait(3000)
        self.closed.emit(str(self.video_path))
        super().closeEvent(event)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path
