"""Своя плашка в углу экрана вместо системного уведомления (как в Spotty).

Уведомления Windows доходят не всегда: «Фокусировка внимания», выключенные
уведомления приложения, полноэкранная игра — и сообщение просто пропадает.
Поэтому рисуем свою, в стиле панели записи.

Правый нижний угол рабочей области экрана, где сейчас курсор, — там же, где
системные. Появляется за 220 мс (прозрачность и подъём на 12 px), уходит
быстрее — за 160 мс: приходит она, когда решила программа, а уходит, когда
человек уже всё решил. Пока курсор на плашке, она не исчезает.

Окно прозрачное (тень, скругление) и при этом спрятано от захвата экрана —
тот же приём, что у панели (widgets.gpu_composited): уведомление, пришедшее
посреди записи, не попадёт в ролик.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QWidget

from ..winapi import exclude_from_capture
from . import motion, theme
from .controls import ui_font
from .icons import app_pixmap
from .widgets import gpu_composited

WIDTH = 340
HEIGHT = 64               # с подзаголовком; без него — SINGLE
SINGLE = 52
THUMB_HEIGHT = 76         # плашка с миниатюрой GIF
MARGIN = 16               # от края рабочей области
SHADOW = 16
RISE = 12
ICON = 30
CLOSE = 20
ENTER_MS, EXIT_MS = 220, 160
DEFAULT_MS = 5000         # короткое сообщение; 0 — висит, пока не тронут


def _shadow(width: int, height: int, radius: float) -> QImage:
    """Мягкая тень: размытый прямоугольник, посчитанный один раз.

    Настоящее размытие дёшево сделать уменьшением и увеличением со
    сглаживанием — два прохода масштаба дают почти гауссово пятно.
    """
    full = QImage(width + SHADOW * 2, height + SHADOW * 2, QImage.Format_ARGB32_Premultiplied)
    full.fill(Qt.transparent)
    painter = QPainter(full)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 150))
    painter.drawRoundedRect(QRectF(SHADOW, SHADOW, width, height), radius, radius)
    painter.end()
    small = full.scaled(full.width() // 6, full.height() // 6,
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(full.width(), full.height(), Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)


class Toast(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        gpu_composited(self)
        self.setMouseTracking(True)
        self.title = ""
        self.subtitle = ""
        self.tag = ""
        self._thumb: QPixmap | None = None
        self._on_click = None
        self._hover = False
        self._hover_close = False
        self._pressed = False
        self._screen = None
        self._shadows: dict[int, QImage] = {}
        self._value = 0.0         # 0 — спрятана, 1 — на месте
        self._target = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(motion.EASE_OUT)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)
        self._life = QTimer(self, singleShot=True)
        self._life.timeout.connect(self.close_message)
        self._left = 0            # сколько оставалось жить, когда навели курсор

    # --- показ ------------------------------------------------------------- #

    def show_message(self, title: str, subtitle: str = "", timeout_ms: int = DEFAULT_MS,
                     on_click=None, tag: str = "", thumbnail: str | None = None) -> None:
        """on_click — что сделать по клику (плашка при этом закрывается).
        tag — метка, чтобы потом закрыть именно эту плашку. thumbnail — путь к
        картинке (первый кадр GIF) вместо значка программы."""
        self.title, self.subtitle, self.tag = title, subtitle, tag
        self._on_click = on_click
        self._thumb = self._load_thumb(thumbnail)
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        self._life.stop()
        self._left = timeout_ms
        if timeout_ms and not self._hover:
            self._life.start(timeout_ms)
        # Экран выбираем один раз: иначе мышь, уведённая на другой монитор,
        # перебросила бы плашку посреди появления.
        if not self.isVisible() or self._value < 0.5:
            self._screen = (QGuiApplication.screenAt(QCursor.pos())
                            or QGuiApplication.primaryScreen())
        self._place()             # высота могла смениться вместе с текстом
        self._animate(1.0, ENTER_MS)
        self.update()

    def close_message(self, tag: str | None = None) -> None:
        if tag is not None and tag != self.tag:
            return
        self._life.stop()
        if self.isVisible():
            self._animate(0.0, EXIT_MS)

    def _load_thumb(self, path: str | None) -> QPixmap | None:
        if not path:
            return None
        image = QImage(path)                 # у GIF — первый кадр
        if image.isNull():
            return None
        ratio = self.devicePixelRatioF() or 1.0
        side = int((THUMB_HEIGHT - 20) * ratio)
        pixmap = QPixmap.fromImage(image.scaled(side * 2, side, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def _animate(self, target: float, duration: int) -> None:
        self._target = target
        self._anim.stop()
        if motion.reduced_motion():
            duration = 120 if target > 0 else 90   # прозрачность остаётся, подъём — нет
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_value(self, value) -> None:
        self._value = float(value)
        self._place()
        self.setWindowOpacity(self._value)
        if self._value > 0 and not self.isVisible():
            self.show()
            exclude_from_capture(int(self.winId()), True)

    def _on_finished(self) -> None:
        if self._value <= 0.001:
            self.hide()
            self.tag = ""

    def _height(self) -> int:
        if self._thumb is not None:
            return THUMB_HEIGHT
        return HEIGHT if self.subtitle else SINGLE

    def _place(self) -> None:
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width, height = WIDTH + SHADOW * 2, self._height() + SHADOW * 2
        rise = 0 if motion.reduced_motion() else round(RISE * (1.0 - self._value))
        self.setGeometry(area.right() + 1 - MARGIN - WIDTH - SHADOW,
                         area.bottom() + 1 - MARGIN - self._height() - SHADOW + rise,
                         width, height)

    # --- мышь -------------------------------------------------------------- #

    def _card(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, WIDTH, self._height())

    def _close_rect(self) -> QRectF:
        card = self._card()
        return QRectF(card.right() - 8 - CLOSE, card.top() + 8, CLOSE, CLOSE)

    def enterEvent(self, event) -> None:
        self._hover = True
        if self._life.isActive():
            self._left = self._life.remainingTime()
            self._life.stop()
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = self._hover_close = self._pressed = False
        # Дочитать не дали — даём ещё немного, а не прячем сразу.
        if self._left and self.isVisible():
            self._life.start(max(self._left, 1500))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        near = self._close_rect().contains(event.position())
        if near != self._hover_close:
            self._hover_close = near
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._card().contains(event.position()):
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self._pressed:
            return
        self._pressed = False
        position = event.position()
        if self._close_rect().contains(position):
            callback = None
        elif self._card().contains(position):
            callback = self._on_click
            if callback is None:
                return
        else:
            self.update()
            return
        self.close_message()
        if callback is not None:
            callback()

    # --- отрисовка --------------------------------------------------------- #

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        height = self._height()
        if height not in self._shadows:
            self._shadows[height] = _shadow(WIDTH, height, 14)
        painter.drawImage(QPointF(0, 4), self._shadows[height])

        card = self._card()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.SURFACE))
        painter.drawRoundedRect(card, 14, 14)
        if self._on_click and (self._hover or self._pressed):
            painter.setBrush(QColor(255, 255, 255, 14 if self._pressed else 8))
            painter.drawRoundedRect(card, 14, 14)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 13.5, 13.5)

        if self._thumb is not None:
            size = self._thumb.deviceIndependentSize()
            media = QRectF(card.left() + 10, card.center().y() - size.height() / 2,
                           size.width(), size.height())
            clip = QPainterPath()
            clip.addRoundedRect(media, 7, 7)
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(media.topLeft(), self._thumb)
            painter.restore()
            painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
            painter.drawRoundedRect(media.adjusted(0.5, 0.5, -0.5, -0.5), 6.5, 6.5)
            left = media.right() + 12
        else:
            mark = app_pixmap(ICON * 2)
            mark.setDevicePixelRatio(2)
            painter.drawPixmap(QPointF(card.left() + 14, card.center().y() - ICON / 2), mark)
            left = card.left() + 14 + ICON + 12

        right = card.right() - (CLOSE + 14 if self._hover else 14)
        width = right - left
        painter.setFont(ui_font(13, QFont.DemiBold))
        painter.setPen(QColor(theme.TEXT))
        metrics = painter.fontMetrics()
        if self.subtitle:
            title_rect = QRectF(left, card.center().y() - 19, width, 18)
        else:
            title_rect = QRectF(left, card.top(), width, card.height())
        painter.drawText(title_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         metrics.elidedText(self.title, Qt.ElideRight, int(width)))
        if self.subtitle:
            painter.setFont(ui_font(12))
            painter.setPen(QColor(theme.TEXT_DIM))
            metrics = painter.fontMetrics()
            painter.drawText(QRectF(left, card.center().y() + 1, width, 17),
                             Qt.AlignVCenter | Qt.AlignLeft,
                             metrics.elidedText(self.subtitle, Qt.ElideRight, int(width)))

        # Крестик — только при наведении: в покое плашка чище.
        if not self._hover:
            return
        rect = self._close_rect()
        if self._hover_close:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 28))
            painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(theme.TEXT if self._hover_close else theme.TEXT_DIM), 1.4,
                            Qt.SolidLine, Qt.RoundCap))
        centre, arm = rect.center(), 3.6
        painter.drawLine(QPointF(centre.x() - arm, centre.y() - arm),
                         QPointF(centre.x() + arm, centre.y() + arm))
        painter.drawLine(QPointF(centre.x() + arm, centre.y() - arm),
                         QPointF(centre.x() - arm, centre.y() + arm))
