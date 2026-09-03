"""Кнопки, которые отвечают на прикосновение.

Таблица стилей Qt меняет цвет по `:hover` и `:pressed` мгновенно и не умеет
`transform` вовсе. Хуже того, фон и рамку стилизованного виджета Qt рисует
ДО `paintEvent`, поэтому масштабировать кнопку изнутри её же отрисовки
невозможно — фон всё равно остаётся во всю ширину (проверено: кнопка с пустым
`paintEvent` продолжает краситься).

Поэтому эта кнопка рисует себя целиком сама: фон, рамку, значок и подпись —
внутри масштабирующего преобразования. Отклик на нажатие — самая заметная
мелочь во всём интерфейсе: без него кнопка кажется картинкой, с ним —
предметом. Масштаб намеренно маленький (0.97): задача — подтвердить, что
нажатие услышано, а не устроить представление.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    QVariantAnimation,
    Qt,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from . import motion, theme

PRESS_SCALE = 0.97          # 0.95–0.98: заметно телу, незаметно глазу
HOVER_ALPHA = 26
PRESS_TINT = 26             # нажатая кнопка ещё и темнеет — как вдавленная

_TEXT_PAD_X, _ICON_PAD_X, _PAD_Y = 14, 9, 8
_ICON_GAP = 7


def _alive(obj) -> bool:
    if obj is None:
        return False
    try:
        from shiboken6 import isValid
    except ImportError:
        return True
    return bool(isValid(obj))


class PressButton(QPushButton):
    """Кнопка с масштабом при нажатии и плавной подсветкой под курсором.

    Вид задаётся `objectName`: ``primary`` — акцентная, ``icon`` — только
    значок, пусто — обычная.
    """

    def __init__(self, text: str = "", parent=None, radius: int = 10) -> None:
        super().__init__(text, parent)
        self.radius = radius
        self._press = 0.0
        self._hover = 0.0
        self._press_anim: QVariantAnimation | None = None
        self._hover_anim: QVariantAnimation | None = None
        self.setCursor(Qt.PointingHandCursor)
        # Гасим оформление из таблицы стилей: иначе её фон лёг бы под наш,
        # неотмасштабированным.
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")
        self.pressed.connect(lambda: self._animate("press", 1.0))
        self.released.connect(lambda: self._animate("press", 0.0))

    # ---- размеры ---------------------------------------------------------
    def _pad_x(self) -> int:
        return _ICON_PAD_X if not self.text() else _TEXT_PAD_X

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        width = height = 0
        if not self.icon().isNull():
            width += self.iconSize().width()
            height = max(height, self.iconSize().height())
        if self.text():
            if width:
                width += _ICON_GAP
            width += metrics.horizontalAdvance(self.text())
            height = max(height, metrics.height())
        return QSize(width + self._pad_x() * 2, height + _PAD_Y * 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ---- состояния -------------------------------------------------------
    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._animate("hover", 1.0)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._animate("hover", 0.0)
        self._animate("press", 0.0)     # увели курсор во время нажатия

    def _animate(self, name: str, target: float) -> None:
        current = getattr(self, f"_{name}")
        anim = getattr(self, f"_{name}_anim")
        if _alive(anim):
            anim.stop()
        if current == target or not self.isEnabled():
            setattr(self, f"_{name}", target)
            self.update()
            return

        anim = QVariantAnimation(self)
        anim.setStartValue(current)       # стартуем с текущего значения:
        anim.setEndValue(target)          # нажатие можно прервать на полпути
        anim.setDuration(60 if motion.reduced_motion() else
                         (motion.PRESS_MS if name == "press" else motion.HOVER_MS))
        # Наведение — единственный случай, где уместен симметричный `ease`;
        # нажатие должно стартовать резко.
        anim.setEasingCurve(QEasingCurve.InOutSine if name == "hover"
                            else QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda value: self._apply(name, value))
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        setattr(self, f"_{name}_anim", anim)

    def _apply(self, name: str, value: float) -> None:
        setattr(self, f"_{name}", float(value))
        self.update()

    # ---- цвета -----------------------------------------------------------
    def _palette(self) -> tuple[QColor, QColor, QColor]:
        """Фон, рамка, цвет содержимого — под текущий вид и доступность."""
        primary = self.objectName() == "primary"
        if not self.isEnabled():
            return (QColor(255, 255, 255, 8), QColor(255, 255, 255, 12),
                    QColor(theme.TEXT_DIM))
        if primary:
            return (QColor(theme.ACCENT), QColor(255, 255, 255, 46), QColor("#FFFFFF"))
        return (QColor(255, 255, 255, 16), QColor(255, 255, 255, 26), QColor(theme.TEXT))

    # ---- отрисовка -------------------------------------------------------
    def paintEvent(self, _event) -> None:
        background, border, foreground = self._palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        scale = 1.0 - (1.0 - PRESS_SCALE) * self._press
        if scale < 1.0:
            centre = QPointF(self.width() / 2.0, self.height() / 2.0)
            painter.translate(centre)
            painter.scale(scale, scale)
            painter.translate(-centre)

        body = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = QPainterPath()
        path.addRoundedRect(body, self.radius, self.radius)
        painter.fillPath(path, background)
        if self._hover > 0.0 and self.isEnabled():
            painter.fillPath(path, QColor(255, 255, 255, int(HOVER_ALPHA * self._hover)))
        if self._press > 0.0:
            painter.fillPath(path, QColor(0, 0, 0, int(PRESS_TINT * self._press)))

        if self.hasFocus():
            border = QColor("#FFFFFF") if self.objectName() == "primary" \
                else QColor(255, 255, 255, 90)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        self._draw_content(painter, foreground)

    def _draw_content(self, painter: QPainter, colour: QColor) -> None:
        icon_size = self.iconSize() if not self.icon().isNull() else QSize(0, 0)
        metrics = self.fontMetrics()
        text_width = metrics.horizontalAdvance(self.text()) if self.text() else 0
        gap = _ICON_GAP if (icon_size.width() and text_width) else 0
        total = icon_size.width() + gap + text_width
        x = (self.width() - total) / 2.0

        if icon_size.width():
            mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
            pixmap = self.icon().pixmap(icon_size, self.devicePixelRatio(), mode)
            painter.drawPixmap(
                QPointF(x, (self.height() - icon_size.height()) / 2.0), pixmap)
            x += icon_size.width() + gap

        if text_width:
            painter.setPen(colour)
            painter.drawText(
                QRectF(x, 0, text_width, self.height()),
                Qt.AlignVCenter | Qt.AlignLeft, self.text())
