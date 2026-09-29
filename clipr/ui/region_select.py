"""Fullscreen rubber-band selector for choosing the capture region.

Covers the whole virtual desktop with a dim scrim; dragging punches a clear
hole through it. Feedback is continuous — the size readout and the cleared area
track the pointer 1:1 for the entire drag, never only on release.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation, QPoint, QRect, Qt, QVariantAnimation, Signal,
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

MIN_DRAG = 8  # hysteresis: below this a drag reads as a click


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

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setMouseTracking(True)

        self._origin: QPoint | None = None
        self._current: QPoint | None = None
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
        if self._origin is None or self._current is None:
            return QRect()
        # QRect(p1, p2) включает обе точки: протягивание 300→900 давало 601 px.
        left, right = sorted((self._origin.x(), self._current.x()))
        top, bottom = sorted((self._origin.y(), self._current.y()))
        return QRect(left, top, right - left, bottom - top)

    def _screen_rect_under(self, pos: QPoint) -> QRect:
        screen = QGuiApplication.screenAt(self.mapToGlobal(pos)) or QGuiApplication.primaryScreen()
        return screen.geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
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
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover = event.position().toPoint()
        if self._origin is not None:
            self._current = self._hover
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._origin is None:
            return
        rect = self._rect()
        if rect.width() < MIN_DRAG or rect.height() < MIN_DRAG:
            # A click, not a drag: take the whole monitor under the cursor.
            rect = self._screen_rect_under(event.position().toPoint())
        self._finish(rect)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._finish(None)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(self._screen_rect_under(self._hover))

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
            self._draw_hint(painter)
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

    def _draw_handles(self, painter: QPainter, rect: QRect, colour: QColor) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawEllipse(point, 3, 3)

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

    def _draw_hint(self, painter: QPainter) -> None:
        text = tr("select.hint")
        metrics = painter.fontMetrics()
        pad = 12
        width = metrics.horizontalAdvance(text) + pad * 2
        height = metrics.height() + pad
        screen = self._screen_rect_under(self._hover).translated(-self._virtual.topLeft())
        badge = QRect(
            screen.center().x() - width // 2,
            screen.center().y() - height // 2,
            width,
            height,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.drawRoundedRect(badge, 10, 10)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(badge, Qt.AlignCenter, text)
