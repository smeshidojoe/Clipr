"""Fullscreen rubber-band selector for choosing the capture region.

Covers the whole virtual desktop with a dim scrim; dragging punches a clear
hole through it. Feedback is continuous — the size readout and the cleared area
track the pointer 1:1 for the entire drag, never only on release.

Opened again, it shows the current area with handles: its edges and corners
can be dragged, and so can the whole frame — no need to draw it from scratch.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation, QPoint, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..ffmpeg import Region
from ..i18n import tr
from ..winapi import monitor_rects
from . import motion, theme
from .handles import EDGE_CURSORS, draw_edge_grips, drag_sides, edge_hit

MIN_DRAG = 8  # hysteresis: below this a drag reads as a click
GRAB = 8      # px around an edge that still grabs it


def _physical_origin(screen) -> tuple[int, int]:
    """Physical top-left of a Qt screen.

    ``QScreen.name()`` is the human-readable monitor name, not the ``\\\\.\\DISPLAY``
    device string, so Qt screens and Win32 monitors are paired by their spatial
    order instead — both coordinate systems describe the same arrangement, so
    sorting by position lines them up. Falls back to scaling the logical origin
    when the two lists disagree.
    """
    screens = sorted(QGuiApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))
    monitors = sorted(monitor_rects().values(), key=lambda r: (r[0], r[1]))
    if len(screens) == len(monitors) and screen in screens:
        index = screens.index(screen)
        rect = monitors[index]
        expected = (
            round(screen.geometry().width() * screen.devicePixelRatio()),
            round(screen.geometry().height() * screen.devicePixelRatio()),
        )
        if (rect[2], rect[3]) == expected:
            return rect[0], rect[1]
    ratio = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    return round(origin.x() * ratio), round(origin.y() * ratio)


def physical_region(rect: QRect) -> Region:
    """Logical Qt coordinates -> physical pixels, which is what FFmpeg wants."""
    screen = QGuiApplication.screenAt(rect.topLeft()) or QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio()
    logical_origin = screen.geometry().topLeft()
    px, py = _physical_origin(screen)
    return Region(
        px + round((rect.x() - logical_origin.x()) * ratio),
        py + round((rect.y() - logical_origin.y()) * ratio),
        round(rect.width() * ratio),
        round(rect.height() * ratio),
    )


class RegionSelector(QWidget):
    """Emits ``selected(QRect)`` in logical coordinates, or ``cancelled()``."""

    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, current: QRect | None = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setMouseTracking(True)

        self._initial = current        # глобальные координаты; в свои — при показе
        self._selection = QRect()      # в координатах окна
        self._drag: str | None = None  # "new", "move" или стороны: "l", "tr"…
        self._press = QPoint()
        self._at_press = QRect()
        self._moved = False
        self._hover = QPoint()
        self._dim = 0.0          # прогресс затемнения, 0..1
        self._fade: QVariantAnimation | None = None

        virtual = QRect()
        for screen in QGuiApplication.screens():
            virtual = virtual.united(screen.geometry())
        self._virtual = virtual
        self.setGeometry(virtual)

    # ---- geometry --------------------------------------------------------
    def _rect(self) -> QRect:
        return self._selection

    @staticmethod
    def _between(a: QPoint, b: QPoint) -> QRect:
        # QRect(p1, p2) включает обе точки: протягивание 300→900 давало 601 px.
        left, right = sorted((a.x(), b.x()))
        top, bottom = sorted((a.y(), b.y()))
        return QRect(left, top, right - left, bottom - top)

    def _hit(self, pos: QPoint) -> str | None:
        if self._selection.isEmpty():
            return None
        return edge_hit(QRectF(self._selection), QPointF(pos), GRAB)

    def _screen_rect_under(self, pos: QPoint) -> QRect:
        screen = QGuiApplication.screenAt(self.mapToGlobal(pos)) or QGuiApplication.primaryScreen()
        return screen.geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._initial is not None:
            self._selection = QRect(self.mapFromGlobal(self._initial.topLeft()),
                                    self._initial.size())
            self._initial = None
        # Затемнение наплывает: это вход в отдельный режим, и резкая заливка
        # экрана читается как сбой, а не как приглашение выделить область.
        if motion.reduced_motion():
            self._dim = 1.0
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(motion.PANEL_IN_MS)
        anim.setEasingCurve(motion.ENTER_CURVE)
        anim.valueChanged.connect(self._set_dim)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._fade = anim

    def _set_dim(self, value) -> None:
        self._dim = float(value)
        self.update()

    # ---- events ----------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._finish(None)
            return
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        self._press = pos
        self._at_press = QRect(self._selection)
        self._moved = False
        self._drag = self._hit(pos) or "new"
        if self._drag == "new":
            self._selection = QRect()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        self._hover = pos
        if self._drag is None:
            self.setCursor(EDGE_CURSORS.get(self._hit(pos), Qt.CrossCursor))
            self.update()
            return
        if (pos - self._press).manhattanLength() >= 2:
            self._moved = True
        if self._drag == "new":
            self._selection = self._between(self._press, pos)
        elif self._drag == "move":
            moved = self._at_press.translated(pos - self._press)
            bounds = self.rect()
            moved.moveLeft(min(max(moved.x(), bounds.left()), bounds.right() + 1 - moved.width()))
            moved.moveTop(min(max(moved.y(), bounds.top()), bounds.bottom() + 1 - moved.height()))
            self._selection = moved
        else:
            # QRectF: у QRect правая граница — последний пиксель, и рамка
            # сжималась бы на пиксель при каждом касании края.
            rect = drag_sides(QRectF(self._at_press), self._drag, QPointF(pos))
            self._selection = rect.toRect().intersected(self.rect())
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is None or event.button() != Qt.LeftButton:
            return
        drag, self._drag = self._drag, None
        rect = self._selection
        if drag == "new" and (rect.width() < MIN_DRAG or rect.height() < MIN_DRAG):
            # A click, not a drag: take the whole monitor under the cursor.
            rect = self._screen_rect_under(event.position().toPoint())
        elif drag != "new" and (rect.width() < MIN_DRAG or rect.height() < MIN_DRAG):
            self._selection = self._at_press      # край стянули в ноль — вернуть
            self.update()
            return
        self._finish(rect)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._finish(None)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Есть рамка — оставить её как есть; нет — весь экран под курсором.
            self._finish(self._selection if not self._selection.isEmpty()
                         else self._screen_rect_under(self._hover))

    def _finish(self, rect: QRect | None) -> None:
        self.hide()
        if rect is None or rect.isEmpty():
            self.cancelled.emit()
        else:
            self.selected.emit(rect)
        self.deleteLater()

    # ---- painting --------------------------------------------------------
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        local = self.rect()
        selection = self._rect()

        painter.fillRect(local, QColor(0, 0, 0, int(110 * self._dim)))
        if selection.isEmpty():
            self._draw_hint(painter, tr("select.hint"))
            return

        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(selection, Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        accent = QColor(theme.ACCENT)
        painter.setPen(QPen(accent, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(selection.adjusted(0, 0, -1, -1))
        self._draw_handles(painter, selection, accent)
        self._draw_size_badge(painter, selection)
        if self._drag is None:
            self._draw_hint(painter, tr("select.adjust_hint"), bottom=True)

    def _draw_handles(self, painter: QPainter, rect: QRect, colour: QColor) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawEllipse(point, 4, 4)
        draw_edge_grips(painter, QRectF(rect))

    def _draw_size_badge(self, painter: QPainter, rect: QRect) -> None:
        physical = physical_region(rect)
        label = f"{physical.width} x {physical.height}"
        metrics = painter.fontMetrics()
        pad = 7
        width = metrics.horizontalAdvance(label) + pad * 2
        height = metrics.height() + pad
        x = rect.center().x() - width // 2
        y = rect.top() - height - 8
        if y < self.rect().top():
            y = rect.top() + 8
        badge = QRect(x, y, width, height)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(badge, Qt.AlignCenter, label)

    def _draw_hint(self, painter: QPainter, text: str, bottom: bool = False) -> None:
        metrics = painter.fontMetrics()
        pad = 12
        width = metrics.horizontalAdvance(text) + pad * 2
        height = metrics.height() + pad
        screen = self._screen_rect_under(self._hover).translated(-self._virtual.topLeft())
        # С рамкой подсказка уходит вниз экрана, чтобы не закрывать саму рамку.
        y = screen.bottom() - height - 48 if bottom else screen.center().y() - height // 2
        badge = QRect(screen.center().x() - width // 2, y, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.drawRoundedRect(badge, 10, 10)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(badge, Qt.AlignCenter, text)
